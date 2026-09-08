"""
Fonte dos dados de tempo real + montagem do <contexto> que vai pro LLM.

Reproduz a `buscar_contexto()` de entregas/chatbot.py (roteador tempo-real x
historico, fronteira `acesso_gestao`), mas com uma chave: a variavel de ambiente
CHARGEGRID_FONTE decide de onde vem o dado de tempo real.

  CHARGEGRID_FONTE=stub  (default) -> dados fixos abaixo. Roda offline, determina
                                      o resultado, usado nos evals e no comparativo.
  CHARGEGRID_FONTE=real            -> importa legado/ev_chargegrid.py e consulta
                                      o Postgres de verdade (precisa de DATABASE_URL).

O stub existe porque o eval tem que ser reprodutivel: se a resposta dependesse do
estado ao vivo do banco, rodar o comparativo duas vezes daria numeros diferentes.
"""

from __future__ import annotations

import os

from src.rag import buscar_documentos

# Mesmas palavras-gatilho do legado (entregas/chatbot.py).
PALAVRAS_TEMPO_REAL = [
    "agora", "hoje", "atual", "ativo", "ativa", "livre", "ocupado", "ocupada",
    "faturamento", "sessoes de hoje", "quantas sessoes", "status",
    "disponivel", "carregando",
]

# --------------------------------------------------------------------- stub fixo
_STUB_STATUS_ESTACOES = {
    1: "Ocupada", 2: "Livre", 3: "Ocupada", 4: "Livre", 5: "Livre",
    6: "Livre", 7: "Ocupada", 8: "Livre",
}
_STUB_FATURAMENTO_DIA = 1284.60
_STUB_SESSOES_DIA = 27
_STUB_SESSOES_ATIVAS = [
    {"estacao": 1, "usuario": "ABC1D23", "kwh": 18.4, "valor": 34.10, "pagamento": "pix"},
    {"estacao": 3, "usuario": "EFG4H56", "kwh": 7.2, "valor": 13.90, "pagamento": "credito"},
    {"estacao": 7, "usuario": "IJK7L89", "kwh": 25.1, "valor": 46.30, "pagamento": "pix"},
]


def _fonte_real():
    """Importa o motor do Sistema-charge-gridd (copiado em legado/)."""
    import sys
    from pathlib import Path

    legado = Path(__file__).resolve().parent.parent.parent / "legado"
    if str(legado) not in sys.path:
        sys.path.insert(0, str(legado))
    import ev_chargegrid  # type: ignore

    return ev_chargegrid


def _dados_tempo_real() -> dict:
    if os.environ.get("CHARGEGRID_FONTE", "stub").lower() == "real":
        ev = _fonte_real()
        return {
            "status_estacoes": ev.obter_status_estacoes(),
            "faturamento_dia": ev.obter_faturamento_dia(),
            "sessoes_dia": ev.contar_sessoes_dia(),
            "sessoes_ativas": ev.listar_sessoes_ativas(),
        }
    return {
        "status_estacoes": _STUB_STATUS_ESTACOES,
        "faturamento_dia": _STUB_FATURAMENTO_DIA,
        "sessoes_dia": _STUB_SESSOES_DIA,
        "sessoes_ativas": _STUB_SESSOES_ATIVAS,
    }


def buscar_contexto(pergunta: str, acesso_gestao: bool = False) -> str:
    """Monta o texto de <contexto>. Igual ao legado:
    - padrao (acesso_gestao=False): so disponibilidade livre/ocupada.
    - acesso_gestao=True: tambem faturamento, sessoes do dia, sessoes ativas e o
      RAG historico comercial.
    """
    usa_tempo_real = any(p in pergunta.lower() for p in PALAVRAS_TEMPO_REAL)
    partes: list[str] = []

    if usa_tempo_real:
        try:
            d = _dados_tempo_real()
            livres = [k for k, v in d["status_estacoes"].items() if v == "Livre"]
            ocupadas = [k for k, v in d["status_estacoes"].items() if v == "Ocupada"]
            partes.append("[DISPONIBILIDADE DAS ESTACOES — agora]")
            partes.append(f"Estacoes ocupadas agora: {ocupadas if ocupadas else 'nenhuma'}")
            partes.append(f"Estacoes livres agora: {livres}")

            if acesso_gestao:
                partes.append(f"Faturamento de hoje (sessoes pagas): R$ {d['faturamento_dia']:.2f}")
                partes.append(f"Total de sessoes iniciadas hoje: {d['sessoes_dia']}")
                for s in d["sessoes_ativas"]:
                    partes.append(
                        f"Sessao ativa — Estacao {s['estacao']}: usuario {s['usuario']}, "
                        f"{s['kwh']:.2f} kWh consumidos, valor acumulado R$ {s['valor']:.2f}, "
                        f"pagamento via {s['pagamento']}."
                    )
        except Exception as e:  # noqa: BLE001
            partes.append(f"[AVISO] Fonte de dados indisponivel: {e}")

    elif acesso_gestao:
        relevantes = buscar_documentos(pergunta)
        if relevantes:
            partes.append("[DADOS HISTORICOS — planilha SP2, 60 sessoes reais]")
            partes.extend(relevantes)

    return "\n".join(partes)

"""
Dados do sistema (tempo real) e montagem do <contexto> que vai junto da pergunta.

Os numeros de tempo real sao fixos aqui de proposito: assim o eval da o mesmo
resultado toda vez que roda. Se dependessem do banco ao vivo, rodar a bateria
duas vezes daria numeros diferentes e a comparacao antes/depois nao valeria.

A fronteira `acesso_gestao` e a mesma do sistema em producao: sem ela, o chat so
enxerga quais estacoes estao livres ou ocupadas — nada de faturamento, sessoes de
outros clientes ou historico comercial.
"""

from __future__ import annotations

from src.rag import buscar_documentos

# Palavras que fazem a pergunta ser sobre o estado AGORA, e nao sobre historico.
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


def _dados_tempo_real() -> dict:
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

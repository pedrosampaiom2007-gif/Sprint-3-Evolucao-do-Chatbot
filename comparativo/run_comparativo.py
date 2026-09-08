"""
run_comparativo.py — roda a versao antiga e a nova nos mesmos casos.

Roda o MESMO eval set (evals/eval_set.json) contra a versao MANUAL/LEGADO
(legado/chatbot_legado.py, o entregas/chatbot.py das Sprints 1/2) e junta com os
numeros da versao LCEL (evals/sprint3_results.json, gerado por run_evals.py --prompt v2)
para montar a tabela antes/depois:

  | metrica                     | Sprints 1/2 (manual) | Sprint 03 (LCEL) |

Saidas:
  comparativo/legado_results.json
  comparativo/resultado_comparativo.json
  comparativo/tabela_antes_depois.md

Pre-requisito: rodar antes  ->  python -m evals.run_evals --prompt v2

--- patches aplicados no legado (documentados no relatorio) ---
1. groq SDK: injeta reasoning_format="hidden". O legado e anterior aos modelos
   com canal de raciocinio (gpt-oss); sem isso o .content volta VAZIO e a
   comparacao seria contra respostas em branco.
2. As 4 funcoes de leitura do banco sao trocadas pelo MESMO stub que a versao
   nova usa (src/integracao/dados_sistema), pra os dois lados verem dados iguais.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import types
from pathlib import Path

from dotenv import load_dotenv

_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "legado"))
load_dotenv(_RAIZ / ".env")

from src.contexto import contar_tokens  # noqa: E402
from src.integracao.dados_sistema import _dados_tempo_real  # noqa: E402
from evals.run_evals import JUIZ_PADRAO, checar_deterministico, fazer_juiz  # noqa: E402

# --- patch 1: reasoning_format no SDK do groq -------------------------------
# O legado e anterior aos modelos com canal de raciocinio (gpt-oss); sem isso o
# .content volta VAZIO e a comparacao seria contra respostas em branco.
from groq.resources.chat.completions import Completions  # noqa: E402

_orig_create = Completions.create


def _create_patched(self, *a, **kw):
    kw.setdefault("reasoning_format", "hidden")
    return _orig_create(self, *a, **kw)


Completions.create = _create_patched

# --- patch 2: ev_chargegrid falso (stub de dados) --------------------------
# O ev_chargegrid.py real puxa solar_optimizer, sklearn, psycopg2... Injetamos
# um modulo falso com as 5 funcoes que chatbot_legado importa, com os MESMOS
# dados que a versao nova usa, pra a comparacao ser justa e rodar offline.
_d = _dados_tempo_real()
_fake_ev = types.ModuleType("ev_chargegrid")
_fake_ev.listar_sessoes_ativas = lambda: _d["sessoes_ativas"]
_fake_ev.obter_status_estacoes = lambda: _d["status_estacoes"]
_fake_ev.obter_faturamento_dia = lambda *a, **k: _d["faturamento_dia"]
_fake_ev.contar_sessoes_dia = lambda *a, **k: _d["sessoes_dia"]
_fake_ev.inicializar_banco = lambda *a, **k: None
sys.modules["ev_chargegrid"] = _fake_ev

import chatbot_legado as legado  # noqa: E402


def _tokens_turno_legado(pergunta: str, acesso: bool, resposta: str) -> int:
    contexto = legado.buscar_contexto(pergunta, acesso_gestao=acesso)
    mensagem = f"Contexto do sistema:\n{contexto}\n\nPergunta: {pergunta}" if contexto else pergunta
    return contar_tokens(legado.SYSTEM_PROMPT) + contar_tokens(mensagem) + contar_tokens(resposta)


def rodar_legado(pausa: float = 16.0) -> dict:
    casos = json.loads((_RAIZ / "evals" / "eval_set.json").read_text(encoding="utf-8"))["casos"]
    julgar = fazer_juiz(JUIZ_PADRAO)
    resultados = []
    print(f"\n== LEGADO (manual, prompt v1) · {len(casos)} casos ==\n")
    for caso in casos:
        acesso = bool(caso.get("acesso_gestao", False))
        t0 = time.perf_counter()
        resp = "[sem resposta]"
        for _tent in range(4):
            try:
                resp = legado.responder(caso["pergunta"], acesso_gestao=acesso)
                break
            except Exception as e:  # noqa: BLE001
                if _tent == 3:
                    resp = f"[ERRO no legado apos retries: {e}]"
                    break
                time.sleep(15 * (_tent + 1))
        latencia = time.perf_counter() - t0

        det = checar_deterministico(caso, resp)
        nota, _ = julgar(caso["pergunta"], caso["resposta_esperada"], resp)
        toks = _tokens_turno_legado(caso["pergunta"], acesso, resp)

        resultados.append({
            "id": caso["id"], "categoria": caso["categoria"], "pergunta": caso["pergunta"],
            "resposta_obtida": resp, "passou_checagens": det["passou"],
            "nota_juiz": None if nota != nota else round(nota, 1),
            "latencia_s": round(latencia, 2), "tokens_turno": toks,
        })
        print(f"  {'OK ' if det['passou'] else 'XX '} [{caso['id']:>2}] {caso['categoria']:<15} "
              f"nota={'n/a' if nota != nota else f'{nota:.1f}'} {latencia:4.1f}s {toks:>4}tok", flush=True)
        time.sleep(pausa)

    notas = [r["nota_juiz"] for r in resultados if r["nota_juiz"] is not None]
    resumo = {
        "n_casos": len(resultados),
        "taxa_checagens_ok": round(sum(r["passou_checagens"] for r in resultados) / len(resultados), 3),
        "nota_media": round(statistics.mean(notas), 2) if notas else None,
        "latencia_media_s": round(statistics.mean(r["latencia_s"] for r in resultados), 2),
        "tokens_turno_medio": round(statistics.mean(r["tokens_turno"] for r in resultados), 1),
        "acuracia_structured_output": None,  # legado devolve texto livre — nao ha schema
    }
    return {"resumo": resumo, "resultados": resultados}


def _linha(nome, antes, depois):
    return f"| {nome} | {antes} | {depois} |"


def montar_tabela(legado_resumo: dict, lcel_resumo: dict) -> str:
    a, d = legado_resumo, lcel_resumo
    linhas = [
        "| Metrica | Sprints 1/2 (versao manual/legado) | Sprint 03 (LCEL) |",
        "|---|---|---|",
        _linha("Qualidade das respostas (nota media 0-10, LLM-juiz)", a["nota_media"], d["nota_media"]),
        _linha("Checagens deterministicas OK", f"{a['taxa_checagens_ok']*100:.0f}%", f"{d['taxa_checagens_ok']*100:.0f}%"),
        _linha("Tokens por turno (medio, aprox. tiktoken)", a["tokens_turno_medio"], d["tokens_turno_medio"]),
        _linha("Latencia media por turno", f"{a['latencia_media_s']} s", f"{d['latencia_media_s']} s"),
        _linha("Acuracia do structured output",
               "n/a (texto livre)",
               f"{d['acuracia_structured_output']*100:.0f}%" if d.get("acuracia_structured_output") is not None else "n/a"),
    ]
    return "\n".join(linhas)


def main() -> None:
    lcel_path = _RAIZ / "evals" / "sprint3_results.json"
    if not lcel_path.exists():
        sys.exit("Rode antes:  python -m evals.run_evals --prompt v2")
    lcel = json.loads(lcel_path.read_text(encoding="utf-8"))

    legado_res = rodar_legado()
    (_RAIZ / "comparativo" / "legado_results.json").write_text(
        json.dumps(legado_res, ensure_ascii=False, indent=2), encoding="utf-8")

    tabela = montar_tabela(legado_res["resumo"], lcel["resumo"])
    (_RAIZ / "comparativo" / "tabela_antes_depois.md").write_text(
        "# Comparativo antes/depois — Sprint 3\n\n" + tabela + "\n", encoding="utf-8")
    (_RAIZ / "comparativo" / "resultado_comparativo.json").write_text(
        json.dumps({"legado": legado_res["resumo"], "lcel": lcel["resumo"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + tabela + "\n")
    print("arquivos: comparativo/legado_results.json · resultado_comparativo.json · tabela_antes_depois.md\n")


if __name__ == "__main__":
    main()

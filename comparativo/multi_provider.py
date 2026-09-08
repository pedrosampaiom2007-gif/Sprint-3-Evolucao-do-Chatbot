"""
multi_provider.py — BONUS da Sprint 3 (+1 pt): "consultar mais de um modelo e
mais de um prompt".

Roda a MESMA pergunta em 2 modelos x 2 versoes de prompt (matriz 2x2) e imprime
as respostas lado a lado + tokens/latencia de cada combinacao. Serve de base pra
secao "multi-provider" do docs/relatorio_modelos.md.

Uso:
  python -m comparativo.multi_provider
  python -m comparativo.multi_provider "Qual o horario de pico?"
"""

from __future__ import annotations

import sys
import time

from dotenv import load_dotenv

from src.chain.builder import construir_chain_conversa
from src.contexto import contar_tokens

load_dotenv()

MODELOS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
PROMPTS = ["v1", "v2"]

PERGUNTA_PADRAO = "Qual ponto de carga teve mais receita no historico e por que?"


def rodar(pergunta: str) -> None:
    print(f"\nPergunta: {pergunta}\n" + "=" * 72)
    for modelo in MODELOS:
        for versao in PROMPTS:
            chain = construir_chain_conversa(
                versao_prompt=versao, acesso_gestao=True, model=modelo
            )
            t0 = time.perf_counter()
            try:
                resp = chain.invoke({"pergunta": pergunta})
            except Exception as e:  # noqa: BLE001
                resp = f"[ERRO: {e}]"
            dt = time.perf_counter() - t0
            print(f"\n--- modelo={modelo}  prompt={versao}  "
                  f"({dt:.1f}s, ~{contar_tokens(resp)} tok resposta) ---")
            print(resp.strip())
            time.sleep(8)  # TPM da conta free
    print("\n" + "=" * 72)


if __name__ == "__main__":
    rodar(sys.argv[1] if len(sys.argv) > 1 else PERGUNTA_PADRAO)

"""
app.py — CLI do chatbot refatorado (Sprint 3).

    python app.py --demo     3 turnos encadeados (evidencia de memoria p/ o relatorio)
    python app.py            conversa livre no terminal ('sair' encerra)

Precisa de GROQ_API_KEY no .env. A orquestracao (guardrails + chain + memoria)
vive em src/assistente.py — aqui e so a casca de terminal.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):  # stdout do Windows e cp1252 por padrao
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

if not os.environ.get("GROQ_API_KEY", "").strip():
    # Sem isto o erro vem como um traceback de 12 linhas la de dentro da
    # biblioteca da Groq, que nao ajuda ninguem a entender o que fazer.
    print(
        "\nFalta a GROQ_API_KEY.\n\n"
        "  1. Crie uma chave gratuita em https://console.groq.com/keys\n"
        "  2. Copie o arquivo .env.example para .env\n"
        "  3. Cole a chave na linha GROQ_API_KEY=\n\n"
        "Os testes offline nao precisam de chave nenhuma:\n"
        "  python -m unittest discover -s tests -v\n"
    )
    raise SystemExit(1)

from src.assistente import Assistente
from src.chain.memoria import limpar_sessao

# ferramenta interna da equipe -> acesso de gestao (ve faturamento/historico comercial).
_assistente = Assistente(versao_prompt="v2", acesso_gestao=True)


DEMO_TURNOS = [
    "Qual carregador teve mais receita no historico?",
    "E o segundo colocado?",                        # so acerta se lembrou do turno 1
    "Quanto foi o ticket medio desse primeiro?",    # "primeiro" = CP-09, do turno 1
]


def rodar_demo() -> None:
    sid = "demo"
    limpar_sessao(sid)
    print("=== DEMO — memoria em 3 turnos (mesmo session_id) ===\n")
    for i, pergunta in enumerate(DEMO_TURNOS, 1):
        print(f"[turno {i}] Voce: {pergunta}")
        print(f"[turno {i}] Bot : {_assistente.responder(pergunta, sid).resposta}\n")


def repl() -> None:
    sid = "terminal"
    print("ChargeGrid Intelligence — chatbot Sprint 3 (LCEL). 'sair' encerra.\n")
    while True:
        try:
            pergunta = input("Voce: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAte mais!")
            break
        if not pergunta:
            continue
        if pergunta.lower() in ("sair", "exit", "quit"):
            print("Ate mais!")
            break
        print(f"Bot : {_assistente.responder(pergunta, sid).resposta}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chatbot ChargeGrid — Sprint 3")
    parser.add_argument("--demo", action="store_true", help="roda os 3 turnos de demonstracao de memoria")
    args = parser.parse_args()
    rodar_demo() if args.demo else repl()

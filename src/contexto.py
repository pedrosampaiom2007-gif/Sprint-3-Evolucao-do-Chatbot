"""
Medicao de tokens com tiktoken.

Serve para: (1) preencher a coluna "tokens do prompt" da tabela de versoes
(prompts/CHANGELOG_PROMPTS.md), (2) medir tokens-por-turno no eval, (3) alimentar
o corte por orcamento de tokens da memoria (src/chain/memoria.py).

Os modelos do Groq (gpt-oss, llama) NAO estao no registro do tiktoken, entao a
gente usa o encoder `cl100k_base` (o do GPT-4 / GPT-3.5) como aproximacao. Nao e
a contagem exata que o Groq cobra, mas e consistente entre as duas versoes do
prompt — que e o que o comparativo precisa. Isso esta documentado no relatorio.
"""

from __future__ import annotations

import tiktoken

_ENCODER = tiktoken.get_encoding("cl100k_base")


def contar_tokens(texto: str) -> int:
    """Numero aproximado de tokens de um texto."""
    return len(_ENCODER.encode(texto))


def contar_tokens_mensagens(mensagens: list) -> int:
    """Soma dos tokens de uma lista de mensagens (dicts {'role','content'} ou
    objetos BaseMessage do LangChain). Aproximacao: conteudo + ~4 tokens de
    overhead por mensagem, mesma heuristica que a OpenAI documenta para o
    formato de chat."""
    total = 0
    for m in mensagens:
        conteudo = getattr(m, "content", None)
        if conteudo is None and isinstance(m, dict):
            conteudo = m.get("content", "")
        total += contar_tokens(str(conteudo)) + 4
    return total

"""
sanitizar_resposta — rede de seguranca de formatacao, portada de
entregas/chatbot.py (_sanitizar_formatacao).

O <tom_de_voz> do system prompt proibe tabela e cabecalho markdown, mas prompt
nao e garantia: a mesma pergunta as vezes volta com tabela inteira. O balao de
chat e estreito e o front nao renderiza tabela — apareceria "|" literal na tela.
Esta funcao e a segunda camada: converte cabecalho em **negrito** e linha de
tabela em item de lista. E o ultimo Runnable da chain de conversa.
"""

from __future__ import annotations

import re

_RE_CABECALHO = re.compile(r"^#{1,6}\s+(.+)")
_RE_LINHA_SEPARADORA_TABELA = re.compile(r"^\|?[\s:|-]+\|[\s:|-]*\|?$")


def sanitizar_resposta(texto: str) -> str:
    linhas_saida: list[str] = []
    for linha in texto.split("\n"):
        bruta = linha.strip()

        cabecalho = _RE_CABECALHO.match(bruta)
        if cabecalho:
            linhas_saida.append(f"**{cabecalho.group(1)}**")
            continue

        if "-" in bruta and _RE_LINHA_SEPARADORA_TABELA.match(bruta):
            continue  # linha "|---|---|" — descarta

        if bruta.startswith("|") and bruta.endswith("|") and len(bruta) > 1:
            celulas = [c.strip() for c in bruta.strip("|").split("|") if c.strip()]
            if len(celulas) >= 2:
                linhas_saida.append(f"- **{celulas[0]}**: {' — '.join(celulas[1:])}")
            elif celulas:
                linhas_saida.append(f"- {celulas[0]}")
            continue

        linhas_saida.append(linha)

    return "\n".join(linhas_saida)

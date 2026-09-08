"""
moderation.py — deteccao de jailbreak / prompt injection ANTES de gastar uma
chamada de LLM (bloco C da rubrica, 15 pts).

Camada de codigo, nao so regra no prompt: se a pergunta bate com um padrao
conhecido de "me faca sair do personagem", a gente recusa na hora, sem mandar
pro modelo. Mais rapido, mais barato, e nao depende do modelo "se comportar".

Nao e a prova de tudo — e uma lista de padroes. A regra no system prompt v2
(<regras_invioaveis>) e a segunda camada, para o que passar daqui.
"""

from __future__ import annotations

import re
import unicodedata

RESPOSTA_PADRAO = (
    "Nao posso fazer isso. Posso ajudar com o Charge Grid Intelligence ou "
    "com duvidas sobre carros eletricos."
)

# padrao -> rotulo (o rotulo entra no log / no eval)
_PADROES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bignore?\b.*\b(instru|regra|acima|anterior|tudo)"), "ignore-instrucoes"),
    (re.compile(r"\besque(c|ç)a\b.*\b(instru|regra|tudo|acima)"), "esqueca-instrucoes"),
    (re.compile(r"\bdesconsidere?\b.*\b(instru|regra|acima|anterior)"), "desconsidere-instrucoes"),
    (re.compile(r"\b(aja|atue|comporte-se|finja)\b.*\bcomo\b.*\b(se|um|uma)\b"), "roleplay"),
    (re.compile(r"\bvoce\b.*\b(agora|a partir de agora)\b.*\b(e|sera|vai ser)\b"), "troca-de-papel"),
    (re.compile(r"\b(a partir de agora|de agora em diante)\b.{0,40}\b(voce|vc|assistente|sera|responda|aja|ignore|passa a)\b"), "troca-de-papel"),
    (re.compile(r"\b(voce|vc|assistente)\b.{0,20}\b(e|sera|vira)\b.{0,30}\b(livre|sem restri|sem limit|sem regra|ilimitad|sem filtro)\b"), "pedido-sem-limites"),
    (re.compile(r"\b(modo|mode)\b.*\b(desenvolvedor|developer|dev|deus|god|dan|sem restri)"), "modo-especial"),
    (re.compile(r"\b(revele?|mostre?|me d(e|ê)|imprima|repita|qual (e|é) o)\b.*\b(system ?prompt|prompt de sistema|suas instru|prompt inicial)"), "vazar-prompt"),
    (re.compile(r"\bsem\b.*\b(filtro|censura|restri|regra|limita)"), "pedido-sem-limites"),
    (re.compile(r"\bprompt injection\b|\bjailbreak\b"), "termo-explicito"),
    (re.compile(r"\bhipoteticamente\b.*\b(sem|ignore|nao precisa)\b"), "hipotetico-evasivo"),
    (re.compile(r"\[\s*admin\s*\]|\bacesso total\b|\bpermissao total\b|\bsou (o |a )?(admin|administrador|gestor)\b|\bmodo admin\b"), "escalonamento-de-acesso"),
    (re.compile(r"\bcontexto do sistema\b\s*:|\bsystem\s*:\s*\[|\bnova instrucao\b\s*:"), "contexto-falso-injetado"),
]


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def detectar_injection(texto: str) -> str | None:
    """Devolve o rotulo do padrao que casou, ou None se nada suspeito."""
    alvo = _normalizar(texto)
    for padrao, rotulo in _PADROES:
        if padrao.search(alvo):
            return rotulo
    return None

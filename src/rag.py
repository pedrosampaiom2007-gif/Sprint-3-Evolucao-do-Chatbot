"""
RAG por palavras-chave — portado quase igual de entregas/chatbot.py do
Sistema-charge-gridd (busca com stopwords, sem acento, palavra inteira +
prefixo, ordenado por numero de termos que casaram).

Ficou fora do chain como um modulo simples: o chain so chama `buscar_documentos`
dentro do passo que monta o <contexto> (src/integracao/dados_sistema.py).
Manter identico ao legado e de proposito — o comparativo antes/depois tem que
comparar o LCEL contra o mesmo RAG, senao a diferenca medida seria do RAG, nao
do refactory.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
with open(_RAIZ / "dados_rag.json", encoding="utf-8") as _f:
    _dados_rag = json.load(_f)

documentos: list[str] = _dados_rag["frases_contexto_rag"]

_STOPWORDS = {
    "a", "as", "ao", "aos", "com", "como", "da", "das", "de", "do", "dos", "e",
    "ele", "ela", "em", "essa", "esse", "esta", "este", "eu", "foi", "ha",
    "isso", "ja", "la", "mais", "mas", "me", "meu", "minha", "muito", "na",
    "nao", "nas", "no", "nos", "num", "o", "os", "ou", "para", "pela", "pelo",
    "por", "pra", "pro", "qual", "quais", "quando", "quanto", "quantos", "que",
    "se", "sem", "ser", "sao", "so", "sua", "seu", "tem", "ter", "um", "uma",
    "voce", "vc",
}


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def _tokenizar(texto: str) -> list[str]:
    palavra: list[str] = []
    palavras: list[str] = []
    for c in _normalizar(texto):
        if c.isalnum():
            palavra.append(c)
        elif palavra:
            palavras.append("".join(palavra))
            palavra = []
    if palavra:
        palavras.append("".join(palavra))
    return palavras


def _palavras_uteis(pergunta: str) -> list[str]:
    return [p for p in _tokenizar(pergunta) if len(p) >= 3 and p not in _STOPWORDS]


def _casa(termo: str, palavras_doc: set[str]) -> bool:
    if termo in palavras_doc:
        return True
    return len(termo) >= 5 and any(p.startswith(termo) for p in palavras_doc)


def buscar_documentos(pergunta: str, limite: int = 5) -> list[str]:
    """Documentos do RAG ordenados por quantos termos da pergunta eles contem.
    Nada casou -> lista vazia (contexto errado e pior que contexto nenhum)."""
    termos = _palavras_uteis(pergunta)
    if not termos:
        return []
    pontuados: list[tuple[int, str]] = []
    for doc in documentos:
        palavras_doc = set(_tokenizar(doc))
        pontos = sum(1 for t in termos if _casa(t, palavras_doc))
        if pontos:
            pontuados.append((pontos, doc))
    pontuados.sort(key=lambda par: par[0], reverse=True)
    return [doc for _, doc in pontuados[:limite]]

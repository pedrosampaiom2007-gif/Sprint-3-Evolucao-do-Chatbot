"""
scope_validator.py — valida se a pergunta esta no escopo GoodWe / EV / CGI ANTES
de chamar o LLM (bloco C da rubrica).

Tres saidas possiveis:
  "ok"               -> segue para o chain normalmente.
  "fora_de_escopo"   -> nao tem a ver com recarga/carro eletrico/CGI. Recusa padrao.
  "dominio_restrito" -> e sobre aconselhamento juridico / financeiro / seguranca
                        eletrica. Encaminha a profissional habilitado.

A heuristica e deliberadamente simples (conjuntos de palavras-chave, sem acento).
Serve de primeiro filtro barato; o system prompt v2 e a rede de tras.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_TERMOS_ESCOPO = {
    # sistema
    "chargegrid", "charge grid", "cgi", "eletroposto", "eletropostos", "estacao",
    "estacoes", "carregador", "carregadores", "recarga", "recargas", "sessao",
    "sessoes", "faturamento", "receita", "ticket", "tarifa", "tarifas", "kwh",
    "dlb", "pico", "demanda", "totem", "ocpp", "goodwe", "sems", "frota", "frotas",
    "ponto de carga", "livre", "livres", "ocupada", "ocupadas", "ocupado",
    "ocupados", "disponivel", "disponiveis",
    # carro eletrico
    "carro eletrico", "carros eletricos", "veiculo eletrico", "ev", "bateria",
    "baterias", "autonomia", "conector", "conectores", "tomada", "plug", "tipo 2",
    "ccs", "corrente alternada", "corrente continua", "ac", "dc", "regenerativa",
    "carregamento", "carregar", "eletrico", "eletricos", "hibrido",
}

_TERMOS_JURIDICO = {
    "processo", "processar", "juridico", "juridica", "advogado", "contrato",
    "clausula", "lei", "legislacao", "direito", "acao judicial", "indenizacao",
    "multa", "responsabilidade civil", "lgpd",
}
_TERMOS_FINANCEIRO = {
    "investir", "investimento", "acao", "acoes", "bolsa", "cripto", "bitcoin",
    "financiamento", "emprestimo", "juros", "rendimento", "aplicar dinheiro",
    "vale a pena comprar", "retorno financeiro", "payback", "roi",
}
_TERMOS_SEGURANCA_ELETRICA = {
    "quadro de luz", "quadro de energia", "disjuntor", "fiacao", "fio", "bitola",
    "aterramento", "choque", "curto", "curto-circuito", "incendio", "220v", "110v",
    "instalar em casa", "ligar direto", "padrao de entrada", "carga do poste",
}


@dataclass
class ResultadoEscopo:
    categoria: str  # "ok" | "fora_de_escopo" | "dominio_restrito"
    subdominio: str | None = None  # "juridico" | "financeiro" | "seguranca_eletrica"
    resposta_padrao: str | None = None


_RECUSA_FORA = (
    "So consigo ajudar com questoes relacionadas a carros eletricos e ao "
    "Charge Grid Intelligence."
)
_RECUSA_RESTRITO = {
    "juridico": "Isso e uma questao juridica — vale falar com um advogado. "
                "Posso ajudar com a operacao do Charge Grid Intelligence.",
    "financeiro": "Nao dou orientacao de investimento — procure um consultor "
                  "financeiro habilitado. Posso explicar como funciona a "
                  "tarifacao do Charge Grid Intelligence, se ajudar.",
    "seguranca_eletrica": "Isso envolve risco eletrico e depende da instalacao "
                          "do local — fale com um eletricista ou engenheiro "
                          "eletricista. Posso explicar como funciona a recarga no geral.",
}


def _norm(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def _tokens(texto: str) -> set[str]:
    """Palavras inteiras, sem acento. Evita 'acao' casar dentro de 'estacoes'."""
    palavra: list[str] = []
    out: set[str] = set()
    for c in _norm(texto):
        if c.isalnum():
            palavra.append(c)
        elif palavra:
            out.add("".join(palavra))
            palavra = []
    if palavra:
        out.add("".join(palavra))
    return out


def _casa_algum(pergunta_norm: str, pergunta_tokens: set[str], termos: set[str]) -> bool:
    """Termo de uma palavra -> tem que ser token inteiro da pergunta.
    Termo com espaco (frase) -> substring no texto normalizado."""
    for t in termos:
        tn = _norm(t)
        if " " in tn:
            if tn in pergunta_norm:
                return True
        elif tn in pergunta_tokens:
            return True
        elif len(tn) >= 5 and any(tok.startswith(tn) for tok in pergunta_tokens):
            return True  # 'carregador' casa 'carregadores', 'recarga' casa 'recargas'
    return False


def avaliar_escopo(pergunta: str) -> ResultadoEscopo:
    alvo = _norm(pergunta)
    toks = _tokens(pergunta)

    for sub, termos in (
        ("juridico", _TERMOS_JURIDICO),
        ("financeiro", _TERMOS_FINANCEIRO),
        ("seguranca_eletrica", _TERMOS_SEGURANCA_ELETRICA),
    ):
        if _casa_algum(alvo, toks, termos):
            return ResultadoEscopo("dominio_restrito", sub, _RECUSA_RESTRITO[sub])

    if _casa_algum(alvo, toks, _TERMOS_ESCOPO):
        return ResultadoEscopo("ok")

    return ResultadoEscopo("fora_de_escopo", resposta_padrao=_RECUSA_FORA)

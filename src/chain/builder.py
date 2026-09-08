"""
builder.py — a chain LCEL end-to-end (Aula 01).

    montar_contexto | prompt | llm | parser

Cada `|` liga um Runnable no proximo: a saida da esquerda e a entrada da direita.
Isso substitui a montagem manual de `messages` + `client.chat.completions.create`
que o entregas/chatbot.py faz na mao.

Dois sabores de chain, porque a Sprint 3 pede as duas coisas e elas nao combinam
num pipe so:
  - construir_chain_conversa()   -> saida: str (texto). Usada no app.py com memoria
                                    (RunnableWithMessageHistory precisa de str/BaseMessage
                                    para guardar no historico).
  - construir_chain_estruturada() -> saida: ConsultaRecarga (objeto Pydantic validado).
                                    Usada no eval de structured output.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable, RunnableLambda

from src.integracao.dados_sistema import buscar_contexto
from src.schemas.consulta_recarga import ConsultaRecarga
from src.util_formato import sanitizar_resposta

_RAIZ = Path(__file__).resolve().parent.parent.parent

# defaults do LLM — documentados no relatorio_modelos.md
GROQ_MODEL_PADRAO = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
TEMPERATURA_PADRAO = 0.4          # baixa: respostas mais previsiveis (mesma escolha do legado)
MAX_TOKENS_PADRAO = 450          # teto de seguranca contra textao (mesma escolha do legado)
TOP_P_PADRAO = 1.0

# Os modelos gpt-oss da Groq respondem em dois canais (raciocinio + resposta final).
# Sem isto, o langchain-groq devolve TUDO em additional_kwargs['reasoning_content'] e
# .content vem VAZIO. "hidden" joga so a resposta final no .content, que e o que a
# chain de conversa consome. Modelo sem raciocinio ignora o parametro.
REASONING_FORMAT_PADRAO = "hidden"


# ----------------------------------------------------------------- prompt loader
def carregar_prompt(versao: str = "v2") -> str:
    """Le prompts/system_prompt_<versao>.md e tira o comentario-cabecalho <!-- ... -->."""
    caminho = _RAIZ / "prompts" / f"system_prompt_{versao}.md"
    texto = caminho.read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->\s*", "", texto, count=1, flags=re.DOTALL).strip()


# --------------------------------------------------------------------------- LLM
def construir_llm(
    *,
    model: str = GROQ_MODEL_PADRAO,
    temperature: float = TEMPERATURA_PADRAO,
    max_tokens: int = MAX_TOKENS_PADRAO,
    top_p: float = TOP_P_PADRAO,
    reasoning_format: str | None = REASONING_FORMAT_PADRAO,
):
    """ChatGroq — o Runnable do modelo. Trocar de provider = trocar so esta funcao."""
    from langchain_groq import ChatGroq  # import tardio: so quem roda o chain precisa da lib

    extra = {}
    if reasoning_format and "gpt-oss" in model:
        extra["reasoning_format"] = reasoning_format
    return ChatGroq(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        model_kwargs={"top_p": top_p},
        max_retries=6,  # conta free tier tem TPM baixo (8000) -> 429 frequente; backoff automatico
        **extra,
    )


# ------------------------------------------------------------------------ prompt
def _montar_chat_prompt(versao_prompt: str) -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", carregar_prompt(versao_prompt)),
        MessagesPlaceholder("historico", optional=True),  # a memoria injeta aqui
        ("human",
         "<contexto>\n{contexto}\n</contexto>\n\n"
         "<pergunta>\n{pergunta}\n</pergunta>"),
    ])


# ---------------------------------------------------- passo que preenche contexto
def _passo_contexto(acesso_gestao_padrao: bool) -> Runnable:
    """RunnableLambda: recebe {'pergunta': ...} e devolve o mesmo dict com
    'contexto' preenchido (roda o RAG / roteador de tempo real). E o primeiro
    elo do pipe — transforma a entrada crua no que o prompt espera.

    `acesso_gestao` e resolvido POR CHAMADA: se vier no dict de entrada, vale
    ele; senao, o padrao da chain. Assim a mesma chain atende o totem (restrito)
    e a ferramenta interna (gestao) sem remontar nada — e o eval consegue testar
    o caminho restrito de verdade.

    Detalhe de context engineering: uma pergunta de continuacao ("e o segundo?")
    nao tem palavra-chave, entao o RAG devolve vazio. Se ja existe historico, em
    vez de dizer "(sem dados)" — o que faz o modelo responder "nao tenho essa
    informacao" mesmo tendo acabado de falar sobre o assunto — a gente instrui a
    usar a conversa anterior."""
    def preencher(entrada: dict) -> dict:
        pergunta = entrada["pergunta"]
        acesso = bool(entrada.get("acesso_gestao", acesso_gestao_padrao))
        contexto = buscar_contexto(pergunta, acesso_gestao=acesso)
        if not contexto:
            tem_historico = bool(entrada.get("historico"))
            contexto = (
                "(sem dados novos nesta pergunta — use o que ja foi dito na conversa acima)"
                if tem_historico else "(sem dados no contexto)"
            )
        return {**entrada, "contexto": contexto}
    return RunnableLambda(preencher)


# ------------------------------------------------------------------------ chains
def construir_chain_conversa(
    *, versao_prompt: str = "v2", acesso_gestao: bool = False, **llm_kwargs
) -> Runnable:
    """montar_contexto | prompt | llm | StrOutputParser | sanitizar  ->  devolve str.
    O ultimo elo (RunnableLambda) e a rede de seguranca de formatacao."""
    prompt = _montar_chat_prompt(versao_prompt)
    llm = construir_llm(**llm_kwargs)
    return (
        _passo_contexto(acesso_gestao)
        | prompt
        | llm
        | StrOutputParser()
        | RunnableLambda(sanitizar_resposta)
    )


def construir_chain_estruturada(
    *, versao_prompt: str = "v2", acesso_gestao: bool = False, **llm_kwargs
) -> Runnable:
    """montar_contexto | prompt | llm.with_structured_output(ConsultaRecarga)
    ->  devolve um objeto ConsultaRecarga ja validado pelos field_validator."""
    prompt = _montar_chat_prompt(versao_prompt)
    llm = construir_llm(**llm_kwargs)
    return _passo_contexto(acesso_gestao) | prompt | llm.with_structured_output(ConsultaRecarga)


if __name__ == "__main__":
    # `python -m src.chain.builder` — precisa de GROQ_API_KEY no ambiente.
    from dotenv import load_dotenv

    load_dotenv()
    chain = construir_chain_conversa(acesso_gestao=True)
    print(chain.invoke({"pergunta": "Qual carregador rendeu mais no historico?"}))

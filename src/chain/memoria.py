"""
memoria.py — memoria de conversa por sessao, com limite de TOKENS (Aula 02).

Diferenca para o legado (`_janela_do_historico` em entregas/chatbot.py): la o corte
era por CONTAGEM DE MENSAGENS (5 trocas). Aqui e por ORCAMENTO DE TOKENS — 5 trocas
curtas ocupam pouco, 5 trocas longas estouram o contexto. Contando token de verdade
(via tiktoken em src/contexto.py), o custo e a latencia de cada turno passam a ter teto.

Peca do LangChain: `RunnableWithMessageHistory` envelopa a chain e, a cada invoke:
  1. busca o historico daquela session_id
  2. injeta no MessagesPlaceholder("historico") do prompt
  3. depois da resposta, salva a nova troca (pergunta + resposta) no historico
"""

from __future__ import annotations

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.runnables.history import RunnableWithMessageHistory

from src.contexto import contar_tokens_mensagens

MAX_TOKENS_HISTORICO_PADRAO = 800


class HistoricoJanelaTokens(BaseChatMessageHistory):
    """Guarda as mensagens da sessao e descarta as mais antigas ate caber no
    orcamento de tokens. E o equivalente ao ConversationTokenBufferMemory."""

    def __init__(self, max_tokens: int = MAX_TOKENS_HISTORICO_PADRAO) -> None:
        self.max_tokens = max_tokens
        self._mensagens: list[BaseMessage] = []

    @property
    def messages(self) -> list[BaseMessage]:
        return self._mensagens

    def add_messages(self, messages: list[BaseMessage]) -> None:
        self._mensagens.extend(messages)
        self._aparar()

    def _aparar(self) -> None:
        # remove do inicio (mais antigo) ate caber; mantem pelo menos a ultima troca
        while len(self._mensagens) > 2 and contar_tokens_mensagens(self._mensagens) > self.max_tokens:
            self._mensagens.pop(0)
        # se sobrou uma resposta do assistente "orfa" no topo, tira ela tambem
        if self._mensagens and isinstance(self._mensagens[0], AIMessage):
            self._mensagens.pop(0)

    def clear(self) -> None:
        self._mensagens = []


_STORE: dict[str, HistoricoJanelaTokens] = {}


def historico_da_sessao(session_id: str, *, max_tokens: int = MAX_TOKENS_HISTORICO_PADRAO) -> HistoricoJanelaTokens:
    if session_id not in _STORE:
        _STORE[session_id] = HistoricoJanelaTokens(max_tokens=max_tokens)
    return _STORE[session_id]


def limpar_sessao(session_id: str) -> None:
    _STORE.pop(session_id, None)


def com_memoria(chain: Runnable, *, max_tokens: int = MAX_TOKENS_HISTORICO_PADRAO) -> RunnableWithMessageHistory:
    """Envelopa uma chain de conversa (a que devolve str) com memoria por sessao.
    Uso:  resposta = chain_com_mem.invoke({"pergunta": "..."}, config={"configurable": {"session_id": "abc"}})
    """
    return RunnableWithMessageHistory(
        chain,
        lambda sid: historico_da_sessao(sid, max_tokens=max_tokens),
        input_messages_key="pergunta",
        history_messages_key="historico",
    )

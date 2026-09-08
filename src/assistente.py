"""
assistente.py — orquestracao de um turno: guardrails -> chain LCEL -> memoria.

Ponto unico usado pelo app.py (CLI) e pelo evals/run_evals.py, pra os dois
testarem exatamente o mesmo caminho.

    detectar_injection ─┐
                        ├─(barra sem gastar LLM)─> resposta padrao
    avaliar_escopo ─────┘
                        └─(ok)─> chain de conversa (com memoria por sessao)
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

try:
    from langchain_core._api import LangChainDeprecationWarning

    warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)
except Exception:  # noqa: BLE001
    warnings.filterwarnings("ignore", message=r".*RunnableWithMessageHistory.*")

from dotenv import load_dotenv

# Carrega o .env aqui tambem, e nao so no app.py: assim quem importar o
# Assistente direto (um script proprio, o REPL do Python) tambem acha a chave.
load_dotenv()

from src.chain.builder import construir_chain_conversa
from src.chain.memoria import com_memoria, historico_da_sessao
from src.guardrails.moderation import (
    RESPOSTA_PADRAO,
    detectar_injection,
    resposta_parece_vazamento,
)
from src.guardrails.scope_validator import avaliar_escopo


@dataclass
class Turno:
    resposta: str
    barrado_por: str | None  # None = passou pela chain; senao o rotulo do guardrail


class Assistente:
    """Guarda a chain (com memoria) montada uma vez. Uma instancia por
    configuracao (versao de prompt + modelo)."""

    def __init__(
        self,
        *,
        versao_prompt: str = "v2",
        acesso_gestao: bool = True,
        max_tokens_historico: int = 800,
        **llm_kwargs,
    ) -> None:
        self.versao_prompt = versao_prompt
        self._acesso_padrao = acesso_gestao
        chain = construir_chain_conversa(
            versao_prompt=versao_prompt, acesso_gestao=acesso_gestao, **llm_kwargs
        )
        self._chain = com_memoria(chain, max_tokens=max_tokens_historico)

    def responder(
        self, pergunta: str, session_id: str = "default", *, acesso_gestao: bool | None = None
    ) -> Turno:
        # --- guardrails de codigo: so os de ALTA precisao barram sem LLM ---
        # injection: padroes explicitos de ataque ("ignore as instrucoes", "aja como...").
        rotulo = detectar_injection(pergunta)
        if rotulo:
            return Turno(f"{RESPOSTA_PADRAO}   [guardrail: {rotulo}]", f"injection:{rotulo}")

        # dominio_restrito (juridico / financeiro / seguranca eletrica) e
        # comparacao de produtos ("qual e melhor?") -> recusa canonica.
        escopo = avaliar_escopo(pergunta)
        if escopo.categoria in ("dominio_restrito", "comparacao_produto"):
            sufixo = f"/{escopo.subdominio}" if escopo.subdominio else ""
            return Turno(
                f"{escopo.resposta_padrao}   [guardrail: {escopo.categoria}{sufixo}]",
                f"{escopo.categoria}{sufixo}",
            )

        # fora_de_escopo NAO barra por codigo: a whitelist de palavras-chave dava
        # falso positivo em pergunta legitima ("como e a cobranca no posto?"). Quem
        # trata "tem restaurante perto?" e a regra <fora_de_escopo> do prompt v2.
        acesso = self._acesso_padrao if acesso_gestao is None else acesso_gestao
        texto = self._chain.invoke(
            {"pergunta": pergunta, "acesso_gestao": acesso},
            config={"configurable": {"session_id": session_id}},
        )

        # guarda de SAIDA: se o modelo vazou trecho do prompt ou confirmou
        # "saida de personagem", troca por recusa e NAO grava isso na memoria
        # (senao o ataque fica "plantado" no historico da sessao).
        if resposta_parece_vazamento(texto):
            hist = historico_da_sessao(session_id)
            if hist.messages:
                del hist.messages[-2:]  # remove a pergunta + a resposta vazada
            return Turno(f"{RESPOSTA_PADRAO}   [guardrail: vazamento-na-saida]", "vazamento-na-saida")

        return Turno(texto, None)

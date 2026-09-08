"""
ConsultaRecarga — o formato estruturado da resposta do chatbot.

E o formato que o LLM tem que devolver quando roda pelo chain estruturado
(`construir_chain_estruturada` em src/chain/builder.py). Em vez de texto solto,
a resposta vira um objeto Python com campos tipados e VALIDADOS: se o modelo
alucinar uma estacao que nao existe ou um valor negativo, a validacao levanta
erro e a gente conta isso como "structured output invalido" na metrica de
acuracia (evals/run_evals.py) — em vez de deixar o dado sujo circular.

Pydantic v2: `field_validator` roda na construcao do objeto. Pode LIMPAR o valor
(ex.: "cp-9" -> "CP-09") devolvendo o valor corrigido, ou REJEITAR levantando
ValueError.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Estacoes que existem de fato na base (dados_rag.json -> receita_por_carregador).
# Serve para o validator barrar "CP-99" alucinado.
ESTACOES_VALIDAS = {f"CP-{n:02d}" for n in range(1, 11)}  # CP-01 .. CP-10

Intencao = Literal["tempo_real", "historico", "duvida_geral", "fora_de_escopo"]
Metrica = Literal[
    "receita", "kwh", "ticket_medio", "sessoes", "disponibilidade",
    "faturamento_dia", "pico_demanda", "tarifa", "nenhuma",
]


class ConsultaRecarga(BaseModel):
    """Resposta estruturada do assistente para uma pergunta do usuario."""

    model_config = {"extra": "forbid"}  # campo a mais na resposta do LLM = erro

    intencao: Intencao = Field(
        description="Classificacao da pergunta. 'tempo_real' = estado atual do "
        "sistema; 'historico' = base SP2; 'duvida_geral' = duvida de motorista "
        "sobre carro eletrico; 'fora_de_escopo' = nao tem a ver com EV/CGI."
    )
    estacao: Optional[str] = Field(
        default=None,
        description="Ponto de carga citado, no formato CP-NN. null se a pergunta "
        "nao for sobre uma estacao especifica.",
    )
    metrica: Metrica = Field(
        default="nenhuma",
        description="Qual numero a pergunta pede. 'nenhuma' para duvida conceitual.",
    )
    valor: Optional[float] = Field(
        default=None,
        description="Valor numerico da resposta (R$, kWh, contagem...), quando houver. "
        "So preencher com numero que veio do <contexto> — nunca estimado.",
    )
    unidade: Optional[str] = Field(
        default=None, description="Unidade do `valor`: 'R$', 'kWh', 'sessoes', 'h'..."
    )
    resposta_ao_usuario: str = Field(
        description="O texto que vai pra tela do chat. Segue o <tom_de_voz> do "
        "system prompt: 2-4 frases OU lista de ate 4 itens, sem tabela/cabecalho, "
        "terminando com uma pergunta de aprofundamento."
    )
    precisa_acesso_gestao: bool = Field(
        default=False,
        description="True se responder de verdade exigiria dado de negocio "
        "(faturamento, receita, ticket) que so a gestao pode ver.",
    )
    fontes: list[str] = Field(
        default_factory=list,
        description="Trechos de <contexto> em que a resposta se apoiou. Vazio se "
        "a resposta foi conceitual ou uma recusa.",
    )

    # ------------------------------------------------------------------ validators

    @field_validator("estacao", mode="before")
    @classmethod
    def normalizar_e_validar_estacao(cls, v: object) -> Optional[str]:
        """Aceita 'cp9', 'CP-9', 'cp 09' e normaliza para 'CP-09'. Rejeita
        qualquer coisa que nao case com o padrao ou que nao exista na base."""
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ValueError("estacao deve ser texto no formato CP-NN")
        m = re.search(r"cp[\s-]*0*(\d{1,2})", v.strip(), flags=re.IGNORECASE)
        if not m:
            raise ValueError(f"estacao '{v}' nao esta no formato CP-NN")
        normalizada = f"CP-{int(m.group(1)):02d}"
        if normalizada not in ESTACOES_VALIDAS:
            raise ValueError(f"estacao '{normalizada}' nao existe na base (CP-01..CP-10)")
        return normalizada

    @field_validator("valor")
    @classmethod
    def valor_nao_negativo(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("valor nao pode ser negativo")
        return v

    @field_validator("resposta_ao_usuario")
    @classmethod
    def resposta_nao_vazia_sem_tabela(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("resposta_ao_usuario nao pode ser vazia")
        if "|" in v or re.search(r"^\s*#{1,6}\s", v, flags=re.MULTILINE):
            # o <tom_de_voz> proibe tabela e cabecalho; se veio assim, e violacao
            raise ValueError("resposta_ao_usuario nao pode conter tabela ('|') nem cabecalho markdown")
        return v

    @field_validator("fontes")
    @classmethod
    def limpar_fontes(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if isinstance(s, str) and s.strip()]


if __name__ == "__main__":
    # Sanidade rapida: roda `python -m src.schemas.consulta_recarga`
    ok = ConsultaRecarga(
        intencao="historico",
        estacao="cp9",
        metrica="receita",
        valor=528.84,
        unidade="R$",
        resposta_ao_usuario="O CP-09 lidera o historico com R$ 528,84 em 6 sessoes. Quer ver os proximos?",
        fontes=["CP-09 e o carregador com maior receita historica: R$ 528.84 ..."],
    )
    print("OK ->", ok.model_dump())

    for ruim in (
        dict(intencao="historico", estacao="CP-99", resposta_ao_usuario="x"),
        dict(intencao="historico", valor=-3, resposta_ao_usuario="x"),
        dict(intencao="historico", resposta_ao_usuario="| a | b |"),
    ):
        try:
            ConsultaRecarga(**ruim)
        except Exception as e:  # noqa: BLE001
            print("rejeitado como esperado ->", type(e).__name__)

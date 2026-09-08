# Relatório de uso de modelos e parâmetros — Sprint 3

Ponto obrigatório (§6 do enunciado): comparar 2+ modelos e documentar
`temperature`, `top_p` e `max_tokens`.

## 1. Provedor e modelos

O time usa **Groq** (nuvem) desde a Sprint 2 — a mesma conta e a mesma
`GROQ_API_KEY` do sistema principal (`Sistema-charge-gridd`). O enunciado cita
`ChatOllama gpt-oss:120b` como **exemplo**; mantivemos Groq por já estar na stack
e não exigir rodar um modelo de 120B localmente.

Modelos comparados (ambos acessíveis na conta):

| Modelo | Papel no projeto |
|--------|------------------|
| `openai/gpt-oss-20b` | modelo padrão da chain (rápido, barato) |
| `openai/gpt-oss-120b` | comparação de qualidade + LLM-juiz dos evals |

> A conta (free tier) só dá acesso aos modelos `openai/gpt-oss-*`. `llama-3.1-8b-instant`
> e `llama-3.3-70b-versatile` retornam 404. Limite de 8000 tokens/minuto (TPM) — por
> isso o `run_evals.py` tem pausa entre casos.

## 2. Parâmetros usados

| Parâmetro | Valor | Por quê |
|-----------|-------|---------|
| `temperature` | **0.4** | Baixa. A mesma pergunta voltava ora como lista de 2 linhas, ora como "artigo" com tabela. Temperatura baixa deixa a saída mais previsível. Mesma escolha do legado. |
| `top_p` | **1.0** | Sem restrição de núcleo — o controle de variabilidade fica todo na `temperature`. |
| `max_tokens` | **450** | Teto contra "textão". Testado no legado: 250 cortava lista de 3 itens no meio; 450 dá folga para terminar o pensamento e ainda corta antes de um textão. **Conta o raciocínio dos modelos gpt-oss** — ver abaixo. |
| `reasoning_format` | **`hidden`** | Os `gpt-oss` respondem em dois canais (raciocínio + resposta final). Sem `hidden`, o `langchain-groq` devolve tudo em `reasoning_content` e `.content` vem **vazio**. `hidden` joga só a resposta final no `.content`. |
| `reasoning_effort` | *(default do modelo)* | Testado `low` — reduz latência e tokens, mas cortamos a qualidade só o suficiente para não valer a pena como padrão. |

Do LLM-juiz dos evals: `temperature=0.0` (avaliação tem que ser o mais
determinística possível), `max_tokens=500`, `reasoning_format=hidden`.

## 3. Comparação `gpt-oss-20b` × `gpt-oss-120b`

Medições diretas (mesma pergunta, `temperature=0.4`, `max_tokens=450`,
`reasoning_format=hidden`, sem contexto RAG para isolar o modelo):

| Modelo | Latência (1 chamada) | Tokens de saída | Comportamento observado |
|--------|----------------------|-----------------|--------------------------|
| `gpt-oss-20b` | ~0,9–1,6 s | ~70–360 | Rápido. Segue bem o `<tom_de_voz>`. Ocasionalmente pede "mais detalhes" cedo demais. |
| `gpt-oss-120b` | ~1,3–1,5 s | ~70–250 | Um pouco mais lento e mais conciso. Recusas mais firmes em pedido ambíguo. |

Bateria completa de evals rodada no **`gpt-oss-20b` com prompt v2** (`evals/sprint3_results.json`):
checagens 100 % (24/24) · recusa jailbreak/injection 100 % (12/12) ·
recusa out-of-scope/domínio 100 % · structured output 100 % (happy path) ·
nota média 9,2/10 (24 casos, LLM-juiz gpt-oss-120b).

Para reexecutar a bateria com o modelo maior:
`python -m evals.run_evals --prompt v2 --modelo openai/gpt-oss-120b`.

**Conclusão:** o `120b` traz ganho marginal de firmeza nas recusas, ao custo de
latência maior. Para um chat de totem/app, em que a resposta precisa ser quase
instantânea e as recusas já ficam em 100 % com o `20b` + guardrails de código, o
**`gpt-oss-20b` é a escolha de produção**; o `120b` fica como modelo do LLM-juiz
dos evals (onde qualidade importa mais que velocidade) e como segundo provedor do
bônus.

## 4. Bônus — multi-provider (2+ modelos e 2+ prompts)

`comparativo/multi_provider.py` roda a **mesma pergunta** na matriz
(gpt-oss-20b, gpt-oss-120b) × (prompt v1, prompt v2) — 2 modelos e 2 prompts — e
imprime as 4 respostas lado a lado com latência e tokens. Saída completa em
`comparativo/saida_multi_provider.txt`.

# Sprint 3 — Evolução do Chatbot ChargeGrid Intelligence

**EV Challenge — GoodWe / FIAP · Prompt and Artificial Intelligence · 2026.2**
Challenge · Sprint 03 · Professor Jorge Luiz Gomes

Refactory do núcleo conversacional do chatbot de gestão de eletropostos para
**LangChain (LCEL)**, evoluindo a versão manual das Sprints 1/2. O ganho é
demonstrado por um comparativo antes/depois (pasta `comparativo/`).

---

## Equipe

| Nome | RM |
|------|----|
| Luan de Araujo Carneiro | 573691 |
| Pedro Sampaio Mochnacs Arruda | 573522 |
| Raul Sampaio Mochnacs Arruda | 573523 |
| Pedro Ribeiro Lopes | 570083 |
| Kevin Rodrigues de Melo | 571777 |
| Pedro Vianna | 570747 |

---

## O que a Sprint 3 entrega (escopo do enunciado)

| # | Item | Onde |
|---|------|------|
| 1 | Chain LCEL end-to-end `prompt \| llm \| parser` | `src/chain/builder.py` |
| 2 | Memória por sessão com limite de **tokens**, 3+ turnos | `src/chain/memoria.py`, `app.py --demo` |
| 3 | Structured output Pydantic v2 (`ConsultaRecarga` + `field_validator`) | `src/schemas/consulta_recarga.py` |
| 4 | Context engineering: prompt versionado (XML) + medição com tiktoken | `prompts/`, `src/contexto.py` |
| 5 | Segurança e guardrails (jailbreak/injection + escopo GoodWe) | `src/guardrails/` |
| 6 | Eval set reexecutado + `sprint3_results.json` | `evals/` |
| 7 | Relatório de modelos e parâmetros | `docs/relatorio_modelos.md` |
| 8 | Relatório de evolução (PDF, ≤5 pág.) com tabela antes/depois | `docs/relatorio_evolucao.md` → `docs/relatorio_evolucao.pdf` |
| Bônus | Multi-provider (2+ modelos e 2+ prompts) | `comparativo/multi_provider.py` |

---

## Estrutura

```
prompts/
  system_prompt_v1.md      prompt legado (Sprints 1/2) — baseline, congelado
  system_prompt_v2.md      prompt refatorado (XML tagging, anti-injection, recusas de domínio)
  CHANGELOG_PROMPTS.md      tabela de versões: o que mudou, por quê, ganho medido
src/
  chain/builder.py         a chain LCEL (conversa -> str · estruturada -> ConsultaRecarga)
  chain/memoria.py         RunnableWithMessageHistory + janela por orçamento de tokens
  schemas/consulta_recarga.py  schema Pydantic v2 do domínio EV
  guardrails/moderation.py     detecção de jailbreak / prompt injection
  guardrails/scope_validator.py  escopo GoodWe / recusas de domínio (jurídico/financeiro/elétrico)
  rag.py                   RAG por palavra-chave (portado do legado, sem alteração)
  contexto.py              medição de tokens (tiktoken)
  integracao/dados_sistema.py  fonte dos dados de tempo real (stub offline | Postgres real)
  assistente.py            orquestração de um turno (guardrails -> chain -> memória)
  util_formato.py          rede de segurança de formatação (remove tabela/cabeçalho)
evals/
  eval_set.json            16 casos: happy path, edge cases, jailbreak, out-of-scope, domínio restrito
  run_evals.py             reexecuta e mede nota, tokens/turno, latência, acurácia do structured output
  sprint3_results.json     resultado (gerado)
comparativo/
  run_comparativo.py       legado (manual) × LCEL nos mesmos casos → tabela antes/depois
  multi_provider.py        bônus: matriz 2 modelos × 2 prompts
  tabela_antes_depois.md   (gerado)
docs/
  relatorio_modelos.md     2+ modelos, temperature / top_p / max_tokens
  relatorio_evolucao.md    fonte do PDF (estrutura do §8 do enunciado)
legado/
  chatbot_legado.py        cópia de entregas/chatbot.py (Sistema-charge-gridd) — o "antes"
  ev_chargegrid.py         motor de dados (só p/ o modo real / comparativo)
app.py                     CLI: --demo (3 turnos) | conversa livre
```

---

## Como rodar

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate no Linux/Mac)
pip install -r requirements.txt

copy .env.example .env            # e preencher GROQ_API_KEY
```

```bash
python app.py --demo                          # 3 turnos encadeados (prova de memória)
python app.py                                 # conversa livre no terminal

python -m evals.run_evals --prompt v2         # roda o eval set -> evals/sprint3_results.json
python -m evals.run_evals --prompt v1         # p/ comparar as duas versões de prompt
python -m comparativo.run_comparativo         # tabela antes/depois (rode o eval v2 antes)
python -m comparativo.multi_provider          # bônus multi-provider
python -m unittest discover -s tests -v       # testes de lógica pura (offline)
```

> **Sem API key no repositório.** `GROQ_API_KEY` vem do `.env` (gitignored). Os
> evals usam `CHARGEGRID_FONTE=stub` (dados fixos, offline) para dar resultado
> reprodutível.

---

## Arquitetura da chain

```
{"pergunta": "..."}
      |
      v
_passo_contexto        RunnableLambda — roda o RAG / roteador de tempo real, preenche <contexto>
      |
      v
ChatPromptTemplate     system (prompt vN) + MessagesPlaceholder(historico) + <contexto>/<pergunta>
      |
      v
ChatGroq               openai/gpt-oss-20b · temperature 0.4 · max_tokens 450 · reasoning_format hidden
      |
      +--> StrOutputParser  --> sanitizar_resposta         (chain de conversa -> str)
      +--> with_structured_output(ConsultaRecarga)         (chain estruturada -> objeto validado)
```

A chain de conversa é envelopada por `RunnableWithMessageHistory`, que injeta o
histórico da `session_id` no `MessagesPlaceholder` e o apara por orçamento de
tokens (`HistoricoJanelaTokens`, equivalente ao `ConversationTokenBufferMemory`).

---

## Limitações conhecidas

- **Conta Groq (free tier):** só modelos `openai/gpt-oss-*` disponíveis; TPM de
  8000 — por isso `run_evals.py` tem pausa entre casos.
- **RAG em pergunta de continuação:** "e o segundo?" não tem palavra-chave, o RAG
  volta vazio e o modelo se apoia só na memória. Ver `docs/relatorio_evolucao.md` §4.
- **Contagem de tokens** usa `cl100k_base` (tiktoken) como aproximação — os modelos
  gpt-oss não estão no registro do tiktoken.
- **`ev_chargegrid.py` (modo real)** depende de `DATABASE_URL` (Postgres/Supabase);
  o padrão é o stub.

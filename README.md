# Chatbot ChargeGrid Intelligence — Sprint 3

**EV Challenge — GoodWe / FIAP · Prompt and Artificial Intelligence · 2026.2**
Turma 1CCPG

Assistente de gestão de eletropostos. Nesta sprint o núcleo da conversa foi reescrito
em LangChain, com memória por tokens, resposta estruturada e uma camada de segurança
contra prompt injection. O relatório de evolução está em
[`docs/relatorio_evolucao.pdf`](docs/relatorio_evolucao.pdf).

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

## Rodando o projeto

Precisa de Python 3.11 ou mais novo.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / Mac

pip install -r requirements.txt
```

**Chave da API.** O chatbot usa a Groq. Crie uma chave gratuita em
[console.groq.com/keys](https://console.groq.com/keys), copie o `.env.example` para
`.env` e cole a chave lá:

```
GROQ_API_KEY=sua_chave_aqui
```

A chave do time não está no repositório — o `.env` é ignorado pelo git.

Depois disso:

```bash
python app.py                  # conversa livre no terminal ("sair" encerra)
python app.py --demo           # 3 turnos encadeados, mostrando a memória funcionando
```

### Testes

```bash
python -m unittest discover -s tests -v     # 21 testes, rodam offline, sem chave
python -m evals.run_evals --prompt v2       # bateria de 24 casos (precisa de chave)
python -m comparativo.run_comparativo       # compara com a versão antiga
```

> A conta gratuita da Groq limita 8 mil tokens por minuto. A bateria completa faz
> pausa entre os casos por causa disso; use `--pausa 5` se a sua conta for paga.

---

## Como o chatbot funciona

```
pergunta
   |
   v
guardrails         barram ataque e assunto perigoso antes de gastar chamada
   |
   v
busca de contexto  procura os dados do sistema que a pergunta pede
   |
   v
prompt             system prompt + historico da conversa + contexto + pergunta
   |
   v
modelo (Groq)      openai/gpt-oss-20b
   |
   +--> texto, limpo de tabela e cabecalho        (conversa normal)
   +--> objeto ConsultaRecarga validado           (quando se quer dado estruturado)
```

A conversa fica guardada por sessão e é cortada por orçamento de tokens, então o
custo e o tempo de cada turno têm teto.

---

## Onde está cada coisa

```
app.py                     o chatbot no terminal
src/
  assistente.py            junta guardrails + chain + memoria
  chain/builder.py         monta a chain (contexto | prompt | modelo | parser)
  chain/memoria.py         memoria por sessao, cortada por tokens
  schemas/                 ConsultaRecarga — a resposta estruturada e suas validacoes
  guardrails/moderation.py deteccao de prompt injection e conferencia da resposta
  guardrails/scope_validator.py  assunto fora do escopo e dominios que exigem profissional
  rag.py                   busca nos dados historicos
  contexto.py              contagem de tokens
  integracao/              dados do sistema (estacoes, faturamento, sessoes)
  util_formato.py          tira tabela e cabecalho da resposta
prompts/                   system prompt v1 e v2 + o que mudou entre eles
evals/                     24 casos de teste + o resultado da ultima execucao
comparativo/               versao antiga x versao nova, lado a lado
docs/                      relatorio de evolucao (PDF) e relatorio de modelos
legado/                    o chatbot das Sprints 1/2, usado so na comparacao
tests/                     testes que rodam offline
```

---

## Segurança

O chatbot recusa tentativa de prompt injection, pedido pra revelar o próprio prompt,
troca de personagem e pergunta fora do assunto. Também não dá conselho jurídico,
financeiro ou de instalação elétrica — nesses casos ele orienta a procurar um
profissional habilitado.

A detecção roda antes da chamada ao modelo e não se deixa enganar por texto ofuscado
(letra trocada por número, caractere invisível, letra espaçada). A resposta do modelo
também é conferida antes de sair.

Para testar: `python -m unittest tests.test_unit.TestModeration -v`.

---

## Limitações conhecidas

- Pergunta encadeada sem palavra-chave ("e o segundo?") não encontra dados na busca e
  o modelo responde só com o que está na memória.
- A contagem de tokens usa o tokenizador do GPT-4 como aproximação, porque os modelos
  `gpt-oss` não têm um público.
- Os dados de tempo real (estações livres, faturamento) são fixos, pra bateria de
  testes dar sempre o mesmo resultado.

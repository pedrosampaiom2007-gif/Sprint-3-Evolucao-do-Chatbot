# Modelos e parâmetros

## Qual provedor usamos

O time usa **Groq** desde a Sprint 2, com a mesma chave que o sistema principal já
usava. Ficamos nele porque o projeto inteiro já estava montado em cima disso e porque
rodar um modelo grande localmente ia travar o notebook de todo mundo.

Os dois modelos que a conta dá acesso:

| Modelo | Onde entra |
|--------|------------|
| `openai/gpt-oss-20b` | o modelo do chatbot — rápido e barato |
| `openai/gpt-oss-120b` | avalia as respostas na bateria de testes |

A conta é gratuita, então só os `gpt-oss` estão liberados (`llama-3.1` e `llama-3.3`
devolvem 404) e o limite é de 8 mil tokens por minuto.

## Parâmetros

| Parâmetro | Valor | Por quê |
|-----------|-------|---------|
| `temperature` | 0.4 | Testando ao vivo, a mesma pergunta às vezes voltava como uma lista de duas linhas e às vezes como um texto enorme com tabela. Baixar a temperatura deixou a resposta previsível. |
| `top_p` | 1.0 | Deixamos o controle todo na temperatura, pra ter só uma variável mexendo no resultado. |
| `max_tokens` | 450 | Teto contra resposta quilométrica. Com 250 uma lista de 3 itens cortava no meio da frase, o que fica pior que uma resposta longa. Com 450 o modelo termina o raciocínio e ainda assim não escreve um artigo. |
| `reasoning_format` | `hidden` | Sem isso o texto da resposta vinha vazio (explicado no relatório de evolução). |

O modelo que dá as notas na bateria roda com `temperature` 0.0, porque avaliação
precisa ser o mais repetível possível.

## Comparando os dois modelos

Mesma pergunta nos dois, mesmos parâmetros:

| Modelo | Tempo | Tokens de resposta | Como se comportou |
|--------|-------|--------------------|-------------------|
| `gpt-oss-20b` | 0,9 – 1,6 s | 70 – 360 | Rápido. Segue bem a instrução de resposta curta. Às vezes oferece detalhe cedo demais. |
| `gpt-oss-120b` | 1,3 – 1,5 s | 70 – 250 | Um pouco mais lento e mais direto. Recusa com mais firmeza quando o pedido é ambíguo. |

Rodando a bateria completa no `gpt-oss-20b`: 24 de 24 casos passaram, os 12 ataques
foram recusados, as respostas estruturadas saíram todas válidas e a nota média ficou
em 9,2.

Pra rodar a bateria com o modelo maior:

```bash
python -m evals.run_evals --prompt v2 --modelo openai/gpt-oss-120b
```

## Conclusão

O `120b` recusa com um pouco mais de firmeza, mas custa mais tempo. Como o chat roda
num totem e num app, onde a resposta precisa ser quase instantânea, e as recusas já
estão em 100 % com o `20b` mais os guardrails de código, ficamos com o
**`gpt-oss-20b` em produção**. O `120b` fica só como avaliador da bateria de testes,
onde qualidade importa mais que velocidade.

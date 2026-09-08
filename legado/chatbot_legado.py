"""
chatbot.py — ChargeGrid Intelligence Assistant, versão local (sem Colab)
GoodWe Challenge - Sprint 3

Mesma lógica do ChargeGrid_Intelligence_chatbot.ipynb (roteador tempo real
vs. histórico, RAG com dados_rag.json, IA via Groq) — só que rodando direto
no seu computador, num terminal, sem precisar de Colab nem upload de nada.
Reaproveita o mesmo .env que a API já usa (DATABASE_URL, GROQ_API_KEY).

Como rodar:
  cd entregas
  py chatbot.py
"""

import os
import json
import re
import unicodedata
from groq import Groq
from dotenv import load_dotenv

from ev_chargegrid import (
    listar_sessoes_ativas,
    obter_status_estacoes,
    obter_faturamento_dia,
    contar_sessoes_dia,
    inicializar_banco,
)

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

# inicializar_banco() é chamado em main() (terminal), não no import: a API já
# inicializa o banco por conta própria e importa este arquivo — fazer no import
# era uma segunda ida ao Postgres em toda subida do servidor, e impedia
# importar o chatbot (pra testar a busca do RAG, por exemplo) sem banco no ar.

PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(PASTA_ATUAL, "dados_rag.json"), "r", encoding="utf-8") as f:
    dados_rag = json.load(f)

documentos = dados_rag["frases_contexto_rag"]

# llama-3.1-8b-instant saiu do catálogo do Groq (a API passou a devolver
# 404 "model_not_found" pra ele) — trocado pelo openai/gpt-oss-20b, que é o
# mais parecido em proposta (rápido, pequeno) entre os modelos que a conta
# tem acesso hoje. Confira periodicamente com GET /openai/v1/models — Groq
# roda vários modelos como "preview" e pode aposentar/trocar sem aviso.
MODELO = "openai/gpt-oss-20b"

# Backstop técnico pra instrução de brevidade do [4] TOM DE VOZ acima: o
# prompt pede resposta simples + pergunta de fechamento ("quer mais
# detalhes?"), mas nada garante que o modelo obedeça sempre — isso é só uma
# rede de segurança contra resposta MUITO longa, não o principal mecanismo
# de encurtar (isso é o prompt). Testado com 250: uma lista de 3 itens já
# cortava no meio da frase — feio, pior que resposta longa. 450 dá folga
# pra terminar o pensamento (incluindo a pergunta de fechamento) em
# qualquer resposta curta de verdade, e ainda corta bem antes de um textão.
MAX_TOKENS_RESPOSTA = 450

# Sem temperature explícita, a API usa o padrão do provedor (geralmente
# alto o suficiente pra dar respostas bem diferentes pra mesma pergunta em
# chamadas separadas) — testado ao vivo: a MESMA pergunta às vezes voltava
# numa lista de 2 linhas, às vezes num "artigo" com tabela e cabeçalhos,
# mesmo com a instrução de formatação no prompt. Temperatura mais baixa
# deixa o modelo mais conservador/previsível — ainda responde bem, só
# elabora menos por conta própria. (_sanitizar_formatacao acima é a
# segunda camada de defesa, pro caso raro de ainda vir tabela/cabeçalho.)
TEMPERATURA = 0.4

# Janela de memória da conversa (usada por responder(), a versão sem estado
# da API): quantas TROCAS (par pergunta+resposta) anteriores entram no
# contexto mandado pro modelo. Por contagem de mensagens, não de tokens —
# um "token buffer" de verdade cortaria pelo tamanho exato em tokens (exige
# o tokenizador específico do modelo, que a gente não tem aqui), mas na
# prática o efeito é o mesmo que se pediu: sem isso, cada pergunta nova
# esquecia tudo que foi dito antes (o chat via cada mensagem como uma
# conversa nova, do zero) — com a janela, ele lembra as últimas
# JANELA_HISTORICO trocas e esquece o que passou disso, ao contrário de
# guardar a conversa inteira (cresceria o custo/latência de toda pergunta
# sem limite, numa sessão de teste longa).
JANELA_HISTORICO = 5

# O cliente do Groq só é criado quando alguém realmente vai perguntar algo.
# Antes ele era criado no import, lendo os.environ["GROQ_API_KEY"] direto: se
# a chave não estivesse configurada (chave rotacionada, deploy novo em que
# esqueceram a variável), o import estourava KeyError e derrubava a API
# INTEIRA junto — totem, dashboard, sessões, pagamento. A chave do chat não
# pode ser capaz de parar a recarga; agora só o /api/chat falha, com uma
# mensagem que diz exatamente o que fazer.
_client = None


def obter_client() -> Groq:
    global _client
    if _client is None:
        chave = os.environ.get("GROQ_API_KEY", "").strip()
        if not chave:
            raise RuntimeError(
                "GROQ_API_KEY não configurada — o assistente precisa dela. "
                "Defina no .env (uso local) ou nas variáveis de ambiente do "
                "serviço (deploy). O resto do sistema funciona sem ela."
            )
        _client = Groq(api_key=chave)
    return _client

SYSTEM_PROMPT = """
[1] IDENTIDADE:
Você é o assistente inteligente do Charge Grid Intelligence, um sistema de gestão de
eletropostos para operações comerciais no contexto do EV Challenge 2026.

[2] CONTEXTO:
O Charge Grid Intelligence é um sistema voltado para postos comerciais e operadores
de frotas que precisam gerenciar eletropostos de alto fluxo de forma eficiente.
Você ajuda de duas formas: (a) consultando dados reais do sistema — sessões de
recarga, receita por ponto de carga, disponibilidade dos carregadores — e (b)
funcionando como um guia de bolso pro motorista, tirando dúvidas gerais sobre
carros elétricos (autonomia, tipos de conector, cuidados com a bateria, como
funciona a recarga).
A partir do Sprint 3, o chatbot tem acesso a dois tipos de dados sobre o sistema:
- DADOS EM TEMPO REAL: estado atual do sistema (carregadores ativos, faturamento de hoje)
- DADOS HISTÓRICOS: análise de 60 sessões reais da base SP2 (receita por carregador,
  pico de demanda, ticket médio, eficiência do DLB)

[3] REGRAS:
- Responda sobre o sistema Charge Grid Intelligence, operação de eletropostos, e
  dúvidas gerais de motoristas sobre carros elétricos.
- Se a pergunta não tiver relação nenhuma com recarga, carros elétricos ou o
  sistema, diga: "Só consigo ajudar com questões relacionadas a carros
  elétricos e ao Charge Grid Intelligence."
- Nunca invente dados do sistema (valores de consumo, faturamento) nem
  especificações técnicas exatas de um modelo específico de carro — se não
  tiver certeza sobre um modelo específico, diga isso claramente em vez de
  arriscar um número.
- Não opine sobre qual marca de carro ou rede de recarga é "melhor" — explique
  conceitos, não compare produtos.
- "Gasto pessoal do motorista logado" e "faturamento/receita total do sistema"
  são coisas DIFERENTES — nunca confunda os dois. Gasto pessoal é o que aquele
  motorista específico pagou; faturamento total é dado de negócio, somando
  todos os clientes. Se a pergunta for "quanto eu gastei" ou parecida, use
  APENAS o dado de "gasto pessoal do motorista logado" quando ele estiver no
  contexto — nunca responda com o faturamento total do sistema nesse caso.
- Se perguntarem sobre faturamento, receita, ticket médio ou qualquer outro
  número de negócio do sistema e esse dado NÃO estiver no contexto fornecido,
  não invente nem estime — diga que essa informação é restrita à gestão e
  não está disponível por aqui.
- Quando tiver dados em tempo real disponíveis no contexto, priorize-os sobre o histórico.

[4] TOM DE VOZ:
Seja claro, objetivo e use linguagem acessível, sem jargões técnicos
desnecessários. Responda sempre em português brasileiro.

Comece SIMPLES, sempre — isso é um chat num app de celular/totem, não um
artigo nem uma central de ajuda. A primeira resposta cabe em 2-4 frases
curtas OU uma lista de até 4 itens curtos — só o essencial da pergunta,
sem introdução/conclusão redundante ("é importante notar que...",
recapitular a pergunta antes de responder). Não abra já com múltiplos
subtópicos, comparação de vários cenários ou exemplos genéricos que
ninguém pediu: se perguntarem "quanto dura a bateria", a resposta é uma
faixa aproximada de anos/km, não uma explicação de todos os fatores que
influenciam autonomia.

Termine perguntando, de forma natural e específica ao assunto (não sempre
a mesma frase pronta), se a pessoa quer mais detalhes sobre algum ponto —
só aprofunda se ela pedir na próxima mensagem, não antes.

Formatação: pode usar **negrito**, `código` e listas com "- item" quando
ajudar a escanear. NUNCA use tabelas (colunas com "|") nem cabeçalhos (##,
###) — o balão do chat é estreito, isso quebra a visualização em vez de
ajudar.

[5] CONTEXTO DO SISTEMA:
- O sistema atende postos comerciais e frotas com múltiplos pontos de carga e alta rotatividade
- A cobrança é feita por kWh consumido com tarifa dinâmica por horário e ocupação
- O chatbot orienta gestores e operadores sobre consumo, faturamento e disponibilidade do sistema
- Picos de demanda são previstos e tarifados para evitar sobrecarga na infraestrutura elétrica
"""

PALAVRAS_TEMPO_REAL = [
    "agora", "hoje", "atual", "ativo", "ativa", "livre", "ocupado", "ocupada",
    "faturamento", "sessões de hoje", "quantas sessões", "status",
    "disponível", "carregando"
]

# Palavras que aparecem em qualquer frase e não dizem nada sobre o assunto.
# Sem essa lista, "o", "de" e "e" casavam com quase todo documento do RAG e a
# busca devolvia sempre os 5 primeiros — perguntar sobre bateria de carro
# elétrico trazia receita por carregador como "contexto".
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
    """minúsculas e sem acento — pra "sessões" casar com "sessoes", que é como
    a maioria das pessoas digita no celular."""
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def _tokenizar(texto: str) -> list[str]:
    """Quebra em palavras, sem acento e sem pontuação. Trabalhar com palavras
    inteiras (e não com "está contido no texto") evita casamento por pedaço:
    "dura", de "quanto dura a bateria", casava com "duração média" e trazia
    dado de receita pra uma pergunta sobre bateria."""
    palavra = []
    palavras = []
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
    # Palavra igual, ou o documento tem uma variação que começa com o termo
    # ("carregador" casa com "carregadores"). O mínimo de 5 letras pro prefixo
    # é o que impede pedaço curto de casar com qualquer coisa.
    if termo in palavras_doc:
        return True
    return len(termo) >= 5 and any(p.startswith(termo) for p in palavras_doc)


def buscar_documentos(pergunta: str, limite: int = 5) -> list[str]:
    """Documentos do RAG ordenados por quantos termos da pergunta eles contêm.
    Só entra quem casa com pelo menos um termo útil — se nada casar, devolve
    lista vazia em vez de contexto aleatório (contexto errado é pior que
    contexto nenhum: o modelo tenta usar o que recebeu)."""
    termos = _palavras_uteis(pergunta)
    if not termos:
        return []

    pontuados = []
    for doc in documentos:
        palavras_doc = set(_tokenizar(doc))
        pontos = sum(1 for t in termos if _casa(t, palavras_doc))
        if pontos:
            pontuados.append((pontos, doc))

    pontuados.sort(key=lambda par: par[0], reverse=True)
    return [doc for _, doc in pontuados[:limite]]


def buscar_contexto(pergunta: str, acesso_gestao: bool = False) -> str:
    """Monta o contexto que vai junto da pergunta pro modelo.

    acesso_gestao=False (padrão): só disponibilidade das estações (livre/
    ocupada) — que é a mesma coisa que qualquer pessoa enxerga parada na
    frente do totem — e nada de dado de negócio.

    acesso_gestao=True: também faturamento, contagem de sessões do dia, as
    sessões ativas dos outros clientes e o histórico comercial do RAG
    (receita por carregador, ticket médio). Só o dashboard logado e as
    ferramentas internas da equipe (chatbot.py no terminal, notebook)
    chegam nesse modo — mesma fronteira que /api/kpis aplica.

    O padrão é o restrito de propósito: qualquer chamada nova que esqueça de
    passar o parâmetro vaza menos, não mais."""
    pergunta_lower = pergunta.lower()
    usa_tempo_real = any(p in pergunta_lower for p in PALAVRAS_TEMPO_REAL)

    partes = []

    if usa_tempo_real:
        try:
            status_estacoes = obter_status_estacoes()
            livres = [k for k, v in status_estacoes.items() if v == "Livre"]
            ocupadas = [k for k, v in status_estacoes.items() if v == "Ocupada"]

            partes.append("[DISPONIBILIDADE DAS ESTAÇÕES — agora]")
            partes.append(f"Estações ocupadas agora: {ocupadas if ocupadas else 'nenhuma'}")
            partes.append(f"Estações livres agora: {livres}")

            if acesso_gestao:
                sessoes_ativas = listar_sessoes_ativas()
                faturamento_hoje = obter_faturamento_dia()
                sessoes_hoje = contar_sessoes_dia()

                partes.append(f"Faturamento de hoje (sessões pagas): R$ {faturamento_hoje:.2f}")
                partes.append(f"Total de sessões iniciadas hoje: {sessoes_hoje}")

                for s in sessoes_ativas:
                    partes.append(
                        f"Sessão ativa — Estação {s['estacao']}: usuário {s['usuario']}, "
                        f"{s['kwh']:.2f} kWh consumidos, valor acumulado R$ {s['valor']:.2f}, "
                        f"pagamento via {s['pagamento']}."
                    )
        except Exception as e:
            partes.append(f"[AVISO] Banco indisponível: {e}")
    elif acesso_gestao:
        relevantes = buscar_documentos(pergunta)
        if relevantes:
            partes.append("[DADOS HISTÓRICOS — planilha SP2, 60 sessões reais]")
            partes.extend(relevantes)

    return "\n".join(partes)


# Rede de segurança pra instrução de formatação do [4] TOM DE VOZ: pedir
# educadamente pra não usar tabela/cabeçalho no prompt NÃO é garantia — a
# mesma pergunta, na mesma sessão de testes, às vezes voltava limpa e às
# vezes voltava com tabela markdown inteira. Como o balão de chat é
# estreito e o renderizador de Markdown do front não trata tabela, uma
# resposta com tabela aparece com "|" literal na tela — pior do que ter
# convertido pra lista aqui.
_RE_CABECALHO = re.compile(r"^#{1,6}\s+(.+)")
_RE_LINHA_SEPARADORA_TABELA = re.compile(r"^\|?[\s:|-]+\|[\s:|-]*\|?$")


def _sanitizar_formatacao(texto: str) -> str:
    linhas_saida = []
    for linha in texto.split("\n"):
        bruta = linha.strip()

        cabecalho = _RE_CABECALHO.match(bruta)
        if cabecalho:
            linhas_saida.append(f"**{cabecalho.group(1)}**")
            continue

        if "-" in bruta and _RE_LINHA_SEPARADORA_TABELA.match(bruta):
            continue  # linha separadora de tabela ("|---|---|") — descarta

        if bruta.startswith("|") and bruta.endswith("|") and len(bruta) > 1:
            celulas = [c.strip() for c in bruta.strip("|").split("|") if c.strip()]
            if len(celulas) >= 2:
                linhas_saida.append(f"- **{celulas[0]}**: {' — '.join(celulas[1:])}")
            elif celulas:
                linhas_saida.append(f"- {celulas[0]}")
            continue

        linhas_saida.append(linha)

    return "\n".join(linhas_saida)


def _janela_do_historico(historico_anterior: list[dict] = None) -> list[dict]:
    """Valida o histórico que o CLIENTE mandou (não confia cegamente — vem
    de fora) e corta pras últimas JANELA_HISTORICO trocas (JANELA_HISTORICO
    * 2 mensagens, pergunta+resposta intercaladas). Entrada mal-formada
    (não é lista, item sem "role"/"content", role que não seja user/
    assistant) é ignorada silenciosamente em vez de derrubar a resposta —
    memória de conversa é um extra, uma pergunta legítima não deveria
    falhar só porque o front mandou o histórico de um jeito inesperado."""
    if not isinstance(historico_anterior, list):
        return []
    limpo = [
        {"role": item["role"], "content": str(item["content"])}
        for item in historico_anterior
        if isinstance(item, dict)
        and item.get("role") in ("user", "assistant")
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]
    return limpo[-(JANELA_HISTORICO * 2):]


historico = [{"role": "system", "content": SYSTEM_PROMPT}]


def chat(pergunta: str) -> str:
    """Versão com memória de conversa, usada só pelo terminal deste arquivo e
    pelo notebook — ferramenta interna da equipe, por isso com acesso de
    gestão. O que o motorista usa no app é a API, que chama responder()."""
    contexto = buscar_contexto(pergunta, acesso_gestao=True)
    if contexto:
        mensagem = f"Contexto do sistema:\n{contexto}\n\nPergunta: {pergunta}"
    else:
        mensagem = pergunta
    historico.append({"role": "user", "content": mensagem})
    resposta = obter_client().chat.completions.create(
        model=MODELO, messages=historico, max_tokens=MAX_TOKENS_RESPOSTA, temperature=TEMPERATURA
    )
    conteudo = _sanitizar_formatacao(resposta.choices[0].message.content)
    historico.append({"role": "assistant", "content": conteudo})
    return conteudo


def responder(
    pergunta: str,
    contexto_extra: str = None,
    acesso_gestao: bool = False,
    historico_anterior: list[dict] = None,
) -> str:
    """Sem estado NO SERVIDOR (não usa/altera o `historico` global do
    módulo — esse é só do chat() de terminal) — mas aceita o histórico
    recente da conversa, mandado pronto pelo CLIENTE a cada chamada. Usada
    pela API (/api/chat), que pode atender vários usuários ao mesmo tempo:
    guardar a conversa no servidor exigiria uma sessão por usuário; deixar
    o cliente reenviar a própria janela de mensagens é mais simples e não
    arrisca misturar a conversa de um com a de outro.

    contexto_extra: dado do motorista logado (gasto pessoal), montado pela
    API — só depois de validar o PIN dele. Este arquivo não sabe nada sobre
    placas/PIN/autenticação, só monta a pergunta com o que a API já mandou
    pronto e verificado.

    acesso_gestao: ver docstring de buscar_contexto. Padrão False = o mínimo
    de acesso; a API só passa True quando o token de admin foi validado.

    historico_anterior: lista de {"role": "user"|"assistant", "content": str},
    mais antiga primeiro — as trocas ANTERIORES da conversa, sem a pergunta
    atual (essa vem no parâmetro `pergunta`). Cortado pra no máximo
    JANELA_HISTORICO trocas mesmo que o cliente mande mais — isso é uma
    janela de memória por contagem de mensagens (bem mais simples que
    contar tokens de verdade, que exigiria um tokenizador específico do
    modelo só pra isso), não memória infinita: cresce o custo/latência de
    toda pergunta nova sem limite, e é o tipo de coisa que uma sessão de
    teste longa (ex: 40 perguntas seguidas) deixaria arrastado."""
    contexto = buscar_contexto(pergunta, acesso_gestao=acesso_gestao)
    if contexto_extra:
        contexto = f"{contexto_extra}\n{contexto}" if contexto else contexto_extra
    mensagem = f"Contexto do sistema:\n{contexto}\n\nPergunta: {pergunta}" if contexto else pergunta

    mensagens = [{"role": "system", "content": SYSTEM_PROMPT}]
    mensagens.extend(_janela_do_historico(historico_anterior))
    mensagens.append({"role": "user", "content": mensagem})

    resposta = obter_client().chat.completions.create(
        model=MODELO,
        messages=mensagens,
        max_tokens=MAX_TOKENS_RESPOSTA,
        temperature=TEMPERATURA,
    )
    return _sanitizar_formatacao(resposta.choices[0].message.content)


def main() -> None:
    inicializar_banco()  # garante que o banco existe antes de qualquer leitura
    print("ChargeGrid Intelligence — CGI Assistant (local)")
    print(f"RAG com {len(documentos)} fragmentos históricos indexados.")
    print("Digite 'sair' para encerrar.\n")

    while True:
        pergunta = input("Você: ").strip()
        if not pergunta:
            continue
        if pergunta.lower() in ("sair", "exit", "quit"):
            print("\nAté mais!")
            break
        print("Bot:", chat(pergunta), "\n")


if __name__ == "__main__":
    main()

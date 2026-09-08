"""
moderation.py — defesa contra jailbreak / prompt injection.

Tres camadas:
  1. NORMALIZACAO agressiva do texto de entrada, pra derrubar ofuscacao
     (caracteres invisiveis, homoglifos, leetspeak, letras espacadas, ruido de
     markdown).
  2. PADROES de entrada — lista grande, PT + EN, separada em "hard" (bloqueia
     sozinho) e "soft" (2+ sinais fracos na mesma mensagem = bloqueia).
  3. GUARDA DE SAIDA — se a resposta do modelo vazar trecho do system prompt ou
     confirmar que "saiu do personagem", o chamador troca por uma recusa.

A 4a camada e a regra <regras_invioaveis> do system prompt v2. E a 5a e a
fronteira de dados: pergunta sem acesso_gestao nao recebe faturamento/sessoes no
contexto, entao nem um jailbreak que "passe" consegue vazar numero de negocio.
"""

from __future__ import annotations

import re
import unicodedata

RESPOSTA_PADRAO = (
    "Nao posso fazer isso. Posso ajudar com o Charge Grid Intelligence ou "
    "com duvidas sobre carros eletricos."
)

# ---------------------------------------------------------------- normalizacao
_ZERO_WIDTH = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x180E, 0x200E, 0x200F], None
)

# homoglifos comuns (cirilico / grego / fullwidth) -> latino
_HOMOGLIFOS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "к": "k", "м": "m", "н": "h", "т": "t", "ѕ": "s", "і": "i", "ј": "j",
    "ο": "o", "ν": "v", "α": "a", "ρ": "p", "ѐ": "e", "４": "4", "３": "3",
    "０": "0", "１": "1",
})

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

_RUIDO = re.compile(r"[>*_`~#|]+")
_ESPACOS = re.compile(r"\s+")
_LETRAS_ESPACADAS = re.compile(r"\b(?:[a-z]\s){2,}[a-z]\b")


def normalizar(texto: str) -> str:
    """Texto minusculo, sem acento, sem truque de ofuscacao."""
    t = unicodedata.normalize("NFKC", texto).lower()
    t = t.translate(_ZERO_WIDTH).translate(_HOMOGLIFOS)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _RUIDO.sub(" ", t)
    # junta "i g n o r e" -> "ignore"
    t = _LETRAS_ESPACADAS.sub(lambda m: m.group(0).replace(" ", ""), t)
    # leetspeak quando o char esta encostado numa letra (pega "1gn0re", "r3gr4s")
    t = re.sub(r"[013457@$](?=[a-z])|(?<=[a-z])[013457@$]",
               lambda m: m.group(0).translate(_LEET), t)
    return _ESPACOS.sub(" ", t).strip()


def _norm_leve(texto: str) -> str:
    """Normalizacao que PRESERVA <, >, _ (usada na guarda de saida, que procura
    fingerprints tipo <identidade> e regras_invioaveis)."""
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return _ESPACOS.sub(" ", t.translate(_ZERO_WIDTH))


def _p(regex: str) -> re.Pattern[str]:
    return re.compile(regex)


# ---- HARD: um sozinho ja bloqueia -----------------------------------------
_HARD: list[tuple[re.Pattern[str], str]] = [
    (_p(r"\b(ignore|ignora|ignorar|esquec|esqueca|desconsidere|desconsidera|disregard|forget|override|overwrite|bypass|nullif)\w*\b.{0,40}\b(instru|regra|prompt|acima|anterior|previous|above|all|tudo|system|contexto|diretri|guidelin|rule)"), "override-instrucoes"),
    (_p(r"\b(nova|new)\b.{0,15}\b(instru|instruction|regra|rule|diretriz|task|tarefa)\w*\b\s*:?"), "nova-instrucao-injetada"),
    (_p(r"\b(revele?|revela|mostre?|mostra|me d[eê]|imprima|print|repeat|repita|reproduz|liste|list|output|diga|de-me|reveal|expose|dump|traduz\w*|translate|resuma|resume|summarize|conte)\b.{0,60}\b(system ?prompt|prompt de sistema|prompt inicial|initial prompt|suas instru|sua instru|your instruction|tuas instru|instrucoes de sistema|instrucoes do sistema|regras acima|texto acima|words above|text above|everything above|tudo (que|acima)|config\w*|system message|mensagem de sistema|suas regras|suas diretrizes)\b"), "vazar-prompt"),
    (_p(r"\b(suas|tuas|your|the|as)\b.{0,15}(instruco\w*|instruction|regras|rules|prompt|diretriz\w*|config\w*|guideline\w*)\b.{0,20}(original|originais|inicial|inici\w*|initial|de sistema|do sistema|\bsystem\b|completa\w*|na integra|internas|secreta\w*)"), "vazar-prompt"),
    (_p(r"\b(mostr\w*|cole?|cola|repita|repeat|revele?|diga|envie|liste|print|output|echo|show|paste)\b.{0,25}(tudo|everything|o (texto|conteudo)|the (text|content|words)|as palavras|mensage\w*)\b.{0,20}(antes|before|acima|above|que precede|no (inicio|topo)|prior)"), "extrair-inicio"),
    (_p(r"\b(what|qual|quais|como)\b.{0,30}\b(your|suas|tuas)\b.{0,20}\b(instruction|instru|guidelines|diretriz|rules|regras|prompt|system message)\b"), "vazar-prompt"),
    (_p(r"\b(repeat|repita|reproduz|echo)\b.{0,25}\b(the )?(words|text|palavras|texto|conteudo|prompt)\b.{0,20}\b(above|acima|antes|before|verbatim|ipsis|literal)\b"), "vazar-prompt"),
    (_p(r"\b(voce|vc|tu|you)\b.{0,25}\b(agora|a partir de agora|from now on|de agora em diante|doravante)\b.{0,25}\b(e|sera|vai ser|is|are|will be|passa a ser|vira)\b"), "troca-de-papel"),
    (_p(r"\b(a partir de agora|from now on|de agora em diante)\b.{0,45}\b(voce|vc|you|assistente|responda|answer|aja|act|ignore|no longer|nao (e|sera) mais)\b"), "troca-de-papel"),
    (_p(r"\b(aja|atue|comporte-se|finja|faca de conta|pretend|act|behave|roleplay|role-play|simule|simulate|imagine que voce)\b.{0,25}\b(as|como|to be|que (e|voce)|sendo|ser|there (are|is) no|nao (ha|existe|tem))\b"), "roleplay"),
    (_p(r"\b(jogo de rpg|role[ -]?play|vamos (jogar|encenar)|no roteiro|no papel de)\b|\binterpret\w*\b.{0,25}\b(personagem|papel|funcionario|role|um [a-z]+ da (goodwe|fiap|empresa)|o (admin|sistema))\b|\bvoce interpreta\b"), "roleplay"),
    (_p(r"\b(modo|mode)\b.{0,15}\b(desenvolvedor|developer|dev|deus|god|admin|manutencao|maintenance|debug|sudo|root|irrestrito|unrestricted|sem restri|sem filtro|livre|jailbreak)\b"), "modo-especial"),
    (_p(r"\b(dan|stan|aim|dude|kevin|do anything now|jailbroken|jailbreak|sydney)\b.{0,25}(mode|prompt|persona|activ|ativ|now|agora|avoid|norm|sem norma|strive)"), "persona-jailbreak"),
    (_p(r"\b(strive to avoid|avoid all|ignore all)\b.{0,10}(norm|rule|guideline|restric)"), "persona-jailbreak"),
    (_p(r"\b(sem|no|without)\b.{0,15}(filtro|filter|censura|censorship|restri|limit|regra|rule|guideline|diretriz|etica|ethic|moral|salvaguard|safeguard|norm)\w*"), "pedido-sem-limites"),
    (_p(r"\b(voce|vc|you|assistente|responda|answer)\b.{0,25}(nao (tem|possui)|has no|have no|no longer|nao (segue|obedece)|without any|sem)\b.{0,25}(regra|rule|restri|limit|filtro|guideline|diretriz|norm)\w*"), "pedido-sem-limites"),
    (_p(r"\b(prefix|prefixe|comece|inicie|start|begin)\b.{0,30}(all |todas |cada |every )?(respons|answer|reply|mensage|saida|resposta)\w*.{0,20}(with|com)\b"), "prefixo-forcado-hard"),
    (_p(r"\bprompt\s*injection\b|\bjailbreak\w*\b"), "termo-explicito"),
    (_p(r"\bsystem[\s-]?prompt\b|\bprompt (de|do) sistema\b|\bprompt inicial\b|\bmeta[\s-]?prompt\b"), "termo-explicito"),
    (_p(r"\b(what|qual|which)\b.{0,25}(written|escrit|dito|said)\b.{0,25}(beginning|inicio|comeco|start|top|antes|before|primeir)"), "extrair-inicio"),
    (_p(r"\b(primeira|first|1a)\b.{0,12}(mensagem|message|instru|prompt)\w*.{0,25}(recebe\w*|received|deste chat|this chat|conversa\w*|conversation|voce|neste chat)"), "extrair-inicio"),
    (_p(r"\[\s*(system|admin|sistema|instru\w*|user|assistant)\s*\]|<\s*(system|sistema|admin|instru\w*)\s*>|^\s*(system|assistant|admin)\s*:"), "delimitador-falso"),
    (_p(r"\b(acesso|permissao|privilegi|access|permission|privilege)\b.{0,15}\b(total|irrestrito|completo|full|admin|root|elevado|de gestao)\b"), "escalonamento-de-acesso"),
    (_p(r"\b(sou|i am|i'm|eu sou)\b.{0,20}\b(o |a |the |your |seu |sua )?(desenvolvedor|developer|admin|administrador|criador|creator|engenheiro|owner|dono|responsavel pelo sistema|da openai|da groq|da fiap)\b"), "autoridade-falsa"),
    (_p(r"\b(contexto do sistema|system context|system override|instrucao de sistema)\b\s*:?\s*\["), "contexto-falso-injetado"),
    (_p(r"\b(base64|rot13|rot-13|hex|hexadecimal|binary|binario|morse|leetspeak|caesar cipher|cifra de cesar)\b.{0,30}\b(decode|decodifi|traduz|converta|execute|run|interpret|siga|follow)\b"), "encoding-smuggling"),
    (_p(r"\b(decode|decodifique|execute|run|rode)\b.{0,20}\b(this|isto|o seguinte|the following|essa string)\b.{0,20}\b(base64|hex|binary|comando|instrucao)\b"), "encoding-smuggling"),
    (_p(r"\breset\w*\b.{0,15}(context|contexto|memoria|memory|conversa|sessao|persona|prompt)"), "reset-contexto"),
    (_p(r"\b(o que|what)\b.{0,25}(voce|you)\b.{0,15}(foi|were|was|is|esta)\b.{0,12}(instru\w*|orientad\w*|program\w*|told|ensinad\w*|configurad\w*|proibid\w*)"), "extrair-instrucoes"),
    (_p(r"\b(desativ\w*|disable|desliga\w*|remova|remove|turn off|derrub\w*|burl\w*|contorn\w*)\b.{0,20}(filtro\w*|filter\w*|restri\w*|censura|salvaguard\w*|guardrail\w*|regra\w*|protec\w*|seguranca|safety|moderac\w*)"), "desativar-guardrail"),
    (_p(r"\b(list\w*|liste|mostr\w*|dump|exporta\w*|todos os|todas as|all the?)\b.{0,20}(usuari\w*|users|motorista\w*|drivers|clientes|placas?)\b.{0,30}(session|sesso\w*|nome\w*|name|dados|placa|cpf|pin|pessoa)"), "exfiltracao-dados"),
    (_p(r"\b(nome\w*|name)s?\b.{0,6}(dos|de|of|das)\b.{0,6}(usuari\w*|motorista\w*|users|drivers|clientes)"), "exfiltracao-dados"),
]

# ---- SOFT: 2+ na mesma mensagem bloqueiam --------------------------------
_SOFT: list[tuple[re.Pattern[str], str]] = [
    (_p(r"\b(hipoteticamente|hypothetically|em teoria|na teoria|imagine|suponha|suppose|and finally|por fim)\b"), "enquadramento-hipotetico"),
    (_p(r"\b(apenas|so|somente|just|only)\b.{0,15}\b(teste|test|brincadeira|joke|ficcao|fiction|estudo|research|pesquisa|academico|educacional|educational)\b"), "pretexto"),
    (_p(r"\b(minha avo|my grandma|grandmother|falecida|deceased|bedtime story|historia pra dormir)\b"), "pretexto-emocional"),
    (_p(r"\b(nao se preocupe|dont worry|relax|tudo bem|it'?s ok|voce pode confiar|trust me|confie em mim)\b"), "tranquilizacao"),
    (_p(r"\b(comece com|responda com|start with|begin with|answer with|output|escreva exatamente)\b.{0,20}\b(claro|sure|certainly|com certeza|ok|entendi|sim)\b"), "prefixo-forcado"),
    (_p(r"\b(nao diga|dont say|never say|jamais diga|evite dizer)\b.{0,25}\b(nao posso|i can'?t|desculpe|sorry|nao consigo)\b"), "proibir-recusa"),
    (_p(r"\b(continue|continua|complete|prossiga)\b.{0,20}\b(a frase|the sentence|o texto|from where|de onde|acima)\b"), "continuacao-forcada"),
    (_p(r"\b(traduza|translate|em ingles|in english|em outro idioma|another language)\b"), "troca-de-idioma"),
    (_p(r"\b(passo a passo|step by step|detalhadamente|em detalhes|sem omitir)\b"), "insistencia-detalhe"),
    (_p(r"\b(entre aspas|between quotes|dentro de um bloco de codigo|in a code block|como json|as json)\b"), "formato-evasivo"),
]

_LIMIAR_SOFT = 2


def varredura(texto: str) -> tuple[bool, str | None, int]:
    """(bloqueado, rotulo, score). rotulo = primeiro hard, ou 'soft:a+b' se
    2+ sinais fracos."""
    alvo = normalizar(texto)
    for padrao, rotulo in _HARD:
        if padrao.search(alvo):
            return True, rotulo, 99
    achados = [rot for padrao, rot in _SOFT if padrao.search(alvo)]
    if len(achados) >= _LIMIAR_SOFT:
        return True, "soft:" + "+".join(achados[:3]), len(achados)
    return False, achados[0] if achados else None, len(achados)


def detectar_injection(texto: str) -> str | None:
    """Compat: rotulo do bloqueio, ou None se passou."""
    bloqueado, rotulo, _ = varredura(texto)
    return rotulo if bloqueado else None


# ------------------------------------------------------------- guarda de saida
# fingerprints do system prompt e de "saiu do personagem".
_VAZAMENTO_SAIDA = _p(
    r"<\s*/?\s*(identidade|dominio|regras_invioaveis|recusas_de_dominio|fora_de_escopo|tom_de_voz|exemplos)\s*>"
    r"|regras_invioaveis|recusas_de_dominio|\bfora_de_escopo\b|\btom_de_voz\b"
    r"|\[\s*[12345]\s*\]\s*(identidade|contexto|regras|tom de voz)"
    r"|reasoning_format|palavras_tempo_real|acesso_gestao"
    r"|(modo (livre|dev|desenvolvedor|dan|god|irrestrito|sem regras)|dan mode|developer mode|jailbreak)\s*(ativ|activ|\bon\b|habilit|enabled|desbloq)"
    r"|\b(sou|estou|agora sou)\b (agora )?(o |a )?(dan|jailbroken|livre de restri|sem restri|desbloqueado)"
    r"|as (minhas )?instrucoes (do meu sistema|de sistema) sao|meu (system )?prompt e|my system prompt is"
    r"|here (are|is) my (instructions|system prompt)|aqui (estao|esta) (as )?minhas instrucoes"
    r"|ok,? unlocked|modo livre ativado"
)


def resposta_parece_vazamento(resposta: str) -> bool:
    """True se a resposta do modelo vazar trecho do prompt ou confirmar
    'saida de personagem' — o chamador troca por RESPOSTA_PADRAO."""
    return bool(_VAZAMENTO_SAIDA.search(_norm_leve(resposta)))

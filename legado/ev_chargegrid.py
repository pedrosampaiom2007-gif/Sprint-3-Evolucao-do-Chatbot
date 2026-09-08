"""
ChargeGrid Intelligence — Plataforma de Gestão Comercial de Recarga EV
GoodWe Challenge - Sprint 3

Módulos entregues pelo Backend (Raul Sampaio):
  - Banco de Dados Postgres (Supabase) integrado com persistência ao vivo.
  - Autenticação de Placas via Criptografia Hash (SHA-256).
  - Mascaramento de dados sensíveis em conformidade com LGPD.
  - Gateway de Pagamentos Integrado (Simulação Mercado Pago Sandbox API).
  - 4 funções de leitura prontas para Lucas (chatbot) e Luan (dashboard).

Integração do modelo ML (Pedro — 07/07):
  - DEMANDA_PREVISTA_POR_HORA substituído por modelo_demanda.pkl (RandomForest).
  - Treinado com dados reais da planilha SP2 (60 sessões, coluna hora sintética).
  - Fallback automático para o dicionário fixo se o .pkl não for encontrado.
"""

import json
import datetime
import os
import psycopg2
import psycopg2.pool
import hashlib
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

import solar_optimizer

try:
    import requests
    _REQUESTS_DISPONIVEL = True
except ImportError:
    _REQUESTS_DISPONIVEL = False

# Banco Postgres real (Supabase), lido do .env — não fica hardcoded no código
# nem no repositório. Substitui o antigo chargegrid.db (arquivo SQLite local),
# assim API, script de console e qualquer processo se conectam ao mesmo banco
# de verdade, de qualquer computador, não só de quem tem o arquivo local.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
try:
    DATABASE_URL = os.environ["DATABASE_URL"]
except KeyError:
    raise SystemExit(
        "[ERRO] DATABASE_URL não está definida.\n"
        "       Crie um arquivo .env na raiz do repositório (um nível acima de\n"
        "       entregas/) com DATABASE_URL=postgresql://... — veja a seção\n"
        "       'Configuração' do README.md."
    )


# ─── Pool de conexões ─────────────────────────────────────────────────────────
# Abrir uma conexão nova no Supabase custa ~1,3s (medido) — mais de 5x o custo
# da consulta em si. Como cada função de leitura abria a sua, o /api/painel
# (que o totem e o dashboard consultam a cada 3-4s) levava ~5s pra responder,
# ou seja, um poll ainda estava no ar quando o próximo começava. O pool mantém
# as conexões abertas e reaproveita: paga-se o 1,3s uma vez, não a cada leitura.
_POOL = None


def _obter_pool() -> "psycopg2.pool.ThreadedConnectionPool":
    global _POOL
    if _POOL is None:
        # ThreadedConnectionPool (e não SimpleConnectionPool) porque a API roda
        # o Flask e a thread de simulação de tempo em paralelo, e as duas leem
        # do banco. keepalives evita que a conexão ociosa seja derrubada em
        # silêncio pelo pooler do Supabase ou por NAT no meio do caminho.
        _POOL = psycopg2.pool.ThreadedConnectionPool(
            1, 5, DATABASE_URL,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
        )
    return _POOL


@contextmanager
def conectar(somente_leitura: bool = False):
    """Empresta uma conexão do pool e devolve no final, sempre — inclusive se
    o bloco levantar exceção (antes, cada função dava conn.close() na última
    linha, então qualquer erro no meio vazava a conexão até estourar o limite
    do Supabase).

    Faz commit no fim do bloco em caso de sucesso e rollback em caso de erro:
    conexão reaproveitada NÃO pode voltar pro pool com transação aberta.
    Uma conexão que deu erro é descartada em vez de devolvida — se o pooler do
    Supabase tiver derrubado as conexões ociosas, o pool se limpa sozinho nas
    próximas chamadas em vez de repetir o erro pra sempre.

    somente_leitura=True liga autocommit: sem transação, a consulta vai e
    volta numa ida só. Com transação, o psycopg2 manda BEGIN antes e COMMIT
    depois — três viagens até o Supabase em vez de uma, e cada viagem custa
    ~350ms daqui. É a diferença entre o painel responder em ~0,4s ou ~1,2s.
    Só pra leitura: escrita continua transacional, senão um cadastro que
    falhasse no meio deixaria conta sem usuário."""
    pool = _obter_pool()
    conn = pool.getconn()
    conn.autocommit = somente_leitura
    try:
        yield conn
        if not somente_leitura:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        pool.putconn(conn, close=True)
        raise
    else:
        pool.putconn(conn)


# ─── Constantes do sistema ────────────────────────────────────────────────────
MAX_ESTACOES          = 10
LIMITE_POTENCIA_GRID  = 50.0
MAX_POTENCIA_ESTACAO  = 22.0
TARIFA_BASE_KWH       = 0.90
CASHBACK_PCT          = 0.05   # 5% de volta em toda sessão paga — benefício de carregar pela ChargeGrid

USAR_API_REAL_MERCADOPAGO = False


def calcular_cashback(valor_sessao: float) -> float:
    """CASHBACK_PCT do valor de uma sessão PAGA, como benefício de carregar
    pela ChargeGrid em vez de um ponto avulso sem fidelização. Não é
    dinheiro sacável — vira saldo trocável no catálogo de resgate (ver
    saldo_cashback/resgatar_cashback, mais abaixo, depois de
    historico_usuario)."""
    return round(valor_sessao * CASHBACK_PCT, 2)

# ─── IA: modelo de previsão de demanda (Kevin / Pedro — 07/07) ────────────────
# Tenta carregar o modelo treinado com dados reais da planilha SP2.
# Se modelo_demanda.pkl não estiver na pasta, usa o dicionário de fallback
# e avisa no console — o sistema continua funcionando normalmente.
_MODELO_ML = None
_DEMANDA_FALLBACK = {
     0: 0.05,  1: 0.03,  2: 0.03,  3: 0.03,  4: 0.05,  5: 0.10,
     6: 0.20,  7: 0.45,  8: 0.70,  9: 0.80, 10: 0.85, 11: 0.90,
    12: 1.00, 13: 0.85, 14: 0.80, 15: 0.85, 16: 0.90, 17: 0.95,
    18: 1.00, 19: 1.00, 20: 0.95, 21: 0.80, 22: 0.55, 23: 0.25,
}

try:
    import joblib
    # Caminho absoluto, ancorado nesta pasta (entregas/) — um caminho relativo
    # só resolveria se o processo fosse iniciado com CWD == entregas/, o que
    # não é o caso ao rodar a API (README manda "cd entregas/files" antes),
    # e fazia o modelo real nunca carregar nesse fluxo (caía sempre no
    # dicionário de fallback, silenciosamente).
    _CAMINHO_MODELO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modelo_demanda.pkl")
    _MODELO_ML = joblib.load(_CAMINHO_MODELO)
    print("[IA] modelo_demanda.pkl carregado com sucesso (RandomForest, dados SP2).")
except Exception:
    print("[IA] modelo_demanda.pkl não encontrado — usando dicionário de fallback.")


# ─── Sessão de recarga comercial ──────────────────────────────────────────────
@dataclass
class SessaoRecarga:
    id_estacao:       int
    id_usuario:       str           = "LIVRE"
    ativa:            bool          = False
    potencia_kw:      float         = 0.0
    kwh_consumidos:   float         = 0.0
    hora_inicio:      int           = 0
    valor_sessao:     float         = 0.0
    metodo_pagamento: str           = "---"
    id_sessao_db:     Optional[int] = None

    def encerrar(self):
        self.id_usuario       = "LIVRE"
        self.ativa            = False
        self.potencia_kw      = 0.0
        self.kwh_consumidos   = 0.0
        self.hora_inicio      = 0
        self.valor_sessao     = 0.0
        self.metodo_pagamento = "---"
        self.id_sessao_db     = None


# ─── Estado do sistema ────────────────────────────────────────────────────────
estacoes: list[SessaoRecarga] = [
    SessaoRecarga(id_estacao=i + 1) for i in range(MAX_ESTACOES)
]
receita_total:         float = 0.0
consumo_total_diario:  float = 0.0


# ─── MÓDULO BACKEND: SEGURANÇA E PERSISTÊNCIA (RAUL) ──────────────────────────

def gerar_hash_placa(placa: str) -> str:
    return hashlib.sha256(placa.strip().upper().encode('utf-8')).hexdigest()


def mascarar_id(uid: str) -> str:
    uid_clean = uid.strip().upper()
    if len(uid_clean) >= 5:
        return f"{uid_clean[:3]}**{uid_clean[-2:]}"
    return f"{uid_clean}**"


def gerar_hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode('utf-8')).hexdigest()


def gerar_hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.strip().encode('utf-8')).hexdigest()


def inicializar_banco():
    with conectar() as conn:
        cursor = conn.cursor()

        # Uma conta pode ter mais de uma placa vinculada (motorista com 2 carros).
        # A placa continua sendo o jeito de logar — a conta só existe pra agrupar
        # o histórico de várias placas da mesma pessoa. pin_hash protege esse
        # histórico: sem ele, bastava saber a placa (sem segredo nenhum) pra ver
        # o histórico de pagamento de qualquer motorista.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contas (
                id       SERIAL PRIMARY KEY,
                nome     TEXT,
                pin_hash TEXT
            )
        """)
        cursor.execute("ALTER TABLE contas ADD COLUMN IF NOT EXISTS pin_hash TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                hash_usuario      TEXT PRIMARY KEY,
                nome              TEXT,
                status            TEXT DEFAULT 'ATIVO',
                conta_id          INTEGER REFERENCES contas(id),
                usuario_mascarado TEXT
            )
        """)
        # Retrofit pra quem já tinha o banco criado antes dessas duas colunas —
        # idempotente, seguro rodar toda vez que o servidor sobe.
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS conta_id INTEGER REFERENCES contas(id)")
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS usuario_mascarado TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessoes (
                id                 SERIAL PRIMARY KEY,
                id_estacao         INTEGER,
                usuario            TEXT,
                data_sessao        TEXT,
                hora_inicio        INTEGER,
                kwh_consumidos     REAL    DEFAULT 0.0,
                valor_sessao       REAL    DEFAULT 0.0,
                metodo_pagamento   TEXT,
                status_pagamento   TEXT    DEFAULT 'PENDENTE',
                ativa              INTEGER DEFAULT 1,
                conta_id           INTEGER REFERENCES contas(id)
            )
        """)
        # conta_id em sessoes é o vínculo EXATO entre sessão e dono. Antes, o
        # histórico era buscado pela placa mascarada (ABC**23) — e duas placas
        # diferentes com as mesmas 3 primeiras e 2 últimas posições geram a
        # mesma máscara, então um motorista via o histórico de pagamento do
        # outro. Sessões antigas (anteriores a esta coluna) ficam com NULL e
        # continuam sendo buscadas pela máscara — ver historico_usuario.
        cursor.execute("ALTER TABLE sessoes ADD COLUMN IF NOT EXISTS conta_id INTEGER REFERENCES contas(id)")
        # Índices nas colunas que a leitura mais usa (histórico do motorista e
        # painel/status das estações batem nelas a cada poll).
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessoes_conta ON sessoes (conta_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessoes_ativa ON sessoes (ativa)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                usuario    TEXT PRIMARY KEY,
                senha_hash TEXT
            )
        """)

        # Resgate de cashback: cada linha é UM resgate (histórico, não um
        # saldo que se sobrescreve) — saldo_cashback() soma o que foi ganho
        # (via sessões pagas) e SUBTRAI o que já está aqui. Sem essa tabela,
        # cashback só era somado, nunca gasto — dava pra "resgatar" o mesmo
        # saldo quantas vezes quisesse, porque nada registrava que já tinha
        # sido usado.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cashback_resgates (
                id              SERIAL PRIMARY KEY,
                conta_id        INTEGER REFERENCES contas(id),
                tipo            TEXT,
                valor_resgatado REAL,
                detalhe         TEXT,
                criado_em       TEXT
            )
        """)

        # Estação em manutenção: a PRESENÇA da linha é o estado ("está em
        # manutenção"), não uma coluna booleana — sem linha = estação normal.
        # Persistido no banco (não em memória, como potencia_kw) porque um
        # carregador com defeito continua com defeito depois da API reiniciar;
        # perder esse estado no restart seria pior que não ter o recurso.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS manutencao_estacoes (
                id_estacao INTEGER PRIMARY KEY,
                motivo     TEXT,
                desde      TIMESTAMP DEFAULT NOW()
            )
        """)

        usuarios_teste = [
            ("ABC1D23", "Cliente Executivo A"),
            ("XYZ9F88", "Frota Corporativa B"),
            ("GHI3K45", "Cliente Shopping C"),
            ("DEF7M01", "Usuário Demo D"),
        ]
        PIN_TESTE = "0000"  # PIN de todas as contas de teste — troque antes de uma apresentação real
        for placa, nome in usuarios_teste:
            hash_placa = gerar_hash_placa(placa)
            cursor.execute("SELECT conta_id FROM usuarios WHERE hash_usuario = %s", (hash_placa,))
            existente = cursor.fetchone()

            if existente is not None and existente[0] is not None:
                # já tem conta — só garante que ela tem PIN também (retrofit de
                # quem ganhou conta antes do pin_hash existir, como aconteceu
                # com essas 4 contas de teste na prática)
                cursor.execute("SELECT pin_hash FROM contas WHERE id = %s", (existente[0],))
                pin_existente = cursor.fetchone()
                if pin_existente and pin_existente[0] is None:
                    cursor.execute(
                        "UPDATE contas SET pin_hash = %s WHERE id = %s",
                        (gerar_hash_pin(PIN_TESTE), existente[0])
                    )
                continue

            cursor.execute(
                "INSERT INTO contas (nome, pin_hash) VALUES (%s, %s) RETURNING id",
                (nome, gerar_hash_pin(PIN_TESTE))
            )
            conta_id = cursor.fetchone()[0]

            if existente is None:
                # nunca cadastrado — cria do zero
                cursor.execute(
                    "INSERT INTO usuarios (hash_usuario, nome, conta_id, usuario_mascarado) VALUES (%s, %s, %s, %s)",
                    (hash_placa, nome, conta_id, mascarar_id(placa))
                )
            else:
                # já cadastrado de antes da coluna conta_id existir — preenche agora
                cursor.execute(
                    "UPDATE usuarios SET conta_id = %s, usuario_mascarado = %s WHERE hash_usuario = %s",
                    (conta_id, mascarar_id(placa), hash_placa)
                )

        # Admin de teste — troque a senha antes da apresentação de verdade.
        cursor.execute(
            "INSERT INTO admins (usuario, senha_hash) VALUES (%s, %s) ON CONFLICT (usuario) DO NOTHING",
            ("admin", gerar_hash_senha("chargegrid2026"))
        )

        _vincular_sessoes_antigas_a_contas(cursor)


def _vincular_sessoes_antigas_a_contas(cursor) -> None:
    """Preenche sessoes.conta_id nas sessões gravadas antes dessa coluna
    existir, usando a placa mascarada — mas SÓ quando a máscara pertence a
    uma única conta. Se duas contas diferentes geram a mesma máscara, a
    sessão fica sem dono (conta_id NULL) e não aparece pra ninguém: preferir
    esconder uma sessão a mostrá-la pro motorista errado.

    Idempotente: depois da primeira execução sobram só as ambíguas, e o
    UPDATE não encontra mais nada pra fazer."""
    cursor.execute("""
        UPDATE sessoes s
           SET conta_id = u.conta_id
          FROM usuarios u
         WHERE s.conta_id IS NULL
           AND u.conta_id IS NOT NULL
           AND u.usuario_mascarado = s.usuario
           AND (SELECT COUNT(DISTINCT u2.conta_id)
                  FROM usuarios u2
                 WHERE u2.usuario_mascarado = s.usuario
                   AND u2.conta_id IS NOT NULL) = 1
    """)


def validar_admin(usuario: str, senha: str) -> bool:
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT senha_hash FROM admins WHERE usuario = %s", (usuario,))
        resultado = cursor.fetchone()
    return resultado is not None and resultado[0] == gerar_hash_senha(senha)


def validar_usuario(id_usuario: str) -> bool:
    hash_busca = gerar_hash_placa(id_usuario)
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM usuarios WHERE hash_usuario = %s", (hash_busca,))
        resultado = cursor.fetchone()
    return resultado is not None and resultado[0] == 'ATIVO'


def conta_da_placa(placa: str) -> Optional[int]:
    """id da conta dona dessa placa (None se a placa não está cadastrada).
    É o vínculo que vai gravado em cada sessão — ver historico_usuario."""
    hash_busca = gerar_hash_placa(placa)
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT conta_id FROM usuarios WHERE hash_usuario = %s", (hash_busca,))
        resultado = cursor.fetchone()
    return resultado[0] if resultado else None


def cadastrar_usuario(placa: str, nome: str, pin: str) -> bool:
    """pin: 4 dígitos escolhidos pelo motorista — protege o histórico de
    pagamento depois (ver validar_pin). Sem isso, bastaria saber a placa
    pra ver o histórico de qualquer um (IDOR)."""
    hash_novo = gerar_hash_placa(placa)
    with conectar() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM usuarios WHERE hash_usuario = %s", (hash_novo,))
        if cursor.fetchone():
            return False  # já cadastrado

        cursor.execute(
            "INSERT INTO contas (nome, pin_hash) VALUES (%s, %s) RETURNING id",
            (nome, gerar_hash_pin(pin))
        )
        conta_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO usuarios (hash_usuario, nome, conta_id, usuario_mascarado) VALUES (%s, %s, %s, %s)",
            (hash_novo, nome, conta_id, mascarar_id(placa))
        )
    return True


def validar_pin(placa: str, pin: str) -> bool:
    """Confere o PIN contra a conta dona dessa placa. Usado antes de mostrar
    histórico de pagamento ou vincular um carro novo — nunca antes de
    iniciar/encerrar recarga no totem, que continua só com a placa."""
    hash_busca = gerar_hash_placa(placa)
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.pin_hash FROM contas c
            JOIN usuarios u ON u.conta_id = c.id
            WHERE u.hash_usuario = %s
        """, (hash_busca,))
        resultado = cursor.fetchone()
    return (
        resultado is not None
        and resultado[0] is not None
        and resultado[0] == gerar_hash_pin(pin)
    )


def vincular_placa(placa_existente: str, placa_nova: str, pin: str) -> bool:
    """Registra placa_nova na MESMA conta de placa_existente — pra motorista
    com mais de um carro. Exige o PIN da conta (prova de que quem está
    pedindo é o dono, não só alguém que sabe a placa existente)."""
    if not validar_pin(placa_existente, pin):
        return False

    hash_existente = gerar_hash_placa(placa_existente)
    hash_novo = gerar_hash_placa(placa_nova)

    with conectar() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT conta_id, nome FROM usuarios WHERE hash_usuario = %s", (hash_existente,))
        resultado = cursor.fetchone()
        if resultado is None:
            return False  # placa existente não está cadastrada

        conta_id, nome = resultado

        cursor.execute("SELECT 1 FROM usuarios WHERE hash_usuario = %s", (hash_novo,))
        if cursor.fetchone():
            return False  # placa nova já está cadastrada (nessa ou noutra conta)

        cursor.execute(
            "INSERT INTO usuarios (hash_usuario, nome, conta_id, usuario_mascarado) VALUES (%s, %s, %s, %s)",
            (hash_novo, nome, conta_id, mascarar_id(placa_nova))
        )
    return True


def criar_pagamento_sandbox(valor: float, id_sessao: Optional[int]) -> dict:
    transacao_id = f"SIM-{uuid.uuid4().hex[:10].upper()}"

    if USAR_API_REAL_MERCADOPAGO and _REQUESTS_DISPONIVEL:
        url = "https://api.mercadopago.com/v1/payments"
        headers = {
            "Authorization": "Bearer TEST-8374928374982374-MOCK-TOKEN",
            "Content-Type": "application/json"
        }
        payload = {
            "transaction_amount": valor,
            "description": f"ChargeGrid CGI - Sessão #{id_sessao}",
            "payment_method_id": "pix",
            "payer": {"email": "motorista@evchallenge.com"}
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=2)
            if response.status_code == 201:
                dados = response.json()
                return {
                    "url": dados.get("point_of_interaction", {})
                               .get("transaction_data", {})
                               .get("ticket_url"),
                    "transacao_id": dados.get("id", transacao_id),
                }
        except Exception:
            pass

    return {
        "url": f"https://www.mercadopago.com.br/sandbox/pay?simulation_id={transacao_id}",
        "transacao_id": transacao_id,
    }


def confirmar_pagamento(id_sessao_db: Optional[int]) -> None:
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE sessoes SET status_pagamento = 'PAGO', ativa = 0 WHERE id = %s",
            (id_sessao_db,)
        )


# ─── MÓDULO: MANUTENÇÃO DE ESTAÇÃO ─────────────────────────────────────────────
# Terceiro estado além de Livre/Ocupada — pra tirar um carregador com defeito
# de circulação sem apagar ele do sistema. Só o admin decide (rotas gated por
# token em api_server.py); o motor só garante a regra de negócio: nunca entra
# em manutenção por cima de uma sessão em andamento (quem já está carregando
# termina normalmente).

def estacao_em_manutencao(id_estacao: int) -> bool:
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM manutencao_estacoes WHERE id_estacao = %s", (id_estacao,))
        return cursor.fetchone() is not None


def estacoes_em_manutencao() -> dict:
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id_estacao, motivo, desde FROM manutencao_estacoes")
        return {
            id_estacao: {"motivo": motivo, "desde": desde.isoformat() if desde else None}
            for id_estacao, motivo, desde in cursor.fetchall()
        }


def aplicar_manutencao(status_por_estacao: dict, em_manutencao: dict) -> dict:
    """Sobrepõe 'Manutenção' num dict de status já calculado (Livre/Ocupada).
    Recebe em_manutencao já buscado (não vai ao banco de novo) — quem chama
    em sequência (como /api/painel) já precisa desse dict pra outra coisa
    (o motivo da manutenção), então buscar aqui de novo dobraria a consulta
    no endpoint mais chamado do sistema.

    Nunca sobrepõe uma estação Ocupada: uma sessão em andamento não é
    interrompida por uma manutenção marcada durante ela — a estação só entra
    de fato em manutenção quando a sessão termina (entrar_em_manutencao já
    recusa estação ativa; isso só afeta o raro caso de marcar bem no instante
    entre o fim de uma sessão e o próximo poll, e nesse caso o pior efeito é
    a manutenção aparecer um ciclo de poll depois, não perder dado)."""
    return {
        n: ("Manutenção" if n in em_manutencao and status != "Ocupada" else status)
        for n, status in status_por_estacao.items()
    }


def entrar_em_manutencao(id_estacao: int, motivo: str = "") -> bool:
    """False se a estação está com sessão ativa — precisa encerrar a recarga
    em andamento antes; motorista no meio de uma sessão não pode ser
    interrompido só porque o admin marcou o carregador como quebrado."""
    if not (1 <= id_estacao <= MAX_ESTACOES):
        return False
    if estacoes[id_estacao - 1].ativa:
        return False
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO manutencao_estacoes (id_estacao, motivo)
            VALUES (%s, %s)
            ON CONFLICT (id_estacao) DO UPDATE SET motivo = EXCLUDED.motivo
        """, (id_estacao, motivo.strip() or None))
    return True


def sair_de_manutencao(id_estacao: int) -> bool:
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM manutencao_estacoes WHERE id_estacao = %s", (id_estacao,))
        apagou = cursor.rowcount > 0
    return apagou


# ─── MÓDULO DE LEITURA — API interna para Lucas (chatbot) e Luan (dashboard) ──

def listar_sessoes_ativas() -> list[dict]:
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id_estacao, usuario, kwh_consumidos, valor_sessao, metodo_pagamento
            FROM sessoes WHERE ativa = 1
        """)
        colunas = ["estacao", "usuario", "kwh", "valor", "pagamento"]
        return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def status_das_sessoes(sessoes_ativas: list[dict]) -> dict:
    """Mesma resposta de obter_status_estacoes(), mas a partir de uma lista de
    sessões que quem chamou JÁ tem em mãos — sem ir ao banco de novo. Existe
    porque o /api/painel precisava das duas coisas e acabava consultando as
    sessões ativas duas vezes por requisição (uma direta, outra dentro de
    obter_status_estacoes), dobrando o custo do endpoint mais chamado do
    sistema."""
    ativas = {s["estacao"] for s in sessoes_ativas}
    return {
        n: ("Ocupada" if n in ativas else "Livre")
        for n in range(1, MAX_ESTACOES + 1)
    }


def obter_status_estacoes() -> dict:
    return status_das_sessoes(listar_sessoes_ativas())


def obter_faturamento_dia(data: Optional[str] = None) -> float:
    data = data or datetime.date.today().isoformat()
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(valor_sessao), 0)
            FROM sessoes
            WHERE status_pagamento = 'PAGO' AND data_sessao = %s
        """, (data,))
        total = cursor.fetchone()[0]
    return round(float(total), 2)


def contar_sessoes_dia(data: Optional[str] = None) -> int:
    data = data or datetime.date.today().isoformat()
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessoes WHERE data_sessao = %s", (data,))
        return cursor.fetchone()[0]


def obter_consumo_dia(data: Optional[str] = None) -> float:
    """kWh entregue no dia, somando TODAS as sessões da data (inclusive as
    ainda em andamento) — diferente de obter_faturamento_dia, que só soma as
    já PAGAS. Vem do banco, não do consumo_total_diario em memória: esse é
    zerado a cada restart da API (mesma limitação do potencia_kw), então não
    dava pra confiar nele pra um relatório."""
    data = data or datetime.date.today().isoformat()
    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(SUM(kwh_consumidos), 0) FROM sessoes WHERE data_sessao = %s",
            (data,)
        )
        total = cursor.fetchone()[0]
    return round(float(total), 2)


def relatorio_do_dia(data: Optional[str] = None) -> dict:
    """Resumo operacional do dia — usado pelo botão 'Baixar relatório' do
    dashboard (ver api_server.py). Só números que já têm função de leitura
    própria; esta função apenas agrupa, não calcula nada novo."""
    data = data or datetime.date.today().isoformat()
    faturamento = obter_faturamento_dia(data)
    sessoes_dia = contar_sessoes_dia(data)
    return {
        "data": data,
        "faturamento": faturamento,
        "sessoes_dia": sessoes_dia,
        "ticket_medio": round(faturamento / sessoes_dia, 2) if sessoes_dia else 0.0,
        "consumo_kwh": obter_consumo_dia(data),
        "estacoes_ativas_agora": contar_ativas(),
        "max_estacoes": MAX_ESTACOES,
        "gerado_em": datetime.datetime.now().isoformat(),
    }


def historico_usuario(placa: str) -> list[dict]:
    """Todas as sessões da CONTA dona dessa placa (motorista com mais de um
    carro vê os dois), mais recentes primeiro.

    A busca é por `sessoes.conta_id`, não pela placa mascarada. A máscara
    (ABC**23) não identifica ninguém sozinha: duas placas com as mesmas 3
    primeiras e 2 últimas posições viram a mesma string, e o motorista de
    uma via o histórico de pagamento da outra (verificado na prática).
    Sessões gravadas antes da coluna conta_id existir são adotadas pela conta
    certa no boot (ver _vincular_sessoes_antigas_a_contas), então o histórico
    de quem já usava o sistema continua aparecendo — só que agora por vínculo
    explícito, não por semelhança de máscara."""
    conta_id = conta_da_placa(placa)

    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        colunas = ["id", "estacao", "data", "hora_inicio", "kwh", "valor",
                   "pagamento", "status_pagamento", "placa"]
        campos = """
            SELECT id, id_estacao, data_sessao, hora_inicio, kwh_consumidos,
                   valor_sessao, metodo_pagamento, status_pagamento, usuario
            FROM sessoes
        """

        if conta_id is not None:
            cursor.execute(campos + " WHERE conta_id = %s ORDER BY id DESC", (conta_id,))
        else:
            # Placa sem cadastro (recarga anônima no totem): não existe conta
            # pra casar, então só resta a máscara — e nesse caso não há PIN
            # nem tela de histórico envolvida.
            cursor.execute(
                campos + " WHERE conta_id IS NULL AND usuario = %s ORDER BY id DESC",
                (mascarar_id(placa),)
            )
        sessoes = [dict(zip(colunas, linha)) for linha in cursor.fetchall()]
        for s in sessoes:
            # só sessão PAGA rende cashback — pendente ainda não é dinheiro
            # que entrou de verdade, não faz sentido "devolver" uma parte dele.
            s["cashback"] = calcular_cashback(s["valor"]) if s["status_pagamento"] == "PAGO" else 0.0
        return sessoes


# ─── Resgate de cashback ───────────────────────────────────────────────────
# taxa_por_real: quanto de prêmio 1 real de cashback vale. Front (app.js)
# mostra essa mesma taxa como prévia antes do motorista escolher — se mudar
# aqui, teria que mudar lá também (é só o texto do rótulo, o valor cobrado
# de verdade sempre vem calculado por este dicionário, nunca do front).
CATALOGO_RESGATE = {
    "milhas":   {"nome": "Milhas aéreas",        "unidade": "milhas",            "taxa_por_real": 20},
    "ingresso": {"nome": "Desconto em ingresso", "unidade": "reais de desconto", "taxa_por_real": 1},
}


def saldo_cashback(placa: str) -> float:
    """Cashback ainda não resgatado: tudo que a conta já ganhou (soma do
    campo "cashback" de historico_usuario, sessões pagas) menos tudo que já
    foi resgatado (cashback_resgates). É a ÚNICA função que subtrai — sem
    isso, o saldo mostrado nunca baixava depois de um resgate, e dava pra
    resgatar a mesma coisa infinitas vezes."""
    conta_id = conta_da_placa(placa)
    if conta_id is None:
        return 0.0

    ganho = sum(s["cashback"] for s in historico_usuario(placa))

    with conectar(somente_leitura=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(SUM(valor_resgatado), 0) FROM cashback_resgates WHERE conta_id = %s",
            (conta_id,)
        )
        resgatado = float(cursor.fetchone()[0])

    return round(ganho - resgatado, 2)


def resgatar_cashback(placa: str, tipo: str) -> Optional[dict]:
    """Resgata TODO o saldo disponível de uma vez (não parcial — mantém a
    tela simples: escolhe o prêmio, não digita quanto). Devolve o recibo do
    resgate, ou None se o tipo não existir no catálogo, a placa não tiver
    conta, ou não sobrar saldo pra resgatar."""
    info = CATALOGO_RESGATE.get(tipo)
    if info is None:
        return None

    conta_id = conta_da_placa(placa)
    if conta_id is None:
        return None

    saldo = saldo_cashback(placa)
    if saldo <= 0:
        return None

    quantidade = round(saldo * info["taxa_por_real"], 2)
    detalhe = f"{quantidade:g} {info['unidade']}"
    agora = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO cashback_resgates (conta_id, tipo, valor_resgatado, detalhe, criado_em)
               VALUES (%s, %s, %s, %s, %s)""",
            (conta_id, tipo, saldo, detalhe, agora)
        )

    return {
        "tipo": tipo,
        "nome": info["nome"],
        "valor_resgatado": saldo,
        "quantidade": quantidade,
        "unidade": info["unidade"],
    }


def obter_potencia_estacoes() -> dict:
    # potencia_kw só existe em memória (não é salvo no banco), então lê de
    # `estacoes` em vez do SQLite, diferente das outras funções de leitura.
    return {e.id_estacao: round(e.potencia_kw, 2) for e in estacoes}


# ─── OCPP 1.6J ────────────────────────────────────────────────────────────────
def ocpp_enviar(action: str, id_estacao: int, payload: dict) -> None:
    msg = {"action": action, "estacaoId": id_estacao, **payload}
    print(f"[OCPP 1.6J] -> {json.dumps(msg, ensure_ascii=False)}")


# ─── Módulo de IA ─────────────────────────────────────────────────────────────
def ia_prever_demanda(hora: int) -> float:
    """
    Retorna fator de ocupação previsto para a hora (0.0 a 1.0).
    Usa modelo_demanda.pkl (RandomForest treinado com dados SP2) se disponível.
    Caso contrário, usa dicionário de fallback.
    """
    if _MODELO_ML is not None:
        valor = float(_MODELO_ML.predict([[hora]])[0])
        return max(0.0, min(1.0, valor))   # garante que fica entre 0 e 1
    return _DEMANDA_FALLBACK.get(hora, 0.5)


HORA_INICIO_MADRUGADA = 0
HORA_FIM_MADRUGADA    = 6      # intervalo [0h, 6h) — antes do movimento normal começar
HORA_INICIO_PICO      = 18
HORA_FIM_PICO         = 20     # intervalo [18h, 20h] — horário de ponta real da rede (padrão ANEEL)
DESCONTO_MADRUGADA    = 0.20   # até 20% mais barato que a tarifa base
DESCONTO_SOLAR        = 0.10   # até 10% mais barato na janela de maior geração solar prevista

# Fuso fixo, não zoneinfo/pytz: o Brasil aboliu o horário de verão em 2019,
# então UTC-3 é o horário civil de Brasília o ano inteiro, sem exceção
# sazonal — não precisa de banco de dados de fuso horário pra isso.
FUSO_BRASIL = datetime.timezone(datetime.timedelta(hours=-3))


def hora_atual_brasil() -> int:
    """Hora corrente (0-23) no fuso de Brasília — NÃO usar datetime.now().hour
    puro pra isso: aquilo pega o fuso do sistema operacional onde o processo
    roda, que localmente é Brasil mas em produção (Render) é UTC. Isso fazia
    a janela de pico/madrugada acionar 3h mais cedo que o horário real de
    Brasília assim que o sistema foi publicado — mesma causa raiz do bug do
    cronômetro do totem, seção "Deploy" do README tem mais contexto."""
    return datetime.datetime.now(FUSO_BRASIL).hour


def ia_calcular_tarifa(hora: int, estacoes_ativas: int) -> float:
    """Fator sobre TARIFA_BASE_KWH. Sobe em horário de pico e ocupação/demanda
    alta; desce em dois incentivos diferentes, que nunca se sobrepõem:

    - `pico` (18h-21h, +30%): horário de ponta real da rede elétrica — janela
      em que a energia efetivamente custa mais caro pra distribuidora, no
      padrão usado pela maioria das concessionárias brasileiras (ANEEL). Não
      é o horário em que a ChargeGrid prevê mais gente carregando — isso é
      papel do fator de `demanda` logo abaixo, que já sobe sozinho quando a
      previsão de ocupação das estações é alta. Antes, meio-dia (12h) também
      contava como "pico" só pelo horário de almoço lotar as estações — mas
      isso é ocupação, não energia mais cara na rede, e como a demanda ao
      meio-dia já é alta no modelo, a sobretaxa de pico dobrava a cobrança
      pelo mesmo motivo (12h chegava a ficar mais caro que hora de ponta de
      verdade). Motivos diferentes, fatores diferentes.
    - madrugada (0h-5h, -20%): achata o pico de demanda da rede — o motivo
      clássico de existir tarifa dinâmica num sistema elétrico.
    - janela solar (-10%, ver solar_optimizer.py): incentiva carregar nas
      horas de maior geração fotovoltaica PREVISTA pro dia (Open-Meteo, sem
      custo, sem chave) — não é geração REAL medida de um inversor (não
      temos um GoodWe conectado pra isso), é o primeiro degrau de reagir a
      previsão solar em vez de só a relógio. Nunca entra em hora de pico
      (não faria sentido dar desconto justo na hora em que a energia da rede
      está mais cara), nem compete com o desconto de madrugada (geração
      solar à noite é zero, a janela nunca cai lá de qualquer forma — o elif
      é só documentação dessa garantia, não uma correção de um caso real
      observado)."""
    fator = 1.0
    pico = HORA_INICIO_PICO <= hora <= HORA_FIM_PICO  # ponta real (rede), não previsão de demanda
    if pico:
        fator += 0.30
    if estacoes_ativas >= 3:
        fator += 0.15
    demanda = ia_prever_demanda(hora)
    if demanda >= 0.90:
        fator += 0.20
    elif demanda >= 0.75:
        fator += 0.10
    if HORA_INICIO_MADRUGADA <= hora < HORA_FIM_MADRUGADA:
        # max(0.80, ...) e não só "fator - desconto": impede que o desconto
        # deixe o fator abaixo de 80% mesmo no caso (raro de madrugada, mas
        # possível se o modelo de IA prever demanda alta) de outro fator ter
        # empurrado o valor pra cima antes — o desconto nunca vira prejuízo.
        fator = max(0.80, fator - DESCONTO_MADRUGADA)
    elif not pico and solar_optimizer.esta_em_janela_solar(hora):
        fator = max(0.85, fator - DESCONTO_SOLAR)
    return round(fator, 2)


# ─── DLB ──────────────────────────────────────────────────────────────────────
def contar_ativas() -> int:
    return sum(1 for e in estacoes if e.ativa)


def balancear_carga() -> None:
    """Rateio igualitário do limite do grid entre as estações ativas — o
    ALGORITMO não mudou. O que mudou é a LINGUAGEM em que o limite é
    comunicado: em vez de só atualizar potencia_kw em memória, cada estação
    ativa recebe uma mensagem SetChargingProfile no formato real do OCPP
    1.6J (chargingProfileId, stackLevel, chargingProfilePurpose,
    chargingRateUnit, limit em W) — o mesmo vocabulário que StartTransaction/
    StopTransaction/MeterValues já usam em ocpp_enviar().

    TxDefaultProfile é o perfil correto pro nosso caso: ele vale pra
    QUALQUER sessão que rodar naquela estação, sem precisar de um
    agendamento específico (isso seria um TxProfile, amarrado a uma
    transação — não temos agendamento de recarga, não seria honesto usar
    esse tipo). stackLevel 0 porque só existe um perfil ativo por vez aqui,
    não uma pilha de prioridades sobrepostas.

    O que isso NÃO é: priorização por veículo (ex: carro com bateria mais
    baixa recebendo mais potência) ou qualquer decisão além de "dividir
    igual entre quem está ativo". Isso seria a evolução real do algoritmo
    de decisão — não fizemos isso, e documentar a diferença entre "fala
    OCPP" e "decide como OCPP" é mais honesto do que deixar a mensagem
    parecer mais esperta do que é."""
    n = contar_ativas()
    if n == 0:
        return
    pot = min(LIMITE_POTENCIA_GRID / n, MAX_POTENCIA_ESTACAO)
    print(f"\n[DLB] Redistribuindo carga: {pot:.1f} kW por estação ({n} ativas).")
    for e in estacoes:
        if e.ativa:
            e.potencia_kw = pot
            ocpp_enviar("SetChargingProfile", e.id_estacao, {
                "chargingProfileId": e.id_estacao,
                "stackLevel": 0,
                "chargingProfilePurpose": "TxDefaultProfile",
                "chargingProfileKind": "Absolute",
                "chargingRateUnit": "W",
                "limit": round(pot * 1000),
            })


# ─── Simulação de passagem de tempo ───────────────────────────────────────────
def _aplicar_avanco(e: "SessaoRecarga", minutos: float, estacoes_ativas: int, cursor) -> None:
    """Núcleo de "avançar o relógio" pra UMA estação: soma o consumo do
    período, recalcula o valor acumulado, grava no banco. Compartilhado por
    simular_tempo() (todas as ativas, +30min fixo, chamado pelo loop
    automático da API a cada SIMULACAO_INTERVALO_SEGUNDOS) e
    avancar_sessao_estacao() (uma estação só, minutos arbitrários, chamado
    pelo botão do admin) — a matemática de tarifação não pode divergir
    entre os dois caminhos."""
    global consumo_total_diario
    fator              = ia_calcular_tarifa(e.hora_inicio, estacoes_ativas)
    energia            = e.potencia_kw * (minutos / 60)
    e.kwh_consumidos  += energia
    consumo_total_diario += energia
    e.valor_sessao     = round(e.kwh_consumidos * TARIFA_BASE_KWH * fator, 2)

    cursor.execute(
        "UPDATE sessoes SET kwh_consumidos = %s, valor_sessao = %s WHERE id = %s",
        (e.kwh_consumidos, e.valor_sessao, e.id_sessao_db)
    )
    ocpp_enviar("MeterValues", e.id_estacao, {
        "usuario":    e.id_usuario,
        "potenciaKw": round(e.potencia_kw, 2),
        "leituraKwh": round(e.kwh_consumidos, 2),
        "valorSessao": e.valor_sessao,
        "fatorTarifa": fator,
        "iaDemanda":  ia_prever_demanda(e.hora_inicio),
    })


def simular_tempo() -> None:
    n = contar_ativas()
    if n == 0:
        print("\n[AVISO] Nenhuma sessão comercial ativa.")
        return

    print("\n[SISTEMA] Avançando +30 min...")
    with conectar() as conn:
        cursor = conn.cursor()
        for e in estacoes:
            if e.ativa:
                _aplicar_avanco(e, 30, n, cursor)


def avancar_sessao_estacao(estacao_num: int, minutos: float) -> bool:
    """Avança UMA sessão específica por `minutos` DE VERDADE — grava no
    banco e atualiza o objeto em memória, ao contrário de uma prévia (ver
    api_estimar_sessao) que só calcula e mostra, sem gravar nada. Existe
    pro admin poder demonstrar o fluxo completo de cobrança (recarga →
    encerrar → recibo com valor real) numa apresentação, sem depender do
    loop automático (+30min a cada 15s reais) pra acumular um valor que
    valha a pena mostrar. Pode ser chamado quantas vezes quiser — cada
    chamada SOMA em cima do que já tinha, não substitui.

    Retorna False se a estação não existir ou não estiver ativa (nada é
    gravado nesse caso)."""
    idx = estacao_num - 1
    if not (0 <= idx < MAX_ESTACOES) or not estacoes[idx].ativa:
        return False
    with conectar() as conn:
        cursor = conn.cursor()
        _aplicar_avanco(estacoes[idx], minutos, contar_ativas(), cursor)
    return True


# ─── Início de sessão ─────────────────────────────────────────────────────────
def iniciar_sessao() -> None:
    print(f"\n--- NOVA SESSÃO COMERCIAL ---")
    print(f"Estação (1 a {MAX_ESTACOES}): ", end="")
    try:
        idx = int(input()) - 1
    except ValueError:
        print("[ERRO] Entrada inválida."); return

    if not (0 <= idx < MAX_ESTACOES):
        print("[ERRO] Estação inexistente."); return
    if estacoes[idx].ativa:
        print("[ERRO] Estação ocupada."); return
    if estacao_em_manutencao(idx + 1):
        print("[ERRO] Estação em manutenção."); return

    print("ID do usuário (placa cadastrada, ex: ABC1D23): ", end="")
    uid = input().strip().upper() or "ANONIMO"

    if uid != "ANONIMO" and not validar_usuario(uid):
        print(f"[BLOQUEADO] Credencial '{uid}' não autorizada.")
        print("[DICA] Placas válidas: ABC1D23, XYZ9F88, GHI3K45, DEF7M01 — ou cadastre uma nova (opção 6).")
        return

    print("Horário de início (0–23): ", end="")
    try:
        hora = int(input())
        hora = hora if 0 <= hora <= 23 else hora_atual_brasil()
    except ValueError:
        hora = hora_atual_brasil()

    print("Método de pagamento (PIX / Cartao / App / QRCode): ", end="")
    pagamento = input().strip() or "App"

    uid_mascarado = mascarar_id(uid)
    data_hoje     = datetime.date.today().isoformat()
    conta_id      = conta_da_placa(uid)

    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessoes
                (id_estacao, usuario, data_sessao, hora_inicio, metodo_pagamento, status_pagamento, ativa, conta_id)
            VALUES (%s, %s, %s, %s, %s, 'PENDENTE', 1, %s)
            RETURNING id
        """, (idx + 1, uid_mascarado, data_hoje, hora, pagamento, conta_id))
        id_gerado_db = cursor.fetchone()[0]

    e = estacoes[idx]
    e.id_usuario       = uid_mascarado
    e.hora_inicio      = hora
    e.ativa            = True
    e.metodo_pagamento = pagamento
    e.id_sessao_db     = id_gerado_db

    demanda = ia_prever_demanda(hora)
    fator   = ia_calcular_tarifa(hora, contar_ativas())
    print(f"\n[OK] Sessão iniciada — Estação {idx+1} | ID DB: #{id_gerado_db}")
    print(f"[IA] Demanda prevista {hora}h: {demanda*100:.0f}% | Tarifa: R$ {TARIFA_BASE_KWH * fator:.2f}/kWh")

    ocpp_enviar("StartTransaction", e.id_estacao, {
        "status":        "Connected",
        "usuario":       uid_mascarado,
        "pagamento":     pagamento,
        "iaDemanda":     demanda,
        "tarifaInicial": round(TARIFA_BASE_KWH * fator, 2),
    })
    balancear_carga()


# ─── Encerramento de sessão ───────────────────────────────────────────────────
def encerrar_sessao() -> None:
    global receita_total
    print(f"\n--- ENCERRAR SESSÃO ---")
    print(f"Estação (1 a {MAX_ESTACOES}): ", end="")
    try:
        idx = int(input()) - 1
    except ValueError:
        print("[ERRO] Entrada inválida."); return

    if not (0 <= idx < MAX_ESTACOES) or not estacoes[idx].ativa:
        print("[ERRO] Estação inativa ou inexistente."); return

    e = estacoes[idx]

    print(f"\n[GATEWAY] Gerando ordem de pagamento...")
    cobranca = criar_pagamento_sandbox(e.valor_sessao, e.id_sessao_db)

    print(f"\n{'='*54}")
    print(f"   RECIBO DE RECARGA COMERCIAL — ChargeGrid Intelligence")
    print(f"{'='*54}")
    print(f"  Estação:      #{e.id_estacao:02d}")
    print(f"  Usuário:      {e.id_usuario}")
    print(f"  Consumo:      {e.kwh_consumidos:.2f} kWh")
    print(f"  Valor Total:  R$ {e.valor_sessao:.2f}")
    print(f"  Pagamento:    {e.metodo_pagamento}")
    print(f"  URL Checkout: {cobranca['url']}")
    print(f"  Transação:    {cobranca['transacao_id']}")
    print(f"{'='*54}")

    input("\n[MERCADO PAGO] Pressione [ENTER] após o pagamento ser concluído...")

    confirmar_pagamento(e.id_sessao_db)
    receita_total += e.valor_sessao

    ocpp_enviar("StopTransaction", e.id_estacao, {
        "status":     "Disconnected",
        "usuario":    e.id_usuario,
        "consumoKwh": round(e.kwh_consumidos, 2),
        "valorFinal": e.valor_sessao,
        "pagamento":  e.metodo_pagamento,
        "financeiro": "APROVADO_MERCADOPAGO",
    })
    e.encerrar()
    balancear_carga()


# ─── Painel operacional ───────────────────────────────────────────────────────
def painel_operacional() -> None:
    n = contar_ativas()
    hora_atual    = hora_atual_brasil()
    demanda_agora = ia_prever_demanda(hora_atual)

    print(f"\n{'='*65}")
    print(f"  ChargeGrid Intelligence — Painel Operacional Comercial")
    print(f"  {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}  |  "
          f"IA: demanda prevista {demanda_agora*100:.0f}% para {hora_atual}h")
    print(f"{'='*65}")
    print(f"  {'EST':<5} {'STATUS':<8} {'USUÁRIO':<14} {'kW':>6} {'kWh':>8} {'VALOR':>10} {'PAGT':<8}")
    print(f"  {'-'*60}")
    for e in estacoes:
        st = "Ocupada" if e.ativa else "Livre"
        print(f"  {e.id_estacao:02d}    {st:<8} {e.id_usuario:<14} "
              f"{e.potencia_kw:>5.1f}  {e.kwh_consumidos:>7.2f}  "
              f"R$ {e.valor_sessao:>6.2f}  {e.metodo_pagamento:<8}")
    print(f"  {'-'*60}")
    pot_total = sum(e.potencia_kw for e in estacoes if e.ativa)
    print(f"  Ativas: {n}/{MAX_ESTACOES} | "
          f"Potência: {pot_total:.1f}/{LIMITE_POTENCIA_GRID:.0f} kW | "
          f"Consumo dia: {consumo_total_diario:.2f} kWh | "
          f"Receita: R$ {receita_total:.2f}")
    print(f"{'='*65}")


# ─── Demo comercial ───────────────────────────────────────────────────────────
def demonstracao_comercial() -> None:
    global receita_total, consumo_total_diario

    print("\n╔══════════════════════════════════════════════╗")
    print("║  DEMO — Ambiente Comercial ChargeGrid        ║")
    print("║  Simula estacionamento de shopping center    ║")
    print("╚══════════════════════════════════════════════╝")

    def setup(idx, uid, hora, pgto):
        uid_m     = mascarar_id(uid)
        data_hoje = datetime.date.today().isoformat()
        conta_id  = conta_da_placa(uid)
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessoes
                    (id_estacao, usuario, data_sessao, hora_inicio, metodo_pagamento, status_pagamento, ativa, conta_id)
                VALUES (%s, %s, %s, %s, %s, 'PENDENTE', 1, %s)
                RETURNING id
            """, (idx + 1, uid_m, data_hoje, hora, pgto, conta_id))
            id_db = cursor.fetchone()[0]
        estacoes[idx].id_usuario       = uid_m
        estacoes[idx].hora_inicio      = hora
        estacoes[idx].ativa            = True
        estacoes[idx].metodo_pagamento = pgto
        estacoes[idx].id_sessao_db     = id_db

    print("\n[CENA 1] Cliente 1 conecta (12h — horário de almoço, pico)")
    setup(0, "ABC1D23", 12, "PIX")
    balancear_carga()
    simular_tempo()

    print("\n[CENA 2] DLB entra em ação — mais 2 clientes chegam")
    setup(1, "XYZ9F88", 12, "Cartao")
    setup(2, "GHI3K45", 12, "App")
    balancear_carga()

    print("\n[CENA 3] Horário de pico noturno — cliente 4 conecta às 19h")
    setup(3, "DEF7M01", 19, "QRCode")
    balancear_carga()
    simular_tempo()

    print("\n[CENA 4] Cliente 1 encerra — recibo emitido + DLB redistribui")
    receita_total += estacoes[0].valor_sessao
    cobranca_demo = criar_pagamento_sandbox(estacoes[0].valor_sessao, estacoes[0].id_sessao_db)
    print(f"\n  RECIBO DEMO: {estacoes[0].id_usuario} | "
          f"{estacoes[0].kwh_consumidos:.2f} kWh | "
          f"R$ {estacoes[0].valor_sessao:.2f} via {estacoes[0].metodo_pagamento}")
    print(f"  [SANDBOX]: {cobranca_demo['url']} (transação {cobranca_demo['transacao_id']})")

    confirmar_pagamento(estacoes[0].id_sessao_db)
    ocpp_enviar("StopTransaction", estacoes[0].id_estacao, {
        "usuario":    estacoes[0].id_usuario,
        "valorFinal": estacoes[0].valor_sessao,
    })
    estacoes[0].encerrar()
    balancear_carga()
    painel_operacional()

    # As estações 2-4 continuam "ativa=1" no banco até aqui — sem isso, a
    # demo deixa 3 sessões fantasmas no Postgres compartilhado, que voltam
    # a ocupar estações reais na próxima recuperação de sessões da API.
    print("\n[CENA 5] Encerrando as demais sessões abertas na demo")
    for i in range(1, 4):
        e = estacoes[i]
        if e.ativa:
            confirmar_pagamento(e.id_sessao_db)
            receita_total += e.valor_sessao
            ocpp_enviar("StopTransaction", e.id_estacao, {
                "usuario":    e.id_usuario,
                "valorFinal": e.valor_sessao,
            })
            e.encerrar()
    balancear_carga()

    receita_total        = 0.0
    consumo_total_diario = 0.0
    print("\n--- FIM DA DEMONSTRAÇÃO ---")


# ─── Menu principal ───────────────────────────────────────────────────────────
def main() -> None:
    inicializar_banco()
    print("\n  ChargeGrid Intelligence — GoodWe Challenge")
    print("  Plataforma de Gestão Comercial de Recarga EV\n")
    # Não roda demonstracao_comercial() automaticamente: ela grava sessões de
    # teste no chargegrid.db compartilhado com API/dashboard/chatbot e não
    # fecha todas (limitação conhecida). Rode pela opção 5 quando quiser.

    while True:
        print("\n══════ ChargeGrid Intelligence — Gestão Comercial ══════")
        print("  1. Iniciar sessão de recarga")
        print("  2. Simular passagem de tempo (+30 min)")
        print("  3. Encerrar sessão e emitir recibo")
        print("  4. Painel operacional")
        print("  5. Rodar demonstração comercial")
        print("  6. Cadastrar novo usuário (placa)")
        print("  7. Ver leitura agregada (chatbot/dashboard)")
        print("  8. Sair")
        print("═══════════════════════════════════════════════════════")
        print("  Escolha: ", end="")
        try:
            op = int(input())
        except ValueError:
            print("[ERRO] Opção inválida."); continue

        if   op == 1: iniciar_sessao()
        elif op == 2: simular_tempo()
        elif op == 3: encerrar_sessao()
        elif op == 4: painel_operacional()
        elif op == 5: demonstracao_comercial()
        elif op == 6:
            print("Placa: ", end="")
            placa_nova = input().strip()
            print("Nome (opcional): ", end="")
            nome_novo = input().strip() or "Usuario Cadastrado"
            # O PIN passou a ser obrigatório quando o histórico de pagamento
            # deixou de ser aberto (ver validar_pin) — sem pedir aqui, esta
            # opção do menu quebrava com TypeError.
            print("PIN de 4 números (usado pra ver o histórico no app): ", end="")
            pin_novo = input().strip()
            if not (pin_novo.isdigit() and len(pin_novo) == 4):
                print("[ERRO] O PIN precisa ter exatamente 4 números.")
            elif cadastrar_usuario(placa_nova, nome_novo, pin_novo):
                print(f"[OK] '{placa_nova}' cadastrado e autorizado.")
            else:
                print(f"[INFO] '{placa_nova}' já estava cadastrado.")
        elif op == 7:
            print(f"\n[LEITURA] Sessões ativas:       {listar_sessoes_ativas()}")
            print(f"[LEITURA] Status das estações:  {obter_status_estacoes()}")
            print(f"[LEITURA] Faturamento hoje:     R$ {obter_faturamento_dia():.2f}")
            print(f"[LEITURA] Sessões hoje:         {contar_sessoes_dia()}")
        elif op == 8:
            print("\n  Sistema encerrado.\n"); break
        else:
            print("[ERRO] Opção inválida.")


if __name__ == "__main__":
    main()

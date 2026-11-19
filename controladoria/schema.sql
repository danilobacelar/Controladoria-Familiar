-- Controladoria Familiar — MVP E01 (SQLite)
-- Derivado de docs/esquema_minimo_referencia.sql. Acrescenta lotes, propostas,
-- lançamentos, tarefas, decisões, vigências de titularidade (IF09),
-- referências declaratórias (DG08) e auditoria append-only (RT-04).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS usuarios (
  id TEXT PRIMARY KEY,
  nome TEXT NOT NULL,
  -- APROVADOR: único papel que decide. AGENTE e ADMIN_TECNICO nunca aprovam (F05 segregação).
  papel TEXT NOT NULL CHECK (papel IN ('APROVADOR','OPERADOR','AGENTE','ADMIN_TECNICO'))
);

CREATE TABLE IF NOT EXISTS fontes_documentais (
  id TEXT PRIMARY KEY,
  nome_seguro TEXT NOT NULL,
  tipo TEXT NOT NULL,
  origem_uri TEXT,
  hash_sha256 TEXT UNIQUE,              -- RT-02: mesmo arquivo não entra duas vezes
  versao TEXT,
  data_documento DATE,
  data_base DATE,
  importado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  acesso_classificacao TEXT NOT NULL DEFAULT 'RESTRITO'
);

CREATE TABLE IF NOT EXISTS lotes_importacao (
  id TEXT PRIMARY KEY,
  fonte_id TEXT REFERENCES fontes_documentais(id),
  estado TEXT NOT NULL CHECK (estado IN ('RECEBIDO','PROCESSADO','QUARENTENA')),
  erro TEXT,
  simulacao INTEGER NOT NULL DEFAULT 0,
  criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pessoas_entidades (
  id TEXT PRIMARY KEY,
  tipo TEXT NOT NULL CHECK (tipo IN ('PESSOA','EMPRESA','COOPERATIVA')),
  nome TEXT NOT NULL,
  papel TEXT,
  alias TEXT,                            -- termo para reconhecer a entidade em extratos
  nucleo_familiar INTEGER NOT NULL DEFAULT 0,
  origem TEXT NOT NULL,
  visibilidade TEXT NOT NULL DEFAULT 'RESTRITO',
  observacoes TEXT
);

CREATE TABLE IF NOT EXISTS instrumentos (
  id TEXT PRIMARY KEY,
  instituicao TEXT NOT NULL,
  tipo TEXT NOT NULL,
  titularidade_legal_relato TEXT,
  conjunta TEXT,
  titularidade_economica_status TEXT NOT NULL DEFAULT 'PENDENTE',
  origem TEXT NOT NULL,
  observacoes TEXT,
  fonte_id TEXT REFERENCES fontes_documentais(id)
);

-- IF09: só existe linha aqui depois de decisão expressa do APROVADOR, conta a conta, com vigência.
CREATE TABLE IF NOT EXISTS tit_economica_vigencias (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instrumento_id TEXT NOT NULL REFERENCES instrumentos(id),
  pessoa_id TEXT NOT NULL REFERENCES pessoas_entidades(id),
  percentual NUMERIC NOT NULL CHECK (percentual >= 0 AND percentual <= 100),
  vigencia_inicio DATE NOT NULL,
  vigencia_fim DATE,
  decisao_id INTEGER NOT NULL REFERENCES decisoes(id)
);

-- IF02 R01: valores do IRPF entram como ABERTURA_HISTORICA_IRPF, sempre com alerta e nunca como saldo atual.
CREATE TABLE IF NOT EXISTS posicoes (
  id TEXT PRIMARY KEY,
  instrumento_id TEXT REFERENCES instrumentos(id),
  instituicao TEXT,
  produto TEXT,
  data_base DATE NOT NULL,
  valor NUMERIC,
  moeda TEXT NOT NULL DEFAULT 'BRL',
  natureza_valor TEXT NOT NULL CHECK (natureza_valor IN ('ABERTURA_HISTORICA_IRPF','SALDO_EXTRATO')),
  evidencia_estado TEXT NOT NULL,
  alerta_atualizacao TEXT,
  saldo_atual_validado INTEGER NOT NULL DEFAULT 0,
  fonte_id TEXT REFERENCES fontes_documentais(id),
  aprovado INTEGER NOT NULL DEFAULT 0,
  observacoes TEXT,
  CHECK (natureza_valor <> 'ABERTURA_HISTORICA_IRPF'
         OR (alerta_atualizacao IS NOT NULL AND saldo_atual_validado = 0))
);

CREATE TABLE IF NOT EXISTS ativos (
  id TEXT PRIMARY KEY,
  classe TEXT NOT NULL,
  descricao TEXT NOT NULL,
  matricula TEXT,
  valor_fiscal_declarado NUMERIC,
  data_base DATE,
  origem TEXT NOT NULL,
  status_relato TEXT,
  status_documental TEXT NOT NULL DEFAULT 'PENDENTE'
);

-- DG08 e dívidas históricas (IF06): referências, não lançamentos econômicos.
CREATE TABLE IF NOT EXISTS referencias_declaratorias (
  id TEXT PRIMARY KEY,
  natureza TEXT NOT NULL,
  contraparte TEXT NOT NULL,
  valor_declarado NUMERIC,
  estado TEXT NOT NULL,
  evidencia TEXT,
  observacao TEXT,
  gera_efeito_economico INTEGER NOT NULL DEFAULT 0 CHECK (gera_efeito_economico = 0)
);

CREATE TABLE IF NOT EXISTS propostas (
  id TEXT PRIMARY KEY,
  fonte_id TEXT NOT NULL REFERENCES fontes_documentais(id),
  lote_id TEXT NOT NULL REFERENCES lotes_importacao(id),
  status TEXT NOT NULL CHECK (status IN ('PROPOSTA_NAO_GRAVADA','EM_REVISAO','ENCERRADA')),
  simulacao INTEGER NOT NULL DEFAULT 0,
  json TEXT NOT NULL,
  criado_por TEXT NOT NULL REFERENCES usuarios(id),
  criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS lancamentos (
  id TEXT PRIMARY KEY,                       -- ID estável (hash) — RT-02
  proposta_id TEXT NOT NULL REFERENCES propostas(id),
  instrumento_id TEXT REFERENCES instrumentos(id),
  linha_origem TEXT NOT NULL,
  data DATE NOT NULL,
  documento TEXT,
  historico TEXT,
  contraparte TEXT,
  valor NUMERIC NOT NULL,
  saldo_extrato NUMERIC,
  -- RT-05: eixos de estado independentes
  estado_documental TEXT NOT NULL DEFAULT 'IDENTIFICADO'
      CHECK (estado_documental IN ('IMPORTADO','IDENTIFICADO')),
  estado_classificacao TEXT NOT NULL DEFAULT 'SUGERIDO'
      CHECK (estado_classificacao IN ('SUGERIDO','CLASSIFICADO')),
  estado_aprovacao TEXT NOT NULL DEFAULT 'PENDENTE'
      CHECK (estado_aprovacao IN ('PENDENTE','APROVADO','REJEITADO','CONTESTACAO')),
  estado_conciliacao TEXT NOT NULL DEFAULT 'NAO_CONCILIADO',
  evento_sugerido TEXT,
  classe_sugerida TEXT,
  confianca TEXT,
  evento TEXT,
  classe TEXT,
  efeito_economico TEXT NOT NULL DEFAULT 'INDEFINIDO'
      CHECK (efeito_economico IN ('INDEFINIDO','NENHUM','RECEITA','DESPESA')),
  simulacao INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tarefas_aprovacao (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tipo TEXT NOT NULL,
  alvo_tabela TEXT NOT NULL,
  alvo_id TEXT NOT NULL,
  justificativa TEXT NOT NULL,
  responsavel_papel TEXT NOT NULL DEFAULT 'APROVADOR',
  exige_individual INTEGER NOT NULL DEFAULT 0,   -- DG08 / IF09: nunca em lote
  elegivel_lote INTEGER NOT NULL DEFAULT 0,
  estado TEXT NOT NULL DEFAULT 'ABERTA' CHECK (estado IN ('ABERTA','DECIDIDA')),
  simulacao INTEGER NOT NULL DEFAULT 0,
  criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CHECK (NOT (exige_individual = 1 AND elegivel_lote = 1))
);

CREATE TABLE IF NOT EXISTS decisoes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tarefa_id INTEGER NOT NULL REFERENCES tarefas_aprovacao(id),
  ator_id TEXT NOT NULL REFERENCES usuarios(id),
  decisao TEXT NOT NULL CHECK (decisao IN ('APROVAR','REJEITAR','CONTESTAR')),
  motivo TEXT NOT NULL CHECK (length(trim(motivo)) > 0),
  dados_json TEXT,
  em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- RT-04: trilha append-only (antes/depois/ator/data/motivo)
CREATE TABLE IF NOT EXISTS auditoria (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ator_id TEXT NOT NULL,
  acao TEXT NOT NULL,
  tabela TEXT NOT NULL,
  registro_id TEXT NOT NULL,
  antes TEXT,
  depois TEXT,
  motivo TEXT
);

CREATE TRIGGER IF NOT EXISTS auditoria_sem_update BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'auditoria é append-only'); END;
CREATE TRIGGER IF NOT EXISTS auditoria_sem_delete BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'auditoria é append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisoes_sem_update BEFORE UPDATE ON decisoes
BEGIN SELECT RAISE(ABORT, 'decisões não podem ser alteradas'); END;
CREATE TRIGGER IF NOT EXISTS decisoes_sem_delete BEFORE DELETE ON decisoes
BEGIN SELECT RAISE(ABORT, 'decisões não podem ser apagadas'); END;
CREATE TRIGGER IF NOT EXISTS referencias_sem_delete BEFORE DELETE ON referencias_declaratorias
BEGIN SELECT RAISE(ABORT, 'referências declaratórias não podem ser apagadas'); END;

-- Simulação nunca altera cadastro de titularidade econômica.
CREATE TRIGGER IF NOT EXISTS vigencia_exige_decisao_real BEFORE INSERT ON tit_economica_vigencias
WHEN (SELECT t.simulacao FROM decisoes d JOIN tarefas_aprovacao t ON t.id = d.tarefa_id
      WHERE d.id = NEW.decisao_id) <> 0
  OR (SELECT u.papel FROM decisoes d JOIN usuarios u ON u.id = d.ator_id
      WHERE d.id = NEW.decisao_id) <> 'APROVADOR'
BEGIN SELECT RAISE(ABORT, 'vigência exige decisão real do APROVADOR'); END;

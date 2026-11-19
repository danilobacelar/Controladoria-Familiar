-- E01 R01 — ESQUEMA MÍNIMO ILUSTRATIVO (PostgreSQL); NÃO EXECUTAR EM PRODUÇÃO SEM REVISÃO
-- Estado e acesso/autorização exigem implementação real de RBAC, RLS, criptografia, trilha append-only e backup.
CREATE TABLE fontes_documentais (
 id TEXT PRIMARY KEY, nome_seguro TEXT NOT NULL, tipo TEXT NOT NULL,
 origem_uri TEXT, hash_sha256 TEXT, versao TEXT, data_documento DATE,
 data_base DATE, importado_em TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
 acesso_classificacao TEXT NOT NULL DEFAULT 'RESTRITO'
);
CREATE TABLE pessoas_entidades (
 id TEXT PRIMARY KEY, tipo TEXT NOT NULL CHECK(tipo IN ('PESSOA','EMPRESA','COOPERATIVA')),
 nome TEXT NOT NULL, visibilidade TEXT NOT NULL DEFAULT 'RESTRITO'
);
CREATE TABLE instrumentos (
 id TEXT PRIMARY KEY, instituicao TEXT NOT NULL, tipo TEXT NOT NULL,
 titularidade_legal_relato TEXT, titularidade_economica_status TEXT NOT NULL DEFAULT 'PENDENTE',
 fonte_id TEXT REFERENCES fontes_documentais(id)
);
CREATE TABLE posicoes (
 id TEXT PRIMARY KEY, instrumento_id TEXT NOT NULL REFERENCES instrumentos(id),
 data_base DATE NOT NULL, valor NUMERIC(19,2), moeda CHAR(3) NOT NULL DEFAULT 'BRL',
 natureza_valor TEXT NOT NULL, evidencia_estado TEXT NOT NULL,
 fonte_id TEXT REFERENCES fontes_documentais(id), aprovado BOOLEAN NOT NULL DEFAULT FALSE,
 CHECK(valor IS NULL OR valor > -1000000000000)
);
CREATE TABLE ativos (
 id TEXT PRIMARY KEY, classe TEXT NOT NULL, descricao TEXT NOT NULL,
 matricula TEXT, status_relato TEXT, status_documental TEXT NOT NULL DEFAULT 'PENDENTE'
);
-- Nota: a DDL acima é ESBOÇO deliberado para discussão. Utilize a versão validada do desenvolvedor
-- e NÃO rode este arquivo automaticamente. Conflitos e permissões são parte da etapa técnica.
-- Entidades adicionais necessárias em produção: operacoes, linhas_balanceadas, lotes_importacao,
-- tarefas_aprovacao, decisoes, pagamentos, compras_cartao, parcelas, faturas, reembolsos,
-- contrapartidas, tit_economica_vigencias, revisoes_fiscais, auditoria_append_only, conciliacoes.

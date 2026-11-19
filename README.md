# Controladoria Familiar — MVP E01

Primeira funcionalidade operacional: **cadastro persistente, leitura de extrato, classificação sugerida e aprovação humana**, com trilha de auditoria somente de inclusão. Uso local, em um computador.

> Não é o ERP definitivo nem livro-razão oficial. O esquema parte de `docs/esquema_minimo_referencia.sql` (ilustrativo) e ainda exige revisão técnica antes de produção (RBAC completo, criptografia, backup).

## O que já funciona
- **Cadastros em staging** a partir dos CSVs de `03_CADASTROS` (pessoas, instrumentos, posições IRPF, bens, direitos/obrigações e empresas relacionadas).
- **Leitura de extrato PDF** → proposta no contrato `proposta_extracao.schema.json` (validada), com origem página/linha, hash do arquivo e conferência do saldo linha a linha.
- **Classificação sugerida** pelo catálogo 03 (nunca definitiva).
- **Fila de aprovação** na web; só o papel APROVADOR decide, sempre com motivo.
- **Auditoria** append-only (antes/depois/ator/data/motivo), decisões imutáveis.

## Regras garantidas pelo código
| Regra | Como |
|---|---|
| IF02 R01 | Posições do IRPF entram como `ABERTURA_HISTORICA_IRPF`, com alerta obrigatório; o banco recusa marcá-las como saldo atual. Painel mostra PL "Não apurado" sem saldo validado (T17). |
| IF09 | Cada conta conjunta gera tarefa individual; vigência só é gravada com percentuais por pessoa somando 100, data de início e decisão real do APROVADOR (trigger no banco). Sem 50/50 automático. |
| DG08 | Créditos declaratórios viram referência sem efeito econômico (bloqueado no banco); aprovação só individual, nunca em lote, nunca por agente/admin. |
| IF06 | Dívida histórica fica `QUITACAO_RELATADA_DOCUMENTOS_PENDENTES`; nenhuma quitação é lançada. |
| RN-01/03, F01 | Pagamento de fatura, transferência interna e aporte doméstico aprovados têm efeito `NENHUM`; entrada desconhecida nunca vira receita sem decisão explícita. |
| RT-02 | Mesmo arquivo (hash) não entra duas vezes; lançamentos têm ID estável. |
| RT-05 | Estados documental, de classificação, de aprovação e de conciliação separados. |
| T13 | Falha de leitura/conferência → lote em QUARENTENA, nada parcial gravado. |
| T14 | Texto com aparência de instrução no documento vira só alerta. |
| T18 | Reabertura de aprovado exige aprovador, motivo e nova tarefa individual. |
| Simulação | Importações marcadas como simulação geram tarefas separadas; decisões nelas nunca produzem efeito. |

## Como rodar (Mac)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install poppler            # opcional: melhora a leitura de PDF (pdftotext)

mkdir -p dados/cadastros        # dados/ é ignorado pelo git
# copie para dados/cadastros: pessoas.csv, instrumentos.csv, posicoes_irpf_2025.csv,
# bens_historicos.csv, direitos_obrigacoes_referencia.csv e empresas_relacionadas.csv (id,nome,alias)
python -m controladoria.cli carregar dados/cadastros

export CONTROLADORIA_PIN='escolha-um-pin'
python -m controladoria.app     # abre em http://127.0.0.1:5000
```
Testes (só dados fictícios): `pytest -q`.

## Pendências conhecidas
- Leitor calibrado para o layout de extrato de conta corrente testado; outros bancos e faturas de cartão precisam de leitores próprios.
- Faturas, parcelas, reembolsos vinculados, conciliação e fechamento mensal ainda não implementados.
- Autenticação local simples por PIN; sem criptografia em repouso. Não usar com dados reais sensíveis antes da revisão de privacidade.

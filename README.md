# Controladoria Familiar — MVP E01

Primeira funcionalidade operacional: **cadastro persistente, leitura de extrato, classificação sugerida e aprovação humana**, com trilha de auditoria somente de inclusão. Uso local, em um único computador.

> Não é o ERP definitivo nem livro-razão oficial. O esquema parte de `docs/esquema_minimo_referencia.sql` (ilustrativo) e ainda exige revisão técnica antes de produção (criptografia, backup, permissões completas).

## Estrutura
```
controladoria/
  schema.sql                     banco SQLite: tabelas, travas e auditoria
  db.py                          conexão, criação do banco, registro de auditoria
  cadastros.py                   carga dos CSVs de cadastro (IF02 R01, IF09, DG08, IF06)
  extrator.py                    leitura do PDF, conferência de saldo, proposta no contrato JSON
  classificador.py               sugestões de classificação (catálogo 03)
  servicos.py                    importação, fila de aprovação, decisões, reabertura, painel
  app.py                         interface web (Flask)
  cli.py                         linha de comando: carga, importação, senha, backup (sem poder de decisão)
  autenticacao.py                senha por usuário (hash), bloqueio por tentativas, auditoria de login
  proposta_extracao.schema.json  contrato da proposta
  templates/                     telas
tests/                           45 testes automáticos, só com dados fictícios
  fixtures/cadastros/            cadastros fictícios
  fixtures/extrato_ficticio.pdf  extrato fictício para o teste de inclusão
docs/esquema_minimo_referencia.sql
iniciar_demo.sh                  inicia a demonstração com dados fictícios
SEGURANCA.md                     o que está protegido e o que falta (criptografia, backup, recuperação, sessões)
```

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
| Evento desconhecido | Só eventos neutros do catálogo (transferência, aporte, pagamento de fatura, aplicação/resgate, baixa de reembolso) são aprovados como sem efeito; evento fora do catálogo ou que exige vínculo (quitação, alienação, mútuo etc.) é recusado e a tarefa continua em revisão. Classe precisa ser código 01.xx/02.xx do catálogo. |
| Permissões | Senha própria por usuário; OPERADOR importa e vê só as próprias propostas; fila, lançamentos, cadastros e auditoria só para o APROVADOR. |
| Simulação | Importações marcadas como simulação geram tarefas separadas; decisões nelas nunca produzem efeito. |

## Executar a demonstração no Mac (passo a passo, sem experiência prévia)

A demonstração usa **somente dados fictícios** (pessoas "Ana", "Bruno" etc.). Nenhum documento real é necessário.

1. **Baixar o código.** No GitHub, abra o repositório, escolha a branch `mvp-e01` (ou `main`, depois do merge), clique em **Code → Download ZIP**. Dê dois cliques no ZIP baixado para descompactar; ficará em Downloads uma pasta com nome parecido com `Controladoria-Familiar-mvp-e01`.
2. **Abrir o Terminal.** Pressione `Command + Espaço`, digite `Terminal` e Enter.
3. **Ir até a pasta.** Digite `cd ` (com espaço), arraste a pasta descompactada do Finder para a janela do Terminal e pressione Enter.
4. **Iniciar.** Digite `bash iniciar_demo.sh` e Enter.
   - Se aparecer um aviso pedindo para instalar as "ferramentas de linha de comando" (Command Line Tools), clique em **Instalar**, aguarde terminar e repita o passo 4. Isso instala o Python da Apple.
   - Na primeira vez, a instalação das bibliotecas leva 1 a 2 minutos.
5. **Usar.** O navegador abre em `http://127.0.0.1:8765` (se não abrir, copie esse endereço no Safari). Entre com o usuário **danilo** e a senha **demo-aprovador** (aprovador) ou **operador** / **demo-operador** (operador). Essas senhas valem só para a demonstração.
6. **Encerrar.** Volte ao Terminal e pressione `Control + C`.

Para recomeçar a demonstração do zero, apague a pasta `dados/demo` dentro da pasta do projeto.

### Roteiro de teste na interface
1. **Painel:** confirme "Patrimônio líquido: Não apurado", 2 contas conjuntas sem validação e 2 referências DG08 pendentes.
2. **Cadastros:** veja as posições IRPF com o alerta IF02 R01 e a dívida histórica como quitação relatada.
3. **Importar:** escolha `tests/fixtures/extrato_ficticio.pdf`, instrumento `A001` e clique em *Gerar proposta*. A proposta mostra 8 linhas e o JSON no contrato.
4. **Importar o mesmo arquivo de novo:** o sistema bloqueia a duplicidade.
5. **Operador:** saia, entre como `operador` e confirme que Fila, Lançamentos, Cadastros e Auditoria não aparecem e retornam "Acesso negado" se digitados; depois volte a entrar como `danilo`.
6. **Fila de aprovação:** aprove o pagamento de fatura (efeito NENHUM), tente aprovar uma entrada desconhecida sem informar evento/classe (bloqueado), tente aprovar uma saída com evento `QUALQUER_COISA` (bloqueado, não vira neutro), tente aprovar DG08 em lote (bloqueado), aprove uma tarefa IF09 com `P001=100;P002=0` e data de vigência.
7. **Lançamentos:** reabra um lançamento aprovado informando motivo.
8. **Auditoria:** confira antes/depois/ator/motivo de cada ação.

## Usar com cadastros próprios (somente após revisão de privacidade)
```bash
mkdir -p dados/cadastros        # dados/ é ignorado pelo git e nunca vai ao GitHub
# copie para dados/cadastros: pessoas.csv, instrumentos.csv, posicoes_irpf_2025.csv,
# bens_historicos.csv, direitos_obrigacoes_referencia.csv e empresas_relacionadas.csv (id,nome,alias)
.venv/bin/python -m controladoria.cli carregar dados/cadastros
.venv/bin/python -m controladoria.cli definir-senha danilo     # pede a senha sem mostrar na tela
.venv/bin/python -m controladoria.cli definir-senha operador   # opcional
.venv/bin/python -m controladoria.app       # http://127.0.0.1:8765
.venv/bin/python -m controladoria.cli backup dados/backups     # cópia consistente com verificação
```
O banco real fica em `dados/controladoria.db` (ou no caminho de `CONTROLADORIA_DB`).

## Testes
```bash
.venv/bin/python -m pytest -q
```
Rodam em Python 3.9 ou superior.

## Limitações conhecidas
- Leitor calibrado para o layout de extrato de conta corrente testado; outros bancos e faturas de cartão precisam de leitores próprios.
- Faturas, parcelas, reembolsos vinculados, conciliação e fechamento mensal ainda não implementados.
- Sem criptografia do banco e sem backup automático/externo: ver [SEGURANCA.md](SEGURANCA.md) antes de usar dados reais.
- Servidor web de desenvolvimento, acessível só no próprio computador (127.0.0.1).

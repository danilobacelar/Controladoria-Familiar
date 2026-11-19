# Segurança — situação do MVP E01 e pendências antes de dados reais

Regra geral: **até os itens "Pendente — bloqueia dados reais" estarem resolvidos, usar apenas dados fictícios ou anonimizados.**

## O que já está implementado
| Área | Implementado |
|---|---|
| Autenticação | Senha própria por usuário (APROVADOR e OPERADOR), guardada só como hash (Werkzeug/scrypt ou PBKDF2). Usuário sem senha não entra. Mensagem genérica de erro. Bloqueio de 15 min após 5 tentativas erradas. Login, falhas e bloqueios auditados. AGENTE e ADMIN_TECNICO não recebem senha nem entram na interface. |
| Permissões | Matriz por tela (`PERMISSOES` em `app.py`): OPERADOR vê painel resumido, importa e vê só as próprias propostas; fila, lançamentos, cadastros, auditoria e qualquer decisão são exclusivos do APROVADOR. O serviço de decisão revalida o papel (defesa em profundidade). |
| Sessão | Cookie `HttpOnly` e `SameSite=Strict`; expira após 30 min sem uso; sessão nova a cada login; chave de sessão aleatória a cada início do servidor; saída por POST. |
| Formulários | Token CSRF em todo POST. |
| Cabeçalhos | `X-Frame-Options: DENY`, `Content-Security-Policy` restritiva, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`. |
| Rede | Servidor escuta só em 127.0.0.1 (o próprio Mac). |
| Integridade | Auditoria e decisões imutáveis (triggers no banco); travas IF02 R01, IF09, DG08 no banco. |
| Backup | Comando `python -m controladoria.cli backup <pasta>`: cópia consistente pela API do SQLite + `PRAGMA integrity_check` + registro na auditoria. |
| Repositório | `dados/` e `*.db` fora do git; testes só com dados fictícios. |

## Pendências
### 1. Criptografia do banco — Pendente, bloqueia dados reais
- **Mínimo imediato:** FileVault ativo no Mac (criptografa o disco inteiro). Verificar em Ajustes do Sistema → Privacidade e Segurança → FileVault.
- **Recomendado:** criptografar o arquivo do banco com SQLCipher (`sqlcipher3`) e chave guardada no Keychain do macOS, nunca em arquivo ou no código. Exige trocar o driver SQLite, migrar o banco e testar desempenho.
- Backups precisam ser criptografados também (ver item 2).

### 2. Backup automático e cópia externa — Pendente, bloqueia dados reais
- Agendar o comando de backup diariamente (launchd do macOS) e manter retenção (ex.: 7 diários, 4 semanais, 12 mensais).
- Copiar para destino fora do Mac, criptografado (ex.: disco externo com APFS criptografado, ou OneDrive com arquivo cifrado por `age`/`gpg`). O OneDrive não deve receber o `.db` em texto aberto.
- Monitorar falhas de backup (alerta no painel).

### 3. Recuperação — Pendente
- Procedimento escrito de restauração: parar o servidor, copiar o backup escolhido para `dados/controladoria.db`, rodar `PRAGMA integrity_check`, subir o servidor, conferir a auditoria.
- Teste de restauração mensal registrado na auditoria.
- Definir RPO (perda máxima aceitável, sugestão: 24 h) e RTO (tempo para voltar, sugestão: 1 h).

### 4. Sessões e autenticação — Parcial
- Falta: troca de senha pela interface, expiração/rotação de senha, segundo fator (TOTP) para o APROVADOR, encerramento de todas as sessões ao trocar senha.
- Falta: servidor de produção (gunicorn/waitress) e HTTPS se o sistema um dia for acessado fora do próprio Mac.

### 5. Privacidade e visibilidade por pessoa — Pendente
- Filtro de visibilidade por pessoa (Patrícia, filhos) conforme T12; hoje o APROVADOR vê tudo e o OPERADOR não vê cadastros.
- Política de retenção e descarte de documentos e dados de terceiros (nomes em extratos).

### 6. Trilha de auditoria fora do banco — Pendente
- A auditoria é imutável dentro do banco, mas quem tiver o arquivo pode apagá-lo inteiro. Exportar periodicamente um resumo assinado (hash encadeado) para local separado.

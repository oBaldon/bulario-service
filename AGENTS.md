# AGENTS.md — Bulário Service

## 1. Contexto e instruções compartilhadas
Este diretório é o repositório Git do serviço produtor de bulas. O Portal e o RAG, quando disponíveis no workspace, encontram-se em `../intelireg/` e `../rag-service/`. O diretório `../intelireg-engineering/` é uma **pasta local não versionada** que reúne documentação e diretrizes compartilhadas; não execute Git ali nem exija histórico, branch, commit ou remoto.

Para tarefas de integração/arquitetura, leia `../intelireg-engineering/AGENTS.md`, se existir, os documentos pertinentes da pasta e os `AGENTS.md` dos demais serviços afetados. A abertura de todas as pastas no VS Code não implica que o Codex carregou automaticamente seus arquivos de instruções. Se não puder acessá-los, informe a limitação e utilize as regras deste arquivo.

## 2. Fontes e estado
- Leia `README.md` integralmente e depois os arquivos pertinentes: `docs/SPRINT02_OPERATIONAL_ACCEPTANCE.md`, `pyproject.toml`, `docker-compose.yml`, `migrations/`, código da ingestão/publicação e testes.
- Verifique `git status --short --branch`, `git rev-parse HEAD` e alterações locais **neste repositório** antes de propor mudanças. Não atribua ao ambiente atual os resultados de execuções anteriores ou de arquivos ZIP.
- Distinga implementação examinada, estado do banco observado, documentação, planejamento e hipótese. Confirme os nomes de scripts, serviços, schemas e entradas no código da revisão disponível.

## 3. Fronteiras e integridade documental
- O Bulário é responsável por ingestão ANVISA, download e armazenamento de PDFs, extração e persistência de texto normalizado, histórico de versões e publicação destinada ao Portal.
- Preserve a responsabilidade de escrita do produtor em `bulario.*` e o contrato de publicação em `public.bulas` (`BULA_CONTRACT_V1` conforme código/documentação da revisão). Não transfira a ingestão para o Portal. Não afirme que o publisher fornece campos clínicos estruturados sem verificar sua implementação.
- Preserve integridade de hashes, proveniência, vínculos entre produto/documento/versão/artefato, idempotência, checkpoints, locks e distinção entre falha transitória e permanente. Não trate endpoints internos de origem como contratos públicos estáveis.
- Qualquer mudança no publisher, contrato ou schema deve ser confrontada com as consultas, snapshots e comparações do Portal em `../intelireg/`.

## 4. Execução, infraestrutura e segurança
- Consulte `Dockerfile`, `docker-compose.yml`, `.env.example`, comandos em `README.md` e entrypoints reais antes de indicar como executar ingestão. Subir o container não implica iniciar sincronização.
- O Compose examinado utiliza rede externa e PostgreSQL compartilhados com o Portal: confirme a configuração atual antes de propor banco/rede novos. Migrations do produtor são independentes das migrations Laravel.
- Por padrão, somente leitura: não edite arquivos, aplique patches, execute ingestão full/incremental, downloads massivos, migrations, reprocessamento, reset, alterações de storage ou comandos contra produção sem autorização explícita.
- Verifique previamente se scripts em `ops/`, smoke tests e testes de integração escrevem no banco, acessam ANVISA ou alteram storage. Não exponha `.env`, tokens ou URLs de conexão com credenciais.

## 5. Entrega e continuidade
- Em propostas de mudança, apresente fluxo atual, código e contratos afetados, efeitos sobre o Portal, compatibilidade de dados, testes e rollback. Prefira patches pontuais preparados para revisão humana; não presuma permissão para editar ou aplicar.
- Quando uma decisão transversal for aprovada, aponte o registro local correspondente em `../intelireg-engineering/` e os READMEs a atualizar. Não presuma que a pasta de engenharia está sob controle de versão.
- Reporte separadamente código preparado, alteração aplicada e teste efetivamente executado.

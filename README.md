# bulario-service — Produtor do Bulário InteliReg

> **Estado do repositório:** serviço Python funcional para descoberta, ingestão, versionamento, armazenamento, extração textual, publicação e auditoria de bulas consumidas pelo Portal InteliReg. O pipeline possui modos `full`, `incremental` e `reconcile`, checkpoint/resume, classificação de falhas, retries controlados, lock operacional, observabilidade estruturada e scheduler de referência.
>
> **Fronteira arquitetural:** o `bulario-service` é o **produtor**. O Portal InteliReg é consumidor read-only de `public.bulas`, do histórico operacional em `bulario.*` e do acervo documental compartilhado.
>
> **Fonte e responsabilidade:** o PDF oficial é a evidência documental primária. Texto normalizado e metadados são artefatos derivados e rastreáveis. A disponibilidade e a interpretação dos dados devem ser conferidas na fonte oficial.

---

## Sumário

1. [Visão geral](#1-visão-geral)
2. [Responsabilidades e limites](#2-responsabilidades-e-limites)
3. [Princípios de integridade](#3-princípios-de-integridade)
4. [Arquitetura e fluxo](#4-arquitetura-e-fluxo)
5. [Modelo de dados](#5-modelo-de-dados)
6. [Contrato com o Portal InteliReg](#6-contrato-com-o-portal-intelireg)
7. [Pré-requisitos](#7-pré-requisitos)
8. [Instalação e configuração](#8-instalação-e-configuração)
9. [Docker e banco compartilhado](#9-docker-e-banco-compartilhado)
10. [Migrations](#10-migrations)
11. [Integração com o Bulário ANVISA](#11-integração-com-o-bulário-anvisa)
12. [Sessão e transporte HTTP](#12-sessão-e-transporte-http)
13. [Download e storage de PDFs](#13-download-e-storage-de-pdfs)
14. [Extração e persistência textual](#14-extração-e-persistência-textual)
15. [Publicação em `public.bulas`](#15-publicação-em-publicbulas)
16. [Histórico documental](#16-histórico-documental)
17. [Pipeline de ingestão](#17-pipeline-de-ingestão)
18. [Modos operacionais](#18-modos-operacionais)
19. [Checkpoint, resume e recuperação](#19-checkpoint-resume-e-recuperação)
20. [Retries e classificação de falhas](#20-retries-e-classificação-de-falhas)
21. [Lock operacional](#21-lock-operacional)
22. [Observabilidade](#22-observabilidade)
23. [Scheduler](#23-scheduler)
24. [Auditoria e hardening](#24-auditoria-e-hardening)
25. [Acervo compartilhado e cutover](#25-acervo-compartilhado-e-cutover)
26. [Testes e smokes](#26-testes-e-smokes)
27. [Segurança e governança](#27-segurança-e-governança)
28. [Troubleshooting](#28-troubleshooting)
29. [Comandos de referência](#29-comandos-de-referência)

---

# 1. Visão geral

O `bulario-service` produz os dados e documentos de bulas utilizados pelo Portal InteliReg.

O serviço é responsável por:

- descobrir produtos no Bulário da ANVISA;
- consultar detalhe e histórico documental;
- baixar e validar PDFs de paciente e profissional;
- persistir os documentos em acervo controlado;
- calcular hashes SHA-256;
- extrair e normalizar texto;
- persistir versões e artefatos operacionais;
- publicar somente a versão corrente validada em `public.bulas`;
- preservar o histórico completo no schema `bulario`;
- executar cargas `full`, `incremental` e `reconcile`;
- pausar e retomar execuções;
- classificar falhas e aplicar retries controlados;
- impedir sincronizações concorrentes incompatíveis;
- emitir observabilidade estruturada;
- auditar o estado publicado e o histórico documental.

O Portal InteliReg não executa ingestão de bulas.

---

# 2. Responsabilidades e limites

## 2.1 O que o serviço faz

O serviço atua como camada de produção documental:

```text
Bulário ANVISA
    ↓
discovery
    ↓
produto + histórico de versões
    ↓
PDF paciente/profissional
    ↓
storage + SHA-256
    ↓
texto normalizado + SHA-256
    ↓
estado operacional em bulario.*
    ↓
validação BULA_CONTRACT_V1
    ↓
publicação da versão corrente em public.bulas
    ↓
Portal InteliReg
```

## 2.2 O que o serviço não faz

O `bulario-service`:

- não é interface de usuário;
- não realiza análise regulatória ou clínica;
- não produz parecer regulatório;
- não executa RAG ou LLM;
- não altera análises do Portal;
- não usa `public.bulas` como tabela de trabalho;
- não deve sobrescrever silenciosamente conflitos de identidade, hash ou fingerprint;
- não deve persistir tokens temporários da ANVISA como parte do contrato público.

---

# 3. Princípios de integridade

O pipeline segue os seguintes princípios:

- o PDF oficial é a evidência documental primária;
- texto extraído é artefato derivado e rastreável;
- `source_product_id` identifica o produto na fonte;
- `source_document_id` identifica a versão documental na fonte;
- `source_fingerprint` representa metadados estáveis da versão;
- hashes dos PDFs representam os bytes efetivamente armazenados;
- hashes do texto representam o conteúdo normalizado;
- ingestões devem ser idempotentes;
- somente registros completos e validados podem chegar a `ready`;
- conflito material não é corrigido silenciosamente;
- detalhes transitórios da fonte ficam isolados do contrato público;
- o Portal consome somente storage keys relativas, nunca caminhos absolutos.

---

# 4. Arquitetura e fluxo

## 4.1 Componentes principais

```text
┌──────────────────────────────────────────┐
│ Bulário ANVISA                           │
│ frontend + interface /api/consulta/*    │
└───────────────────┬──────────────────────┘
                    │
         Chrome / sessão autenticada
                    │
                    ▼
┌──────────────────────────────────────────┐
│ Connector ANVISA                         │
│ discovery + detalhe + histórico + PDFs  │
└───────────────────┬──────────────────────┘
                    │
                    ▼
┌──────────────────────────────────────────┐
│ Coordinator de ingestão                  │
│ full / incremental / reconcile           │
│ checkpoint / retry / lock                │
└───────────────┬──────────────────────────┘
                │
        ┌───────▼────────┐
        │ PostgreSQL      │
        │ schema bulario  │
        └───────┬────────┘
                │
        ┌───────▼────────┐
        │ Acervo /data    │
        │ PDFs            │
        └───────┬────────┘
                │
        ┌───────▼────────┐
        │ pdftotext       │
        │ texto normalizado│
        └───────┬────────┘
                │
                ▼
        BULA_CONTRACT_V1
                │
                ▼
        public.bulas
                │
                ▼
        Portal InteliReg
```

## 4.2 Transações

Chamadas externas e processamento de PDF não mantêm transação PostgreSQL longa.

Os marcos operacionais são persistidos em transações curtas. A publicação pública e a transição final para `ready` são confirmadas de forma controlada.

Arquivos já gravados no storage podem permanecer após falha de banco ou pipeline. Isso é intencional: artefatos válidos podem ser reutilizados por uma reexecução idempotente.

---

# 5. Modelo de dados

O produtor mantém seu estado operacional no schema `bulario`.

## 5.1 Execuções

### `bulario.ingestion_runs`

Representa uma execução de sincronização.

Campos operacionais incluem conceitos como:

- modo;
- janela temporal;
- status;
- `page_size`;
- `last_completed_page`;
- `last_checkpoint_at`;
- timestamps de execução.

Estados observados incluem:

```text
running
paused
completed
failed
```

`paused` representa execução retomável.

### `bulario.ingestion_items`

Representa um produto processado dentro de uma execução.

O ciclo operacional é:

```text
discovered
    ↓
fetching
    ↓
downloaded
    ↓
normalized
    ↓
ready
```

Falhas são persistidas com metadados controlados, incluindo:

```text
error_code
error_message
error_class
retry_count
```

## 5.2 Produtos e versões

### `bulario.products`

Identidade operacional do produto na fonte.

### `bulario.document_versions`

Mantém cada `source_document_id` conhecido, incluindo versão corrente e histórico.

Apenas uma versão deve ser `current` por produto.

Metadados podem estar ausentes quando não são capturados pela fonte ou pelo pipeline. `NULL` não deve ser interpretado como ausência regulatória do atributo.

## 5.3 Documentos

### `bulario.document_artifacts`

Mantém os PDFs de paciente/profissional, storage key, tamanho e SHA-256.

### `bulario.document_text_artifacts`

Mantém o texto normalizado derivado de cada PDF.

A identidade textual considera:

```text
document_artifact_id + normalization_version
```

A versão atual de normalização é `v1`.

A proveniência completa é:

```text
produto
→ versão
→ PDF
→ SHA-256 do PDF
→ texto normalizado
→ SHA-256 do texto
```

---

# 6. Contrato com o Portal InteliReg

A fronteira pública é:

```text
public.bulas
```

O Portal:

- lê `public.bulas`;
- lê o histórico operacional em `bulario.*`;
- lê PDFs no acervo compartilhado;
- não grava no schema operacional do produtor.

O `bulario-service`:

- é proprietário das escritas em `bulario.*`;
- publica a versão corrente em `public.bulas`;
- mantém o histórico fora da listagem pública corrente;
- não depende do Portal para processar a ingestão.

O contrato estável de consumo é `BULA_CONTRACT_V1`.

O identificador de origem publicado segue:

```text
anvisa:{source_product_id}:{source_document_id}
```

Tokens temporários de download não fazem parte do contrato.

---

# 7. Pré-requisitos

## 7.1 Execução local

- Python 3.13;
- `uv`;
- PostgreSQL acessível;
- `pdftotext`/Poppler;
- Google Chrome para o transporte atualmente validado.

## 7.2 Execução containerizada

- Docker;
- Docker Compose;
- rede do Compose do InteliReg acessível;
- PostgreSQL do InteliReg ativo.

---

# 8. Instalação e configuração

## 8.1 Dependências

```bash
uv sync
```

## 8.2 Ambiente

```bash
cp .env.example .env
```

O `.env` é carregado automaticamente quando existe. Variáveis já presentes no ambiente têm precedência.

O parser aceita:

```text
KEY=VALUE
export KEY=VALUE
valores entre aspas simples ou duplas
comentários
linhas vazias
```

## 8.3 Variáveis principais

As variáveis efetivamente disponíveis devem ser conferidas em `.env.example`.

Entre as utilizadas pelo serviço:

| Variável | Função |
|---|---|
| `APP_ENV` | identifica o ambiente |
| `DATABASE_URL` | conexão SQLAlchemy/psycopg no host |
| `BULARIO_DOCKER_DATABASE_URL` | conexão usada dentro do container |
| `INTELIREG_DOCKER_NETWORK` | rede Docker do InteliReg |
| `BULARIO_STORAGE_ROOT` | raiz física do acervo para execução local/container |
| `BULAS_ARCHIVE_HOST_PATH` | caminho do acervo no host para montagem Docker |
| `BULARIO_INCREMENTAL_OVERLAP_DAYS` | overlap configurável do incremental |

URLs `postgresql://` podem ser normalizadas internamente para `postgresql+psycopg://`.

---

# 9. Docker e banco compartilhado

O `bulario-service` não sobe PostgreSQL próprio.

Primeiro, no Portal:

```bash
cd ../intelireg
docker compose up -d db
```

Depois, no `bulario-service`:

```bash
docker compose up --build
```

Por padrão, o serviço utiliza a rede Docker do InteliReg e acessa `db:5432`.

Se a rede tiver outro nome, ajuste:

```text
INTELIREG_DOCKER_NETWORK
```

Encerrar somente o produtor:

```bash
docker compose down
```

O PostgreSQL continua sob responsabilidade do Compose do Portal.

---

# 10. Migrations

As migrations pertencem ao `bulario-service` e são executadas com Alembic.

Atualizar:

```bash
uv run alembic upgrade head
```

Verificar revisão:

```bash
uv run alembic current
```

Via Docker:

```bash
docker compose run --rm app uv run alembic upgrade head
```

As migrations operacionais atuam no schema `bulario`. `public.bulas` é a fronteira de publicação, não o schema de trabalho do produtor.

---

# 11. Integração com o Bulário ANVISA

A integração foi construída a partir da interface observada no frontend público do Bulário.

A família de endpoints `/api/consulta/*` **não deve ser tratada como API pública documentada ou estável**.

O connector suporta:

- descoberta paginada por período;
- `count=100` por padrão no adapter de discovery;
- detalhe por `idProduto`;
- histórico paginado;
- deduplicação da versão vigente quando repetida no histórico;
- download documental;
- timeout;
- retries controlados;
- classificação de erros;
- telemetria segura.

Mudanças da fonte podem exigir manutenção do adapter.

---

# 12. Sessão e transporte HTTP

## 12.1 Estratégia atualmente validada

O acesso HTTP direto sem sessão pode ser bloqueado pela origem. O transporte validado estabelece a sessão por Google Chrome/Playwright e reutiliza cookies e `User-Agent` em memória no cliente `httpx`.

Componentes:

- `AnvisaBrowserSessionBootstrap`;
- `AnvisaAuthenticatedHttpClient`.

Cookies e tokens não devem ser impressos.

## 12.2 Robustez HTTP

Falhas transitórias incluem:

```text
429
500
502
503
504
ConnectTimeout
ReadTimeout
transport errors
```

`403` representa sessão rejeitada/bloqueada e recebe tratamento específico para evitar retry cego.

A telemetria deve conter apenas dados operacionais, por exemplo:

```text
path
page
attempt
status
elapsed
outcome
```

---

# 13. Download e storage de PDFs

Para cada documento de paciente/profissional o downloader:

- exige HTTP `200`;
- rejeita corpo vazio;
- valida `%PDF-`;
- calcula SHA-256;
- mantém o token temporário fora dos logs;
- associa o PDF ao `source_document_id`.

A storage key é relativa e determinística:

```text
bulas/{source_product_id}/{source_document_id}/{patient|professional}.pdf
```

Exemplo:

```text
bulas/1174609/35480554/patient.pdf
```

A implementação:

- rejeita escape da raiz;
- usa escrita temporária e rename atômico;
- recalcula tamanho e hash;
- reutiliza arquivo idêntico;
- gera conflito quando a mesma key contém bytes divergentes;
- não sobrescreve silenciosamente conteúdo divergente.

---

# 14. Extração e persistência textual

A extração usa `pdftotext` do Poppler.

A normalização `v1`:

- aplica Unicode NFKC;
- uniformiza quebras de linha;
- remove bytes NUL;
- remove espaços finais;
- reduz sequências excessivas de linhas vazias;
- não resume nem reinterpreta conteúdo regulatório.

Cada artefato textual preserva:

```text
source_product_id
source_document_id
kind
document_storage_key
document_sha256
text_sha256
character_count
text_content
extraction_method
normalization_version
```

Comportamento:

```text
mesmo PDF + mesma normalization_version + mesmo texto
→ idempotente

mesmo PDF + mesma normalization_version + texto divergente
→ conflito

mesmo PDF + nova normalization_version
→ novo artefato permitido
```

---

# 15. Publicação em `public.bulas`

## 15.1 Candidato

Antes da escrita, o pipeline materializa um `BulaPublicationCandidate`.

O candidato exige consistência entre:

- identidade da fonte;
- versão corrente;
- metadados disponíveis;
- PDFs de paciente e profissional;
- storage keys seguras;
- hashes dos PDFs;
- textos persistidos;
- hashes e contagens textuais;
- proveniência;
- `ingestion_status=ready`.

## 15.2 Política do publisher

O publisher é transacional e usa lock por `source_record_id` para evitar publicação concorrente da mesma versão lógica.

Política:

```text
candidate inválido
→ nenhuma escrita

source_record_id inexistente
→ INSERT

registro existente e conteúdo idêntico
→ unchanged / no-op

registro existente com divergência material
→ conflito

mais de uma linha para o mesmo source_record_id
→ conflito operacional
```

O publisher não sobrescreve conflito.

---

# 16. Histórico documental

O detalhe/histórico da ANVISA é tratado como conjunto de versões documentais.

Para cada `source_document_id`:

- a versão é persistida em `bulario.document_versions`;
- PDFs disponíveis são baixados;
- os artefatos são persistidos;
- texto normalizado é persistido;
- a rastreabilidade é preservada.

A versão corrente deve possuir os documentos necessários para publicação.

Versões históricas podem ter disponibilidade documental parcial, conforme a fonte.

Somente a versão `current` gera o candidato publicado em `public.bulas`.

---

# 17. Pipeline de ingestão

O pipeline completo por produto é:

```text
ingestion_run
→ discovery
→ ingestion_item=discovered
→ fetching
→ detalhe + histórico
→ download dos PDFs
→ storage + SHA-256
→ downloaded
→ persistência operacional
→ extração e normalização
→ persistência textual
→ normalized
→ BULA_CONTRACT_V1
→ publisher
→ ready
```

O coordinator permite múltiplos produtos dentro do mesmo run.

Falha de um produto não desfaz produtos já concluídos.

Discovery suporta múltiplas páginas e deduplicação por `source_product_id`.

---

# 18. Modos operacionais

A CLI oficial expõe:

```text
full
incremental
reconcile
```

Todos reutilizam o mesmo núcleo de ingestão, checkpoint, retry, publicação e lock.

## 18.1 Full

Usado para carga ampla de uma janela explícita.

```bash
uv run python -m bulario_service.sync full \
  --period-start 2026-01-01T00:00:00.000Z \
  --period-end 2026-08-29T23:59:59.999Z \
  --page-size 10 \
  --max-pages 5 \
  --max-products 20 \
  --headed
```

Os limites por invocação são guardrails. Um run pode ficar `paused` e continuar com o mesmo `run_id`.

## 18.2 Incremental

A primeira execução exige início explícito:

```bash
uv run python -m bulario_service.sync incremental \
  --initial-period-start 2026-08-29T00:00:00.000Z \
  --period-end 2026-08-30T23:59:59.999Z \
  --page-size 5 \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

Depois de existir incremental `completed`, a próxima janela é derivada do último incremental concluído com overlap configurável.

O overlap é técnico e configurável; não representa obrigação regulatória ou regra homologada da fonte.

## 18.3 Reconcile

Executa uma janela mais ampla para redescobrir possíveis lacunas.

A janela é explícita:

```bash
uv run python -m bulario_service.sync reconcile \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-31T23:59:59.999Z \
  --page-size 5 \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

Reconciliation não relaxa idempotência nem regras de conflito.

---

# 19. Checkpoint, resume e recuperação

## 19.1 Checkpoint

`last_completed_page` representa a última página integralmente processada.

O checkpoint só avança após os produtos admitidos naquela página chegarem a estado terminal aplicável.

Se a execução parar no meio de uma página por guardrail, essa página deve ser consultada novamente no resume.

## 19.2 Resume

Full:

```bash
uv run python -m bulario_service.sync full \
  --resume RUN_ID \
  --max-pages 5 \
  --max-products 20 \
  --headed
```

Incremental:

```bash
uv run python -m bulario_service.sync incremental \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

Reconcile:

```bash
uv run python -m bulario_service.sync reconcile \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

A janela e o `page_size` persistidos são reutilizados.

## 19.3 Auto-resume

O scheduler usa `--auto-resume`.

Regra:

```text
1 incremental paused
→ retoma

0 paused
→ abre próxima janela

mais de 1 paused
→ falha controlada
```

## 19.4 Recuperação de run failed

Runs incrementais legados ou não resolvidos podem exigir recuperação explícita:

```bash
uv run python -m bulario_service.sync incremental \
  --recover-failed RUN_ID \
  --max-pages 1 \
  --max-products 2 \
  --headed
```

A recuperação mantém o checkpoint do mesmo run.

---

# 20. Retries e classificação de falhas

Classes operacionais:

```text
transient
source_blocked
permanent
conflict
unknown
```

## 20.1 Transient

Exemplos:

- timeout;
- HTTP `429`;
- HTTP `500/502/503/504`;
- erros transitórios de transporte.

Podem ser reabertos no mesmo item/run, respeitando limites.

## 20.2 Source blocked

Exemplo típico:

```text
HTTP 403
```

Não recebe retry cego no coordinator. O run pode ser pausado para nova sessão/invocação.

## 20.3 Permanent

Exemplos:

- payload incompatível;
- documento inválido;
- erro HTTP não transitório.

## 20.4 Conflict

Divergência material em:

- identidade;
- fingerprint;
- PDF;
- texto;
- storage;
- publicação.

Não há sobrescrita automática.

## 20.5 Configuração por execução

```text
--max-product-retries
--retry-backoff-seconds
```

Exemplo:

```bash
uv run python -m bulario_service.sync incremental \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --max-product-retries 2 \
  --retry-backoff-seconds 2 \
  --headed
```

---

# 21. Lock operacional

`full`, `incremental` e `reconcile` compartilham um PostgreSQL advisory lock global:

```text
bulario-service:sync:global:v1
```

O lock é session-level porque o pipeline realiza múltiplos commits.

O serviço usa:

```text
pg_try_advisory_lock(...)
pg_advisory_unlock(...)
```

Uma segunda sincronização incompatível falha rapidamente em vez de aguardar indefinidamente.

O exit code documentado para lock ocupado é:

```text
3
```

---

# 22. Observabilidade

A CLI mantém saída humana em `stdout` e emite eventos JSON Lines em `stderr`.

Eventos:

```text
sync_started
sync_result
sync_blocked
sync_failed
sync_invalid_request
```

Métricas de resultado incluem, quando aplicável:

```text
run_id
run_status
mode
period_start
period_end
resumed
start_page
checkpoint_page
pages_fetched
source_total_elements
discovered
duplicates
skipped_terminal
processed
ready
failed
published
inserted
unchanged
conflicts
retries
failed_by_class
duration_seconds
stopped_by_page_limit
stopped_by_product_limit
stopped_by_source_blocked
```

Valores sensíveis são sanitizados defensivamente.

Não registrar:

```text
Authorization
Cookie
Bearer tokens
passwords
secrets
tokens temporários
conteúdo integral de PDF
texto integral de bula em logs operacionais
```

---

# 23. Scheduler

A execução incremental recorrente pode ser disparada por `systemd --user`.

Arquivos:

```text
ops/systemd/bulario-incremental.service.in
ops/systemd/bulario-incremental.timer
ops/systemd/install-user-timer.sh
ops/systemd/uninstall-user-timer.sh
```

Instalar:

```bash
./ops/systemd/install-user-timer.sh
```

Verificar:

```bash
systemctl --user status bulario-incremental.timer
systemctl --user list-timers bulario-incremental.timer
```

Executar manualmente:

```bash
systemctl --user start bulario-incremental.service
```

Logs:

```bash
journalctl --user -u bulario-incremental.service
```

Desinstalar:

```bash
./ops/systemd/uninstall-user-timer.sh
```

O scheduler apenas chama a CLI oficial. Ele não contém lógica paralela de ingestão.

A configuração de referência utiliza execução incremental com `--auto-resume`.

A frequência do timer é uma decisão operacional e não deve ser tratada como requisito regulatório.

`full` e `reconcile` permanecem operações explícitas.

---

# 24. Auditoria e hardening

## 24.1 Auditoria operacional

```bash
uv run python -m bulario_service.operational_audit
```

É read-only:

- não abre Chrome;
- não consulta a ANVISA;
- não grava banco;
- não altera arquivos.

Valida, entre outros:

- publicações ANVISA existentes;
- unicidade de `source_record_id`;
- `ready`;
- vínculo com versão operacional;
- fingerprints;
- PDFs;
- `%PDF-`;
- SHA-256 físico/operacional/público;
- texto normalizado `v1`;
- `character_count`;
- `text_sha256`;
- runs inconsistentes;
- histórico documental;
- versão `current`;
- artefatos e storage keys.

Falha de invariável retorna exit code `2`.

## 24.2 Hardening de conflitos

```bash
uv run python -m bulario_service.smoke_hardening
```

Valida, com rollback, cenários como:

```text
publicação idêntica
→ unchanged

fingerprint divergente sob mesma identidade
→ bloqueado

hash de PDF divergente sob mesma identidade
→ bloqueado

texto divergente sob mesma normalization_version
→ bloqueado

metadado material divergente sob mesmo source_document_id
→ bloqueado
```

## 24.3 Handoff produtor → Portal

```bash
uv run python -m bulario_service.smoke_portal_handoff
```

Valida a publicação `ready`, os vínculos operacionais e os PDFs físicos.

Esse smoke comprova o lado produtor. Os testes consumidores continuam pertencendo ao Portal.

---

# 25. Acervo compartilhado e cutover

A convenção local recomendada é:

```text
~/Projetos/
├── bulario-service/
├── intelireg/
└── intelireg-data/
    └── bulas/
```

Produtor local:

```dotenv
BULARIO_STORAGE_ROOT=../intelireg-data
```

Docker:

```dotenv
BULAS_ARCHIVE_HOST_PATH=../intelireg-data
BULARIO_STORAGE_ROOT=/data
```

Uma storage key:

```text
bulas/143989/35481769/patient.pdf
```

resolve para:

```text
host:      ../intelireg-data/bulas/143989/35481769/patient.pdf
container: /data/bulas/143989/35481769/patient.pdf
```

O Portal monta o mesmo acervo como read-only.

## 25.1 Cutover de acervo legado

Dry-run:

```bash
uv run python -m bulario_service.smoke_storage_cutover
```

Aplicar:

```bash
uv run python -m bulario_service.smoke_storage_cutover --write
```

O cutover:

- usa `document_artifacts` como inventário;
- valida `%PDF-`;
- valida hash de origem e destino;
- copia atomicamente;
- não altera storage keys;
- não altera banco;
- não remove automaticamente o acervo legado;
- recusa sobrescrever arquivo divergente.

---

# 26. Testes e smokes

## 26.1 Testes automatizados

Local:

```bash
uv run pytest
```

Docker:

```bash
docker compose run --rm app uv run pytest
```

Os totais de testes não são fixados neste README; consulte a saída da suíte corrente.

## 26.2 Smoke básico da fonte

```bash
uv run python -m bulario_service.smoke_anvisa
```

Não grava banco nem baixa PDFs.

## 26.3 Diagnóstico de transporte

```bash
uv run python -m bulario_service.anvisa_transport_probe \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

## 26.4 Observador de rede

```bash
uv run python -m bulario_service.anvisa_network_observer --headed
```

## 26.5 Sessão autenticada

```bash
uv run python -m bulario_service.smoke_anvisa_session \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

## 26.6 PDFs

```bash
uv run python -m bulario_service.smoke_anvisa_documents \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

## 26.7 Storage

```bash
uv run python -m bulario_service.smoke_anvisa_storage \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

## 26.8 Persistência operacional

```bash
uv run python -m bulario_service.smoke_anvisa_persistence \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

## 26.9 Texto

Somente extração:

```bash
uv run python -m bulario_service.smoke_document_text
```

Extração + persistência:

```bash
uv run python -m bulario_service.smoke_document_text_persistence
```

## 26.10 Contrato público

Dry-run:

```bash
uv run python -m bulario_service.smoke_publication_contract
```

Schema consumidor:

```bash
uv run python -m bulario_service.smoke_portal_schema
```

Publisher com rollback:

```bash
uv run python -m bulario_service.smoke_portal_publisher
```

Publisher com commit:

```bash
uv run python -m bulario_service.smoke_portal_publisher --write
```

## 26.11 E2E de um produto

```bash
uv run python -m bulario_service.smoke_e2e_pipeline \
  --period-start 2026-08-28T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

## 26.12 Batch controlado

```bash
uv run python -m bulario_service.smoke_batch_ingestion \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-29T23:59:59.999Z \
  --page-size 2 \
  --max-pages 2 \
  --max-products 4 \
  --headed
```

## 26.13 Lock

```bash
uv run python -m bulario_service.smoke_operational_lock \
  --hold-seconds 20
```

## 26.14 Handoff

```bash
uv run python -m bulario_service.smoke_portal_handoff
```

## 26.15 Auditoria

```bash
uv run python -m bulario_service.operational_audit
```

---

# 27. Segurança e governança

Controles esperados:

- logs sem cookies/tokens;
- storage keys relativas;
- validação de PDF;
- SHA-256;
- idempotência;
- conflitos bloqueados;
- publisher transacional;
- advisory lock;
- histórico rastreável;
- texto derivado vinculado ao PDF;
- separação produtor/consumidor;
- auditoria read-only;
- ausência de autocorreção silenciosa.

A integração com a fonte deve respeitar mecanismos de proteção. HTTP `403`, `429` ou mudanças da origem não justificam contorno de controles da fonte.

---

# 28. Troubleshooting

## 28.1 PostgreSQL indisponível

Confirme o banco do Portal:

```bash
cd ../intelireg
docker compose ps
docker compose logs db
```

## 28.2 Migration pendente

```bash
uv run alembic current
uv run alembic upgrade head
```

## 28.3 HTTP 403

Interprete como sessão rejeitada ou bloqueio da fonte.

Valide primeiro:

```bash
uv run python -m bulario_service.anvisa_network_observer --headed
```

ou:

```bash
uv run python -m bulario_service.smoke_anvisa_session \
  --period-start INICIO \
  --period-end FIM \
  --headed
```

Não implemente retry cego de `403`.

## 28.4 HTTP 429

É tratado como limitação transitória da fonte. O connector aplica retries limitados; se a falha persistir, preserve o checkpoint e retome depois.

## 28.5 Run `paused`

Retome o mesmo `run_id` com o mesmo modo:

```bash
uv run python -m bulario_service.sync MODE \
  --resume RUN_ID \
  --max-pages N \
  --max-products N \
  --headed
```

## 28.6 Incremental `failed`

Use recuperação explícita somente quando apropriado:

```bash
uv run python -m bulario_service.sync incremental \
  --recover-failed RUN_ID \
  --max-pages 1 \
  --max-products 2 \
  --headed
```

## 28.7 Lock ocupado

Outra sincronização incompatível está ativa. Verifique os processos e aguarde a liberação; não force dois `full/incremental/reconcile` concorrentes.

## 28.8 PDF divergente

Não sobrescreva o arquivo. Investigue:

- `source_product_id`;
- `source_document_id`;
- tipo;
- storage key;
- SHA-256 persistido;
- SHA-256 físico;
- fingerprint.

## 28.9 Auditoria falha

Execute:

```bash
uv run python -m bulario_service.operational_audit
```

Trate a invariável reportada antes de novas cargas amplas.

---

# 29. Comandos de referência

Esta seção consolida as principais operações disponíveis no repositório.

> Os períodos abaixo são exemplos. Ajuste a janela ao objetivo da execução e valide o comportamento da fonte antes de cargas extensas.

## 29.1 Setup e banco

```bash
# Dependências
uv sync

# Configuração
cp .env.example .env

# Banco compartilhado
cd ../intelireg
docker compose up -d db
cd ../bulario-service

# Migrations
uv run alembic upgrade head
uv run alembic current

# Testes
uv run pytest
```

## 29.2 Docker

```bash
# Subir produtor
docker compose up --build

# Testes no container
docker compose run --rm app uv run pytest

# Migration no container
docker compose run --rm app uv run alembic upgrade head

# Encerrar produtor
docker compose down
```

## 29.3 Diagnóstico da fonte e transporte

```bash
# Smoke mínimo de discovery + detalhe
uv run python -m bulario_service.smoke_anvisa

# Probe de transportes
uv run python -m bulario_service.anvisa_transport_probe \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed

# Observar requisição real da SPA
uv run python -m bulario_service.anvisa_network_observer --headed

# Bootstrap de sessão + HTTP direto
uv run python -m bulario_service.smoke_anvisa_session \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

## 29.4 Documentos, storage e texto

```bash
# Download e validação dos PDFs
uv run python -m bulario_service.smoke_anvisa_documents \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed

# Download + storage
uv run python -m bulario_service.smoke_anvisa_storage \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed

# Persistência operacional
uv run python -m bulario_service.smoke_anvisa_persistence \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed

# Extração textual
uv run python -m bulario_service.smoke_document_text

# Extração + persistência textual
uv run python -m bulario_service.smoke_document_text_persistence
```

## 29.5 Contrato e publisher

```bash
# Materializar/validar candidato sem publicação
uv run python -m bulario_service.smoke_publication_contract

# Inspecionar schema consumidor
uv run python -m bulario_service.smoke_portal_schema

# Publisher com rollback
uv run python -m bulario_service.smoke_portal_publisher

# Publisher com commit explícito
uv run python -m bulario_service.smoke_portal_publisher --write

# Validar handoff produtor → Portal
uv run python -m bulario_service.smoke_portal_handoff
```

## 29.6 Pipeline E2E

```bash
# Um produto, ponta a ponta
uv run python -m bulario_service.smoke_e2e_pipeline \
  --period-start 2026-08-28T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed

# Batch controlado multi-page
uv run python -m bulario_service.smoke_batch_ingestion \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-29T23:59:59.999Z \
  --page-size 2 \
  --max-pages 2 \
  --max-products 4 \
  --headed
```

## 29.7 Carga full

```bash
# Nova carga full controlada
uv run python -m bulario_service.sync full \
  --period-start 2026-01-01T00:00:00.000Z \
  --period-end 2026-08-29T23:59:59.999Z \
  --page-size 10 \
  --max-pages 5 \
  --max-products 20 \
  --headed

# Continuar o mesmo full run
uv run python -m bulario_service.sync full \
  --resume RUN_ID \
  --max-pages 5 \
  --max-products 20 \
  --headed
```

Para uma carga ampla, repita o `--resume RUN_ID` enquanto o run permanecer `paused`. Não abra um novo full run para continuar a mesma janela.

## 29.8 Incremental

```bash
# Primeiro incremental
uv run python -m bulario_service.sync incremental \
  --initial-period-start 2026-08-29T00:00:00.000Z \
  --period-end 2026-08-30T23:59:59.999Z \
  --page-size 5 \
  --max-pages 2 \
  --max-products 10 \
  --headed

# Incremental seguinte
uv run python -m bulario_service.sync incremental \
  --period-end 2026-08-31T23:59:59.999Z \
  --max-pages 2 \
  --max-products 10 \
  --headed

# Resume
uv run python -m bulario_service.sync incremental \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --max-product-retries 2 \
  --retry-backoff-seconds 2 \
  --headed

# Recuperar explicitamente incremental failed
uv run python -m bulario_service.sync incremental \
  --recover-failed RUN_ID \
  --max-pages 1 \
  --max-products 2 \
  --headed
```

## 29.9 Reconciliation

```bash
# Nova reconciliação
uv run python -m bulario_service.sync reconcile \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-31T23:59:59.999Z \
  --page-size 5 \
  --max-pages 2 \
  --max-products 10 \
  --headed

# Resume da reconciliação
uv run python -m bulario_service.sync reconcile \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

## 29.10 Hardening, lock e auditoria

```bash
# Conflitos/idempotência/rollback
uv run python -m bulario_service.smoke_hardening

# Lock operacional
uv run python -m bulario_service.smoke_operational_lock \
  --hold-seconds 20

# Auditoria read-only completa
uv run python -m bulario_service.operational_audit
```

## 29.11 Acervo

```bash
# Ver o que precisa migrar
uv run python -m bulario_service.smoke_storage_cutover

# Efetivar cutover
uv run python -m bulario_service.smoke_storage_cutover --write
```

## 29.12 Scheduler

```bash
# Instalar timer
./ops/systemd/install-user-timer.sh

# Estado
systemctl --user status bulario-incremental.timer
systemctl --user list-timers bulario-incremental.timer

# Disparar manualmente
systemctl --user start bulario-incremental.service

# Logs
journalctl --user -u bulario-incremental.service

# Remover timer
./ops/systemd/uninstall-user-timer.sh
```

## 29.13 Sequência recomendada para validar uma instalação nova

```bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run alembic current
uv run pytest

uv run python -m bulario_service.smoke_anvisa
uv run python -m bulario_service.smoke_anvisa_session \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed

uv run python -m bulario_service.smoke_e2e_pipeline \
  --period-start 2026-08-28T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed

uv run python -m bulario_service.smoke_hardening
uv run python -m bulario_service.smoke_portal_handoff
uv run python -m bulario_service.operational_audit
```

Essa sequência valida primeiro dependências e integração mínima; depois prova o pipeline ponta a ponta, hardening, handoff e auditoria antes de uma carga ampla.

## 29.14 Sequência recomendada para uma carga completa

```bash
# 1. Validar banco e invariantes antes da carga
uv run alembic upgrade head
uv run pytest
uv run python -m bulario_service.operational_audit

# 2. Iniciar full run
uv run python -m bulario_service.sync full \
  --period-start INICIO \
  --period-end FIM \
  --page-size 10 \
  --max-pages 5 \
  --max-products 20 \
  --headed

# 3. Se o run ficar paused, continuar o MESMO run
uv run python -m bulario_service.sync full \
  --resume RUN_ID \
  --max-pages 5 \
  --max-products 20 \
  --headed

# 4. Repetir o resume enquanto necessário
# 5. Auditar ao final
uv run python -m bulario_service.operational_audit

# 6. Validar handoff final
uv run python -m bulario_service.smoke_portal_handoff
```

Para cargas reais, acompanhe duração, `run_status`, checkpoint, contadores de `ready/failed`, conflitos, retries e eventos estruturados. Não interprete silêncio prolongado como travamento sem antes verificar processo/logs: operações com navegador, rede, PDFs e múltiplos produtos podem levar minutos.


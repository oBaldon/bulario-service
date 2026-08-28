# bulario-service

Serviço produtor de dados e documentos de bulas para o ecossistema InteliReg.

## Responsabilidade

Este repositório é responsável pela ingestão, preparação e publicação das bulas consumidas pelo Portal InteliReg.

O Portal InteliReg permanece consumidor dos dados produzidos por este serviço.

## Princípios

- o PDF oficial é a evidência documental primária;
- o conteúdo textual extraído é um artefato derivado e rastreável;
- somente registros completos e validados podem ser publicados;
- ingestões devem ser idempotentes;
- detalhes específicos da fonte regulatória devem permanecer isolados da aplicação.

## Estado atual

O projeto possui bootstrap Python, configuração mínima, ambiente Docker, persistência operacional inicial no PostgreSQL compartilhado com o InteliReg e operações de aplicação para controlar execuções e itens de ingestão.

O schema `bulario` contém as estruturas operacionais do produtor:

- `bulario.ingestion_runs`;
- `bulario.ingestion_items`;
- `bulario.products`;
- `bulario.document_versions`;
- `bulario.document_artifacts`;
- `bulario.document_text_artifacts`.

A aplicação já consegue iniciar/finalizar uma execução, registrar itens descobertos e validar as transições operacionais `discovered → fetching → downloaded → normalized → ready`, com falha terminal rastreável por `error_code` e `error_message`.

A integração inicial com o Bulário da ANVISA já possui adapter para:

- descoberta paginada de produtos por período, usando `count=100` por padrão;
- leitura de detalhe e histórico por `idProduto`;
- paginação do histórico;
- deduplicação da bula vigente quando ela também aparece no histórico;
- normalização de erro HTTP, timeout, JSON inválido e payload inesperado.

A interface `/api/consulta/*` foi observada no frontend público da ANVISA e não é tratada como API pública documentada ou estável. Os testes utilizam fixtures sanitizadas derivadas das respostas reais capturadas durante a investigação.

Ainda não há:

- download de PDFs;
- extração textual;
- publicação para o Portal.

## Requisitos

Para execução local:

- Python 3.13;
- uv.

Para execução containerizada:

- Docker;
- Docker Compose.

## Instalação local

```bash
uv sync
```

O `uv sync` também atualiza `uv.lock` quando as dependências declaradas no `pyproject.toml` mudam.

## Execução local

A configuração local é carregada automaticamente de `.env` quando o arquivo existe. Variáveis já presentes no ambiente do processo têm precedência e não são sobrescritas pelo arquivo.

Crie o arquivo local a partir do exemplo:

```bash
cp .env.example .env
```

Depois:

```bash
uv run python -m bulario_service
```

O mesmo carregamento é usado pelo Alembic. Portanto, com `DATABASE_URL` definido no `.env`, não é necessário executar `export DATABASE_URL=...` antes das migrations:

```bash
uv run alembic upgrade head
uv run alembic current
```

Em Docker, CI ou produção, valores injetados pelo ambiente continuam tendo precedência sobre o `.env`.

O parser local suporta o formato usado pelo projeto: comentários, linhas vazias, `KEY=VALUE`, `export KEY=VALUE` e valores entre aspas simples ou duplas.

## Testes locais

```bash
uv run pytest
```

## Docker

Subir o ambiente:

```bash
docker compose up --build
```

O `bulario-service` não sobe um PostgreSQL próprio. No ambiente local ele reutiliza a instância PostgreSQL já executada pelo Compose do InteliReg.

Antes de subir o serviço, inicialize ao menos o banco do InteliReg:

```bash
cd ../intelireg
docker compose up -d db
```

Por padrão, o `bulario-service` entra na rede Docker `intelireg-local_default` e conecta ao serviço `db` na porta interna `5432`. Caso o projeto InteliReg utilize outro nome de rede, ajuste `INTELIREG_DOCKER_NETWORK`.

Executar os testes dentro do container:

```bash
docker compose run --rm app uv run pytest
```

Encerrar o ambiente:

```bash
docker compose down
```

O comando acima encerra somente os containers pertencentes ao `bulario-service`; o PostgreSQL do InteliReg permanece sob responsabilidade do Compose do Portal.

## Smoke test da fonte ANVISA

Existe um smoke test manual e controlado para validar se o ambiente de execução consegue acessar diretamente a interface do Bulário observada no frontend da ANVISA.

Ele solicita apenas um registro por padrão e consulta o detalhe do primeiro produto:

```bash
uv run python -m bulario_service.smoke_anvisa
```

Também é possível informar explicitamente o período:

```bash
uv run python -m bulario_service.smoke_anvisa \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z
```

Esse comando não grava no banco e não faz download de PDFs. Ele existe apenas para validar listagem + detalhe em ambiente real. Falhas como HTTP 403/Cloudflare devem ser tratadas como bloqueio da fonte para HTTP automatizado e não como motivo para contornar mecanismos de proteção.

## Probe de transporte com Playwright

O acesso HTTP direto com `httpx` retornou HTTP 403 no smoke test real. Para definir o transporte operacional antes de avançar para ingestão em volume, o projeto possui um probe que compara três caminhos na mesma sessão:

1. `fetch()` executado pela página do Chromium;
2. `BrowserContext.request` do Playwright;
3. `httpx` usando, somente em memória, os cookies obtidos da sessão do navegador e o mesmo `User-Agent`.

O probe usa `count=1` por padrão e não grava no banco nem baixa PDFs.

Execução headless:

```bash
uv run python -m bulario_service.anvisa_transport_probe \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z
```

Execução com navegador visível:

```bash
uv run python -m bulario_service.anvisa_transport_probe \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

O perfil persistente fica em `.playwright/anvisa-profile`, já ignorado pelo Git. Cookies não são impressos nem persistidos pelo probe fora do próprio perfil do Chromium.

A escolha do transporte definitivo deve ser feita somente após comparar os resultados reais. A preferência técnica é utilizar o caminho de menor custo que permaneça estável para listagem, detalhes e documentos.

## Observação da requisição real da SPA

Quando a navegação headed responde `200`, mas chamadas reproduzidas imediatamente retornam `403`, use o observador de rede para capturar a primeira chamada relevante que o próprio frontend fizer com sucesso.

Execute:

```bash
uv run python -m bulario_service.anvisa_network_observer --headed
```

Depois, na janela do Chromium:

1. aguarde o Bulário carregar;
2. faça uma busca normal ou abra o detalhe de um produto;
3. o processo capturará a primeira resposta `200` de listagem, detalhe ou documento;
4. em seguida comparará a mesma URL via `page.fetch`, `context.request` e `httpx`.

O observador redige headers sensíveis como `Cookie`, `Authorization` e `Set-Cookie` antes de imprimir qualquer informação. Ele não persiste tokens ou cookies fora do perfil do Chromium.

O objetivo desse passo é identificar o estado/requisição real utilizada pela SPA antes de escolher o transporte definitivo da ingestão.


## Google Chrome como browser do Playwright

O Chromium empacotado pelo Playwright recebeu bloqueio da origem durante os testes, enquanto o Google Chrome instalado no mesmo host acessou o Bulário normalmente. Por isso, os probes Playwright usam `channel="chrome"` por padrão e um perfil persistente separado:

```text
.playwright/anvisa-profile-google-chrome
```

Teste do observador com Google Chrome visível:

```bash
uv run python -m bulario_service.anvisa_network_observer --headed
```

Teste comparativo de transportes com Google Chrome:

```bash
uv run python -m bulario_service.anvisa_transport_probe \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

Para comparação diagnóstica com o Chromium empacotado pelo Playwright, use explicitamente:

```bash
--browser-channel chromium
```

Essa seleção ainda é diagnóstica e não define o transporte definitivo do pipeline de ingestão.

## Bootstrap de sessão para HTTP direto

Após validar que `httpx` funciona quando recebe a sessão estabelecida pelo Google Chrome, o projeto possui um bootstrap dedicado:

- `AnvisaBrowserSessionBootstrap`: abre o Chrome, acessa o Bulário, coleta cookies + `User-Agent` em memória e fecha o browser;
- `AnvisaAuthenticatedHttpClient`: cria um `httpx.Client` com esse estado.

O smoke abaixo valida se o HTTP direto continua funcionando **depois que o browser já foi fechado**:

```bash
uv run python -m bulario_service.smoke_anvisa_session \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

O teste executa somente:

1. bootstrap da sessão;
2. fechamento do Chrome;
3. uma página pequena de discovery via `httpx`;
4. detalhe do primeiro produto retornado via `httpx`.

Ainda não há renovação automática de sessão, ingestão em massa ou download de PDFs nesta etapa.

## Robustez HTTP para detalhe e histórico

O connector usa timeout de leitura de 60 segundos por padrão e até três tentativas para falhas transitórias. O comportamento é diferente por classe de erro:

- `403`: sessão rejeitada; não é repetido automaticamente pelo connector;
- `500`, `502`, `503`, `504`: retry com backoff;
- `ConnectTimeout` e `ReadTimeout`: retry com backoff;
- outros erros HTTP: falha imediata.

O smoke de sessão imprime telemetria segura por chamada:

```text
ANVISA HTTP path=/api/consulta/bulario/1174609 page=1 attempt=1 status=200 elapsed=2.81s outcome=ok
```

A telemetria contém somente método, path, página, tentativa, status, duração e resultado. Cookies, tokens e headers sensíveis não são registrados.

## Download e validação de PDFs

O serviço possui downloader documental para os tokens de bula retornados pelo detalhe da ANVISA.

Para cada documento paciente/profissional, o downloader:

- usa a mesma sessão `httpx` criada após o bootstrap do Google Chrome;
- chama o endpoint de documento com o token temporário;
- exige HTTP `200`;
- rejeita corpo vazio;
- valida a assinatura binária `%PDF-`;
- calcula SHA-256;
- retorna bytes, tamanho, hash, tipo e `source_document_id` em memória;
- nunca inclui o token temporário na telemetria.

O smoke real baixa somente os PDFs da versão vigente do primeiro produto encontrado:

```bash
uv run python -m bulario_service.smoke_anvisa_documents \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

A saída esperada inclui linhas como:

```text
ANVISA PDF type=patient source_document_id=35480554 attempt=1 status=200 bytes=123456 elapsed=0.80s outcome=ok
PDF validated type=patient bytes=123456 sha256=<64 hex>
```

O token da ANVISA não é impresso. Nesta etapa os bytes permanecem apenas em memória; storage definitivo e extração textual entram em incrementos posteriores.

## Storage local de documentos

Os PDFs validados podem ser persistidos em storage local por `LocalDocumentStorage`.

A storage key é relativa e determinística:

```text
bulas/{source_product_id}/{source_document_id}/{patient|professional}.pdf
```

Exemplo:

```text
bulas/1174609/35480554/patient.pdf
```

A implementação:

- nunca persiste caminho absoluto como storage key;
- rejeita tentativa de escape da raiz de storage;
- grava em arquivo temporário e promove via rename atômico;
- recalcula SHA-256 e tamanho após a escrita;
- reutiliza idempotentemente o arquivo quando a mesma key já contém o mesmo hash;
- gera conflito quando a mesma key contém conteúdo diferente;
- não sobrescreve silenciosamente uma versão documental divergente.

O smoke real executa discovery, detalhe, download dos PDFs vigentes e storage local:

```bash
uv run python -m bulario_service.smoke_anvisa_storage \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

Por padrão, a raiz física é `./storage`, ignorada pelo Git. Ela pode ser alterada com:

```bash
--storage-root /caminho/para/storage
```

Nesta etapa não há ainda persistência dos metadados no banco, publicação no contrato `public.bulas` ou extração de texto.

## Persistência operacional de produtos, versões e artefatos

O produtor persiste seu estado de trabalho exclusivamente no schema `bulario`.

As tabelas operacionais documentais são:

```text
bulario.products
bulario.document_versions
bulario.document_artifacts
```

A identidade da fonte permanece separada dos hashes dos PDFs:

- `source_product_id`: identidade do produto na fonte;
- `source_document_id`: identidade da versão documental na fonte;
- `source_fingerprint`: SHA-256 de metadados estáveis da versão, sem tokens transitórios e sem a flag `current`;
- `document_artifacts.sha256`: SHA-256 dos bytes do PDF persistido.

Uma alteração de hash sob a mesma versão/tipo de artefato, ou uma alteração do `source_fingerprint` sob o mesmo `source_document_id`, é tratada como conflito operacional e não é sobrescrita silenciosamente.

`document_versions.last_ingestion_item_id` permite vincular a versão ao item de ingestão mais recente; o item referencia sua `ingestion_run`. O vínculo é opcional para smokes manuais.

Antes do primeiro uso:

```bash
uv run alembic upgrade head
```

O smoke real, após aplicar a migration, executa o fluxo completo até a persistência operacional:

```bash
uv run python -m bulario_service.smoke_anvisa_persistence \
  --period-start 2026-08-26T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

O comando é idempotente para a mesma versão e os mesmos hashes. Ele ainda não publica em `public.bulas`.

## Extração e normalização textual

O PDF oficial permanece a evidência documental primária. O texto é um artefato derivado para etapas posteriores de comparação, indexação e RAG.

A extração usa `pdftotext` (Poppler). O Dockerfile instala explicitamente `poppler-utils`; no host local, `pdftotext` deve estar disponível no `PATH`.

Cada artefato textual mantém rastreabilidade para `source_product_id`, `source_document_id`, tipo paciente/profissional, `document_storage_key`, `document_sha256`, `text_sha256` e quantidade de caracteres.

A normalização aplica Unicode NFKC, uniformiza quebras de linha, remove bytes `NUL`, remove espaços finais e limita sequências excessivas de linhas vazias. O conteúdo regulatório não é resumido nem reinterpretado nesta etapa.

Smoke sobre os PDFs operacionais já armazenados:

```bash
uv run python -m bulario_service.smoke_document_text
```

Saída esperada:

```text
Text extracted kind=patient source_document_id=... document_sha256=... text_sha256=... characters=... storage_key=...
Text extracted kind=professional source_document_id=... document_sha256=... text_sha256=... characters=... storage_key=...
extracted_texts=2
```

Nesta etapa o texto permanece em memória durante o smoke. A persistência do artefato textual e a publicação em `public.bulas` continuam separadas.

## Persistência operacional do texto derivado

Os textos normalizados são persistidos em `bulario.document_text_artifacts`, vinculados diretamente ao PDF operacional correspondente.

A identidade do artefato textual considera:

```text
document_artifact_id + normalization_version
```

O registro mantém:

- método de extração (`pdftotext-layout-utf8`);
- versão de normalização (`v1`);
- `text_sha256`;
- `character_count`;
- `text_content`;
- vínculo ao PDF por `document_artifact_id`.

A cadeia de proveniência permanece:

```text
produto → versão → PDF → SHA-256 do PDF → texto normalizado → SHA-256 do texto
```

Comportamento:

```text
mesmo PDF + mesma normalization_version + mesmo texto/hash
→ idempotente

mesmo PDF + mesma normalization_version + texto/hash divergente
→ conflito operacional

mesmo PDF + nova normalization_version
→ novo artefato textual permitido
```

Antes do uso:

```bash
uv run alembic upgrade head
```

O head esperado passa a ser `20260828_0003`.

Smoke real:

```bash
uv run python -m bulario_service.smoke_document_text_persistence
```

O smoke usa os PDFs já existentes no storage, extrai/normaliza novamente, persiste os artefatos textuais e pode ser executado repetidamente de forma idempotente.

Ainda não há publicação em `public.bulas`.

## Dry-run do contrato público

Antes de qualquer escrita em `public.bulas`, o produtor materializa um `BulaPublicationCandidate` a partir do estado operacional e valida a completude do registro.

O candidato exige:

- identidade e proveniência da fonte;
- `source_record_id`;
- `source_fingerprint`;
- `ingested_at`;
- `ingestion_status=ready`;
- metadados regulatórios disponíveis;
- PDF paciente e profissional;
- storage keys relativas e seguras;
- SHA-256 de ambos os PDFs;
- texto persistido para ambos os documentos;
- SHA-256 do texto, método de extração e versão de normalização;
- consistência entre texto, contagem de caracteres e hash.

O identificador de origem atualmente materializado é:

```text
anvisa:{source_product_id}:{source_document_id}
```

A URL de proveniência é a interface estável do Bulário, nunca o token temporário usado para download do PDF.

Smoke sem escrita no contrato público:

```bash
uv run python -m bulario_service.smoke_publication_contract
```

A saída deve conter:

```text
BULA_CONTRACT_V1 candidate: OK ...
public_bulas_written=0
```

Este incremento é intencionalmente dry-run. O adapter SQL para `public.bulas` só deve ser habilitado após reconciliar os nomes/tipos exatos do schema consumidor e executar testes equivalentes ao consumer contract do Portal.

## Reconciliação do schema consumidor

O publisher não assume nomes ou tipos de colunas de `public.bulas`. Antes de habilitar qualquer escrita, o serviço inspeciona o schema real do PostgreSQL compartilhado.

Execute:

```bash
uv run python -m bulario_service.smoke_portal_schema
```

O comando lista somente metadados de schema:

- colunas, tipos, nulabilidade e defaults;
- constraints;
- índices;
- presença dos campos centrais já conhecidos do contrato.

Ele não lê conteúdo de bulas e não escreve em `public.bulas`.

A saída termina com:

```text
public_bulas_written=0
```

O resultado desse smoke é a fonte de verdade para implementar o adapter SQL do publisher no próximo incremento. Isso evita inventar aliases, tipos ou regras de unicidade que não estejam no contrato consumidor real.

## Publisher transacional para `public.bulas`

Após a reconciliação do schema real, o produtor publica somente os campos efetivamente presentes e necessários no contrato consumidor:

- `medicamento`;
- `empresa`;
- `numero_registro`;
- `num_expediente`;
- `cnpj`;
- `data_publicacao`;
- `bula_paciente`;
- `bula_profissional`;
- `source_record_id`;
- `source_url`;
- `source_fingerprint`;
- `ingested_at`;
- `ingestion_status`;
- `bula_paciente_sha256`;
- `bula_profissional_sha256`;
- `created_at`;
- `updated_at`.

Os campos analíticos e estruturados legados permanecem nulos/default quando não são produzidos pelo pipeline atual.

Como `public.bulas` não possui UNIQUE em `source_record_id`, o publisher obtém `pg_advisory_xact_lock(hashtextextended(source_record_id, 0))` antes de consultar/inserir. Isso serializa publicações concorrentes da mesma versão lógica sem alterar o schema consumidor.

Política:

```text
candidate inválido/incompleto
→ nenhuma escrita

source_record_id inexistente
→ INSERT ready

source_record_id existente e conteúdo público idêntico
→ unchanged / no-op

source_record_id existente com fingerprint, hash, storage key ou metadado divergente
→ conflito; nenhuma sobrescrita

mais de uma linha existente para o mesmo source_record_id
→ conflito operacional
```

Smoke seguro por padrão:

```bash
uv run python -m bulario_service.smoke_portal_publisher
```

Sem `--write`, a operação é executada e revertida com rollback:

```text
public_bulas_committed=0
```

Após validar o dry-run real, a escrita é habilitada explicitamente:

```bash
uv run python -m bulario_service.smoke_portal_publisher --write
```

Uma segunda execução com `--write` deve retornar `action=unchanged`, comprovando idempotência do contrato público.

## Pipeline E2E controlado

O serviço possui agora uma orquestração real para um produto por execução. O objetivo deste comando é provar o fluxo completo e a rastreabilidade antes de iniciar carga em volume.

Fluxo:

```text
ingestion_run
→ discovery ANVISA
→ ingestion_item discovered
→ fetching
→ detalhe e versão vigente
→ download paciente/profissional
→ storage + SHA-256
→ downloaded
→ persistência operacional
→ extração/normalização textual
→ persistência textual
→ normalized
→ BULA_CONTRACT_V1
→ publisher public.bulas
→ ready
→ run completed
```

Os marcos operacionais são confirmados em transações curtas. Chamadas externas e processamento de PDF não mantêm uma transação PostgreSQL aberta.

A publicação e a transição final `normalized → ready`, junto com `run → completed`, são confirmadas no mesmo commit. Se ocorrer erro antes desse commit, a transação atual é revertida e o item/run são persistidos como `failed`.

Arquivos PDF já gravados no storage antes de uma falha podem permanecer no filesystem. Isso é intencional: são artefatos content-addressable/idempotentes e podem ser reutilizados na reexecução; um arquivo isolado no storage não equivale a uma publicação `ready`.

Execução real controlada:

```bash
uv run python -m bulario_service.smoke_e2e_pipeline \
  --period-start 2026-08-28T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z
```

Para visualizar o bootstrap do Chrome:

```bash
uv run python -m bulario_service.smoke_e2e_pipeline \
  --period-start 2026-08-28T00:00:00.000Z \
  --period-end 2026-08-29T00:00:00.000Z \
  --headed
```

A execução processa somente o primeiro produto da primeira página (`page_size=1`). Em sucesso, a saída termina com:

```text
E2E pipeline: OK ... publish_action=inserted|unchanged ...
run_status=completed
item_status=ready
```

Uma reexecução idêntica deve produzir `publish_action=unchanged`, mantendo novos `ingestion_run`/`ingestion_item` de auditoria, mas sem duplicar produto, versão, PDFs, textos ou linha pública.

Carga completa e incremental em volume ainda não são iniciadas por este comando.

## Banco compartilhado com o InteliReg

Nesta fase, produtor e Portal utilizam a mesma instância e o mesmo database PostgreSQL. Essa decisão permite que o produtor publique no contrato já consumido pelo Portal sem criar sincronização entre bancos independentes.

A separação será lógica:

- tabelas operacionais do produtor serão mantidas fora do domínio público do Portal;
- `public.bulas` continuará sendo a fronteira de publicação consumida pelo InteliReg;
- o produtor não deverá usar `public.bulas` como tabela de trabalho da ingestão.

A definição das tabelas e permissões será feita no incremento de persistência/migrations.


## Migrations

As migrations pertencem ao `bulario-service` e são executadas com Alembic. Elas atuam no mesmo database PostgreSQL do InteliReg, mas mantêm os dados operacionais do produtor no schema `bulario`.

Execução local:

```bash
uv run alembic upgrade head
```

Execução via Docker:

```bash
docker compose run --rm app uv run alembic upgrade head
```

Verificar a revisão atual:

```bash
uv run alembic current
```

O primeiro incremento cria apenas:

- `bulario.ingestion_runs`: identifica uma execução de ingestão e seu estado;
- `bulario.ingestion_items`: registra os itens descobertos/processados dentro de uma execução, incluindo payload operacional, fingerprint e eventual erro.

`public.bulas` não é alterada por estas migrations. A publicação nesse contrato será implementada em uma etapa posterior da sprint.

## Configuração

As variáveis disponíveis neste estágio estão documentadas em `.env.example`.

- `APP_ENV`: identifica o ambiente da aplicação;
- `DATABASE_URL`: conexão SQLAlchemy/psycopg utilizada ao executar o serviço diretamente no host;
- `BULARIO_DOCKER_DATABASE_URL`: conexão utilizada pelo container do `bulario-service`;

URLs legadas iniciadas por `postgresql://` são normalizadas internamente para `postgresql+psycopg://`, garantindo o uso do driver psycopg 3 configurado pelo projeto.
- `INTELIREG_DOCKER_NETWORK`: nome da rede Docker criada pelo Compose do InteliReg.
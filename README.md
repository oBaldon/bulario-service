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

### Persistência do histórico documental

No pipeline operacional por produto, o detalhe retornado pela ANVISA é tratado como um conjunto de versões documentais, e não apenas como a bula vigente.

Para cada `source_document_id` retornado no detalhe/histórico:

- a versão é persistida em `bulario.document_versions`;
- PDFs de paciente e profissional são baixados quando o respectivo token existe;
- cada PDF é armazenado em `bulas/{source_product_id}/{source_document_id}/{kind}.pdf`;
- os artefatos físicos e seus hashes são persistidos em `bulario.document_artifacts`;
- o texto normalizado é persistido por artefato em `bulario.document_text_artifacts`.

A versão vigente continua sendo obrigatória e deve possuir os dois documentos (`patient` e `professional`) para que o produto avance até publicação. Versões históricas podem ter somente um dos documentos, conforme a disponibilidade observada na fonte.

Somente a versão marcada como `current` é usada para construir o candidato publicado em `public.bulas`. O histórico permanece no schema operacional `bulario`, preservando a listagem principal do Portal como representação da versão corrente.

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

## Sprint 02 - Batch Ingestion Coordinator

A Etapa 23 introduz o primeiro coordenador multi-produto da Sprint 02. O objetivo desta etapa é estabelecer a fronteira de um único `ingestion_run` contendo múltiplos produtos, ainda limitado à primeira página de discovery.

O fluxo desta etapa é:

```text
ingestion_run
  -> discovery page 1
  -> produto A -> pipeline existente -> ready
  -> produto B -> pipeline existente -> ready|failed
  -> produto C -> pipeline existente -> ready
  -> run completed|failed
```

Cada produto reutiliza o mesmo núcleo de processamento validado na Sprint 01. Uma falha de um produto é revertida e persistida no item correspondente sem desfazer produtos já concluídos; o coordinator continua com os demais produtos da página.

Nesta etapa:

- `batch item = produto`;
- apenas a primeira página é processada;
- ainda não existe checkpoint/resume;
- ainda não existe modo full/incremental/reconcile;
- ainda não existe scheduler;
- ainda não existe retry automático;
- o contrato `BULA_CONTRACT_V1` não é alterado.

O status final do run permanece simples nesta etapa:

```text
todos os itens prontos -> completed
um ou mais itens falhos -> failed
```

Os resultados individuais continuam preservados. Portanto, um run `failed` pode conter itens `ready` já concluídos e publicados corretamente.

Smoke real multi-produto:

```bash
uv run python -m bulario_service.smoke_batch_ingestion \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-31T23:59:59.999Z \
  --page-size 2 \
  --headed
```

Em uma execução integralmente bem-sucedida, a saída termina com:

```text
Batch ingestion: run_id=... run_status=completed discovered=2 processed=2 ready=2 failed=0
batch_ingestion_ready=true
```

A paginação multi-page será adicionada na Etapa 24. Este smoke existe para validar especificamente a nova fronteira multi-produto antes de avançar para checkpoint e carga ampla.

## Hardening de conflitos e rollback

Após a validação E2E, o serviço possui um smoke de hardening que usa um documento operacional vigente já publicado para comprovar as barreiras de imutabilidade e idempotência sem depender de nova chamada à ANVISA.

Execute:

```bash
uv run python -m bulario_service.smoke_hardening
```

O smoke procura a versão operacional vigente mais recente que possua exatamente uma linha pública `ready` e valida:

```text
rerun público idêntico
→ unchanged

mesmo source_record_id + source_fingerprint divergente
→ bloqueado

mesmo source_record_id + hash de PDF divergente
→ bloqueado

mesmo PDF + mesma normalization_version + texto divergente
→ bloqueado

mesmo source_document_id + metadado material divergente
→ bloqueado
```

Cada tentativa de conflito é seguida por rollback. O smoke não cria nova versão, não altera PDF/texto persistido e não publica linha adicional em `public.bulas`.

A saída termina com:

```text
hardening_committed_mutations=0
```

Os testes automatizados também cobrem:

- falha de extração antes do publisher, com `item/run=failed`;
- falha controlada após tentativa de publicação e antes do commit final, confirmando rollback;
- nova `source_document_id` como nova versão lógica válida;
- mutação material sob a mesma `source_document_id` como conflito.

Arquivos PDF eventualmente já presentes no storage continuam reutilizáveis e não representam publicação parcial.

## Handoff do produtor para o Portal

O fechamento do lado produtor inclui uma validação explícita da linha `ready` mais recente publicada em `public.bulas`.

Execute:

```bash
uv run python -m bulario_service.smoke_portal_handoff
```

O smoke valida, sem alterar dados:

```text
public.bulas ready
→ source_record_id parseável
→ versão operacional correspondente
→ source_fingerprint público = operacional
→ patient/professional presentes
→ storage keys públicos = operacionais
→ storage keys relativos e seguros
→ arquivos PDF existem no storage compartilhado
→ arquivos iniciam com %PDF-
→ SHA-256 em disco = SHA-256 operacional = SHA-256 público
```

Em sucesso, termina com:

```text
producer_portal_handoff_ready=true
```

Esse resultado comprova a prontidão do contrato **do lado do produtor**. Ele não substitui os testes consumidores do repositório Portal. Para o encerramento integral da Sprint 01 ainda devem ser executados, no código atual do Portal, os testes do contrato consumidor e do serving privado de PDF aplicáveis.

## Acervo físico compartilhado

O contrato de publicação usa **storage keys relativas**. O caminho físico do acervo é configuração de infraestrutura e não pertence ao contrato entre produtor e Portal.

Para desenvolvimento local com os repositórios em `~/Projetos`, a convenção recomendada é manter um diretório irmão e neutro:

```text
~/Projetos/
├── bulario-service/
├── intelireg/
└── intelireg-data/
    └── bulas/
```

No `bulario-service`:

```env
BULARIO_STORAGE_ROOT=../intelireg-data
```

Quando o produtor roda via Docker Compose, o host path é informado separadamente:

```env
BULAS_ARCHIVE_HOST_PATH=../intelireg-data
```

e montado no container em `/data`, com:

```text
BULARIO_STORAGE_ROOT=/data
```

Isso mantém duas camadas distintas:

```text
storage_key
= identidade lógica persistida no contrato

BULARIO_STORAGE_ROOT
= detalhe físico de deployment
```

Com essa convenção, uma storage key lógica como:

```text
bulas/143989/35481769/patient.pdf
```

resolve fisicamente para:

```text
host:      ../intelireg-data/bulas/143989/35481769/patient.pdf
container: /data/bulas/143989/35481769/patient.pdf
```

A raiz física não inclui o segmento lógico `bulas`; isso evita caminhos
redundantes como `intelireg-data/bulas/bulas/...` sem alterar o contrato.

O Portal receberá, em patch próprio, o mesmo acervo físico como **read-only**, sem precisar conhecer `BULARIO_STORAGE_ROOT`.

### Cutover dos PDFs já ingeridos

Os PDFs produzidos antes desta configuração permanecem no storage legado `./storage`. Para migrá-los ao acervo neutro sem alterar storage keys nem banco, use primeiro o dry-run:

```bash
uv run python -m bulario_service.smoke_storage_cutover
```

Ele usa `bulario.document_artifacts` como inventário e informa `pending_copy=N`, sem escrever.

Depois efetive:

```bash
uv run python -m bulario_service.smoke_storage_cutover --write
```

A rotina:

- valida que source e target são diretórios distintos;
- valida `%PDF-`;
- valida SHA-256 do arquivo de origem;
- copia atomicamente;
- valida novamente SHA-256 no destino;
- reutiliza arquivos de destino quando já possuem o mesmo hash;
- recusa sobrescrever arquivo divergente;
- não altera registros do banco;
- não remove o acervo legado.

Após o cutover, os smokes que manipulam PDFs usam `BULARIO_STORAGE_ROOT` por padrão. `--storage-root` permanece disponível apenas como override explícito.

## Sprint 02 - Etapa 24: discovery paginada multi-page

O Batch Ingestion Coordinator agora pode percorrer múltiplas páginas de discovery dentro do mesmo `ingestion_run`.

A unidade operacional continua sendo o produto. O coordinator mantém um conjunto de `source_product_id` já vistos no run para impedir processamento duplicado caso o mesmo produto apareça novamente em páginas diferentes.

Limites operacionais:

```text
page_size
= quantidade solicitada por página

max_pages
= máximo de páginas que podem ser consultadas no run

max_products
= máximo de produtos únicos que podem ser processados
```

Esses limites existem para manter execuções reais controladas enquanto checkpoint/resume ainda não foi implementado.

O resultado do batch informa:

```text
pages_fetched
discovered_count
duplicate_count
processed_count
ready_count
failed_count
stopped_by_page_limit
stopped_by_product_limit
```

Regras importantes:

- produtos duplicados entre páginas contam em `duplicate_count`, mas são processados apenas uma vez;
- `discovered_count` representa produtos únicos admitidos para processamento;
- `max_products` pode interromper o processamento no meio de uma página já consultada;
- `max_pages` impede consultar a página seguinte;
- falha de um produto continua isolada dos demais;
- falha de discovery em qualquer página encerra o run como `failed`;
- checkpoint e `--resume` permanecem fora desta etapa e serão implementados na Etapa 25.

Smoke real controlado:

```bash
uv run python -m bulario_service.smoke_batch_ingestion \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-29T23:59:59.999Z \
  --page-size 2 \
  --max-pages 2 \
  --max-products 4 \
  --headed
```

## Sprint 02 - Etapa 25: checkpoint e resume

Os `ingestion_runs` agora persistem metadados suficientes para retomada controlada:

```text
mode
period_start
period_end
page_size
last_completed_page
last_checkpoint_at
```

A migration correspondente é:

```text
20260831_0004_add_ingestion_run_checkpoint
```

Antes de executar os novos smokes em um banco existente:

```bash
uv run alembic upgrade head
uv run alembic current
```

### Semântica do checkpoint

`last_completed_page` representa a última página de discovery integralmente processada. O checkpoint só avança depois que todos os produtos admitidos daquela página chegaram a um estado terminal para esta etapa (`ready` ou `failed`).

Se `--max-products` interromper o run no meio de uma página, essa página **não** é marcada como concluída. No resume ela é consultada novamente e os produtos que já possuem item terminal no mesmo run são ignorados.

Isso evita depender da hipótese de que a composição de uma página permaneça imutável entre duas chamadas.

### Estado `paused`

Um stop controlado por `--max-pages` ou `--max-products`, quando ainda há trabalho a percorrer, deixa o run em:

```text
paused
```

Esse estado significa que a execução pode ser retomada. Ele não é um estado do contrato público `BULA_CONTRACT_V1`; pertence exclusivamente ao modelo operacional do produtor.

Runs `completed` e `failed` permanecem terminais e não podem ser retomados nesta etapa.

### Novo run controlado

Exemplo para processar apenas uma página e gerar um checkpoint resumível:

```bash
uv run python -m bulario_service.smoke_batch_ingestion \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-29T23:59:59.999Z \
  --page-size 2 \
  --max-pages 1 \
  --max-products 4 \
  --headed
```

Se houver mais páginas, o resultado esperado contém:

```text
run_status=paused
start_page=1
last_completed_page=1
stopped_by_page_limit=true
```

### Resume

Para retomar, informe apenas o `run_id` pausado:

```bash
uv run python -m bulario_service.smoke_batch_ingestion \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 4 \
  --headed
```

A janela e o `page_size` persistidos no run são reutilizados automaticamente.

É permitido informar novamente `--period-start`, `--period-end` ou `--page-size`, mas os valores devem coincidir exatamente com o run original. Divergência é rejeitada antes de alterar o estado do run.

O resume:

- preserva a janela original;
- inicia em `last_completed_page + 1`;
- quando necessário, repete a primeira página ainda não concluída;
- ignora itens `ready` ou `failed` já persistidos no mesmo run;
- atualiza checkpoint somente após página integralmente processada;
- continua usando o mesmo `ingestion_run`;
- não cria uma segunda execução para representar a retomada.

Retry de itens `failed` ainda não pertence a esta etapa. A política de retry e classificação avançada de falhas será introduzida posteriormente na Sprint 02.

## Sprint 02 - Etapa 26: full load controlado

A interface operacional oficial passa a expor o primeiro comando de sincronização:

```bash
python -m bulario_service.sync full
```

O modo `full` reutiliza o mesmo coordinator, checkpoint e mecanismo de resume já validados, mas persiste `ingestion_runs.mode = full`. Runs `full` só podem ser retomados pelo mesmo modo.

### Guardrails operacionais

Para evitar uma carga ampla acidentalmente ilimitada, o comando `full` usa defaults conservadores por invocação:

```text
max_pages=10
max_products=20
```

Esses limites não definem o tamanho total da carga. Quando ainda houver trabalho, o run fica `paused` e pode ser retomado pelo mesmo `run_id`.

O objetivo é permitir uma carga grande em blocos controlados:

```text
full run
  -> bloco 1 -> checkpoint -> paused
  -> resume  -> bloco 2 -> checkpoint -> paused
  -> resume  -> bloco 3 -> ... -> completed
```

### Novo full run

Exemplo controlado:

```bash
uv run python -m bulario_service.sync full \
  --period-start 2026-01-01T00:00:00.000Z \
  --period-end 2026-08-29T23:59:59.999Z \
  --page-size 10 \
  --max-pages 5 \
  --max-products 20 \
  --headed
```

A saída resume:

```text
run_id
run_status
resumed
start_page
last_completed_page
pages_fetched
source_total_elements
discovered
duplicates
skipped_terminal
processed
ready
failed
duration_seconds
stopped_by_page_limit
stopped_by_product_limit
```

`source_total_elements` é o total informado pela fonte na página consultada e serve como referência operacional da dimensão da janela. Não deve ser usado como garantia permanente de cardinalidade da fonte.

`duration_seconds` mede somente a invocação corrente, e não a soma histórica de todos os resumes do run.

### Resume do full load

```bash
uv run python -m bulario_service.sync full \
  --resume RUN_ID \
  --max-pages 5 \
  --max-products 20 \
  --headed
```

A janela e o `page_size` originais são reutilizados. O mesmo `run_id` avança até `completed` ou volta a `paused` quando um guardrail é atingido.

### Critério desta etapa

A Etapa 26 considera o full load operacionalmente comprovado quando uma janela substancial puder ser percorrida em vários blocos do mesmo run, com:

- checkpoint crescente;
- resume sem nova janela;
- `unchanged` para versões já conhecidas;
- `inserted` somente para versões novas;
- ausência de duplicação;
- métricas de volume e duração por invocação;
- eventual conclusão do run ou pausa controlada previsível.

Incremental, retry avançado, reconciliation, lock e scheduler permanecem fora desta etapa.

## Sprint 02 - Etapa 27: incremental com overlap

A CLI operacional passa a expor:

```bash
python -m bulario_service.sync incremental
```

O modo incremental persiste `ingestion_runs.mode = incremental` e reutiliza o mesmo mecanismo de checkpoint/resume do full load.

### Regra da janela

Quando existe um run incremental `completed`, a próxima janela é calculada por:

```text
period_start = period_end do último incremental completed - overlap
period_end   = instante UTC atual ou --period-end explícito
```

Runs `paused` ou `failed` não são usados como âncora para uma nova janela. Um run pausado deve ser retomado com `--resume`.

O overlap é configurável:

```env
BULARIO_INCREMENTAL_OVERLAP_DAYS=7
```

O valor `7` é um default técnico inicial, não uma regra regulatória nem um comportamento homologado da ANVISA. Ele deve ser calibrado com evidência operacional.

### Primeira execução incremental

Quando ainda não existe incremental `completed`, o serviço não inventa retrospectiva. É obrigatório informar explicitamente o início inicial:

```bash
uv run python -m bulario_service.sync incremental \
  --initial-period-start 2026-08-29T00:00:00.000Z \
  --period-end 2026-08-30T23:59:59.999Z \
  --page-size 5 \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

A saída inclui:

```text
Incremental window:
period_start
period_end
overlap_days
based_on_run_id

Incremental sync:
run_id
run_status
run_mode
period_start
period_end
resumed
start_page
last_completed_page
pages_fetched
source_total_elements
discovered
duplicates
skipped_terminal
processed
ready
failed
duration_seconds
```

Se o guardrail for atingido, o run fica `paused`. Continue o mesmo run:

```bash
uv run python -m bulario_service.sync incremental \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

Em `--resume`, não informe `--initial-period-start`, `--period-end` nem `--overlap-days`: a janela persistida é obrigatoriamente reutilizada. `--page-size`, quando informado, ainda precisa coincidir com o valor persistido.

### Incrementais seguintes

Depois que um incremental terminar como `completed`, um novo comando pode omitir `--initial-period-start`:

```bash
uv run python -m bulario_service.sync incremental \
  --period-end 2026-08-31T23:59:59.999Z \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

O serviço encontra o último incremental concluído e aplica o overlap configurado. A sobreposição pode redescobrir produtos já conhecidos; a idempotência do pipeline deve resultar em `unchanged`, enquanto versões realmente novas resultam em `inserted`.

### Segurança operacional

A resolução da janela ocorre antes de abrir o navegador. Assim, configuração inválida ou ausência de `--initial-period-start` na primeira execução falha rapidamente sem iniciar sessão ANVISA.

Esta etapa ainda não implementa retry avançado, reconciliation, advisory lock ou scheduler.

## Sprint 02 - Etapa 28: retry e classificação de falhas

A ingestão passa a distinguir falhas operacionais por classe:

```text
transient
source_blocked
permanent
conflict
unknown
```

### Política

`transient` inclui falhas como timeout e HTTP `500/502/503/504` após esgotar o retry HTTP do adapter. Esses itens podem ser reabertos no mesmo `ingestion_run`, reutilizando o mesmo `ingestion_item`.

`source_blocked` representa rejeição de sessão, como HTTP `403`. O coordinator não faz retry cego na mesma invocação: interrompe o avanço e deixa o run `paused`.

`permanent` cobre payload inválido, resposta HTTP não transitória e documento inválido. Não há retry automático.

`conflict` cobre divergência material em identidade/versionamento/storage/publicação. Não há retry automático.

`unknown` permanece sem retry automático até haver classificação explícita.

### Metadados persistidos

A migration:

```text
20260831_0005_add_ingestion_retry_metadata
```

adiciona a `bulario.ingestion_items`:

```text
error_class
retry_count
```

`retry_count` registra quantas reaberturas operacionais já foram feitas para aquele mesmo item. O item não é duplicado.

Antes do uso:

```bash
uv run alembic upgrade head
uv run alembic current
```

### Retry em execução

Além das tentativas HTTP internas do adapter, o coordinator permite retry do pipeline completo do produto:

```text
--max-product-retries 2
--retry-backoff-seconds 2
```

Esses defaults são conservadores e podem ser alterados por invocação.

Quando um produto falha como transitório durante a invocação, o coordinator pode reabrir o mesmo item e tentar novamente. Se o run já estava `paused`, itens transitórios pendentes são tratados antes do discovery continuar.

Isso permite recuperar falhas de páginas que já foram checkpointadas, sem depender de redescobrir aquela página.

### Compatibilidade com falhas anteriores

Itens criados antes desta migration podem ter `error_class = NULL`. Para preservar o histórico, o coordinator reconhece mensagens legadas de timeout e HTTP `500/502/503/504` como transitórias.

Assim, falhas reais anteriores podem ser recuperadas no mesmo run após a migration, desde que ainda estejam dentro do limite de retries.

### Observabilidade da CLI

A saída de `full` e `incremental` passa a incluir:

```text
retries
stopped_by_source_blocked
```

E cada item inclui:

```text
error_class
retries
```

### Exemplo de resume incremental

```bash
uv run python -m bulario_service.sync incremental \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --max-product-retries 2 \
  --retry-backoff-seconds 2 \
  --headed
```

Se houver um item transitório pendente no run, ele é tentado antes das próximas páginas.

Um retry recuperado deve aparecer como `status=ready` e `retries>0`. Falhas permanentes ou conflitos permanecem `failed`. Um bloqueio de sessão causa pausa controlada.

## Sprint 02 - Etapa 29: reconciliation

A CLI operacional passa a expor um modo separado para varreduras amplas:

```bash
python -m bulario_service.sync reconcile
```

O reconciliation não substitui o incremental. Ele existe para executar uma janela temporal mais ampla e reencontrar produtos/versões que possam ter sido perdidos por indisponibilidade temporária da fonte, alteração de paginação ou outra lacuna operacional.

O run é persistido como:

```text
mode=reconciliation
```

e só pode ser retomado pelo mesmo modo.

### Janela explícita

A periodicidade e a abrangência definitiva do reconciliation ainda não estão homologadas. Por isso, esta etapa não inventa uma janela automática.

Um novo run exige:

```text
--period-start
--period-end
```

Exemplo:

```bash
uv run python -m bulario_service.sync reconcile \
  --period-start 2026-08-01T00:00:00.000Z \
  --period-end 2026-08-31T23:59:59.999Z \
  --page-size 5 \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

O objetivo operacional é que produtos/versões já presentes atravessem o pipeline de forma idempotente e terminem com:

```text
publish_action=unchanged
```

Enquanto uma versão válida realmente ausente pode resultar em:

```text
publish_action=inserted
```

O reconciliation não cria uma semântica especial de sobrescrita nem relaxa regras de conflito.

### Guardrails e resume

Os defaults conservadores por invocação são:

```text
max_pages=5
max_products=20
max_product_retries=2
retry_backoff_seconds=2
```

Quando um limite é atingido, o run fica `paused` e pode ser retomado:

```bash
uv run python -m bulario_service.sync reconcile \
  --resume RUN_ID \
  --max-pages 2 \
  --max-products 10 \
  --headed
```

O mesmo `run_id`, janela, `page_size`, checkpoint e política de retry continuam sendo reutilizados.

### Critério desta etapa

A reconciliação é considerada operacionalmente comprovada quando uma janela mais ampla que o incremental normal consegue:

```text
descobrir múltiplas páginas
reprocessar registros conhecidos sem duplicação
publicar somente versões realmente ausentes
preservar conflitos
pausar e retomar pelo mesmo run
usar retry para falhas transitórias
```

Nenhuma migration adicional é necessária nesta etapa.

Operational lock, observabilidade estruturada e scheduler continuam reservados às etapas seguintes da Sprint 02.

## Sprint 02 - Etapa 30: operational lock

Os comandos operacionais oficiais:

```text
full
incremental
reconcile
```

passam a compartilhar um único PostgreSQL advisory lock global:

```text
bulario-service:sync:global:v1
```

Nesta etapa os três modos são tratados como incompatíveis entre si por padrão. Se qualquer um deles já estiver executando, uma segunda sincronização falha rapidamente em vez de aguardar indefinidamente.

### Por que o lock é session-level

O coordinator faz vários `commit`s durante a ingestão. Portanto, um `pg_advisory_xact_lock` seria liberado no primeiro commit e não protegeria a execução inteira.

O serviço usa uma conexão PostgreSQL dedicada e mantém um lock de sessão durante toda a operação:

```text
pg_try_advisory_lock(...)
```

A conexão do lock é separada das sessões/transações usadas pelo pipeline.

Ao final, inclusive quando o corpo da operação falha, o serviço executa:

```text
pg_advisory_unlock(...)
```

e fecha a conexão dedicada.

### Falha rápida e exit code

Quando o lock já está ocupado, a CLI não abre o Chrome e retorna:

```text
exit code 3
```

com mensagem semelhante a:

```text
Incremental sync blocked: another incompatible bulario sync is already running
```

Os demais exit codes permanecem com a semântica atual.

### Smoke real do lock

Existe um smoke sem ingestão e sem navegador:

```bash
uv run python -m bulario_service.smoke_operational_lock \
  --hold-seconds 20
```

Enquanto o primeiro processo mantém o lock, uma segunda execução do mesmo smoke deve retornar rapidamente:

```text
operational_lock_acquired=false
```

Depois da liberação, uma nova execução deve conseguir adquirir o lock novamente.

Esse smoke usa exatamente a mesma chave global dos comandos `full`, `incremental` e `reconcile`.

### Escopo desta etapa

Não há migration nova e não há Redis.

Esta etapa não implementa scheduler nem observabilidade estruturada. O objetivo é somente impedir concorrência operacional incompatível e comprovar liberação segura do lock.

## Sprint 02 - Etapa 31: observabilidade estruturada

Os comandos operacionais `full`, `incremental` e `reconcile` passam a emitir observabilidade em JSON Lines no `stderr`, preservando a saída humana existente no `stdout`.

Eventos estruturados:

```text
sync_started
sync_result
sync_blocked
sync_failed
sync_invalid_request
```

Cada linha contém:

```text
timestamp
service=bulario-service
event
```

e os campos operacionais aplicáveis.

### Métricas de resultado

O evento `sync_result` consolida, por invocação:

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

`published` representa registros efetivamente inseridos em `public.bulas` naquela invocação; `unchanged` permanece separado para evidenciar idempotência.

### Segurança dos logs

A camada estruturada aplica sanitização defensiva para campos e textos contendo marcadores sensíveis, incluindo:

```text
Authorization
Cookie
password
secret
token
Bearer
```

Esses valores são substituídos por:

```text
[REDACTED]
```

A mesma sanitização é usada nas mensagens humanas de erro da CLI. Cookies, tokens temporários, credenciais e headers de autenticação não devem ser registrados.

### Compatibilidade operacional

A observabilidade não altera:

```text
exit codes
checkpoint/resume
retry
operational lock
publicação
storage
```

Não há migration nova.

Prometheus, Grafana, tracing distribuído ou infraestrutura externa de logging não são requisitos desta etapa. As linhas JSON podem ser coletadas posteriormente pelo mecanismo de logs do host/container.

## Sprint 02 - Etapa 32: scheduler operacional

A execução contínua do incremental pode ser disparada por `systemd --user`. O scheduler não contém lógica de ingestão: ele chama exclusivamente a CLI oficial:

```text
python -m bulario_service.sync incremental
```

A implementação de referência fica em:

```text
ops/systemd/bulario-incremental.service.in
ops/systemd/bulario-incremental.timer
ops/systemd/install-user-timer.sh
ops/systemd/uninstall-user-timer.sh
```

Não há migration nova.

### Auto-resume seguro para scheduler

O scheduler usa:

```text
--auto-resume
```

Essa opção existe para impedir que cada disparo crie um novo incremental quando o anterior ficou `paused` por `max_pages` ou `max_products`.

A regra é:

```text
exatamente 1 incremental paused
→ retoma o mesmo run

nenhum incremental paused
→ cria a próxima janela com a lógica incremental já existente

mais de 1 incremental paused
→ falha de forma controlada; não escolhe arbitrariamente
```

`--auto-resume` é mutuamente exclusivo com `--resume` e com overrides manuais de janela.

O primeiro incremental da instalação continua exigindo preparação manual caso ainda não exista nenhum incremental concluído. O scheduler não inventa uma janela retrospectiva inicial.

### Timer systemd de referência

O timer entregue usa, como default técnico inicial:

```text
OnBootSec=5min
OnUnitInactiveSec=1h
RandomizedDelaySec=5min
Persistent=true
```

A frequência de uma hora não representa obrigação regulatória nem decisão definitiva de produto. É apenas um valor operacional inicial e deve ser calibrado conforme volume da fonte, janela incremental, custo e necessidade do ambiente.

O serviço chama:

```text
incremental
--auto-resume
--max-pages 5
--max-products 20
--headed
```

O modo `--headed` foi mantido porque é o transporte que está validado no ambiente atual contra a ANVISA. Para funcionamento como `systemd --user`, a sessão gráfica precisa estar disponível. O instalador importa `DISPLAY` e `XAUTHORITY` quando presentes.

Se o acesso headless vier a ser validado futuramente, `--headed` pode ser removido do unit sem alterar o pipeline.

### Instalação no usuário atual

A partir da raiz do repositório:

```bash
./ops/systemd/install-user-timer.sh
```

O instalador:

```text
detecta o caminho real do repositório
usa .venv/bin/python
renderiza o service em ~/.config/systemd/user
instala o timer
executa daemon-reload
habilita e inicia o timer
lista o próximo disparo
```

Verificação:

```bash
systemctl --user status bulario-incremental.timer
systemctl --user list-timers bulario-incremental.timer
```

Execução manual do unit:

```bash
systemctl --user start bulario-incremental.service
```

Logs:

```bash
journalctl --user -u bulario-incremental.service
```

A saída JSON estruturada da Etapa 31 é capturada pelo journal sem necessidade de Prometheus, Grafana ou agente adicional.

### Desinstalação

```bash
./ops/systemd/uninstall-user-timer.sh
```

Esse comando remove somente os units de usuário do scheduler. Não altera banco, archive, migrations, `.env` ou dados ingeridos.

### Reconciliation e full

`full` não é agendado automaticamente.

`reconcile` também não recebe timer nesta etapa porque sua periodicidade e abrangência temporal ainda não estão homologadas. A varredura ampla continua sendo iniciada explicitamente pela CLI até que essa política seja definida.

O operational advisory lock da Etapa 30 continua protegendo contra qualquer concorrência incompatível entre disparos do timer e execuções manuais.

## Sprint 02 - Etapa 32: hardening após smoke real do scheduler

O primeiro disparo real do `systemd --user` revelou `HTTP 429` no discovery da ANVISA.

Esse retorno é tratado como **rate limit transitório**, não como erro permanente.

A política passa a considerar transitórios:

```text
429
500
502
503
504
timeouts / transport errors
```

O connector mantém retries limitados com backoff. Se o discovery continuar falhando de forma transitória após esgotar os retries do adapter, o run:

```text
permanece no mesmo run_id
preserva last_completed_page
é marcado como paused
não é finalizado como failed
```

A CLI ainda retorna erro operacional naquela invocação, permitindo que o scheduler tente novamente no próximo disparo.

### Proteção contra salto após run failed

`--auto-resume` não cria silenciosamente um novo incremental quando existe um incremental não resolvido em estado:

```text
failed
running
```

Para `failed`, a CLI exige recuperação explícita do operador.

Essa proteção evita que um scheduler abandone um checkpoint interrompido e abra outra janela baseada somente no último run concluído.

### Recuperação explícita de run failed legado

Runs que ficaram `failed` antes deste hardening podem ser reabertos explicitamente:

```bash
uv run python -m bulario_service.sync incremental \
  --recover-failed RUN_ID \
  --max-pages 1 \
  --max-products 2 \
  --headed
```

`--recover-failed`:

```text
exige que o run exista
exige mode=incremental
exige status=failed
remove finished_at
reabre como paused
retoma pelo checkpoint persistido
```

A opção é mutuamente exclusiva com:

```text
--resume
--auto-resume
overrides manuais de janela
```

Depois de uma recuperação bem-sucedida, o timer volta a usar `--auto-resume` normalmente.

## Sprint 02 - Etapa 33: Operational E2E / Hardening

A etapa final adiciona uma auditoria operacional read-only:

```bash
uv run python -m bulario_service.operational_audit
```

Ela não abre Chrome, não acessa a ANVISA e não grava no banco.

A auditoria verifica o conjunto de publicações ANVISA em `public.bulas` e o archive compartilhado:

```text
existência de publicações ANVISA
source_record_id sem duplicidade
ingestion_status=ready
campos mínimos do contrato preenchidos
nenhum run em running
nenhum incremental em failed
no máximo um incremental paused
cada public row vinculada à versão operacional correta
source_fingerprint consistente
PDF paciente/profissional existente
assinatura %PDF-
SHA-256 físico = operacional = público
texto normalizado v1 presente
character_count consistente
text_sha256 consistente
```

A validação documental é feita para **todas** as publicações ANVISA `ready`, não apenas para a última linha.

Saída esperada:

```text
{"event":"operational_audit",...,"ok":true}
sprint02_operational_audit_ready=true
```

O comando retorna exit code `2` quando alguma invariável falha.

### Incremento 35 - Auditoria do histórico documental

A mesma auditoria também cobre o histórico operacional persistido no schema `bulario`.

Além das invariantes da publicação corrente, ela valida:

```text
quantidade de produtos e versões operacionais
quantidade de versões históricas
produtos com múltiplas versões
exatamente uma versão current por produto
nenhuma document_version sem artifact
storage_key coerente com produto/versão/tipo
tipo de artifact restrito a patient/professional
arquivo físico existente
size_bytes consistente
SHA-256 físico = document_artifacts.sha256
texto normalizado v1 presente para cada PDF
```

As métricas correspondentes são incluídas no JSON de `operational_audit`. A verificação continua estritamente read-only: não corrige, remove ou recria registros/arquivos automaticamente.

A matriz consolidada de fechamento está em:

```text
docs/SPRINT02_OPERATIONAL_ACCEPTANCE.md
```

Não há migration nova nesta etapa.

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
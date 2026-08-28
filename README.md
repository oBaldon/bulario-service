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

O schema `bulario` contém somente as estruturas operacionais necessárias neste incremento:

- `bulario.ingestion_runs`;
- `bulario.ingestion_items`.

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

```bash
uv run python -m bulario_service
```

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
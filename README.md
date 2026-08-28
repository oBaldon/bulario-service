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

O projeto possui bootstrap Python, configuração mínima e ambiente Docker preparado para usar o PostgreSQL do InteliReg.

Ainda não há:

- integração com a ANVISA;
- persistência de dados da aplicação;
- migrations;
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

## Banco compartilhado com o InteliReg

Nesta fase, produtor e Portal utilizam a mesma instância e o mesmo database PostgreSQL. Essa decisão permite que o produtor publique no contrato já consumido pelo Portal sem criar sincronização entre bancos independentes.

A separação será lógica:

- tabelas operacionais do produtor serão mantidas fora do domínio público do Portal;
- `public.bulas` continuará sendo a fronteira de publicação consumida pelo InteliReg;
- o produtor não deverá usar `public.bulas` como tabela de trabalho da ingestão.

A definição das tabelas e permissões será feita no incremento de persistência/migrations.

## Configuração

As variáveis disponíveis neste estágio estão documentadas em `.env.example`.

- `APP_ENV`: identifica o ambiente da aplicação;
- `DATABASE_URL`: conexão utilizada ao executar o serviço diretamente no host;
- `BULARIO_DOCKER_DATABASE_URL`: conexão utilizada pelo container do `bulario-service`;
- `INTELIREG_DOCKER_NETWORK`: nome da rede Docker criada pelo Compose do InteliReg.
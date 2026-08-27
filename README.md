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

O projeto possui bootstrap Python, configuração mínima e ambiente Docker com PostgreSQL.

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

O Compose cria somente os componentes atualmente necessários:

- `app`: aplicação Python;
- `db`: PostgreSQL.

Executar os testes dentro do container:

```bash
docker compose run --rm app uv run pytest
```

Encerrar o ambiente:

```bash
docker compose down
```

Para também remover o volume local do PostgreSQL:

```bash
docker compose down -v
```

## Configuração

As variáveis disponíveis neste estágio estão documentadas em `.env.example`.

- `APP_ENV`: identifica o ambiente da aplicação;
- `DATABASE_URL`: conexão PostgreSQL que será utilizada pela camada de persistência nos próximos incrementos.
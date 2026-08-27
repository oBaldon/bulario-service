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

Bootstrap inicial do projeto.

Ainda não há:

- integração com a ANVISA;
- persistência em banco;
- download de PDFs;
- extração textual;
- publicação para o Portal.

## Requisitos

- Python 3.13+
- uv

## Instalação

```bash
uv sync
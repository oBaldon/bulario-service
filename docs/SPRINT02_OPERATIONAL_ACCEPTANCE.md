# Sprint 02 — Operational Acceptance

Este documento consolida os critérios técnicos de fechamento da Sprint 02 do `bulario-service`.

## Objetivo

Ao final da Sprint 02, o serviço deve operar como produtor contínuo de Bulário ANVISA, mantendo o Portal InteliReg como consumidor read-only do contrato `public.bulas`.

O fechamento não inclui API HTTP pública, frontend administrativo, RAG/LLM no produtor, multi-source, Redis obrigatório, Celery/RabbitMQ ou mudança do Portal para sistema mestre.

## Matriz de aceitação

| Critério | Evidência automatizada / operacional |
| --- | --- |
| Full multi-page e múltiplos produtos | `tests/test_batch_ingestion.py`, CLI `sync full` |
| Checkpoint por página | `tests/test_batch_ingestion.py` |
| Resume do mesmo run | `tests/test_batch_ingestion.py`, `tests/test_sync.py` |
| Item terminal não duplicado no resume | `tests/test_batch_ingestion.py` |
| Incremental com overlap | `tests/test_incremental.py` |
| Reconciliation idempotente | `tests/test_batch_ingestion.py`, CLI `sync reconcile` |
| Retry transitório | `tests/test_retry_policy.py`, `tests/test_batch_ingestion.py` |
| HTTP 429 como transitório | `tests/test_anvisa.py`, `tests/test_retry_policy.py` |
| Falha transitória de discovery preserva checkpoint | `tests/test_batch_ingestion.py` |
| Conflito material bloqueado | `tests/test_hardening.py`, `tests/test_publication_publisher.py` |
| Lock operacional global | `tests/test_operational_lock.py`, smoke real |
| Exit code 3 em concorrência incompatível | `tests/test_sync.py` |
| Observabilidade JSON sanitizada | `tests/test_observability.py`, `tests/test_sync.py` |
| Scheduler chama somente CLI oficial | `tests/test_scheduler_assets.py` |
| Auto-resume seguro | `tests/test_incremental.py`, `tests/test_sync.py` |
| Failed incremental não é abandonado silenciosamente | `tests/test_incremental.py` |
| Recuperação explícita de failed | `tests/test_sync.py`, `ingestion.py` |
| Publicação apenas `ready` | `operational_audit.py` |
| Sem duplicidade de `source_record_id` público ANVISA | `operational_audit.py` |
| PDFs físicos válidos e hash consistente | `portal_handoff.py`, `operational_audit.py` |
| Texto derivado v1 presente e hash consistente | `portal_handoff.py`, `operational_audit.py` |
| Portal continua consumidor do archive/contrato | `tests/test_portal_handoff.py`, auditoria operacional |

## Auditoria operacional de fechamento

Executar:

```bash
uv run python -m bulario_service.operational_audit
```

O comando é read-only em banco e archive. Ele não abre navegador e não acessa a ANVISA.

A auditoria falha se detectar:

- nenhuma publicação ANVISA;
- `source_record_id` público duplicado;
- publicação ANVISA com `ingestion_status` diferente de `ready`;
- campos documentais mínimos ausentes;
- run ainda marcado `running`;
- incremental não resolvido em `failed`;
- mais de um incremental `paused`;
- public row sem versão operacional correspondente;
- divergência de `source_fingerprint`;
- PDF paciente/profissional ausente, inválido ou com hash divergente;
- ausência ou inconsistência do texto normalizado `v1`.

Saída de sucesso:

```text
{"event":"operational_audit",...,"ok":true}
sprint02_operational_audit_ready=true
```

## Interpretação

A auditoria verifica invariantes técnicas do produtor e do handoff. Ela não constitui validação regulatória do conteúdo das bulas e não substitui revisão especializada ou conferência na fonte oficial.

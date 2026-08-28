from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from bulario_service.publication_contract import (
    BulaPublicationCandidate,
    validate_publication_candidate,
)


class BulaPublicationError(RuntimeError):
    pass


class BulaPublicationConflictError(BulaPublicationError):
    pass


@dataclass(frozen=True)
class PublishResult:
    action: str
    row_id: int
    source_record_id: str


def publish_candidate(
    session: Session,
    *,
    candidate: BulaPublicationCandidate,
    now: datetime | None = None,
) -> PublishResult:
    validate_publication_candidate(candidate)
    published_at = now or datetime.now(timezone.utc)

    session.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended(:source_record_id, 0)
            )
            """
        ),
        {"source_record_id": candidate.source_record_id},
    )

    existing = session.execute(
        text(
            """
            SELECT
                id,
                medicamento,
                empresa,
                numero_registro,
                num_expediente,
                cnpj,
                data_publicacao,
                bula_paciente,
                bula_profissional,
                source_record_id,
                source_url,
                source_fingerprint,
                ingestion_status,
                bula_paciente_sha256,
                bula_profissional_sha256
            FROM public.bulas
            WHERE source_record_id = :source_record_id
            ORDER BY id
            """
        ),
        {"source_record_id": candidate.source_record_id},
    ).mappings().all()

    if len(existing) > 1:
        raise BulaPublicationConflictError(
            "multiple public.bulas rows already exist for source_record_id="
            f"{candidate.source_record_id}"
        )

    if len(existing) == 1:
        row = existing[0]
        _assert_existing_row_matches(row, candidate)
        return PublishResult(
            action="unchanged",
            row_id=row["id"],
            source_record_id=candidate.source_record_id,
        )

    result = session.execute(
        text(
            """
            INSERT INTO public.bulas (
                medicamento,
                empresa,
                numero_registro,
                num_expediente,
                cnpj,
                data_publicacao,
                bula_paciente,
                bula_profissional,
                source_record_id,
                source_url,
                source_fingerprint,
                ingested_at,
                ingestion_status,
                bula_paciente_sha256,
                bula_profissional_sha256,
                created_at,
                updated_at
            ) VALUES (
                :medicamento,
                :empresa,
                :numero_registro,
                :num_expediente,
                :cnpj,
                :data_publicacao,
                :bula_paciente,
                :bula_profissional,
                :source_record_id,
                :source_url,
                :source_fingerprint,
                :ingested_at,
                :ingestion_status,
                :bula_paciente_sha256,
                :bula_profissional_sha256,
                :created_at,
                :updated_at
            )
            RETURNING id
            """
        ),
        {
            "medicamento": candidate.product_name,
            "empresa": candidate.company_name,
            "numero_registro": candidate.registration_number,
            "num_expediente": candidate.expedient,
            "cnpj": candidate.company_cnpj,
            "data_publicacao": candidate.source_publication_date,
            "bula_paciente": candidate.patient.storage_key,
            "bula_profissional": candidate.professional.storage_key,
            "source_record_id": candidate.source_record_id,
            "source_url": candidate.source_url,
            "source_fingerprint": candidate.source_fingerprint,
            "ingested_at": candidate.ingested_at,
            "ingestion_status": candidate.ingestion_status,
            "bula_paciente_sha256": candidate.patient.document_sha256,
            "bula_profissional_sha256": candidate.professional.document_sha256,
            "created_at": _as_naive_utc(published_at),
            "updated_at": _as_naive_utc(published_at),
        },
    )

    return PublishResult(
        action="inserted",
        row_id=result.scalar_one(),
        source_record_id=candidate.source_record_id,
    )


def _assert_existing_row_matches(row, candidate: BulaPublicationCandidate) -> None:
    expected = {
        "medicamento": candidate.product_name,
        "empresa": candidate.company_name,
        "numero_registro": candidate.registration_number,
        "num_expediente": candidate.expedient,
        "cnpj": candidate.company_cnpj,
        "data_publicacao": candidate.source_publication_date,
        "bula_paciente": candidate.patient.storage_key,
        "bula_profissional": candidate.professional.storage_key,
        "source_record_id": candidate.source_record_id,
        "source_url": candidate.source_url,
        "source_fingerprint": candidate.source_fingerprint,
        "ingestion_status": candidate.ingestion_status,
        "bula_paciente_sha256": candidate.patient.document_sha256,
        "bula_profissional_sha256": candidate.professional.document_sha256,
    }
    mismatches = [
        field
        for field, expected_value in expected.items()
        if row[field] != expected_value
    ]
    if mismatches:
        raise BulaPublicationConflictError(
            "published logical version is immutable; existing row differs "
            f"source_record_id={candidate.source_record_id} "
            f"fields={','.join(sorted(mismatches))}"
        )


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)

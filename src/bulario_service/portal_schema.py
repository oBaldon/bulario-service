from dataclasses import dataclass
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session


class PortalBulasSchemaError(RuntimeError):
    """Raised when public.bulas cannot be reconciled safely."""


@dataclass(frozen=True)
class PortalColumn:
    name: str
    data_type: str
    udt_name: str
    nullable: bool
    default: str | None
    ordinal_position: int


@dataclass(frozen=True)
class PortalConstraint:
    name: str
    constraint_type: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class PortalIndex:
    name: str
    definition: str


@dataclass(frozen=True)
class PortalBulasSchema:
    columns: tuple[PortalColumn, ...]
    constraints: tuple[PortalConstraint, ...]
    indexes: tuple[PortalIndex, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


def inspect_public_bulas_schema(session: Session) -> PortalBulasSchema:
    exists = session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'bulas'
            )
            """
        )
    ).scalar_one()

    if not exists:
        raise PortalBulasSchemaError("public.bulas does not exist")

    column_rows = session.execute(
        text(
            """
            SELECT
                column_name,
                data_type,
                udt_name,
                is_nullable,
                column_default,
                ordinal_position
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'bulas'
            ORDER BY ordinal_position
            """
        )
    ).mappings().all()

    constraint_rows = session.execute(
        text(
            """
            SELECT
                tc.constraint_name,
                tc.constraint_type,
                kcu.column_name,
                kcu.ordinal_position
            FROM information_schema.table_constraints AS tc
            LEFT JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_catalog = kcu.constraint_catalog
             AND tc.constraint_schema = kcu.constraint_schema
             AND tc.constraint_name = kcu.constraint_name
            WHERE tc.table_schema = 'public'
              AND tc.table_name = 'bulas'
            ORDER BY
                tc.constraint_name,
                kcu.ordinal_position NULLS LAST
            """
        )
    ).mappings().all()

    index_rows = session.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'bulas'
            ORDER BY indexname
            """
        )
    ).mappings().all()

    constraints_by_name: dict[str, dict[str, object]] = {}
    for row in constraint_rows:
        name = row["constraint_name"]
        entry = constraints_by_name.setdefault(
            name,
            {
                "constraint_type": row["constraint_type"],
                "columns": [],
            },
        )
        if row["column_name"] is not None:
            entry["columns"].append(row["column_name"])

    return PortalBulasSchema(
        columns=tuple(
            PortalColumn(
                name=row["column_name"],
                data_type=row["data_type"],
                udt_name=row["udt_name"],
                nullable=row["is_nullable"] == "YES",
                default=row["column_default"],
                ordinal_position=row["ordinal_position"],
            )
            for row in column_rows
        ),
        constraints=tuple(
            PortalConstraint(
                name=name,
                constraint_type=entry["constraint_type"],
                columns=tuple(entry["columns"]),
            )
            for name, entry in sorted(constraints_by_name.items())
        ),
        indexes=tuple(
            PortalIndex(
                name=row["indexname"],
                definition=row["indexdef"],
            )
            for row in index_rows
        ),
    )


def assess_publication_contract_columns(
    schema: PortalBulasSchema,
) -> dict[str, bool]:
    """Report only evidence from the real table; do not invent aliases."""
    expected = (
        "source_record_id",
        "source_url",
        "source_fingerprint",
        "ingested_at",
        "ingestion_status",
    )
    names = set(schema.column_names)
    return {name: name in names for name in expected}

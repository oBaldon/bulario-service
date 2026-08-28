from unittest.mock import Mock

import pytest

from bulario_service.portal_schema import (
    PortalBulasSchema,
    PortalBulasSchemaError,
    PortalColumn,
    PortalConstraint,
    PortalIndex,
    assess_publication_contract_columns,
    inspect_public_bulas_schema,
)


class Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows


def test_inspect_public_bulas_schema_reads_columns_constraints_indexes() -> None:
    session = Mock()
    session.execute.side_effect = [
        Result(scalar=True),
        Result(
            rows=[
                {
                    "column_name": "id",
                    "data_type": "bigint",
                    "udt_name": "int8",
                    "is_nullable": "NO",
                    "column_default": None,
                    "ordinal_position": 1,
                },
                {
                    "column_name": "source_record_id",
                    "data_type": "character varying",
                    "udt_name": "varchar",
                    "is_nullable": "NO",
                    "column_default": None,
                    "ordinal_position": 2,
                },
            ]
        ),
        Result(
            rows=[
                {
                    "constraint_name": "bulas_pkey",
                    "constraint_type": "PRIMARY KEY",
                    "column_name": "id",
                    "ordinal_position": 1,
                },
                {
                    "constraint_name": "bulas_source_record_unique",
                    "constraint_type": "UNIQUE",
                    "column_name": "source_record_id",
                    "ordinal_position": 1,
                },
            ]
        ),
        Result(
            rows=[
                {
                    "indexname": "bulas_pkey",
                    "indexdef": (
                        "CREATE UNIQUE INDEX bulas_pkey "
                        "ON public.bulas USING btree (id)"
                    ),
                }
            ]
        ),
    ]

    schema = inspect_public_bulas_schema(session)

    assert schema.column_names == ("id", "source_record_id")
    assert schema.columns[1].nullable is False
    assert schema.constraints[1].columns == ("source_record_id",)
    assert schema.indexes[0].name == "bulas_pkey"


def test_inspect_public_bulas_schema_rejects_missing_table() -> None:
    session = Mock()
    session.execute.return_value = Result(scalar=False)

    with pytest.raises(
        PortalBulasSchemaError,
        match="public.bulas does not exist",
    ):
        inspect_public_bulas_schema(session)


def test_assessment_reports_exact_names_without_guessing_aliases() -> None:
    schema = PortalBulasSchema(
        columns=(
            PortalColumn(
                name="source_record_id",
                data_type="text",
                udt_name="text",
                nullable=False,
                default=None,
                ordinal_position=1,
            ),
            PortalColumn(
                name="ingestion_status",
                data_type="text",
                udt_name="text",
                nullable=False,
                default=None,
                ordinal_position=2,
            ),
        ),
        constraints=(),
        indexes=(),
    )

    result = assess_publication_contract_columns(schema)

    assert result["source_record_id"] is True
    assert result["ingestion_status"] is True
    assert result["source_url"] is False
    assert result["source_fingerprint"] is False
    assert result["ingested_at"] is False

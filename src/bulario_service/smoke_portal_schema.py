import json
import sys

from sqlalchemy.orm import Session

from bulario_service.config import load_settings
from bulario_service.db import create_database_engine
from bulario_service.portal_schema import (
    PortalBulasSchemaError,
    assess_publication_contract_columns,
    inspect_public_bulas_schema,
)


def run_smoke() -> int:
    engine = None
    try:
        engine = create_database_engine(load_settings())

        with Session(engine) as session:
            schema = inspect_public_bulas_schema(session)

        print("public.bulas schema: OK")
        print(f"columns={len(schema.columns)}")
        for column in schema.columns:
            print(
                "COLUMN "
                f"position={column.ordinal_position} "
                f"name={column.name} "
                f"type={column.data_type} "
                f"udt={column.udt_name} "
                f"nullable={str(column.nullable).lower()} "
                f"default={column.default!r}"
            )

        print(f"constraints={len(schema.constraints)}")
        for constraint in schema.constraints:
            print(
                "CONSTRAINT "
                f"name={constraint.name} "
                f"type={constraint.constraint_type} "
                f"columns={','.join(constraint.columns)}"
            )

        print(f"indexes={len(schema.indexes)}")
        for index in schema.indexes:
            print(
                "INDEX "
                f"name={index.name} "
                f"definition={index.definition}"
            )

        assessment = assess_publication_contract_columns(schema)
        print(
            "CORE_CONTRACT_COLUMNS "
            + json.dumps(
                assessment,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        print("public_bulas_written=0")
        return 0

    except (PortalBulasSchemaError, RuntimeError) as exc:
        print(
            f"public.bulas schema inspection failed: {exc}",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


def main() -> None:
    raise SystemExit(run_smoke())


if __name__ == "__main__":
    main()

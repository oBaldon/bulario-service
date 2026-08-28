from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://consultas.anvisa.gov.br"
DEFAULT_DISCOVERY_PAGE_SIZE = 100
DEFAULT_DETAIL_PAGE_SIZE = 10

_REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Authorization": "Guest",
}


class AnvisaSourceError(RuntimeError):
    """Raised when the ANVISA source cannot be reached successfully."""


class AnvisaPayloadError(AnvisaSourceError):
    """Raised when ANVISA returns an unexpected or invalid payload."""


@dataclass(frozen=True)
class DiscoveredProduct:
    source_product_id: int
    registration_number: str | None
    product_name: str | None
    current_expedient: str | None
    company_name: str | None
    company_cnpj: str | None
    process_number: str | None
    publication_date: str | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class DiscoveryPage:
    items: tuple[DiscoveredProduct, ...]
    total_elements: int
    total_pages: int
    page: int
    page_size: int
    last: bool


@dataclass(frozen=True)
class BulaVersion:
    source_document_id: int
    expedient: str | None
    registration_number: str | None
    publication_date: str | None
    status: str | None
    patient_token: str | None
    professional_token: str | None
    current: bool
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class ProductDetail:
    source_product_id: int
    registration_number: str | None
    product_name: str | None
    versions: tuple[BulaVersion, ...]


class AnvisaBularioConnector:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "AnvisaBularioConnector":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def discover_page(
        self,
        *,
        page: int,
        period_start: str,
        period_end: str,
        page_size: int = DEFAULT_DISCOVERY_PAGE_SIZE,
    ) -> DiscoveryPage:
        if page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if page_size < 1:
            raise ValueError("page_size must be greater than or equal to 1")

        payload = self._get_json(
            "/api/consulta/bulario",
            params={
                "column": "",
                "count": page_size,
                "filter[periodoPublicacaoFinal]": period_end,
                "filter[periodoPublicacaoInicial]": period_start,
                "order": "asc",
                "page": page,
            },
        )
        return self._parse_discovery_page(payload)

    def get_product_detail(
        self,
        source_product_id: int,
        *,
        page_size: int = DEFAULT_DETAIL_PAGE_SIZE,
    ) -> ProductDetail:
        if source_product_id < 1:
            raise ValueError("source_product_id must be greater than or equal to 1")
        if page_size < 1:
            raise ValueError("page_size must be greater than or equal to 1")

        page = 1
        product_name: str | None = None
        registration_number: str | None = None
        current_document_id: int | None = None
        versions_by_id: dict[int, dict[str, Any]] = {}

        while True:
            payload = self._get_json(
                f"/api/consulta/bulario/{source_product_id}",
                params={
                    "column": "",
                    "count": page_size,
                    "order": "asc",
                    "page": page,
                },
            )

            if not isinstance(payload, dict):
                raise AnvisaPayloadError("product detail payload must be an object")

            if page == 1:
                product_name = _optional_string(payload.get("nomeProduto"))
                registration_number = _optional_string(payload.get("registroProduto"))

                current = payload.get("bulaAtual")
                if not isinstance(current, dict):
                    raise AnvisaPayloadError("product detail is missing bulaAtual")

                current_document_id = _required_int(
                    current.get("idDocumento"),
                    field="bulaAtual.idDocumento",
                )
                versions_by_id[current_document_id] = current

            history = payload.get("historico")
            if not isinstance(history, dict):
                raise AnvisaPayloadError("product detail is missing historico")

            content = history.get("content")
            if not isinstance(content, list):
                raise AnvisaPayloadError("historico.content must be a list")

            for raw_version in content:
                if not isinstance(raw_version, dict):
                    raise AnvisaPayloadError("historico.content items must be objects")
                document_id = _required_int(
                    raw_version.get("idDocumento"),
                    field="historico.content.idDocumento",
                )
                versions_by_id[document_id] = raw_version

            last = history.get("last")
            if not isinstance(last, bool):
                raise AnvisaPayloadError("historico.last must be a boolean")
            if last:
                break

            total_pages = _required_int(
                history.get("totalPages"),
                field="historico.totalPages",
            )
            if page >= total_pages:
                raise AnvisaPayloadError(
                    "historico pagination is inconsistent: last=false on final page"
                )
            page += 1

        if current_document_id is None:
            raise AnvisaPayloadError("product detail did not identify current document")

        versions = tuple(
            self._parse_version(raw, current_document_id=current_document_id)
            for _, raw in sorted(
                versions_by_id.items(),
                key=lambda pair: pair[0],
                reverse=True,
            )
        )

        return ProductDetail(
            source_product_id=source_product_id,
            registration_number=registration_number,
            product_name=product_name,
            versions=versions,
        )

    def _get_json(self, path: str, *, params: dict[str, Any]) -> Any:
        try:
            response = self._client.get(
                path,
                params=params,
                headers=_REQUEST_HEADERS,
            )
        except httpx.TimeoutException as exc:
            raise AnvisaSourceError("ANVISA request timed out") from exc
        except httpx.HTTPError as exc:
            raise AnvisaSourceError("ANVISA request failed") from exc

        if response.status_code != 200:
            raise AnvisaSourceError(f"ANVISA returned HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise AnvisaPayloadError("ANVISA returned invalid JSON") from exc

    @staticmethod
    def _parse_discovery_page(payload: Any) -> DiscoveryPage:
        if not isinstance(payload, dict):
            raise AnvisaPayloadError("discovery payload must be an object")

        content = payload.get("content")
        if not isinstance(content, list):
            raise AnvisaPayloadError("discovery content must be a list")

        items: list[DiscoveredProduct] = []
        for raw_item in content:
            if not isinstance(raw_item, dict):
                raise AnvisaPayloadError("discovery content items must be objects")
            items.append(
                DiscoveredProduct(
                    source_product_id=_required_int(
                        raw_item.get("idProduto"),
                        field="content.idProduto",
                    ),
                    registration_number=_optional_string(
                        raw_item.get("numeroRegistro")
                    ),
                    product_name=_optional_string(raw_item.get("nomeProduto")),
                    current_expedient=_optional_string(raw_item.get("expediente")),
                    company_name=_optional_string(raw_item.get("razaoSocial")),
                    company_cnpj=_optional_string(raw_item.get("cnpj")),
                    process_number=_optional_string(raw_item.get("numProcesso")),
                    publication_date=_optional_string(raw_item.get("data")),
                    raw_payload=raw_item,
                )
            )

        response_page = _required_int(payload.get("number"), field="number")

        return DiscoveryPage(
            items=tuple(items),
            total_elements=_required_int(
                payload.get("totalElements"),
                field="totalElements",
            ),
            total_pages=_required_int(payload.get("totalPages"), field="totalPages"),
            page=response_page + 1,
            page_size=_required_int(payload.get("size"), field="size"),
            last=_required_bool(payload.get("last"), field="last"),
        )

    @staticmethod
    def _parse_version(
        raw_version: dict[str, Any],
        *,
        current_document_id: int,
    ) -> BulaVersion:
        document_id = _required_int(
            raw_version.get("idDocumento"),
            field="idDocumento",
        )
        return BulaVersion(
            source_document_id=document_id,
            expedient=_optional_string(raw_version.get("expediente")),
            registration_number=_optional_string(raw_version.get("numeroRegistro")),
            publication_date=_optional_string(raw_version.get("dataPublicacao")),
            status=_optional_string(
                raw_version.get("situacaoAtual") or raw_version.get("descSituacao")
            ),
            patient_token=_optional_string(raw_version.get("idBulaPaciente")),
            professional_token=_optional_string(
                raw_version.get("idBulaProfissional")
            ),
            current=document_id == current_document_id,
            raw_payload=raw_version,
        )


def _required_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AnvisaPayloadError(f"{field} must be an integer")
    return value


def _required_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise AnvisaPayloadError(f"{field} must be a boolean")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int)):
        return str(value)
    raise AnvisaPayloadError("expected string-compatible value")

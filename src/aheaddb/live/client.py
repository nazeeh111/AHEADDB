"""Typed synchronous SDK. Read failures are explicit and writes carry caller keys."""
from __future__ import annotations

from types import TracebackType
from urllib.parse import quote, urlsplit

import httpx

from .contracts import (
    Answer,
    Bootstrap,
    Edit,
    EditReceipt,
    EvaluationReport,
    EvaluationRequest,
    ModelReceipt,
    ModelRecipe,
    Status,
    ViewDefinition,
    ViewReceipt,
)
from .store import Unavailable


class Client:
    def __init__(self, base_url: str = "http://127.0.0.1:8790", *, timeout: float = 10,
                 transport: httpx.BaseTransport | None = None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Live v1 client requires a local HTTP origin")
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  trust_env=False, follow_redirects=False)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise Unavailable("Live service unreachable; source coverage is unknown") from exc
        if response.status_code == 503:
            raise Unavailable(response.json().get("detail", "Live service unavailable"))
        response.raise_for_status()
        return response.json()

    def read(self, name: str, *, generation: str | None = None, max_age_seconds: float | None = None,
             allow_stale: bool = False) -> Answer:
        params: dict = {"allow_stale": str(allow_stale).lower()}
        if generation is not None:
            params["generation"] = generation
        if max_age_seconds is not None:
            params["max_age_seconds"] = max_age_seconds
        return Answer.model_validate(self._request("GET", f"/v1/views/{quote(name, safe='')}", params=params))

    def bootstrap(self, source: Bootstrap) -> int:
        return EditReceipt.model_validate(self._request("POST", "/v1/source/bootstrap",
                                                        json=source.model_dump(mode="json", by_alias=True))).revision

    def evaluate(self, request: EvaluationRequest) -> EvaluationReport:
        return EvaluationReport.model_validate(self._request("POST", "/v1/evaluations", json=request.model_dump(mode="json")))

    def edit(self, edit: Edit) -> int:
        return EditReceipt.model_validate(self._request("POST", "/v1/source/edits", json=edit.model_dump(mode="json"))).revision

    def register_model(self, recipe: ModelRecipe) -> str:
        return ModelReceipt.model_validate(self._request("POST", "/v1/models", json=recipe.model_dump(mode="json"))).model_id

    def publish_view(self, definition: ViewDefinition) -> str:
        return ViewReceipt.model_validate(self._request("POST", "/v1/views", json=definition.model_dump(mode="json"))).view_name

    def status_typed(self) -> Status:
        return Status.model_validate(self._request("GET", "/v1/status"))

    def status(self) -> dict:
        return self.status_typed().model_dump(mode="json")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()

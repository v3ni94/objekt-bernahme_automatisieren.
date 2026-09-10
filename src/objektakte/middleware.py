"""Request-ID fuer Korrelation von Logs und Audit (docs/architektur.md 10.4)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from objektakte.logging_json import request_id_var


class RequestIdMiddleware:
    header = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.META.get(self.header, "")
        request_id = incoming[:36] if incoming and len(incoming) <= 64 else str(uuid.uuid4())
        request.request_id = request_id  # type: ignore[attr-defined]
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-ID"] = request_id
        return response

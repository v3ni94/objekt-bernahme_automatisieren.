"""Endpunkte der lesenden CRM-Schnittstelle unter /api/crm/v1/ (Schnittstellenvertrag M29 Stufe 3).

Nur GET, Antworten JSON (UTF-8), Paginierung ?page=1&page_size=100 (hoechstens 500). Unbekannte Objektnummer 404.
Es gibt keinen schreibenden Endpunkt.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.crm_api import data
from apps.crm_api.auth import error, json_response, require_scope
from apps.crm_api.models import SCOPE_DOCUMENTS, SCOPE_OBJECTS, SCOPE_PERSONS

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


class BadRequest(ValueError):
    pass


def _positive_int(request, name: str, default: int) -> int:
    raw = request.GET.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise BadRequest(f"{name} muss eine ganze Zahl sein") from exc
    if value < 1:
        raise BadRequest(f"{name} muss mindestens 1 sein")
    return value


def _paginate(request, items) -> dict:
    """items: Liste oder QuerySet. page_size ueber dem Hoechstwert wird auf 500 begrenzt."""
    page = _positive_int(request, "page", 1)
    page_size = min(_positive_int(request, "page_size", DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)
    count = len(items) if isinstance(items, list) else items.count()
    start = (page - 1) * page_size
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "results": list(items[start : start + page_size]),
    }


def _page_response(request, items, row=None):
    try:
        body = _paginate(request, items)
    except BadRequest as exc:
        return error(400, str(exc))
    if row is not None:
        body["results"] = [row(x) for x in body["results"]]
    return json_response(body)


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # Ein "+" der Zeitzone aus einer nicht kodierten URL kommt als Leerzeichen an
    value = parse_datetime(raw.replace(" ", "+"))
    if value is None:
        d = parse_date(raw)
        if d is None:
            raise BadRequest("since muss ein Zeitpunkt nach ISO 8601 sein")
        value = datetime.combine(d, time.min)
    if timezone.is_naive(value):
        value = value.replace(tzinfo=UTC)
    return value


def _not_found():
    return error(404, "Objekt nicht gefunden")


@require_scope(SCOPE_OBJECTS)
def object_list(request):
    objects = data.list_objects()
    try:
        body = _paginate(request, objects)
    except BadRequest as exc:
        return error(400, str(exc))
    body["results"] = data.object_summaries(body["results"])
    return json_response(body)


@require_scope(SCOPE_OBJECTS)
def object_detail(request, number: str):
    obj = data.find_object(number)
    if obj is None:
        return _not_found()
    return json_response(data.object_detail(obj))


@require_scope(SCOPE_DOCUMENTS)
def object_documents(request, number: str):
    obj = data.find_object(number)
    if obj is None:
        return _not_found()
    try:
        since = _parse_since(request.GET.get("since"))
    except BadRequest as exc:
        return error(400, str(exc))
    qs = data.filter_documents(
        data.documents_queryset(obj), folder=(request.GET.get("folder") or "").strip() or None, since=since
    )
    return _page_response(request, qs, data.document_row)


@require_scope(SCOPE_PERSONS)
def object_owners(request, number: str):
    obj = data.find_object(number)
    if obj is None:
        return _not_found()
    return _page_response(request, data.owners(obj))


@require_scope(SCOPE_PERSONS)
def object_tenants(request, number: str):
    obj = data.find_object(number)
    if obj is None:
        return _not_found()
    return _page_response(request, data.tenants(obj))

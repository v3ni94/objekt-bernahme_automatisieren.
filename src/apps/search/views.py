"""Globale Suche (CR 13): Filter und Volltext, Ergebnisse nach Art gruppiert, Rechte serverseitig."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.documents.models import DocumentCategory, DocumentType
from apps.objects.models import ManagementType
from apps.search.services import Filters, SearchIndex


@login_required
def search_page(request):
    filters = Filters.from_params(request.GET)
    result = SearchIndex.query(filters, request.user)
    return render(
        request,
        "search/page.html",
        {
            "filters": filters,
            "result": result,
            "params": request.GET,
            "categories": DocumentCategory.objects.filter(is_active=True).order_by("code"),
            "document_types": DocumentType.objects.filter(is_active=True)
            .select_related("subfolder")
            .order_by("category_id", "subfolder__code", "name"),
            "management_types": ManagementType.choices,
        },
    )

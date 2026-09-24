"""Ablagejahr fuer die Jahresordner unter 03_Buchhaltung (Wunsch der Geschaeftsfuehrung 24.09.2026): Abrechnungs-
oder Wirtschaftsjahr (period_year), sonst der Beginn des Zeitraums (period_from), sonst das Jahr des Dokumentdatums,
sonst kein Jahr (Ablage flach in 03). Eine Funktion fuer Ablage, Pruefcenter und Umzugsbefehl, damit alle denselben
Ordner nennen."""

from __future__ import annotations

YEAR_FOLDER_CATEGORY = "03"
_MIN_YEAR, _MAX_YEAR = 1990, 2100


def filing_year(period_year, period_from=None, document_date=None) -> int | None:
    for candidate in (period_year, getattr(period_from, "year", None), getattr(document_date, "year", None)):
        if candidate:
            year = int(candidate)
            if _MIN_YEAR <= year <= _MAX_YEAR:
                return year
    return None


def document_filing_year(doc) -> int | None:
    return filing_year(doc.period_year, doc.period_from, doc.document_date)


def is_year_folder_target(category_code: str | None, subfolder_code: str | None) -> bool:
    return category_code == YEAR_FOLDER_CATEGORY and not subfolder_code

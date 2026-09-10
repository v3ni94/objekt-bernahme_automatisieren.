"""Rechenmodell fuer die Performance-Vorgabe aus CR-05 Abschnitt 7 und 14.

Formeln aus docs/umsetzungsplan.md Abschnitt 2.8 (Pruefpunkt P1):

    cpu_seconds  = S * (1 - d) * t_ocr + S * d * t_txt
    wall_seconds = cpu_seconds / (P * e)
    p_min        = cpu_seconds / (e * budget)

S      Seiten je Objekt (CR: 10.000)
d      Anteil Seiten mit Textebene (Digitalseiten)
t_ocr  Sekunden je Scan-Seite je Prozess
t_txt  Sekunden je Digitalseite (Textextraktion plus Seitenbild)
P      parallele OCR-Prozesse
e      Skalierungseffizienz (1,0 = ideal)
budget OCR-Zeitbudget in Sekunden (Plan: 7.200 s, damit Nachlauf und Reserve
       innerhalb der 3-Stunden-Vorgabe Platz haben)

Alle Eingaben sind Messwerte oder als ANNAHME gekennzeichnete Planungsgroessen;
das Modul selbst enthaelt keine Annahmen ueber den Zielserver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PAGES_PER_OBJECT = 10_000          # CR-05 Abschnitt 7
TOTAL_BUDGET_SECONDS = 3 * 3600    # CR-05 Abschnitt 7: unter 3 Stunden
OCR_BUDGET_SECONDS = 2 * 3600      # Umsetzungsplan 2.8: OCR-Anteil unter 2 Stunden


def cpu_seconds(pages: int, digital_share: float, t_ocr: float, t_txt: float) -> float:
    """CPU-Sekunden fuer ein Objekt mit `pages` Seiten."""
    if not 0.0 <= digital_share <= 1.0:
        raise ValueError("digital_share muss zwischen 0 und 1 liegen")
    scan_pages = pages * (1.0 - digital_share)
    digital_pages = pages * digital_share
    return scan_pages * t_ocr + digital_pages * t_txt


def wall_seconds(cpu: float, processes: int, efficiency: float) -> float:
    """Wandzeit bei `processes` parallelen Prozessen und Skalierungseffizienz."""
    if processes < 1:
        raise ValueError("processes muss mindestens 1 sein")
    if not 0.0 < efficiency <= 1.0:
        raise ValueError("efficiency muss zwischen 0 (ausschliesslich) und 1 liegen")
    return cpu / (processes * efficiency)


def p_min(cpu: float, efficiency: float, budget: float = OCR_BUDGET_SECONDS) -> int:
    """Kleinste ganze Prozesszahl, die das Budget haelt."""
    return max(1, math.ceil(cpu / (efficiency * budget)))


def scaling_efficiency(pages_per_second_single: float, pages_per_second_parallel: float,
                       processes: int) -> float:
    """e = Durchsatz mit P Prozessen / (P * Durchsatz mit einem Prozess)."""
    if processes < 1 or pages_per_second_single <= 0:
        raise ValueError("ungueltige Eingaben fuer die Skalierungseffizienz")
    return pages_per_second_parallel / (processes * pages_per_second_single)


@dataclass(frozen=True)
class Scenario:
    name: str
    digital_share: float
    t_ocr: float
    t_txt: float

    def cpu(self, pages: int = PAGES_PER_OBJECT) -> float:
        return cpu_seconds(pages, self.digital_share, self.t_ocr, self.t_txt)


def scenario_table(scenarios: list[Scenario], process_counts: list[int], efficiency: float,
                   pages: int = PAGES_PER_OBJECT,
                   limit_seconds: float = OCR_BUDGET_SECONDS) -> list[dict]:
    """Zeilen fuer die Szenariotabelle aus Umsetzungsplan 2.8."""
    rows = []
    for sc in scenarios:
        cpu = sc.cpu(pages)
        cells = {}
        for p in process_counts:
            wall = wall_seconds(cpu, p, efficiency)
            cells[p] = {
                "wall_seconds": wall,
                "wall_minutes": wall / 60.0,
                "within_ocr_budget": wall <= limit_seconds,
                "within_total_budget": wall <= TOTAL_BUDGET_SECONDS,
            }
        rows.append({
            "scenario": sc.name,
            "digital_share": sc.digital_share,
            "t_ocr": sc.t_ocr,
            "t_txt": sc.t_txt,
            "cpu_seconds": cpu,
            "p_min": p_min(cpu, efficiency, limit_seconds),
            "cells": cells,
        })
    return rows

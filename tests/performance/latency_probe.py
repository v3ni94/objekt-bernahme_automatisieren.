#!/usr/bin/env python3
"""Latenzsonde (M7 Schritt 10, M13 Schritt 2, B-35): p95 und p99 je Endpunkt bei mehreren gleichzeitigen Nutzern.
Endpunkte nach Umsetzungsplan 2.17: Objektansicht, Review-Liste, Detail, Vorschaubild, Eigentuemersuche, Suche,
Berichte. Die Entscheidung (POST) wird nicht von der Sonde ausgeloest, weil sie Daten veraendert; ihre Laufzeit wird
in tests/integration/review/test_latenz.py und ueber die Dauer der Aktion im Audit gemessen. Zielwert p95 unter 2 s,
p99 unter 4 s (ANNAHME H01).

Aufruf gegen einen Server (waehrend des Performance-Laufs, Messdatei nach /srv/objektakte/exports/perf/):
    LATENZ_BASE_URL=https://... LATENZ_SESSION_COOKIE=... python3 latency_probe.py --users 5 --rounds 20 \
        --object 1 --case 1 --document 1 --query Abrechnung > latenz.json
Der Login mit TOTP ist nicht automatisiert; die Sonde erwartet das Sitzungs-Cookie eines angemeldeten Nutzers.
Endpunkte, die 4xx liefern (zum Beispiel fehlende Fall-ID), werden mit Statuscode im Bericht ausgewiesen und nicht
in die Perzentile aufgenommen.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def measure(fetch, endpoints: dict[str, str], *, users: int, rounds: int) -> dict:
    """fetch(name, url) -> Statuscode; misst je Endpunkt Wanduhrzeit je Anfrage."""
    samples: dict[str, list[float]] = {name: [] for name in endpoints}
    statuses: dict[str, dict[int, int]] = {name: {} for name in endpoints}

    def worker(_):
        for _round in range(rounds):
            for name, url in endpoints.items():
                t0 = time.perf_counter()
                status = fetch(name, url)
                dt = time.perf_counter() - t0
                statuses[name][status] = statuses[name].get(status, 0) + 1
                if status < 400:
                    samples[name].append(dt)

    with ThreadPoolExecutor(max_workers=users) as pool:
        list(pool.map(worker, range(users)))
    report = {}
    for name, values in samples.items():
        report[name] = {
            "n": len(values),
            "p50_s": round(statistics.median(values), 4) if values else None,
            "p95_s": round(percentile(values, 95), 4),
            "p99_s": round(percentile(values, 99), 4),
            "max_s": round(max(values), 4) if values else None,
            "status": {str(k): v for k, v in sorted(statuses[name].items())},
        }
    worst = max((r["p95_s"] for r in report.values()), default=0)
    complete = all(r["n"] > 0 for r in report.values())
    report["_summary"] = {
        "users": users,
        "rounds": rounds,
        "p95_max_s": worst,
        "ok": worst < 2.0 and complete,
        "alle_endpunkte_erreichbar": complete,
    }
    return report


def main() -> int:
    import os
    import urllib.request

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--case", type=int, default=1, help="Fall-ID fuer die Detailansicht")
    ap.add_argument("--object", type=int, default=1, help="Objekt-ID fuer Objektansicht und Eigentuemersuche")
    ap.add_argument("--document", type=int, default=1, help="Dokument-ID fuer das Vorschaubild (Seite 1)")
    ap.add_argument("--query", default="Abrechnung", help="Suchwort fuer die Volltextsuche")
    args = ap.parse_args()
    base = os.environ.get("LATENZ_BASE_URL")
    cookie = os.environ.get("LATENZ_SESSION_COOKIE")
    if not base or not cookie:
        sys.exit("LATENZ_BASE_URL und LATENZ_SESSION_COOKIE setzen")
    from urllib.parse import quote

    endpoints = {
        "objektansicht": f"{base}/objekte/{args.object}/",
        "liste": f"{base}/review/",
        "detail": f"{base}/review/{args.case}/",
        "vorschaubild": f"{base}/dokumente/{args.document}/seite/1.jpg",
        "eigentuemersuche": f"{base}/review/objekte/{args.object}/eigentuemer.json?q=mu",
        "suche": f"{base}/suche/?q={quote(args.query)}",
        "berichte": f"{base}/berichte/",
    }

    def fetch(name, url):
        if not url.startswith(("http://", "https://")):
            return 599
        req = urllib.request.Request(url, headers={"Cookie": f"sessionid={cookie}"})  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                resp.read()
                return resp.status
        except Exception:
            return 599

    print(
        json.dumps(
            measure(fetch, endpoints, users=args.users, rounds=args.rounds), indent=1, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

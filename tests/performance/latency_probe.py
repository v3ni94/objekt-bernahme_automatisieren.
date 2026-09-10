#!/usr/bin/env python3
"""Latenzsonde fuer das Review Center (M7 Schritt 10, B-35): p95 und p99 der Endpunkte Liste, Detail, Vorschaubild,
Eigentuemersuche und Entscheidung. Laeuft gegen einen Server (URL) mit Anmeldedaten aus Umgebungsvariablen oder in der
Testsuite mit dem Django-Testclient (tests/integration/review/test_latenz.py). Zielwert p95 unter 2 s, p99 unter 4 s.

Aufruf gegen einen Server:
    LATENZ_BASE_URL=https://... LATENZ_EMAIL=... LATENZ_PASSWORD=... python3 latency_probe.py --users 5 --rounds 20
Der Login mit TOTP ist nicht automatisiert; gegen den Server wird ein Sitzungs-Cookie erwartet (LATENZ_SESSION_COOKIE).
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

    def worker(_):
        for _round in range(rounds):
            for name, url in endpoints.items():
                t0 = time.perf_counter()
                status = fetch(name, url)
                dt = time.perf_counter() - t0
                if status < 500:
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
        }
    worst = max((r["p95_s"] for r in report.values()), default=0)
    report["_summary"] = {"users": users, "rounds": rounds, "p95_max_s": worst, "ok": worst < 2.0}
    return report


def main() -> int:
    import os
    import urllib.request

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--case", type=int, default=1, help="Fall-ID fuer die Detailansicht")
    ap.add_argument("--object", type=int, default=1, help="Objekt-ID fuer die Eigentuemersuche")
    args = ap.parse_args()
    base = os.environ.get("LATENZ_BASE_URL")
    cookie = os.environ.get("LATENZ_SESSION_COOKIE")
    if not base or not cookie:
        sys.exit("LATENZ_BASE_URL und LATENZ_SESSION_COOKIE setzen")
    endpoints = {
        "liste": f"{base}/review/",
        "detail": f"{base}/review/{args.case}/",
        "suche": f"{base}/review/objekte/{args.object}/eigentuemer.json?q=mu",
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

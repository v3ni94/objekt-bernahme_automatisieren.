"""Auszugsbildung fuer Stufe 3 (E 4.3): erste Seiten plus letzte Seite aus dem maskierten Seitentext, Kopf- und
Fusszeilen entdoppelt, Leerzeilen verdichtet, auf das Tokenbudget gekuerzt (Kuerzungsstelle [...]); Dateiname maskiert
(Namen ersetzt, Zahlen und Einheitenmuster bleiben)."""

from __future__ import annotations

import re
from collections import Counter

from apps.config import store

MARK = " [...] "


def compress_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text or "").strip()


def dedupe_headers(pages: list[str]) -> list[str]:
    """Zeilen, die auf mindestens drei Seiten identisch am Anfang oder Ende stehen, bleiben nur auf der ersten Seite."""
    if len(pages) < 3:
        return pages
    first_lines = Counter()
    last_lines = Counter()
    split = [p.strip().split("\n") for p in pages]
    for lines in split:
        if lines:
            first_lines[lines[0].strip()] += 1
            last_lines[lines[-1].strip()] += 1
    repeated = {line for line, n in first_lines.items() if n >= 3 and line} | {
        line for line, n in last_lines.items() if n >= 3 and line
    }
    out = []
    for i, lines in enumerate(split):
        if i == 0:
            out.append("\n".join(lines))
            continue
        kept = [line for line in lines if line.strip() not in repeated or line.strip() in ("",)]
        out.append("\n".join(kept))
    return out


def token_budget_chars() -> int:
    max_tokens = int(store.get("ai.max_input_tokens", 3000))
    chars_per_token = float(store.get("ai.chars_per_token", 3.5))
    return int(max_tokens * chars_per_token)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.7)
    tail = max_chars - head - len(MARK)
    return text[:head].rstrip() + MARK + text[-tail:].lstrip() if tail > 0 else text[:max_chars]


def build_excerpt(
    pages: dict[int, str], *, first_pages: int | None = None, max_chars: int | None = None
) -> str:
    first_pages = first_pages or int(store.get("classification.first_pages", 3))
    max_chars = max_chars or token_budget_chars()
    if not pages:
        return ""
    numbers = sorted(pages)
    selected = numbers[:first_pages]
    if numbers[-1] not in selected:
        selected.append(numbers[-1])
    texts = dedupe_headers([pages[n] or "" for n in selected])
    body = "\n\f".join(compress_blank_lines(t) for t in texts)
    return truncate(body, max_chars)


def mask_filename(name: str, party_names: list[str]) -> str:
    """Dateiname ohne Personennamen (Auflage Ue15): bekannte Nachnamen und Firmen werden durch [NAME] ersetzt."""
    masked = name or ""
    for token in sorted({n for n in party_names if n and len(n) >= 3}, key=len, reverse=True):
        masked = re.sub(re.escape(token), "[NAME]", masked, flags=re.IGNORECASE)
    return masked

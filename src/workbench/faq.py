"""Deterministic FAQ matching. The model is never consulted here."""

from __future__ import annotations

import json

from .guard import compact


def _bigrams(text: str) -> set[str]:
    value = compact(text)
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def similarity(inbound: str, question: str, variants: list[str]) -> float:
    """Return 0..1; substring hits win, otherwise bigram overlap is used."""
    needle = compact(inbound)
    if len(needle) < 2:
        return 0.0
    best = 0.0
    for candidate in (question, *variants):
        haystack = compact(candidate)
        if not haystack:
            continue
        if needle == haystack:
            return 1.0
        if len(needle) >= 4 and needle in haystack:
            best = max(best, 0.95)
            continue
        left, right = _bigrams(needle), _bigrams(haystack)
        if not left or not right:
            continue
        overlap = len(left & right)
        best = max(best, overlap / len(left | right))
    return round(best, 4)


def parse_row(row) -> dict:
    return {
        "id": row["id"],
        "category": row["category"],
        "question": row["question"],
        "variants": json.loads(row["variants_json"]),
        "answer": row["answer"],
        "platforms": json.loads(row["platforms_json"]),
        "languages": json.loads(row["languages_json"]),
        "auto_send": bool(row["auto_send"]),
        "sensitive": bool(row["sensitive"]),
        "source": row["source"],
        "reviewed_at": row["reviewed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def match(inbound: str, faqs: list[dict], threshold: float = 0.3) -> tuple[dict, float] | None:
    """Pick the best reviewed FAQ for an inbound message."""
    best: tuple[dict, float] | None = None
    for faq in faqs:
        if not faq.get("reviewed_at"):
            continue
        score = similarity(inbound, faq["question"], faq.get("variants", []))
        if score >= threshold and (best is None or score > best[1]):
            best = (faq, score)
    return best

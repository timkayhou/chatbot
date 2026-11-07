"""Deterministic entity preservation checks for drafted replies."""

from __future__ import annotations

import re

_PRICE = re.compile(r"[¥￥$€]\s?\d[\d,]*(?:\.\d+)?")
_URL = re.compile(r"https?://[^\s，。；、）)】\]]+")


def extract(text: str) -> dict[str, set[str]]:
    return {
        "price": set(_PRICE.findall(text or "")),
        "url": {value.rstrip(".,;:") for value in _URL.findall(text or "")},
    }


def missing_entities(reference: str, draft: str) -> list[str]:
    """Return reference entities that did not survive into the draft.

    Only prices and URLs are checked deterministically; model names and numbers
    are left to the human reviewer and the outbound guard.
    """
    wanted = extract(reference)
    missing: list[str] = []
    for kind in ("price", "url"):
        for value in sorted(wanted[kind]):
            if value not in (draft or ""):
                missing.append(value)
    return missing

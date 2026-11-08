"""Deterministic entity preservation checks for drafted replies."""

from __future__ import annotations

import re

_PRICE = re.compile(r"[¥￥$€]\s?\d[\d,]*(?:\.\d+)?")
_URL = re.compile(r"https?://[^\s，。；、）)】\]]+")
# Model-like tokens contain both a letter and a digit, e.g. X1, M3, G10, A-2048.
_MODEL = re.compile(r"\b[A-Za-z][A-Za-z0-9-]*\d[A-Za-z0-9-]*\b")
# Capitalized product phrases ending in a number, e.g. "Huawei MateBook 14".
_NAME = re.compile(r"\b(?:[A-Z][A-Za-z0-9-]*\s+){1,3}\d[A-Za-z0-9]*\b")


def extract(text: str) -> dict[str, set[str]]:
    return {
        "price": set(_PRICE.findall(text or "")),
        "url": {value.rstrip(".,;:") for value in _URL.findall(text or "")},
        "model": set(_MODEL.findall(text or "")),
    }


def missing_entities(reference: str, draft: str) -> list[str]:
    """Return reference entities that did not survive into the draft.

    Prices, URLs and letter+digit model tokens are checked; free-form numbers and
    product names without a letter-digit token are left to the human reviewer.
    """
    wanted = extract(reference)
    missing: list[str] = []
    for kind in ("price", "url", "model"):
        for value in sorted(wanted[kind]):
            if value not in (draft or ""):
                missing.append(value)
    return missing


_TOKEN = "⟦E{}⟧"


def protect(text: str, prefix: str = "E") -> tuple[str, dict[str, str]]:
    """Replace entities with sentinels so a model cannot translate or reformat them."""
    mapping: dict[str, str] = {}
    protected = text or ""
    values = set()
    for found in extract(protected).values():
        values.update(found)
    values.update(_NAME.findall(protected))
    for value in sorted(values, key=len, reverse=True):
        token = _TOKEN.format(len(mapping) + 1)
        mapping[token] = value
        protected = protected.replace(value, token)
    return protected, mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    restored = text or ""
    for token, value in mapping.items():
        restored = restored.replace(token, value)
    return restored


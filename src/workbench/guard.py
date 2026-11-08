"""Deterministic outbound content checks.

The guard is deliberately independent from any language model. Platform-specific
terms belong in local configuration, not in this public module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from urllib.parse import urlparse


_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_SOFT_SEPARATORS = re.compile(r"[\s._·•・,，:：;；/\\|\-—–~～]+")
_URL = re.compile(r"(?i)(?:https?://|www\.)\S+|\b[a-z0-9][a-z0-9-]{0,62}(?:\.[a-z0-9][a-z0-9-]{0,62})+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\s()（）.·•・/\\|\-—–~～]*){7,15}(?!\d)")


@dataclass(frozen=True)
class GuardHit:
    rule: str
    source: str
    detail: str


@dataclass(frozen=True)
class GuardResult:
    verdict: str
    hits: tuple[GuardHit, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GuardPolicy:
    blocked_terms: tuple[str, ...] = ()
    blocked_patterns: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    block_urls: bool = True
    block_phone_numbers: bool = True
    block_qr_payloads: bool = True


def normalize(text: str) -> str:
    """Normalize common visual obfuscation without inventing replacement text."""
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).casefold()


def compact(text: str) -> str:
    return _SOFT_SEPARATORS.sub("", normalize(text))


def _domain_allowed(candidate: str, allowed: tuple[str, ...]) -> bool:
    value = candidate if "://" in candidate else f"https://{candidate}"
    hostname = (urlparse(value).hostname or "").casefold().rstrip(".")
    return any(hostname == domain.casefold() or hostname.endswith("." + domain.casefold()) for domain in allowed)


def check_text(
    text: str,
    policy: GuardPolicy,
    *,
    extracted_text: tuple[str, ...] = (),
    qr_payloads: tuple[str, ...] = (),
) -> GuardResult:
    """Check visible text plus caller-supplied OCR and QR decoder output."""
    hits: list[GuardHit] = []
    sources = (("body", text),) + tuple(("ocr", item) for item in extracted_text)
    patterns = []
    for pattern in policy.blocked_patterns:
        try:
            patterns.append(re.compile(pattern))
        except re.error:  # a broken local rule must not disable the guard
            continue

    for source, value in sources:
        normalized = normalize(value)
        collapsed = compact(value)
        if policy.block_phone_numbers and (_PHONE.search(normalized) or re.search(r"\d{7,15}", collapsed)):
            hits.append(GuardHit("contact.phone", source, "possible phone number"))
        if policy.block_urls:
            for match in _URL.finditer(normalized):
                candidate = match.group(0).rstrip(".,;:!?)]}")
                if not _domain_allowed(candidate, policy.allowed_domains):
                    hits.append(GuardHit("external.url", source, "non-allowlisted address"))
                    break
        for term in policy.blocked_terms:
            if compact(term) and compact(term) in collapsed:
                hits.append(GuardHit("term.blocked", source, "blocked configured term"))
                break
        if patterns and any(pattern.search(normalized) for pattern in patterns):
            hits.append(GuardHit("term.pattern", source, "blocked configured pattern"))

    if policy.block_qr_payloads:
        for payload in qr_payloads:
            if normalize(payload).strip():
                hits.append(GuardHit("media.qr", "qr", "non-empty QR payload"))

    return GuardResult("block" if hits else "pass", tuple(hits))


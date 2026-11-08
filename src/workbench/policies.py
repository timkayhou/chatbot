"""Platform-specific content policies loaded from local private configuration.

Rule dictionaries mention specific platforms, so they are never committed. The
public default is an empty generic policy; without local files the guard still
checks URLs, phone-like numbers, QR payloads and the global term list.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .guard import GuardPolicy


@dataclass(frozen=True)
class PlatformPolicy:
    id: str
    name: str
    version: str = ""
    blocked_terms: tuple[str, ...] = ()
    blocked_patterns: tuple[str, ...] = ()
    notes: str = ""


GENERIC = PlatformPolicy(id="generic", name="通用", version="builtin")


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def load_policies(directory: str | Path = "private/rules") -> dict[str, PlatformPolicy]:
    policies = {"generic": GENERIC}
    rules_dir = Path(directory)
    if not rules_dir.exists():
        return policies
    for path in sorted(rules_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        policy_id = str(data.get("id") or path.stem).strip()[:40]
        if not policy_id:
            continue
        patterns = []
        for pattern in _strings(data.get("blocked_patterns")):
            try:
                re.compile(pattern)
            except re.error:
                continue
            patterns.append(pattern)
        policies[policy_id] = PlatformPolicy(
            id=policy_id,
            name=str(data.get("name") or policy_id)[:60],
            version=str(data.get("version") or "")[:40],
            blocked_terms=_strings(data.get("blocked_terms")),
            blocked_patterns=tuple(patterns),
            notes=str(data.get("notes") or "")[:500],
        )
    return policies


def guard_for(policy: PlatformPolicy, base: GuardPolicy | None = None) -> GuardPolicy:
    """Merge a platform policy into the global guard settings."""
    base = base or GuardPolicy()
    return GuardPolicy(
        blocked_terms=tuple(dict.fromkeys((*base.blocked_terms, *policy.blocked_terms))),
        blocked_patterns=tuple(dict.fromkeys((*base.blocked_patterns, *policy.blocked_patterns))),
        allowed_domains=base.allowed_domains,
        block_urls=base.block_urls,
        block_phone_numbers=base.block_phone_numbers,
        block_qr_payloads=base.block_qr_payloads,
    )

"""Channel definitions.

Real platform names, colors and account hints belong in local private
configuration. The public defaults stay deliberately generic.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


DEFAULT_CHANNELS = (
    {"id": "channel-a", "name": "渠道 A", "accent": "#3B6EF6", "skin": "blue", "sort_order": 1},
    {"id": "channel-b", "name": "渠道 B", "accent": "#0F9D58", "skin": "green", "sort_order": 2},
    {"id": "channel-c", "name": "渠道 C", "accent": "#E0364A", "skin": "red", "sort_order": 3},
    {"id": "channel-d", "name": "渠道 D", "accent": "#6B4DFF", "skin": "violet", "sort_order": 4},
)

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _accent(value: object, fallback: str) -> str:
    text = str(value or "")
    return text if _COLOR.match(text) else fallback


def load_channels(path: str | Path = "private/channels.json") -> list[dict]:
    """Read the local channel list, falling back to neutral defaults."""
    defaults = [dict(item) for item in DEFAULT_CHANNELS]
    config = Path(path)
    if not config.exists():
        return defaults
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, list):
        return defaults
    channels: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        channel_id = str(item.get("id") or "").strip()[:40]
        if not channel_id:
            continue
        name = str(item.get("name") or channel_id).strip()[:60] or channel_id
        channels.append(
            {
                "id": channel_id,
                "name": name,
                "accent": _accent(item.get("accent"), defaults[min(index, 3)]["accent"]),
                "skin": str(item.get("skin") or "plain")[:20],
                "sort_order": int(item.get("sort_order") or index + 1),
            }
        )
    return channels or defaults

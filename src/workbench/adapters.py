"""Read-only channel adapter contract and connection state machine.

Real platform adapters are local, private integrations. This module only defines
the contract and the states the run center may display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


STATE_LABELS = {
    "not_installed": "未安装",
    "not_running": "未启动",
    "not_logged_in": "未登录",
    "logged_in": "已登录",
    "read_ok": "读取正常",
    "send_limited": "发送受限",
    "needs_human": "需要人工",
    "error": "异常",
    "simulated": "模拟",
    "unknown": "未知",
}

SEND_STATES = {"send_limited", "needs_human", "error"}


@dataclass(frozen=True)
class AdapterStatus:
    state: str
    detail: str = ""
    client_version: str = ""
    adapter: str = ""
    read_ok: bool = False
    send_ok: bool = False
    last_read_at: str | None = None

    def __post_init__(self) -> None:
        if self.state not in STATE_LABELS:
            raise ValueError(f"unknown connection state: {self.state}")


@dataclass(frozen=True)
class IncomingMessage:
    external_id: str
    customer_name: str
    body: str
    occurred_at: str
    locale: str = "zh-CN"


class ReadOnlyAdapter(Protocol):
    """A channel adapter may read, but never send."""

    channel_id: str

    def probe(self) -> AdapterStatus:
        ...

    def read_messages(self, limit: int = 20) -> list[IncomingMessage]:
        ...


@dataclass
class ConnectionManager:
    adapters: dict[str, ReadOnlyAdapter] = field(default_factory=dict)

    @classmethod
    def from_list(cls, adapters: list[ReadOnlyAdapter] | None = None) -> "ConnectionManager":
        return cls({adapter.channel_id: adapter for adapter in (adapters or [])})

    def probe(self) -> dict[str, AdapterStatus]:
        """Probe every registered adapter; failures become an error state, never raise."""
        result: dict[str, AdapterStatus] = {}
        for channel_id, adapter in self.adapters.items():
            try:
                result[channel_id] = adapter.probe()
            except Exception as error:  # a broken adapter must not break the panel
                result[channel_id] = AdapterStatus(
                    state="error",
                    detail=f"{type(error).__name__}: {error}",
                    adapter=type(adapter).__name__,
                )
        return result

    def read(self, channel_id: str, limit: int = 20) -> list[IncomingMessage]:
        adapter = self.adapters.get(channel_id)
        if adapter is None:
            raise KeyError(f"no adapter for channel: {channel_id}")
        return adapter.read_messages(limit)

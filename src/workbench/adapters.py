"""Read-only channel adapter contract and connection state machine.

Real platform adapters are local, private integrations. This module only defines
the contract and the states the run center may display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
import urllib.request


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
    message_id: str = ""


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


@dataclass
class HttpPollingAdapter:
    """Generic read-only adapter for the local mock platform or a private bridge.

    It can read a session state and new messages; it deliberately has no send
    or write method.
    """

    channel_id: str
    base_url: str
    token: str
    timeout: float = 5.0
    cursor: int = 0

    def _get(self, path: str, query: dict[str, object] | None = None) -> dict:
        url = f"{self.base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def probe(self) -> AdapterStatus:
        try:
            data = self._get("/session")
        except HTTPError as error:
            error.close()
            return AdapterStatus(
                state="error",
                detail=f"{type(error).__name__}: {error}",
                adapter="HttpPollingAdapter",
            )
        except (OSError, json.JSONDecodeError) as error:
            return AdapterStatus(
                state="error",
                detail=f"{type(error).__name__}: {error}",
                adapter="HttpPollingAdapter",
            )
        try:
            return AdapterStatus(
                state=str(data.get("state") or "unknown"),
                detail=str(data.get("detail") or ""),
                client_version=str(data.get("client_version") or ""),
                adapter="HttpPollingAdapter",
                read_ok=str(data.get("state")) == "read_ok",
                send_ok=False,
            )
        except ValueError as error:
            return AdapterStatus(state="error", detail=str(error), adapter="HttpPollingAdapter")

    def read_messages(self, limit: int = 20) -> list[IncomingMessage]:
        data = self._get("/messages", {"since": self.cursor, "limit": limit})
        messages = [
            IncomingMessage(
                external_id=str(item["external_id"]),
                customer_name=str(item["customer_name"]),
                body=str(item["body"]),
                occurred_at=str(item["occurred_at"]),
                locale=str(item.get("locale") or "zh-CN"),
                message_id=str(item.get("message_id") or ""),
            )
            for item in data.get("messages", [])
        ]
        self.cursor = int(data.get("cursor", self.cursor))
        return messages

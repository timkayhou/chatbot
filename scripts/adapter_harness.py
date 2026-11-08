"""Exercise the read-only adapter pipeline against the local mock platform."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.adapters import HttpPollingAdapter
from workbench.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Read mock messages through the adapter contract")
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--token", default="test-token")
    parser.add_argument("--database", default="data/adapter-demo.sqlite")
    parser.add_argument("--channel", default="mock-channel")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    store = Store(args.database)
    store.sync_channels(
        [
            {
                "id": args.channel,
                "name": "适配器测试渠道",
                "accent": "#556677",
                "skin": "plain",
                "sort_order": 90,
                "policy": "generic",
            }
        ]
    )
    adapter = HttpPollingAdapter(args.channel, args.url, args.token)
    status = adapter.probe()
    store.set_channel_connection(
        args.channel,
        state=status.state,
        detail=status.detail,
        client_version=status.client_version,
        adapter=status.adapter,
        read_ok=status.read_ok,
    )
    messages = adapter.read_messages(args.limit)
    inserted = store.ingest_messages(args.channel, [asdict(message) for message in messages])
    print(
        json.dumps(
            {
                "state": status.state,
                "detail": status.detail,
                "client_version": status.client_version,
                "read": len(messages),
                "inserted": inserted,
                "cursor": adapter.cursor,
                "sent": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

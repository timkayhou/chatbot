"""Serve the loopback mock platform for adapter development."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.mock_platform import create_mock_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the loopback mock platform")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--token", default="test-token")
    args = parser.parse_args()
    server = create_mock_server(args.port, args.token)
    print(f"Mock platform: http://127.0.0.1:{args.port} (token: {args.token})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

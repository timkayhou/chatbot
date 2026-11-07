"""Run a bounded loopback-only inference soak test."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * fraction) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8081/completion")
    parser.add_argument("--duration", type=int, default=7200)
    parser.add_argument("--tokens", type=int, default=64)
    parser.add_argument("--heartbeat", type=int, default=60)
    parser.add_argument("--api-key")
    parser.add_argument("--log", help="also append UTF-8 JSON lines to this file")
    args = parser.parse_args()

    log = open(args.log, "w", encoding="utf-8") if args.log else None

    def emit(event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False)
        print(line, flush=True)
        if log:
            log.write(line + "\n")
            log.flush()

    context = "你是本地电脑维修客服。只根据已审核资料回答，不承诺未确认的价格或时间。" * 18
    scenarios = (
        "电脑开机后风扇声音很大，而且偶尔自动关机。请给出安全的初步排查建议。",
        "笔记本无法连接无线网络。请说明客户可以先确认哪些项目。",
        "系统更新后启动变慢。请提供不涉及删除客户数据的检查步骤。",
        "客户询问维修时间，但当前没有排期资料。请给出谨慎答复。",
    )
    started = time.monotonic()
    next_heartbeat = started + args.heartbeat
    latencies: list[float] = []
    failures = 0
    run = 0

    while time.monotonic() - started < args.duration:
        run += 1
        prompt = f"{context}\n测试序号：{run}\n客户：{scenarios[run % len(scenarios)]}\n客服："
        payload = json.dumps(
            {
                "prompt": prompt,
                "n_predict": args.tokens,
                "temperature": 0,
                "ignore_eos": True,
                "cache_prompt": False,
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if args.api_key:
            headers["Authorization"] = f"Bearer {args.api_key}"
        request = urllib.request.Request(args.url, data=payload, headers=headers)
        request_started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.load(response)
            elapsed = time.perf_counter() - request_started
            if result.get("tokens_predicted") != args.tokens:
                raise RuntimeError("incomplete generation")
            latencies.append(elapsed)
        except Exception as error:  # keep the soak running to measure recovery
            failures += 1
            emit({"event": "failure", "run": run, "error": str(error)})

        now = time.monotonic()
        if now >= next_heartbeat:
            emit(
                {
                    "event": "heartbeat",
                    "elapsed_seconds": round(now - started),
                    "runs": run,
                    "failures": failures,
                    "last_seconds": round(latencies[-1], 3) if latencies else None,
                }
            )
            next_heartbeat = now + args.heartbeat

    summary = {
        "event": "complete",
        "duration_seconds": round(time.monotonic() - started),
        "runs": run,
        "successes": len(latencies),
        "failures": failures,
        "median_seconds": round(statistics.median(latencies), 3) if latencies else None,
        "p95_seconds": round(percentile(latencies, 0.95), 3) if latencies else None,
        "max_seconds": round(max(latencies), 3) if latencies else None,
    }
    emit(summary)
    if log:
        log.close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()

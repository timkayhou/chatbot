"""Read-only system readiness and local inference probes."""

from __future__ import annotations

import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def parse_soak_log(path: str | Path) -> dict:
    log_path = Path(path)
    if not log_path.exists():
        return {"state": "not_started", "runs": 0, "failures": 0, "elapsed_seconds": 0}
    latest: dict | None = None
    failure_events = 0
    latest_run = 0
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest_run = max(latest_run, int(event.get("run", event.get("runs", 0))))
        if event.get("event") == "failure":
            failure_events += 1
        if event.get("event") in {"heartbeat", "complete"}:
            latest = event
    if latest is None:
        return {"state": "starting", "runs": 0, "failures": 0, "elapsed_seconds": 0}
    failures = max(int(latest.get("failures", 0)), failure_events)
    state = "failed" if failures else ("passed" if latest["event"] == "complete" else "running")
    return {
        "state": state,
        "runs": max(int(latest.get("runs", 0)), latest_run),
        "failures": failures,
        "elapsed_seconds": latest.get("duration_seconds", latest.get("elapsed_seconds", 0)),
        "last_seconds": latest.get("last_seconds"),
        "p95_seconds": latest.get("p95_seconds"),
        "max_seconds": latest.get("max_seconds"),
    }


def _request(url: str, api_key: str, *, payload: dict | None = None, timeout: int = 10) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def inference_health(base_url: str, api_key: str) -> dict:
    try:
        result = _request(f"{base_url.rstrip('/')}/health", api_key, timeout=3)
        return {"state": "ready" if result.get("status") == "ok" else "attention", "detail": result.get("status", "unknown")}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        return {"state": "offline", "detail": str(error)}


def inference_test(base_url: str, api_key: str) -> dict:
    started = time.perf_counter()
    try:
        result = _request(
            f"{base_url.rstrip('/')}/completion",
            api_key,
            payload={
                "prompt": "Reply briefly to confirm the local inference service is available.",
                "n_predict": 16,
                "temperature": 0,
                "ignore_eos": True,
            },
            timeout=30,
        )
        return {
            "state": "passed",
            "seconds": round(time.perf_counter() - started, 3),
            "tokens": result.get("tokens_predicted", 0),
        }
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        return {"state": "failed", "detail": str(error)}


def alerts(
    *,
    inference: dict,
    soak: dict,
    backup_age_hours: float | None,
    db_bytes: int,
    pending_candidates: int = 0,
) -> list[dict]:
    """Local-only warnings shown at the top of the run center."""
    items: list[dict] = []
    if inference["state"] != "ready":
        items.append({"level": "danger", "text": "本地 AI 服务不可用，草稿会退回 FAQ 或手工填写"})
    if soak["state"] == "failed":
        items.append({"level": "danger", "text": "最近一次稳定性测试出现失败，请查看日志"})
    if backup_age_hours is None:
        items.append({"level": "warning", "text": "尚未创建数据库备份，建议运行 scripts/backup_db.py"})
    elif backup_age_hours > 168:
        items.append({"level": "warning", "text": f"最近一次备份在 {backup_age_hours / 24:.0f} 天前"})
    if db_bytes > 1_000_000_000:
        items.append({"level": "warning", "text": "数据库已超过 1 GB，建议检查数据保留策略"})
    if pending_candidates:
        items.append({"level": "info", "text": f"有 {pending_candidates} 条 FAQ 候选待处理"})
    return items


def readiness(
    soak: dict,
    inference: dict,
    backend: str = "local",
    *,
    approved_faq: int = 0,
    mock_channels: int = 0,
) -> dict:
    drafting_ready = inference["state"] == "ready"
    duration_seconds = int(soak.get("elapsed_seconds") or 0)
    long_soak_passed = soak["state"] == "passed" and duration_seconds >= 7200
    faq_state = "ready" if approved_faq >= 5 else ("attention" if approved_faq else "todo")
    items = [
        {"id": "workbench", "label": "本地人工工作台", "state": "ready", "detail": "粘贴、校验、批准和复制可用"},
        {"id": "guard", "label": "发送规则校验", "state": "ready", "detail": "文字、OCR 与二维码检查已接入"},
        {"id": "inference", "label": "本地 AI 服务", "state": inference["state"], "detail": f"{backend} · {inference['detail']}"},
        {
            "id": "soak",
            "label": f"本地推理稳定性（{backend}）",
            "state": soak["state"],
            "detail": f"{duration_seconds // 60} 分钟 · {soak['runs']} 次请求 · {soak['failures']} 次失败",
        },
        {
            "id": "drafting",
            "label": "AI 生成草稿",
            "state": "ready" if drafting_ready else "attention",
            "detail": "本地模型起草，人工编辑与校验" if drafting_ready else "需要本地 AI 服务在线",
        },
        {"id": "faq", "label": "FAQ 知识库", "state": faq_state, "detail": f"已审核 {approved_faq} 条，草稿可引用" if approved_faq else "尚未建立已审核业务答案"},
        {"id": "channels", "label": "真实消息渠道", "state": "todo", "detail": f"已配置 {mock_channels} 个模拟渠道；真实只读采集未接入"},
        {
            "id": "operations",
            "label": "长期运行保障",
            "state": "attention",
            "detail": "启动、备份、恢复演练、日志轮转和告警已实现；启动项现场验收待完成",
        },
        {
            "id": "release_soak",
            "label": "发布前 2 小时长稳",
            "state": "passed" if long_soak_passed else "todo",
            "detail": "已连续 2 小时无失败" if long_soak_passed else "正式启用前执行",
        },
    ]
    ready_count = sum(item["state"] in {"ready", "passed"} for item in items)
    return {
        "mode": "manual",
        "formal_ready": False,
        "ready_count": ready_count,
        "total_count": len(items),
        "items": items,
    }


"""Local chat completion that only ever produces a draft for human review."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request


SYSTEM_PROMPT = (
    "你是本地电脑维修店的客服助手，只负责起草回复，供人工审核后再决定是否使用。"
    "必须遵守：不承诺未确认的价格、库存、预约时间或保修范围；"
    "不要使用“免费”“保证”“一定”“包修”等承诺性措辞，也不要提及具体金额；"
    "价格符号（¥、￥、$ 等）、数字、单位、品牌与型号、编号和 URL 必须原样保留；"
    "不翻译品牌与型号，品牌名保持拉丁字母原形（如 Huawei 不写成 华为），不把 ¥ 改成其他货币符号；"
    "不索要也不提供联系方式、链接、二维码；"
    "不提供规避平台规则的写法；信息不足时直接说明需要人工确认。"
)

LANGUAGE_NAMES = {
    "zh-CN": "简体中文",
    "zh-TW": "繁体中文（必须全部使用繁体字，不得混入简体字）",
    "ja": "日语（商业客服敬语，です・ます体）",
    "en": "英语（自然、简洁的服务表达）",
}

MAX_INBOUND_CHARS = 4000
MAX_DRAFT_TOKENS = 320

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?(?:</think>|<｜end▁of▁thinking｜>)", re.DOTALL)


class GenerationUnavailable(Exception):
    """The local model is offline, timed out or returned unusable text."""


def build_messages(inbound: str, language: str, reference: str | None = None) -> list[dict[str, str]]:
    if language not in LANGUAGE_NAMES:
        raise ValueError("unsupported language")
    inbound = inbound.strip()
    if not inbound or len(inbound) > MAX_INBOUND_CHARS:
        raise ValueError("invalid inbound message")
    instruction = (
        f"客户当前语言是{LANGUAGE_NAMES[language]}。请用该语言回复，不要混用其他语言。"
        "只输出回复正文，不要解释你的思路，不超过 120 个汉字（其他语言不超过 80 词）。"
    )
    if reference:
        instruction += (
            f"\n下面是一条已审核的参考答案，请按客户语言改写后使用；"
            f"价格、数字、单位、产品型号和 URL 必须原样保留，不得添加未确认的信息：\n{reference}"
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{instruction}\n\n客户消息：\n{inbound}"},
    ]


def _clean(text: str) -> str:
    text = _THINK_BLOCK.sub("", text or "")
    return text.strip()


def generate_draft(
    base_url: str,
    api_key: str,
    inbound: str,
    language: str,
    *,
    reference: str | None = None,
    timeout: int = 90,
) -> dict:
    """Return a suggested reply. It is never persisted or sent from here."""
    payload = {
        "messages": build_messages(inbound, language, reference),
        "max_tokens": MAX_DRAFT_TOKENS,
        "temperature": 0.2,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        error.close()
        raise GenerationUnavailable(f"local model unavailable: {error}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationUnavailable(f"local model unavailable: {error}") from error

    try:
        draft = _clean(result["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as error:
        raise GenerationUnavailable("local model returned an unexpected response") from error
    if not draft:
        raise GenerationUnavailable("local model returned an empty draft")
    usage = result.get("usage") or {}
    return {
        "draft": draft,
        "seconds": round(time.perf_counter() - started, 3),
        "tokens": int(usage.get("completion_tokens") or 0),
    }

"""Seed clearly-labelled demo conversations for the simulated chat UI."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from workbench.channels import load_channels
from workbench.store import Store


DEMO = (
    {
        "customer": ("sim-wx-1", "陈雨", "zh-CN", ("老客户",), "模拟客户：2024 年来店清过灰"),
        "messages": (
            ("in", "你好，我的笔记本开机很慢，还经常卡住。"),
            ("out", "您好，可以先看看开机启动项和磁盘占用。方便发一下型号吗？"),
            ("in", "是 ThinkPad X1 Carbon，用了三年。"),
            ("out", "收到。建议先做磁盘检测；如果方便可以预约到店，检测后再给方案。"),
            ("in", "那周末可以吗？"),
        ),
        "repeat": ("预约需要提前多久？", "一般提前一天预约即可，具体时间以人工确认为准。"),
        "pending": "那周六上午可以吗？",
    },
    {
        "customer": ("sim-wx-2", "佐藤健", "ja", ("新客户",), "模拟客户：日本語対応"),
        "messages": (
            ("in", "ノートパソコンのファンがうるさくて、たまに電源が落ちます。"),
            ("out", "ご連絡ありがとうございます。まず熱とほこりの状態を確認したいので、型番を教えていただけますか。"),
            ("in", "Lenovo ThinkPad X1 Carbon です。"),
            ("out", "ありがとうございます。安全のため、電源を切ってから通気口を確認してください。詳しい点検は店頭で承ります。"),
        ),
    },
    {
        "customer": ("sim-wecom-1", "王经理", "zh-CN", ("企业客户",), "模拟客户：办公室 6 台设备"),
        "messages": (
            ("in", "办公室有几台电脑开机后黑屏，能上门看一下吗？"),
            ("out", "可以。请先确认电源和显示器连接是否正常，并告知大致数量和地址所在区域。"),
            ("in", "6 台，都是同一天出现的。"),
            ("out", "明白。这类情况常见于供电或系统更新问题，我们先远程判断，再安排上门。"),
        ),
        "repeat": ("预约需要提前多久？", "一般提前一天预约即可，具体时间以人工确认为准。"),
        "pending": "下午三点之后方便吗？",
    },
    {
        "customer": ("sim-wecom-2", "林小姐", "zh-TW", (), "模拟客戶：繁中回覆"),
        "messages": (
            ("in", "筆電更新系統後變得很慢，該怎麼處理？"),
            ("out", "您好，可以先確認更新是否完成，並檢查開機啟動項目。若仍有問題，建議到店檢測。"),
            ("in", "檢測需要收費嗎？"),
            ("out", "檢測與費用的細節需要由人工確認，我不會先給未確認的報價。"),
        ),
    },
    {
        "customer": ("sim-xhs-1", "小鹿", "zh-CN", ("小红书咨询",), "模拟客户：从笔记私信"),
        "messages": (
            ("in", "看到你们的笔记，想问下旧笔记本还能修吗？"),
            ("out", "要看具体故障。可以先描述现象和型号，我们判断是否值得修。"),
            ("in", "2019 年的 MacBook Air，电池不耐用了。"),
            ("out", "这类情况通常可以先做电池检测，再决定是否更换。具体方案和价格需要人工确认。"),
        ),
        "pending": "换电池大概要多久？",
    },
    {
        "customer": ("sim-xhs-2", "Momo", "zh-TW", (), "模拟客戶：私訊詢問"),
        "messages": (
            ("in", "請問你們有提供到府收件嗎？"),
            ("out", "目前是否需要到府收件需由人工確認，請先告知所在區域。"),
        ),
    },
    {
        "customer": ("sim-douyin-1", "阿凯", "zh-CN", ("短视频咨询",), "模拟客户：评论转私信"),
        "messages": (
            ("in", "视频里那个清灰多少钱？"),
            ("out", "清灰的具體價格需要依機型與狀況確認，我先幫您記錄型號。"),
            ("in", "联想小新 Pro 16。"),
            ("out", "收到。建議先確認風扇與散熱狀況，再給您方案；不會先承諾未確認的價格。"),
        ),
    },
    {
        "customer": ("sim-douyin-2", "Emma Wilson", "en", (), "Simulated customer: English"),
        "messages": (
            ("in", "Hi, do you repair laptops with a broken charging port?"),
            ("out", "Hello, yes, we can check it. Could you tell me the model and what happens when you plug in the charger?"),
            ("in", "It's a Dell XPS 13. The charger light stays off."),
            ("out", "Thanks. Please bring the laptop and charger so we can test the port and adapter before confirming any repair."),
        ),
        "pending": "How long would the check take?",
    },
)


def build(store: Store, force: bool = False) -> dict:
    channels = load_channels()
    store.sync_channels(channels)
    created = {"channels": 0, "conversations": 0, "messages": 0, "faqs": 0, "candidates": 0}
    now = datetime.now(UTC)
    demo_index = 0
    for channel in channels:
        if store.list_conversations(channel["id"]) and not force:
            continue
        created["channels"] += 1
        for _ in range(2):
            item = DEMO[demo_index % len(DEMO)]
            demo_index += 1
            external_id, name, locale, tags, note = item["customer"]
            customer_id = store.upsert_customer(
                channel["id"],
                external_id,
                name,
                avatar=name[:1].upper(),
                note=note,
                tags=tags,
                locale=locale,
                profile={"simulated": True, "channel": channel["id"]},
            )
            conversation_id = store.create_chat_conversation(channel["id"], customer_id, name, locale)
            created["conversations"] += 1
            base = now - timedelta(minutes=40 * (demo_index + 1))
            for offset, (direction, body) in enumerate(item["messages"]):
                store.add_message(
                    conversation_id,
                    direction,
                    body,
                    "mock",
                    created_at=(base + timedelta(minutes=offset * 7)).isoformat(),
                )
                created["messages"] += 1
            if item.get("repeat"):
                question, answer = item["repeat"]
                store.add_message(
                    conversation_id,
                    "in",
                    question,
                    "mock",
                    created_at=(base + timedelta(minutes=40)).isoformat(),
                )
                store.add_message(
                    conversation_id,
                    "out",
                    answer,
                    "mock",
                    created_at=(base + timedelta(minutes=42)).isoformat(),
                )
                created["messages"] += 2
            store.mark_read(conversation_id)
            if item.get("pending"):
                store.add_message(
                    conversation_id,
                    "in",
                    item["pending"],
                    "mock",
                    created_at=(base + timedelta(minutes=48)).isoformat(),
                )
                created["messages"] += 1
    if not store.list_faq():
        for question, answer, variants in (
            ("第一次到店维修需要带什么？", "带上设备和电源适配器即可；如有购买凭证或保修卡也请一并带上。", ["到店要带什么", "维修需要带什么"]),
            ("可以先远程判断问题吗？", "可以先描述现象，由客服判断是否需要到店检测；涉及硬件的问题仍需现场检查。", ["能不能远程看", "远程能判断吗"]),
        ):
            store.create_faq(
                category="示例",
                question=question,
                answer=answer,
                variants=variants,
                languages=["zh-CN"],
                reviewed=True,
            )
            created["faqs"] += 1
    created["candidates"] = store.build_faq_candidates()
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo conversations for the simulated chat UI")
    parser.add_argument("--database", default="data/workbench.sqlite")
    parser.add_argument("--force", action="store_true", help="seed again even if conversations exist")
    args = parser.parse_args()
    store = Store(args.database)
    print(json.dumps(build(store, args.force), ensure_ascii=False))


if __name__ == "__main__":
    main()

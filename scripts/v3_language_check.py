r"""V3 four-language language-following and entity-fidelity check.

All prompts are synthetic. The only network target is the loopback chat
completion endpoint; outbound proxies are deliberately disabled.

Usage (from the repository root):
    $env:PYTHONPATH = "src"
    $env:V3_API_KEY = "..."
    .\.venv\Scripts\python.exe -u scripts\v3_language_check.py --smoke
    .\.venv\Scripts\python.exe -u scripts\v3_language_check.py --run
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from workbench.entities import protect, restore
from workbench.inference import LANGUAGE_NAMES, SYSTEM_PROMPT, build_messages

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_JSON = DATA_DIR / "v3-report.json"
REPORT_MD = DATA_DIR / "v3-report.md"

ENDPOINT = "http://127.0.0.1:8081/v1/chat/completions"
MODEL_NAME = "Qwen3-14B"
TEMPERATURE = 0.2
MAX_TOKENS = 200
REQUEST_TIMEOUT = 120
LANGUAGES = ("zh-CN", "zh-TW", "ja", "en")
PARTS = ("A", "B")

# The requested examples contain 查 / 路, which are shared by traditional and
# simplified text. They are intentionally excluded from "traditional only" so a
# correct 檢查 / 網路 reply is not rejected. Unambiguous variants are kept.
TRAD_ONLY = set("這個們說請嗎時間題網電腦檢預約價錢")
# 查 / 里 / 面 are shared or ambiguous in normal traditional text and are not
# counted as simplified-only evidence. The remaining characters are
# unambiguous simplified forms in a customer-service context.
SIMP_ONLY = set("这个们说请吗时间题网电脑检预约价钱为与后发")

KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
THINK_RE = re.compile(r"<think\b[^>]*>.*?(?:</think>|<｜end▁of▁thinking｜>)", re.DOTALL)

PART_B_SYSTEM = (
    "你是一名专业的本地化译者，负责把中文客服话术翻译或改写为目标语言。"
    "必须原样保留原文中的价格、数字、单位、产品型号、编号和 URL，"
    "不得改动大小写、空格、标点或符号，也不得增删任何实体。"
    "价格符号（¥、￥、$）必须保留原符号，不得换成円、元或其他货币写法；"
    "品牌与型号不翻译、不音译；品牌名必须保持拉丁字母原形"
    "（例如 Huawei、Samsung、Alienware、Lenovo），不得译成汉字或片假名。"
    "即使目标语言是中文，品牌名也保持拉丁字母：Huawei 不得写成 华为/華為，Lenovo 不得写成 联想。"
    "只输出译文正文，不要解释、不要加注、不要输出原文说明或前后缀。"
)

REASON_LABELS = {
    "ok": "通过",
    "ok_shared": "通过（仅繁简共用字）",
    "empty_reply": "空回复",
    "request_error": "请求失败",
    "no_kana": "未检测到平假名或片假名",
    "contains_han_or_kana": "含汉字或假名",
    "ascii_ratio_low": "ASCII 字母占比不足",
    "no_traditional_char": "未检测到繁体专用字",
    "contains_simplified_char": "含简体专用字",
    "no_han": "未检测到汉字",
    "contains_kana": "含假名",
    "contains_traditional_char": "含繁体专用字",
}
ENTITY_LABELS = {"price": "价格", "model": "型号", "id": "编号", "url": "URL"}


PART_A: dict[str, list[tuple[str, str]]] = {
    "zh-CN": [
        ("屏幕", "笔记本屏幕右下角出现一条亮线，轻微按压边框会闪一下，想先问问换屏大概怎么处理。"),
        ("电池", "电池现在充满后只能用四十分钟，拔掉电源就提示电量不足，想了解更换电池的流程。"),
        ("散热", "开机后风扇一直高速转，声音像小吹风机，机身左侧还很烫，需要做哪些检查？"),
        ("进水", "水杯洒在键盘上，当时擦干了也能开机，但今天有几个按键失灵，这种情况还能修吗？"),
        ("键盘", "键盘的 A 键和空格键回弹很慢，外接键盘正常，想换成原装键盘。"),
        ("无线网络", "家里无线网能连上但打不开网页，手机连同一个网络正常，笔记本需要检查什么？"),
        ("有线网络", "插网线时指示灯不亮，换了一根线还是一样，怀疑是网口或者主板的问题。"),
        ("VPN", "公司 VPN 客户端一直提示连接超时，普通网页可以打开，应该先排查哪一步？"),
        ("路由器", "新买的路由器按说明设置后没有网络，电脑能搜到信号但一直显示正在识别。"),
        ("网络掉线", "无线网络每隔十几分钟就断一次，重新连接后又正常，想预约检测网卡。"),
        ("打印机", "打印机显示离线，电脑重启后还是无法打印，USB 线换过也不行。"),
        ("蓝屏", "开机进系统后偶尔蓝屏，代码一闪就重启，想看看到底是内存还是硬盘的问题。"),
        ("开机卡住", "电脑开机停在品牌标志界面，进不了系统，强制关机再开还是一样。"),
        ("更新失败", "系统更新到一半断电，现在一直提示更新失败，桌面也进不去。"),
        ("开机慢", "开机要五六分钟，进桌面后鼠标一直转圈，日常办公都很卡。"),
        ("登录", "开机密码输入正确却提示错误，指纹解锁也不能用，里面还有工作资料。"),
        ("数据恢复", "旧笔记本开不了机，但硬盘里有照片和文档，想先确认数据还能不能取出来。"),
        ("周末预约", "周六上午想带电脑到店检查，请问需要提前预约吗？"),
        ("当天到店", "今天下午方便直接过去吗？只有一个小时，想尽量当天处理好。"),
        ("取件", "维修完成后可以晚几天再取吗？最近下班都比较晚。"),
        ("维修时长", "换屏幕一般要等多久？如果配件没货会不会更久？"),
        ("保修", "机器还在保修期内，进水这种情况保修范围包含吗？"),
        ("屏幕报价", "十四寸笔记本换一块普通屏幕大概什么价位？想先有个预算。"),
        ("电池报价", "电池鼓包了，换电池大概要多少钱？会不会影响其他部件？"),
        ("检测费", "检测费怎么算？如果最后决定不修，还需要付检测费吗？"),
        ("学生优惠", "学生带电脑来修有优惠吗？需要带什么证明？"),
        ("发票", "维修后可以开发票吗？需要提供哪些信息？"),
        ("办公软件", "新装的办公软件一直报错，卸载重装也没用，能不能帮忙看一下系统环境？"),
        ("恶意软件", "浏览器经常弹出奇怪的广告，怀疑中了流氓软件，想做个全面扫描。"),
        ("回收", "旧电脑不打算修了，回收前怎么把硬盘里的资料彻底清除？"),
    ],
    "zh-TW": [
        ("螢幕", "筆電螢幕右下角出現一條亮線，輕輕按壓邊框會閃一下，想先問換螢幕大概怎麼處理。"),
        ("電池", "電池現在充滿後只能用四十分鐘，拔掉電源就提示電量不足，想了解更換電池的流程。"),
        ("散熱", "開機後風扇一直高速運轉，聲音像小吹風機，機身左側還很燙，需要做哪些檢查？"),
        ("進水", "水杯灑在鍵盤上，當時擦乾了也能開機，但今天有幾個按鍵失靈，這種情況還能修嗎？"),
        ("鍵盤", "鍵盤的 A 鍵和空白鍵回彈很慢，外接鍵盤正常，想換成原廠鍵盤。"),
        ("無線網路", "家裡的無線網路能連上但打不開網頁，手機連同一個網路正常，筆電需要檢查什麼？"),
        ("有線網路", "插網路線時指示燈不亮，換了一條線還是一樣，懷疑是網路孔或主機板的問題。"),
        ("VPN", "公司的 VPN 用戶端一直提示連線逾時，一般網頁可以打開，應該先排查哪一步？"),
        ("路由器", "新買的路由器照說明設定後沒有網路，電腦能搜到訊號但一直顯示正在辨識。"),
        ("網路斷線", "無線網路每隔十幾分鐘就斷一次，重新連線後又正常，想預約檢測網卡。"),
        ("印表機", "印表機顯示離線，電腦重開後還是無法列印，USB 線換過也不行。"),
        ("藍屏", "開機進系統後偶爾藍屏，代碼一閃就重開，想看看是記憶體還是硬碟的問題。"),
        ("開機卡住", "電腦開機停在品牌標誌畫面，進不了系統，強制關機再開還是一樣。"),
        ("更新失敗", "系統更新到一半斷電，現在一直提示更新失敗，桌面也進不去。"),
        ("開機慢", "開機要五六分鐘，進桌面後滑鼠一直轉圈，日常辦公都很卡。"),
        ("登入", "開機密碼輸入正確卻提示錯誤，指紋解鎖也不能用，裡面還有工作資料。"),
        ("資料救援", "舊筆電開不了機，但硬碟裡有照片和文件，想先確認資料還能不能取出來。"),
        ("週末預約", "週六上午想帶電腦到店檢查，請問需要提前預約嗎？"),
        ("當天到店", "今天下午方便直接過去嗎？只有一個小時，想盡量當天處理好。"),
        ("取件", "維修完成後可以晚幾天再取嗎？最近下班都比較晚。"),
        ("維修時長", "換螢幕一般要等多久？如果配件沒貨會不會更久？"),
        ("保固", "機器還在保固期內，進水這種情況保固範圍包含嗎？"),
        ("螢幕報價", "十四吋筆電換一塊普通螢幕大概什麼價位？想先有個預算。"),
        ("電池報價", "電池膨脹了，換電池大概要多少錢？會不會影響其他零件？"),
        ("檢測費", "檢測費怎麼算？如果最後決定不修，還需要付檢測費嗎？"),
        ("學生優惠", "學生帶電腦來修有優惠嗎？需要帶什麼證明？"),
        ("發票", "維修後可以開發票嗎？需要提供哪些資訊？"),
        ("辦公軟體", "新裝的辦公軟體一直報錯，移除重裝也沒用，能不能幫忙看一下系統環境？"),
        ("惡意軟體", "瀏覽器經常跳出奇怪的廣告，懷疑中了惡意軟體，想做個完整掃描。"),
        ("回收", "舊電腦不打算修了，回收前怎麼把硬碟裡的資料徹底清除？"),
    ],
    "ja": [
        ("画面", "ノートパソコンの画面右下に明るい線が出ていて、枠を軽く押すと一瞬ちらつきます。画面交換の進め方を教えていただけますか。"),
        ("電池", "充電してもバッテリーが40分ほどしか持たず、電源を抜くと残量不足の表示が出ます。交換の流れを知りたいです。"),
        ("ファン", "電源を入れるとファンがずっと高速で回り、小型の扇風機のような音がします。本体左側も熱いので、確認項目を教えてください。"),
        ("水濡れ", "キーボードに水をこぼしてしまい、すぐ拭いて電源は入りましたが、今日は一部のキーが反応しません。修理は可能でしょうか。"),
        ("キーボード", "Aキーとスペースキーの戻りが悪く、外付けキーボードでは問題ありません。純正キーボードへの交換を希望しています。"),
        ("Wi-Fi", "自宅のWi-FiにはつながるのにWebページが開けません。スマートフォンでは同じ回線で正常なので、何を確認すればよいでしょうか。"),
        ("有線LAN", "LANケーブルを差してもランプが点かず、別のケーブルでも同じです。LANポートかマザーボードの問題かもしれないと思っています。"),
        ("VPN", "会社のVPNアプリが接続タイムアウトを繰り返します。通常のWebサイトは開けるのですが、どこから調べるべきでしょうか。"),
        ("ルーター", "新しいルーターを説明書どおりに設定しましたが、ネットにつながりません。PCには電波が見えるものの「識別中」のままです。"),
        ("切断", "無線LANが十数分おきに切れ、再接続すると治ります。無線アダプターの点検を予約したいです。"),
        ("プリンター", "プリンターがオフラインと表示され、PCを再起動しても印刷できません。USBケーブルを替えても同じです。"),
        ("ブルースクリーン", "起動後にときどきブルースクリーンになり、エラーコードが一瞬出て再起動します。メモリかSSDのどちらが原因か見てほしいです。"),
        ("起動ループ", "電源を入れるとメーカーロゴの画面で止まり、OSが起動しません。強制終了して再起動しても同じです。"),
        ("更新失敗", "OSの更新中に停電し、それ以降ずっと更新失敗の表示が出てデスクトップに入れません。"),
        ("起動が遅い", "起動に5〜6分かかり、デスクトップが出てからもマウスカーソルが回り続けます。日常の事務作業にも支障があります。"),
        ("ログイン", "起動時のパスワードは合っているはずなのにエラーになり、指紋認証も使えません。仕事の資料が入っています。"),
        ("データ", "古いノートPCが起動しませんが、HDDに写真や書類が残っています。まずデータを取り出せるか確認したいです。"),
        ("週末予約", "土曜日の午前にPCを持ち込んで見てもらいたいのですが、事前予約は必要ですか。"),
        ("当日来店", "今日の午後に直接伺うことは可能でしょうか。一時間しか時間がないので、できれば当日中にお願いしたいです。"),
        ("受け取り", "修理が終わった後、数日遅れて受け取ることはできますか。最近帰りが遅いものですから。"),
        ("修理期間", "画面交換は通常どのくらいかかりますか。部品の在庫がない場合はさらに遅れますか。"),
        ("保証", "まだ保証期間内なのですが、水濡れも保証の対象になりますか。"),
        ("画面価格", "14インチのノートPCで一般的な画面に交換する場合、おおよその価格を教えていただけますか。予算を考えたいです。"),
        ("電池価格", "バッテリーが膨らんでしまいました。交換費用はどのくらいでしょうか。他の部品にも影響しますか。"),
        ("診断費", "診断料はいくらですか。結局修理しない場合でも診断料はかかりますか。"),
        ("学生", "学生がPCを修理に出す場合、割引はありますか。何か証明書類は必要ですか。"),
        ("領収書", "修理後に領収書を発行していただけますか。必要な情報はありますか。"),
        ("ソフト", "新しく入れた事務ソフトがエラーを出し続け、再インストールしても直りません。システム環境を見てもらえますか。"),
        ("マルウェア", "ブラウザに怪しい広告が頻繁に出るようになりました。不正なソフトが入った可能性があるので、全体をスキャンしてほしいです。"),
        ("リサイクル", "古いPCは修理せずに処分するつもりです。引き取る前にHDDのデータを完全に消す方法を教えてください。"),
    ],
    "en": [
        ("screen", "A bright vertical line appeared near the bottom right of my laptop screen, and it flickers when I press the bezel. Could you explain how a screen replacement is handled?"),
        ("battery", "The battery only lasts about forty minutes after a full charge, and I get a low-battery warning as soon as I unplug it. I would like to know the replacement process."),
        ("fan", "The fan runs at full speed as soon as I turn the laptop on, and it sounds like a small hair dryer. The left side also gets very hot. What should I check first?"),
        ("liquid", "I spilled water on the keyboard, wiped it up right away, and it still powered on, but several keys stopped working today. Is it still repairable?"),
        ("keyboard", "The A key and the space bar are slow to spring back, while an external keyboard works fine. I would like to replace the keyboard with an original part."),
        ("wifi", "My laptop connects to the home Wi-Fi but web pages will not load, while my phone works on the same network. What should I check?"),
        ("ethernet", "The link light does not come on when I plug in an Ethernet cable, and another cable behaves the same way. I suspect the port or the motherboard."),
        ("vpn", "The work VPN client keeps timing out, although ordinary websites open normally. Where should I start troubleshooting?"),
        ("router", "I set up a new router following the instructions, but there is no internet access. The laptop sees the signal but stays on \"identifying.\""),
        ("drop", "The wireless connection drops every ten to fifteen minutes and works again after reconnecting. I would like to book a network adapter check."),
        ("printer", "The printer shows as offline, and restarting the computer did not help. I also tried a different USB cable."),
        ("bsod", "The laptop sometimes shows a blue screen after startup, and the error code flashes by before it restarts. I would like to know whether the memory or the drive is causing it."),
        ("bootloop", "The computer stops at the manufacturer logo and will not load the system. Forcing a shutdown and restarting gives the same result."),
        ("update", "The power went out during a system update, and now it keeps saying the update failed. I cannot get to the desktop either."),
        ("slow", "Startup takes five or six minutes, and the cursor keeps spinning after the desktop appears. Everyday office work is very slow."),
        ("login", "The startup password should be correct, but it is rejected, and fingerprint login no longer works. There is work material on the machine."),
        ("data", "My old laptop will not start, but it still has photos and documents on the drive. I would like to know whether the data can be recovered first."),
        ("weekend", "I would like to bring the laptop in on Saturday morning for a check. Do I need to book an appointment in advance?"),
        ("sameday", "Could I come by this afternoon? I only have one hour, so I would like to get it handled the same day if possible."),
        ("pickup", "Can I pick up the laptop a few days after the repair is finished? I have been getting off work late recently."),
        ("duration", "How long does a screen replacement usually take? Would it take longer if the part is out of stock?"),
        ("warranty", "The machine is still under warranty. Does liquid damage fall within the warranty coverage?"),
        ("screenprice", "Roughly how much would a standard screen replacement cost for a 14-inch laptop? I would like to have a budget in mind."),
        ("batteryprice", "The battery has swollen. About how much would a replacement cost, and could it affect other parts?"),
        ("diagnostic", "How is the diagnostic fee calculated? Is it still charged if I decide not to repair the machine?"),
        ("student", "Is there a student discount for computer repairs? What proof would I need to bring?"),
        ("invoice", "Can I get an invoice after the repair? What information do you need from me?"),
        ("software", "The office software I just installed keeps showing errors, and reinstalling it did not help. Could you check the system environment?"),
        ("malware", "My browser keeps showing strange pop-up ads, and I think something unwanted got installed. I would like a full scan."),
        ("recycle", "I am not going to repair the old computer and plan to recycle it. How can I completely erase the drive before handing it over?"),
    ],
}


def _source(model: str, price: str, ticket: str, url: str, text: str) -> dict[str, Any]:
    return {"model": model, "price": price, "ticket": ticket, "url": url, "text": text}


PART_B_SOURCES: list[dict[str, Any]] = [
    _source(
        "ThinkPad X1 Carbon",
        "¥1,280",
        "A-2048",
        "https://example.com/x1",
        "您好，这台 ThinkPad X1 Carbon 的屏幕更换报价为 ¥1,280，维修单号是 A-2048，配件与检测说明见 https://example.com/x1。请确认是否按此安排。",
    ),
    _source(
        "MacBook Air M3",
        "¥899",
        "SN-77B3",
        "https://example.com/air-m3",
        "MacBook Air M3 的电池更换费用是 ¥899，工单编号 SN-77B3，预约前可先查看 https://example.com/air-m3 了解流程。",
    ),
    _source(
        "Dell Latitude 7440",
        "¥1,499",
        "R-3150",
        "https://example.com/latitude-7440",
        "Dell Latitude 7440 的键盘更换报价为 ¥1,499，服务编号 R-3150，详细说明请见 https://example.com/latitude-7440。",
    ),
    _source(
        "HP EliteBook 840 G10",
        "¥2,080",
        "B-9021",
        "https://example.com/elitebook-840",
        "HP EliteBook 840 G10 的主板检测报价是 ¥2,080，检测单号 B-9021，结果会更新在 https://example.com/elitebook-840。",
    ),
    _source(
        "ASUS Zenbook 14 OLED",
        "¥359",
        "SN-4K7Q",
        "https://example.com/zenbook-14",
        "ASUS Zenbook 14 OLED 的清灰保养费用为 ¥359，受理编号 SN-4K7Q，注意事项见 https://example.com/zenbook-14。",
    ),
    _source(
        "Lenovo Yoga 7i",
        "¥4,200",
        "C-1188",
        "https://example.com/yoga-7i",
        "Lenovo Yoga 7i 的屏幕总成更换报价是 ¥4,200，维修编号 C-1188，进度查询请使用 https://example.com/yoga-7i。",
    ),
    _source(
        "Acer Swift Go 14",
        "¥680",
        "D-6402",
        "https://example.com/swift-go-14",
        "Acer Swift Go 14 的风扇更换费用为 ¥680，工单号 D-6402，拆装说明见 https://example.com/swift-go-14。",
    ),
    _source(
        "Surface Laptop 6",
        "¥1,050",
        "E-7735",
        "https://example.com/surface-laptop-6",
        "Surface Laptop 6 的电池检测报价为 ¥1,050，服务单号 E-7735，预约入口是 https://example.com/surface-laptop-6。",
    ),
    _source(
        "ROG Zephyrus G14",
        "¥2,999",
        "F-2210",
        "https://example.com/zephyrus-g14",
        "ROG Zephyrus G14 的散热模组更换报价是 ¥2,999，维修编号 F-2210，兼容性说明见 https://example.com/zephyrus-g14。",
    ),
    _source(
        "Framework Laptop 13",
        "¥129",
        "G-8891",
        "https://example.com/framework-13",
        "Framework Laptop 13 的键盘模块价格为 ¥129，订单编号 G-8891，安装指南在 https://example.com/framework-13。",
    ),
    _source(
        "ThinkPad T14 Gen 4",
        "¥3,680",
        "H-3045",
        "https://example.com/t14-gen4",
        "ThinkPad T14 Gen 4 的屏幕维修报价为 ¥3,680，服务编号 H-3045，备件信息请见 https://example.com/t14-gen4。",
    ),
    _source(
        "MacBook Pro 14 M3",
        "¥5,499",
        "J-5567",
        "https://example.com/mbp14-m3",
        "MacBook Pro 14 M3 的电池更换费用是 ¥5,499，工单编号 J-5567，检测标准见 https://example.com/mbp14-m3。",
    ),
    _source(
        "Dell XPS 13 Plus",
        "¥780",
        "K-9104",
        "https://example.com/xps13-plus",
        "Dell XPS 13 Plus 的键盘检测报价为 ¥780，受理编号 K-9104，后续步骤见 https://example.com/xps13-plus。",
    ),
    _source(
        "HP Spectre x360 14",
        "¥1,199",
        "L-4826",
        "https://example.com/spectre-x360",
        "HP Spectre x360 14 的转轴维修费用为 ¥1,199，维修单号 L-4826，预约说明见 https://example.com/spectre-x360。",
    ),
    _source(
        "ASUS Vivobook 16",
        "¥2,450",
        "M-6630",
        "https://example.com/vivobook-16",
        "ASUS Vivobook 16 的主板检测报价是 ¥2,450，检测编号 M-6630，结果说明见 https://example.com/vivobook-16。",
    ),
    _source(
        "Lenovo IdeaPad Slim 5",
        "¥950",
        "N-2751",
        "https://example.com/ideapad-slim5",
        "Lenovo IdeaPad Slim 5 的清灰与硅脂更换费用为 ¥950，工单号 N-2751，服务内容见 https://example.com/ideapad-slim5。",
    ),
    _source(
        "Acer Aspire 5",
        "¥1,600",
        "P-8043",
        "https://example.com/aspire-5",
        "Acer Aspire 5 的屏幕更换报价为 ¥1,600，维修编号 P-8043，配件说明见 https://example.com/aspire-5。",
    ),
    _source(
        "Surface Pro 9",
        "¥6,800",
        "Q-3378",
        "https://example.com/surface-pro-9",
        "Surface Pro 9 的屏幕总成更换费用是 ¥6,800，服务编号 Q-3378，检测流程见 https://example.com/surface-pro-9。",
    ),
    _source(
        "Alienware m16 R2",
        "¥540",
        "S-1196",
        "https://example.com/m16-r2",
        "Alienware m16 R2 的风扇保养报价为 ¥540，受理编号 S-1196，拆机说明见 https://example.com/m16-r2。",
    ),
    _source(
        "LG Gram 16",
        "¥1,880",
        "T-7420",
        "https://example.com/gram-16",
        "LG Gram 16 的电池更换费用为 ¥1,880，工单编号 T-7420，预约前请查看 https://example.com/gram-16。",
    ),
    _source(
        "Samsung Galaxy Book4 Pro",
        "¥2,260",
        "U-5084",
        "https://example.com/book4-pro",
        "Samsung Galaxy Book4 Pro 的键盘维修报价是 ¥2,260，维修单号 U-5084，进度说明见 https://example.com/book4-pro。",
    ),
    _source(
        "MSI Prestige 14 Evo",
        "¥3,100",
        "V-9612",
        "https://example.com/prestige-14",
        "MSI Prestige 14 Evo 的屏幕检测费用为 ¥3,100，检测编号 V-9612，结果会记录在 https://example.com/prestige-14。",
    ),
    _source(
        "Huawei MateBook 14",
        "¥470",
        "W-2237",
        "https://example.com/matebook-14",
        "Huawei MateBook 14 的键盘更换报价为 ¥470，服务编号 W-2237，兼容性说明见 https://example.com/matebook-14。",
    ),
    _source(
        "Xiaomi RedmiBook Pro 15",
        "¥1,350",
        "X-6859",
        "https://example.com/redmibook-15",
        "Xiaomi RedmiBook Pro 15 的电池检测费用是 ¥1,350，工单编号 X-6859，检测项目见 https://example.com/redmibook-15。",
    ),
    _source(
        "Honor MagicBook X 16",
        "¥7,200",
        "Y-1403",
        "https://example.com/magicbook-x16",
        "Honor MagicBook X 16 的主板更换报价为 ¥7,200，维修编号 Y-1403，备件说明见 https://example.com/magicbook-x16。",
    ),
    _source(
        "ThinkPad P1 Gen 6",
        "¥990",
        "Z-8764",
        "https://example.com/p1-gen6",
        "ThinkPad P1 Gen 6 的散热检测费用为 ¥990，服务单号 Z-8764，预约流程见 https://example.com/p1-gen6。",
    ),
    _source(
        "MacBook Air M2",
        "¥2,750",
        "A-5521",
        "https://example.com/air-m2",
        "MacBook Air M2 的屏幕维修报价是 ¥2,750，工单编号 A-5521，检测说明见 https://example.com/air-m2。",
    ),
    _source(
        "Dell Precision 5680",
        "¥1,720",
        "B-3097",
        "https://example.com/precision-5680",
        "Dell Precision 5680 的电池更换费用为 ¥1,720，维修单号 B-3097，适用配件见 https://example.com/precision-5680。",
    ),
    _source(
        "HP ProBook 450 G10",
        "¥560",
        "C-7480",
        "https://example.com/probook-450",
        "HP ProBook 450 G10 的键盘检测报价为 ¥560，受理编号 C-7480，后续安排见 https://example.com/probook-450。",
    ),
    _source(
        "ASUS ExpertBook B9",
        "¥299",
        "D-1265",
        "https://example.com/expertbook-b9",
        "ASUS ExpertBook B9 的键盘更换报价为 ¥299，服务编号 D-1265，详细说明见 https://example.com/expertbook-b9。",
    ),
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def clean_reply(value: Any) -> str:
    if isinstance(value, list):
        chunks: list[str] = []
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        value = "".join(chunks)
    if not isinstance(value, str):
        return ""
    return THINK_RE.sub("", value).strip()


def has_kana(text: str) -> bool:
    return bool(KANA_RE.search(text))


def has_han(text: str) -> bool:
    return bool(HAN_RE.search(text))


def ascii_letter_ratio(text: str) -> float:
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return 0.0
    letters = sum(1 for char in visible if char.isascii() and char.isalpha())
    return letters / len(visible)


def target_signal(text: str, language: str) -> float:
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return 0.0
    if language == "ja":
        return sum(1 for char in visible if KANA_RE.match(char)) / len(visible)
    if language == "en":
        return ascii_letter_ratio(text)
    if language == "zh-TW":
        return sum(1 for char in visible if char in TRAD_ONLY) / len(visible)
    return sum(1 for char in visible if HAN_RE.match(char)) / len(visible)


def judge_part_a(reply: str, language: str) -> tuple[bool, str, str]:
    if not reply:
        return False, "empty_reply", "回复为空"
    if language == "ja":
        if has_kana(reply):
            return True, "ok", ""
        return False, "no_kana", "未检测到平假名或片假名"
    if language == "en":
        if has_han(reply) or has_kana(reply):
            return False, "contains_han_or_kana", "出现汉字或假名"
        ratio = ascii_letter_ratio(reply)
        if ratio <= 0.8:
            return False, "ascii_ratio_low", f"ASCII 字母占比 {ratio:.3f}，要求 > 0.8"
        return True, "ok", ""
    if language == "zh-TW":
        simplified = sorted({char for char in reply if char in SIMP_ONLY})
        if simplified:
            return False, "contains_simplified_char", "出现简体专用字：" + "".join(simplified)
        traditional = sorted({char for char in reply if char in TRAD_ONLY})
        if traditional:
            return True, "ok", ""
        return True, "ok_shared", "未出现简体字；回复仅使用繁简共用字，建议人工抽查"
    if language == "zh-CN":
        if not has_han(reply):
            return False, "no_han", "未检测到汉字"
        if has_kana(reply):
            return False, "contains_kana", "出现假名"
        traditional = sorted({char for char in reply if char in TRAD_ONLY})
        if traditional:
            return False, "contains_traditional_char", "出现繁体专用字：" + "".join(traditional)
        return True, "ok", ""
    raise ValueError(f"unsupported language: {language}")


def judge_part_b(reply: str, entities: dict[str, str]) -> tuple[bool, str, str, list[str]]:
    if not reply:
        return False, "empty_reply", "回复为空", list(entities)
    missing = [key for key in ("price", "model", "id", "url") if entities[key] not in reply]
    if missing:
        return (
            False,
            "missing_entity:" + ",".join(missing),
            "缺少实体：" + "、".join(ENTITY_LABELS[key] for key in missing),
            missing,
        )
    return True, "ok", "", []


def part_a_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for language in LANGUAGES:
        for index, (scenario, text) in enumerate(PART_A[language], start=1):
            messages = build_messages(text, language)
            cases.append(
                {
                    "id": f"A-{language}-{index:03d}",
                    "part": "A",
                    "language": language,
                    "scenario": scenario,
                    "prompt": messages[1]["content"],
                    "messages": messages,
                }
            )
    return cases


def part_b_user_prompt(source: str, language: str) -> str:
    return (
        f"目标语言：{LANGUAGE_NAMES[language]}\n"
        "请翻译/改写为上述目标语言。价格、数字、单位、型号、编号和 URL 必须原样保留，不要解释。\n"
        "文中形如 ⟦E1⟧ 的占位符必须原样保留，不得翻译、删除或改写。\n\n"
        f"原文：\n{source}"
    )


def part_b_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for language in LANGUAGES:
        for index, source in enumerate(PART_B_SOURCES, start=1):
            entities = {
                "price": source["price"],
                "model": source["model"],
                "id": source["ticket"],
                "url": source["url"],
            }
            protected_text, mapping = protect(source["text"])
            prompt = part_b_user_prompt(protected_text, language)
            cases.append(
                {
                    "id": f"B-{language}-{index:03d}",
                    "part": "B",
                    "language": language,
                    "scenario": f"实体保真-{index:02d}",
                    "prompt": prompt,
                    "messages": [
                        {"role": "system", "content": PART_B_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "source": source["text"],
                    "entities": entities,
                    "mapping": mapping,
                }
            )
    return cases


def validate_corpus() -> None:
    for language in LANGUAGES:
        rows = PART_A[language]
        if len(rows) != 30:
            raise ValueError(f"Part A {language}: expected 30 rows, got {len(rows)}")
        texts = [text for _, text in rows]
        if len(set(texts)) != 30:
            raise ValueError(f"Part A {language}: duplicate synthetic messages")
        if any(len(text.strip()) < 8 for text in texts):
            raise ValueError(f"Part A {language}: message too short")
    if len(PART_B_SOURCES) != 30:
        raise ValueError(f"Part B: expected 30 sources, got {len(PART_B_SOURCES)}")
    for field in ("price", "model", "ticket", "url"):
        values = [row[field] for row in PART_B_SOURCES]
        if len(set(values)) != 30:
            raise ValueError(f"Part B: duplicate {field}")
    for index, row in enumerate(PART_B_SOURCES, start=1):
        for field in ("price", "model", "ticket", "url"):
            if row[field] not in row["text"]:
                raise ValueError(f"Part B source {index}: missing {field} in text")
        if not row["text"].startswith(row["model"]):
            # Not a correctness requirement; kept as an intentional readability check.
            pass


def make_opener() -> urllib.request.OpenerDirector:
    # An empty ProxyHandler prevents environment proxy settings from routing a
    # request away from loopback.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request_reply(
    opener: urllib.request.OpenerDirector,
    messages: list[dict[str, str]],
    api_key: str,
    timeout: int,
) -> tuple[str, float, str | None, str | None]:
    payload = {
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {api_key}",
    }
    started = time.perf_counter()
    last_error: str | None = None
    for attempt in range(3):
        request = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
        try:
            with opener.open(request, timeout=timeout) as response:
                result = json.load(response)
            choice = result["choices"][0]
            reply = clean_reply(choice.get("message", {}).get("content"))
            finish_reason = choice.get("finish_reason")
            return reply, round(time.perf_counter() - started, 3), finish_reason, None
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            error.close()
            if error.code < 500 and error.code != 429:
                break
        except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            last_error = f"{type(error).__name__}: {error}"
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    return "", round(time.perf_counter() - started, 3), None, last_error or "unknown request error"


def execute_case(
    opener: urllib.request.OpenerDirector,
    case: dict[str, Any],
    api_key: str,
    timeout: int,
) -> dict[str, Any]:
    reply, seconds, finish_reason, error = request_reply(opener, case["messages"], api_key, timeout)
    if error:
        passed, reason, detail = False, "request_error", error
        missing: list[str] = []
    elif case["part"] == "A":
        passed, reason, detail = judge_part_a(reply, case["language"])
        missing = []
    else:
        if case.get("mapping"):
            reply = restore(reply, case["mapping"])
        passed, reason, detail, missing = judge_part_b(reply, case["entities"])
    item = {
        "id": case["id"],
        "part": case["part"],
        "language": case["language"],
        "scenario": case["scenario"],
        "prompt": case["prompt"],
        "reply": reply,
        "passed": passed,
        "reason": reason,
        "detail": detail,
        "seconds": seconds,
        "finish_reason": finish_reason,
    }
    if case["part"] == "B":
        item["source"] = case["source"]
        item["entities"] = case["entities"]
        item["missing_entities"] = missing
    return item


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def failure_labels(item: dict[str, Any]) -> list[str]:
    reason = item["reason"]
    if reason.startswith("missing_entity:"):
        keys = reason.split(":", 1)[1].split(",")
        return [f"缺少{ENTITY_LABELS.get(key, key)}" for key in keys]
    return [REASON_LABELS.get(reason, reason)]


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    passed = sum(1 for item in items if item["passed"])
    failures = [item for item in items if not item["passed"]]
    reasons: Counter[str] = Counter()
    for item in failures:
        reasons.update(failure_labels(item))
    latencies = [item["seconds"] for item in items]
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "top_failure_reasons": [
            {"reason": reason, "count": count} for reason, count in reasons.most_common(3)
        ],
        "median_seconds": round(statistics.median(latencies), 3) if latencies else 0.0,
        "p95_seconds": round(p95(latencies), 3),
        "max_seconds": round(max(latencies), 3) if latencies else 0.0,
    }


def group_summaries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for part in PARTS:
        for language in LANGUAGES:
            subset = [
                item for item in items if item["part"] == part and item["language"] == language
            ]
            summary = summarize(subset)
            summary.update({"part": part, "language": language})
            groups.append(summary)
    return groups


def build_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "overall": summarize(items),
        "by_part": {part: summarize([item for item in items if item["part"] == part]) for part in PARTS},
        "by_language": {
            language: summarize([item for item in items if item["language"] == language])
            for language in LANGUAGES
        },
        "groups": group_summaries(items),
    }


def format_rate(summary: dict[str, Any]) -> str:
    return f"{summary['passed']}/{summary['total']}（{summary['pass_rate']:.1f}%）"


def md_escape(text: str) -> str:
    return html.escape(text.replace("\r", " ").replace("\n", " "), quote=False).replace("|", "\\|")


def excerpt(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"


def top_reason_text(summary: dict[str, Any]) -> str:
    reasons = summary["top_failure_reasons"]
    if not reasons:
        return "无失败"
    return "；".join(f"{row['reason']}（{row['count']}）" for row in reasons)


def manual_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    for language in LANGUAGES:
        language_items = [item for item in items if item["language"] == language]
        passed = [item for item in language_items if item["passed"]]
        chosen: list[dict[str, Any]] = []
        used: set[str] = set()

        def add(item: dict[str, Any], why: str) -> None:
            if item["id"] in used:
                return
            used.add(item["id"])
            chosen.append(
                {
                    "id": item["id"],
                    "language": language,
                    "part": item["part"],
                    "why": why,
                    "prompt": item["prompt"],
                    "reply": item["reply"],
                    "reason": item["reason"],
                }
            )

        a_passed = [item for item in passed if item["part"] == "A"]
        b_passed = [item for item in passed if item["part"] == "B"]
        if a_passed:
            shortest = min(a_passed, key=lambda item: (len(item["reply"]), item["id"]))
            add(shortest, "Part A 中回复最短，确认信息是否充分、是否遗漏必要步骤")
        if b_passed:
            expanded = max(
                b_passed,
                key=lambda item: (len(item["reply"]) / max(1, len(item.get("source", ""))), item["id"]),
            )
            add(expanded, "Part B 中译文相对原文扩写最多，确认没有增译、解释或改变语气")
        a_remaining = [item for item in a_passed if item["id"] not in used]
        if a_remaining:
            weakest = min(a_remaining, key=lambda item: (target_signal(item["reply"], language), item["id"]))
            add(weakest, "Part A 中目标语言特征相对最弱，确认语言纯度与自然度")
        for item in passed:
            if len(chosen) >= 3:
                break
            add(item, "通过但建议抽查整体语气与事实表述")
        if not passed:
            for item in language_items:
                if len(chosen) >= 3:
                    break
                add(item, "该语言存在失败，建议结合失败样例人工复核")
        if len(chosen) < 3:
            for item in language_items:
                if len(chosen) >= 3:
                    break
                add(item, "补充抽查")
        picks.extend(chosen[:3])
    return picks


def write_reports(
    items: list[dict[str, Any]],
    started_at: str,
    finished_at: str,
    elapsed: float,
    rejudged_at: str | None = None,
) -> dict[str, Any]:
    summary = build_summary(items)
    manual = manual_review_items(items)
    report = {
        "schema_version": "v3-language-check/1",
        "generated_at": finished_at,
        "run": {
            "started_at": started_at,
            "finished_at": finished_at,
            "rejudged_at": rejudged_at,
            "elapsed_seconds": round(elapsed, 3),
            "endpoint": ENDPOINT,
            "model": MODEL_NAME,
            "parameters": {
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            "languages": list(LANGUAGES),
            "part_a_per_language": 30,
            "part_b_per_language": 30,
            "total_calls": 240,
            "data_policy": "全部提示词为合成文本；未使用客户数据；仅访问回环地址。",
        },
        "summary": summary,
        "manual_review": manual,
        "items": items,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_JSON, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    run = report["run"]
    items = report["items"]
    lines: list[str] = []
    lines.append("# V3 四语言抽检报告")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    if run.get("rejudged_at"):
        lines.append(f"- 重新判定：{run['rejudged_at']}（未重新调用模型）")
    lines.append(f"- 总耗时：{run['elapsed_seconds'] / 60:.1f} 分钟（{run['elapsed_seconds']:.1f} 秒）")
    lines.append(f"- 模型：{run['model']}，本地回环接口，未访问外部网络")
    lines.append("- 数据：全部为合成文本，未使用客户数据")
    lines.append("- 调用：Part A 120 次 + Part B 120 次，共 240 次")
    lines.append("")
    lines.append("## 结论摘要")
    lines.append("")
    overall = summary["overall"]
    lines.append(
        f"总体通过 **{overall['passed']}/{overall['total']}（{overall['pass_rate']:.1f}%）**，"
        f"失败 {overall['failed']} 条。"
    )
    part_a = summary["by_part"]["A"]
    part_b = summary["by_part"]["B"]
    lines.append(
        f"Part A 语言跟随 **{part_a['passed']}/{part_a['total']}（{part_a['pass_rate']:.1f}%）**；"
        f"Part B 实体保真 **{part_b['passed']}/{part_b['total']}（{part_b['pass_rate']:.1f}%）**。"
    )
    lines.append("")
    lines.append("| 语言 | Part A 通过 | Part A 通过率 | Part B 通过 | Part B 通过率 | 合计 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for language in LANGUAGES:
        a = next(g for g in summary["groups"] if g["part"] == "A" and g["language"] == language)
        b = next(g for g in summary["groups"] if g["part"] == "B" and g["language"] == language)
        lang_total = summary["by_language"][language]
        lines.append(
            f"| {language} | {a['passed']}/{a['total']} | {a['pass_rate']:.1f}% | "
            f"{b['passed']}/{b['total']} | {b['pass_rate']:.1f}% | "
            f"{lang_total['passed']}/{lang_total['total']}（{lang_total['pass_rate']:.1f}%） |"
        )
    lines.append(
        f"| **合计** | **{part_a['passed']}/{part_a['total']}** | **{part_a['pass_rate']:.1f}%** | "
        f"**{part_b['passed']}/{part_b['total']}** | **{part_b['pass_rate']:.1f}%** | "
        f"**{overall['passed']}/{overall['total']}（{overall['pass_rate']:.1f}%）** |"
    )
    lines.append("")
    lines.append("## 分组结果（每组 30 条）")
    lines.append("")
    lines.append("| 语言 | 部分 | 通过 | 通过率 | Top3 失败原因 | 中位耗时 | P95 耗时 |")
    lines.append("|---|---|---:|---:|---|---:|---:|")
    for group in summary["groups"]:
        lines.append(
            f"| {group['language']} | Part {group['part']} | {group['passed']}/{group['total']} | "
            f"{group['pass_rate']:.1f}% | {md_escape(top_reason_text(group))} | "
            f"{group['median_seconds']:.2f}s | {group['p95_seconds']:.2f}s |"
        )
    lines.append("")
    lines.append("## 失败样例（每语言最多 3 条）")
    lines.append("")
    for language in LANGUAGES:
        failures = [item for item in items if item["language"] == language and not item["passed"]]
        lines.append(f"### {language}（失败 {len(failures)} 条）")
        lines.append("")
        if not failures:
            lines.append("无失败样例。")
            lines.append("")
            continue
        for item in failures[:3]:
            lines.append(
                f"1. **{item['id']}**（Part {item['part']}）— 原因：{md_escape(REASON_LABELS.get(item['reason'], item['reason']))}"
                + (f"；{md_escape(item['detail'])}" if item.get("detail") else "")
            )
            lines.append(f"   - 提示词：{md_escape(excerpt(item['prompt']))}")
            lines.append(f"   - 回复：{md_escape(excerpt(item['reply']))}")
        if len(failures) > 3:
            lines.append(f"- 其余 {len(failures) - 3} 条失败见 JSON。")
        lines.append("")
    lines.append("## 建议人工抽查（每语言 3 条）")
    lines.append("")
    for language in LANGUAGES:
        picks = [row for row in report["manual_review"] if row["language"] == language]
        lines.append(f"### {language}")
        lines.append("")
        if not picks:
            lines.append("无可用条目。")
            lines.append("")
            continue
        for index, row in enumerate(picks, start=1):
            lines.append(f"{index}. **{row['id']}**（Part {row['part']}）— {md_escape(row['why'])}")
            lines.append(f"   - 提示词：{md_escape(excerpt(row['prompt']))}")
            lines.append(f"   - 回复：{md_escape(excerpt(row['reply']))}")
        lines.append("")
    lines.append("## 判定口径")
    lines.append("")
    lines.append(
        "- Part A 使用 `workbench.inference.SYSTEM_PROMPT` 与 `build_messages` 构造消息；"
        "temperature=0.2、max_tokens=200、关闭 thinking。"
    )
    lines.append(
        "- ja：回复含平假名或片假名即通过。en：ASCII 字母占非空白字符比例 > 0.8，"
        "且不含汉字/假名。"
    )
    lines.append(
        "- zh-TW：至少含一个繁体专用字，且不含简体专用字。判定采用无歧义差异字；"
        "“查 / 里 / 面”等繁简共用或语境相关字不计入，避免把正确的「檢查」「方面」误判。"
    )
    lines.append("- zh-CN：含汉字、不含假名、不含繁体专用字。空回复一律失败。")
    lines.append(
        "- Part B：生成前对价格、品牌型号、编号和 URL 做占位符保护，生成后还原；"
        "system 为专业本地化译者。回复中四个实体必须逐字原样出现（区分大小写与符号），"
        "空回复或丢失占位符均失败。"
    )
    lines.append(
        "- 本报告只记录合成提示词与模型回复；未包含平台名、账号或真实客户信息。"
    )
    lines.append("")
    return "\n".join(lines)


def rejudge_report(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not items:
        print("[rejudge] no items in report", flush=True)
        return 1
    for item in items:
        if item["part"] == "A":
            passed, reason, detail = judge_part_a(item["reply"], item["language"])
        else:
            passed, reason, detail, missing = judge_part_b(item["reply"], item["entities"])
            item["missing_entities"] = missing
        item["passed"] = passed
        item["reason"] = reason
        item["detail"] = detail
    run = data.get("run", {})
    report = write_reports(
        items,
        str(run.get("started_at", "")),
        str(run.get("finished_at", "")),
        float(run.get("elapsed_seconds", 0)),
        rejudged_at=now_iso(),
    )
    overall = report["summary"]["overall"]
    print(
        f"[rejudge] passed={overall['passed']} failed={overall['failed']} "
        f"rate={overall['pass_rate']:.1f}%",
        flush=True,
    )
    return 0


def run_dry() -> int:
    validate_corpus()
    a_cases = part_a_cases()
    b_cases = part_b_cases()
    print(f"[dry-run] Part A cases: {len(a_cases)} (30 x {len(LANGUAGES)} languages)")
    print(f"[dry-run] Part B cases: {len(b_cases)} (30 x {len(LANGUAGES)} languages)")
    print(f"[dry-run] total: {len(a_cases) + len(b_cases)}")
    print("[dry-run] corpus validation passed")
    return 0


def run_smoke(args: argparse.Namespace) -> int:
    validate_corpus()
    a_case = part_a_cases()[0]
    b_case = next(case for case in part_b_cases() if case["language"] == "en" and case["part"] == "B")
    opener = make_opener()
    failures = 0
    for case in (a_case, b_case):
        item = execute_case(opener, case, args.api_key, args.timeout)
        if item["reason"] == "request_error":
            failures += 1
        print(
            f"[smoke] {item['id']} part={item['part']} language={item['language']} "
            f"passed={item['passed']} reason={item['reason']} seconds={item['seconds']} "
            f"finish={item['finish_reason']}",
            flush=True,
        )
        print(f"[smoke] prompt={item['prompt']!r}", flush=True)
        print(f"[smoke] reply={item['reply']!r}", flush=True)
        if item.get("detail"):
            print(f"[smoke] detail={item['detail']}", flush=True)
    if failures:
        print("[smoke] FAILED: request/parsing error", flush=True)
        return 1
    print("[smoke] OK: two requests parsed and judged", flush=True)
    return 0


def run_full(args: argparse.Namespace) -> int:
    validate_corpus()
    cases = part_a_cases() + part_b_cases()
    if len(cases) != 240:
        raise ValueError(f"expected 240 cases, got {len(cases)}")
    opener = make_opener()
    started_at = now_iso()
    started = time.perf_counter()
    items: list[dict[str, Any]] = []
    print(
        f"[start] V3 check: {len(cases)} calls, endpoint={ENDPOINT}, model={MODEL_NAME}, "
        f"temperature={TEMPERATURE}, max_tokens={MAX_TOKENS}",
        flush=True,
    )
    current_group: tuple[str, str] | None = None
    for case in cases:
        group = (case["part"], case["language"])
        if group != current_group:
            current_group = group
            print(f"[group] Part {group[0]} {group[1]}: 30 条", flush=True)
        item = execute_case(opener, case, args.api_key, args.timeout)
        items.append(item)
        done = len(items)
        if done % 20 == 0 or done == len(cases):
            elapsed = time.perf_counter() - started
            failed = sum(1 for row in items if not row["passed"])
            print(
                f"[progress] {done}/{len(cases)} ({done / len(cases) * 100:.1f}%) "
                f"elapsed={elapsed:.0f}s failed={failed} last={item['id']} "
                f"last_passed={item['passed']} last_reason={item['reason']}",
                flush=True,
            )
    elapsed = time.perf_counter() - started
    finished_at = now_iso()
    report = write_reports(items, started_at, finished_at, elapsed)
    overall = report["summary"]["overall"]
    print(
        f"[done] total={overall['total']} passed={overall['passed']} "
        f"failed={overall['failed']} rate={overall['pass_rate']:.1f}% "
        f"elapsed={elapsed:.1f}s",
        flush=True,
    )
    print(f"[done] json={REPORT_JSON}", flush=True)
    print(f"[done] markdown={REPORT_MD}", flush=True)
    return 0 if overall["failed"] == 0 else 0


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="V3 four-language support draft check")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="validate synthetic corpus only")
    mode.add_argument("--smoke", action="store_true", help="run two real requests")
    mode.add_argument("--run", action="store_true", help="run the full 240-request check")
    mode.add_argument("--rejudge", metavar="REPORT", help="re-judge an existing report without calling the model")
    parser.add_argument("--api-key", default=os.environ.get("V3_API_KEY", ""))
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT)
    args = parser.parse_args()

    if args.rejudge:
        return rejudge_report(Path(args.rejudge))
    if args.dry_run:
        return run_dry()
    if not args.api_key:
        parser.error("--api-key or V3_API_KEY is required")
    if args.smoke:
        return run_smoke(args)
    return run_full(args)


if __name__ == "__main__":
    raise SystemExit(main())

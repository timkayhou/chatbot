# chatbot

完全运行于本地 Windows 的多平台 AI 智能客服系统，仅供本人使用。

## 当前状态

P0 技术审查已经完成。第一阶段能力已可用：

- **人工发送工作台**：粘贴消息、本地模型起草、人工编辑、规则校验、批准、复制。
- **模拟消息中心**：按渠道展示会话列表、聊天内容、客户信息和人工模拟回复，界面模拟手机聊天。
- **FAQ 知识库**：人工录入与审核、按相似问法匹配、从历史聊天归纳候选。
- **运行中心**：本地 AI、稳定性、数据备份与启用清单。

程序不接入真实账号，也不自动发送；模拟回复只写入本机数据库。

## 运行

需要 Python 3.12 或更新版本。纯文本功能不依赖第三方运行库；启用离线图片 OCR 和二维码检查时安装媒体依赖：

```powershell
uv sync --extra media --extra test
```

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m workbench.app
```

打开 `http://127.0.0.1:8765`：

- `/` 运行中心
- `/chat` 模拟消息中心
- `/faq` FAQ 知识库

本地数据默认保存在被 Git 忽略的 `data/workbench.sqlite`。

连接已启动的 `llama-server` 时，通过环境变量提供回环地址、临时 API 密钥、后端名称和稳定性日志路径；密钥不写入仓库。默认读取 SYCL 的 15 分钟稳定性日志，切换后端时用 `WORKBENCH_SOAK_LOG` 指向对应日志。

### 渠道与模拟数据

渠道名称、颜色和皮肤放在本地私有配置 `private/channels.json`，不进入公开仓库；未配置时使用中性的“渠道 A–D”。首次使用可生成演示会话：

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\seed_demo.py
```

演示数据全部标记为模拟客户，可随时清空。

## 长期运行

```powershell
powershell -File scripts\install_startup.ps1   # 登录启动 + 每日 03:30 维护
powershell -File scripts\start_inference.ps1   # 启动 16K 上下文的本地千问推理服务
powershell -File scripts\start_workbench.ps1   # 立即按私有环境变量启动
$env:PYTHONPATH = "src"; .venv\Scripts\python.exe scripts\backup_db.py
$env:PYTHONPATH = "src"; .venv\Scripts\python.exe scripts\rotate_logs.py
```

- 备份使用 SQLite 在线备份，默认保留 14 份，位于 `data/backups/`。
- 应用启动时以及之后每小时会检查备份是否超过 24 小时，并归档 30 天前的日志。
- 运行日志写入 `data/logs/workbench.log`，按 2 MB × 5 轮转。
- 权限不足时，启动项会退回当前用户的“启动”文件夹，维护脚本仍可手动运行。
- 工作台启动链会先确认本地千问推理服务可用；已运行的健康实例不会重复启动。当前验证配置固定使用 SYCL、Qwen3-14B Q5 和 16K 上下文。

## AI 协作与本地循环

多模型协作规则见 [`docs/ai-collaboration-v2.md`](docs/ai-collaboration-v2.md)。本地循环只读检查本仓库与同级 `my-site` 的 Git 快照，不调用模型、不联网、不修改仓库：

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\local_loop.py --once
```

快照无变化时不写报告；变化或异常时写入 `data/local-loop/` 的 Markdown/JSON，交给 Codex 核对。每日维护也会执行一次。

## 真实渠道（只读）

每个平台通过 `src/workbench/adapters.py` 的只读适配器接入：适配器只能 `probe()` 连接状态和读取消息，接口中不提供发送方法。连接状态分为未安装、未启动、未登录、已登录、读取正常、发送受限、需要人工、异常和模拟，运行中心会逐个渠道显示。

真实适配器属于本地私有代码，放在 `private/`，不进入公开仓库；未注册适配器的渠道保持“模拟”状态。采集到的消息通过 `Store.ingest_messages` 幂等写入，重复读取不会产生重复消息。

## 平台规则校验

每个渠道可以绑定一套本地规则词典，放在 `private/rules/<platform>.json`（不提交）。词典覆盖站外引流、联系方式、二维码、明显交易词和其他平台名；聊天草稿按当前渠道的词典预检，命中即显示拦截，批准接口也会再次拒绝。

规则词典只做拦截，不提供拆字、谐音或图片变形等绕过写法。公开仓库不含具体平台词表，缺少本地词典时仍会检查 URL、电话样式数字、二维码和全局词表。

## 适配器测试环境

不需要真实账号即可跑通只读适配器链路：

```powershell
# 终端 1：本地模拟平台（仅回环，只读，POST 返回 405）
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\mock_platform.py --port 8091 --token test-token

# 终端 2：用轮询适配器读取并入库（重复运行不会重复写入）
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\adapter_harness.py --database data\adapter-demo.sqlite
```

`scripts/adapter_harness.py` 会先探测连接状态，再读取 4 条合成消息并幂等写入；输出包含 `sent: false`。真实适配器只需实现同样的 `probe()` / `read_messages()` 契约，真正的客户端和账号仍由你在本机提供。

## 测试

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

四语言抽检（每语言 30 条语言跟随 + 30 条实体保真，共 240 次本地调用）：

```powershell
$env:PYTHONPATH = "src"
$env:V3_API_KEY = "<本地推理密钥>"
.venv\Scripts\python.exe scripts\v3_language_check.py --run
```

结果写入 `data/v3-report.json` 和 `data/v3-report.md`。Part B 会在生成前把价格、品牌型号、编号和 URL 替换为占位符，生成后还原；`--rejudge <report>` 可在不重新调用模型的情况下按最新判定口径重算报告。

图片只在本机内存中分析，数据库保存文件摘要、OCR 文本和二维码结果，不保存图片本体。特定平台的规则词典属于本地私有配置，不进入公开仓库。

需求和优先级见 [开发交接文档 V2.0（公开版）](docs/handoff-v2.0.md)。

## 数据安全

客户聊天记录、账号登录状态、模型和安装资源只保存在本机，不提交到仓库。忽略规则见 [.gitignore](.gitignore)。

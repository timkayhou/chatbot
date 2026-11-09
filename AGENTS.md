# AGENTS.md

## 项目定义

- 本项目是仅供本人使用、完全在本地 Windows 环境运行的多平台 AI 客服辅助工作台，用于集中处理消息草拟、人工审核、FAQ 知识管理和运行状态检查。
- 系统采用 Python 3.12+ 与 SQLite，服务仅监听回环地址；不接入真实平台账号执行自动发送，客户数据、登录状态和模型资源均保留在本机。
- 应用入口为 `src/workbench/app.py`，本地访问地址为 `http://127.0.0.1:8765`。
- Python 依赖由 `uv` 管理；媒体处理与测试能力分别作为可选依赖组安装。

## 常用命令

```powershell
uv sync --extra media --extra test
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m workbench.app
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\backup_db.py
.venv\Scripts\python.exe scripts\local_loop.py --once
```

## 安全与数据治理

- `data/`、`private/`、`resources/`、`*.sqlite`、`*.db`、日志和模型权重均被 Git 忽略，不得提交。
- 真实适配器只读：只实现 `probe()` / `read_messages()`；不提供发送方法，不自动点赞、回复、发布或删除。
- 不把客户姓名、电话、地址、订单号、平台账号/会话 ID 写入公开仓库或第三方。
- 无用户明确授权：不 push、不部署、不登录第三方账号、不做真实平台写操作。

## AI 协作规范

- 跨仓库长期规则见 `../my-site/docs/ai-collaboration-v2.md`；本地补充见 `docs/ai-collaboration-v2.md`。
- 用户直接决定优先；Codex 协调/验收，Harness 执行/自审，Claude 仅条件复核，本地循环承担零模型检查。
- 单主题实施、验证后 commit；不夹带其他任务修改，不伪造完成状态。

## 本地检查循环

- `scripts/local_loop.py --once` 只读检查工作树，快照无变化不写报告、不调用 AI。
- 变化/异常时写 `data/local-loop/`；报告交给 Codex，不自动修复或外发。

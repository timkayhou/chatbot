# AI 协作 v2.0（chatbot 本地补充）

跨仓库的完整规则见 [`my-site/docs/ai-collaboration-v2.md`](../../my-site/docs/ai-collaboration-v2.md)。本文件只记录本仓库特有部分，避免复制长期规则。

## 本仓库角色

- Harness 负责实现、测试、自审和本地单主题 commit。
- Codex 负责协调、证据核对和验收；Claude 仅在安全/隐私/数据丢失/不可逆操作/重大架构或争议不收敛时复核。
- 本地循环只读检查 `chatbot` 与 `my-site` 的 Git 快照；无变化不调用 AI。

## 任务生命周期

需求冻结 → 派发准备 → 单主题实施/自审 → 本地确定性验证 → 条件反审 → 条件终审 → Codex 验收。

## 交接胶囊

必填：任务 ID、目标/决定、范围/禁区、基线 commit、工作树状态、文件归属、已改文件、diff 摘要、测试命令/退出码、风险/阻塞、阶段/下一动作、授权、回滚。

## 本地循环

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\local_loop.py --once
```

- 默认检查本仓库与同级 `my-site`。
- 快照无变化时不写报告；变化/异常时写 `data/local-loop/` 的 Markdown/JSON。
- 只读 Git，不 push、不部署、不上传、不修改仓库。

## 数据边界

- 不提交 `data/`、`private/`、`resources/`、数据库、日志、模型。
- 真实适配器只读；发送必须人工逐条确认。
- 客户数据只在本机；备份/导出不进入 Git。

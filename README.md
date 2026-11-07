# chatbot

完全运行于本地 Windows 的多平台 AI 智能客服系统，仅供本人使用。

## 当前状态

P0 技术审查已经完成。当前正在验证第一阶段的“人工发送本地工作台”：客户消息由人工粘贴，回复经过确定性规则校验和审批后复制，由人工在原界面发送。程序不接入账号，也不自动发送。

## 运行

需要 Python 3.12 或更新版本。纯文本工作台不依赖第三方运行库；启用离线图片 OCR 和二维码检查时安装媒体依赖：

```powershell
uv sync --extra media --extra test
```

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m workbench.app
```

打开 `http://127.0.0.1:8765`。本地数据默认保存在被 Git 忽略的 `data/workbench.sqlite`。

首页包含可视化运行中心，显示本地 AI 通信、稳定性测试进度和正式启用清单。连接已启动的 `llama-server` 时，通过环境变量提供回环地址、临时 API 密钥、后端名称和稳定性日志路径；密钥不写入仓库。

运行测试：

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

图片只在本机内存中分析，数据库保存文件摘要、OCR 文本和二维码结果，不保存图片本体。特定平台的规则词典属于本地私有配置，不进入公开仓库。

需求和优先级见 [开发交接文档 V2.0（公开版）](docs/handoff-v2.0.md)。

## 数据安全

客户聊天记录、账号登录状态、模型和安装资源只保存在本机，不提交到仓库。忽略规则见 [.gitignore](.gitignore)。

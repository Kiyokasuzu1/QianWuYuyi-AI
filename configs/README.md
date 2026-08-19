# configs/ — 配置与环境变量说明

本目录包含 QianWuYuyi-AI 首次部署所需的配置模板。**所有模板均不含真实密钥或用户数据。**

## 文件说明

| 文件 | 用途 |
|------|------|
| `config.example.yaml` | 主配置模板。复制为部署根目录的 `config.yaml` 后按需修改 |
| `env.example` | 环境变量模板。复制为部署根目录的 `.env` 后填入真实值 |

## 环境变量说明（.env）

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `DEEPSEEK_API_KEY` | 推荐 | 空 | DeepSeek API Key。为空且无 OPENAI_API_KEY 时进入 mock 模式 |
| `OPENAI_API_KEY` | 否 | 空 | 备用 OpenAI 兼容 Key |
| `YUYI_LLM_MOCK` | 否 | 空 | `1/true/yes` 强制 mock 模式（首次部署验证链路建议开启） |
| `YUYI_API_PORT` | 否 | `5000` | API 服务监听端口 |
| `YUYI_ADMIN_PREFIX` | 否 | `/admin` | Admin 控制台路径前缀 |
| `YUYI_ADMIN_TOKEN` | 建议 | 空 | Admin 敏感端点（屏幕/控制）访问 token。不配置则仅允许本机回环访问 |
| `YUYI_REMOTE_TOKEN` | 建议 | 空 | 本地代理连接 token。**为空或弱默认值时 Agent Server 拒绝启动（fail-closed）** |
| `YUYI_REMOTE_PORT` | 否 | `8765` | 远程 WebSocket 监听端口 |
| `ONEBOT_URL` / `ONEBOT_TOKEN` | 否 | 空 | OneBot 主动消息通道（不使用主动消息可留空） |
| `ASTRBOT_URL` | 否 | 空 | AstrBot 备用通道 |
| `TARGET_USER_QQ` | 否 | 空 | 主动消息目标 QQ（留空则用 config.yaml 的 `initiative.target_user_qq`） |
| `MIN_CHECK_SECONDS` / `MAX_CHECK_SECONDS` | 否 | 300/900 | 主动消息检查间隔 |
| `PYTHON_BIN` | 否 | `python3` | 脚本使用的 Python 命令 |
| `YUYI_LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `YUYI_DATA_DIR` | 否 | `./data` | 数据目录 |

生成强随机 token 的方法：`openssl rand -hex 32`

## config.example.yaml 说明

- 模板已脱敏：`memory.target_user_id` 与 `initiative.target_user_qq` 为占位符，首次部署时改为实际用户标识。
- `llm.api_key` 使用 `${DEEPSEEK_API_KEY}` 引用环境变量，**不要**在 yaml 中写入明文 Key。
- `remote.auth_token` 留空时由 `YUYI_REMOTE_TOKEN` 环境变量提供。
- `system_version_commit` 已标注本包对应的冻结提交。

## 首次部署初始化步骤

```bash
cd QianWuYuyi-AI                       # 部署根目录
# 1. 配置模板
cp configs/config.example.yaml config.yaml      # 修改 target_user_id / target_user_qq
cp configs/env.example .env                     # 填入 DEEPSEEK_API_KEY / YUYI_REMOTE_TOKEN / YUYI_ADMIN_TOKEN
# 2. 创建虚拟环境并安装依赖
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 3. embedding 模型（首次运行自动下载，或提前执行）
python -c "import chromadb.utils.embedding_functions as ef; ef.SentenceTransformerEmbeddingFunction(model_name='BAAI/bge-small-zh-v1.5')"
# 4. 数据目录由程序首次运行时自动创建（data/）
# 5. 验证（mock 模式）
YUYI_LLM_MOCK=1 bash start.sh --foreground
#    另开终端: curl http://127.0.0.1:5000/health
```

> Tesseract 说明：`pytesseract` 需要系统安装 tesseract-ocr 与中文语言包
> （`apt install tesseract-ocr tesseract-ocr-chi-sim`），仅屏幕 OCR 功能需要。

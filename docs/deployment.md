# Yuyi 部署指南 (Phase D.0)

> 本文档面向「第一次部署 Yuyi 的运维人员」, 按步骤可完成最小可用部署。

## 1. 部署目标

完成以下能力的最小可用版本:

- 一键启动: `python -m api_server` 或 `bash scripts/start_yuyi.sh`
- API 可访问: `http://127.0.0.1:5000/v1/chat/completions`
- Admin 控制台: `http://127.0.0.1:5000/admin/`
- 远程模块 WebSocket: `ws://127.0.0.1:8765`
- 进程保活 (systemd): 失败 10s 自动重启
- 数据持久化: `data/` 目录下 Memory/Personality/SelfModel 重启后保留

## 2. 环境要求

| 项目 | 要求 | 说明 |
|------|------|------|
| OS | Linux (Ubuntu 22.04+ 推荐) | Windows/macOS 暂未完整支持 systemd |
| Python | 3.11 | 必须 3.11, 避免类型注解差异 |
| 内存 | ≥ 2 GB | LLM 调用瞬时较高 |
| 磁盘 | ≥ 5 GB | 依赖 + data + logs |
| 端口 | 5000 (API), 8765 (Remote) | 可在 .env 中调整 |

## 3. 快速部署 (5 步)

### Step 1: 拉取代码

```bash
cd /root
git clone <repo-url> QianWuYuyi-AI
cd QianWuYuyi-AI
```

### Step 2: 安装依赖

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 3: 配置环境变量

```bash
cp .env.example .env
nano .env  # 填入 DEEPSEEK_API_KEY 或 OPENAI_API_KEY
```

### Step 4: Preflight 检查

```bash
python3 scripts/preflight_check.py
```

要求全部通过, 失败项按提示修复。

### Step 5: 启动服务

```bash
# 方式 A: 前台启动 (调试)
python3 -m api_server

# 方式 B: 后台启动 (生产)
bash scripts/start_yuyi.sh

# 方式 C: systemd 部署
sudo cp deploy/systemd/yuyi-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yuyi-api
```

## 4. 验证部署

### 4.1 健康检查

```bash
python3 scripts/health_check.py
```

### 4.2 首次运行真实链路测试

```bash
python3 scripts/first_run_test.py
```

要求 5/5 全部通过, 输出 `羽依已 '活起来' (D.0 首次运行通过)`。

### 4.3 手动聊天测试

```bash
curl -X POST http://127.0.0.1:5000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "yuyi",
    "messages": [{"role": "user", "content": "你好, 你是谁?"}],
    "user": "deploy_check"
  }'
```

## 5. 配置项说明

### 5.1 .env 关键变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 二选一 | DeepSeek LLM 凭据 |
| `OPENAI_API_KEY` | 二选一 | OpenAI 兼容 LLM 凭据 |
| `YUYI_LLM_MOCK` | 否 | 1 = mock 模式, 不调用真实 LLM |
| `YUYI_REMOTE_TOKEN` | 否 | 远程模块 WebSocket 鉴权 |
| `YUYI_REMOTE_PORT` | 否 | 远程模块端口, 默认 8765 |
| `YUYI_API_PORT` | 否 | API 端口, 默认 5000 |

### 5.2 config.yaml 关键项

不要修改 `config.yaml` 中的 API Key (项目硬约束)。
所有敏感配置通过 `.env` 注入。

## 6. 部署后目录结构

```
QianWuYuyi-AI/
├── data/                    # 持久化数据
│   ├── memory/              # 长期记忆 (SQLite + 向量)
│   ├── personality/         # 人格数据
│   ├── self_model/          # 自我模型
│   ├── emotion/             # 情绪状态
│   └── growth/              # 成长状态
├── logs/                    # 运行日志
│   ├── yuyi-api.log
│   └── yuyi-sender.log
├── deploy/
│   ├── systemd/             # systemd 单元
│   └── nginx/               # 反向代理配置
├── scripts/                 # 运维脚本
│   ├── preflight_check.py
│   ├── health_check.py
│   ├── first_run_test.py
│   ├── start_yuyi.sh
│   ├── stop_yuyi.sh
│   └── clean_data.sh
├── .env                     # 运行时环境变量 (不入 git)
└── config.yaml              # 静态配置
```

## 7. 常见问题

### Q1: 启动失败, 提示 "config.yaml not found"

确保在项目根目录执行命令, 或在 systemd 单元中正确设置 `WorkingDirectory`。

### Q2: LLM 调用 401/403

检查 `.env` 中的 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 是否正确。
注意 `config.yaml` 中不要填写 API Key, 必须通过环境变量注入。

### Q3: 远程模块连接失败

1. 确认 `YUYI_REMOTE_PORT=8765` 在 `.env` 中
2. 确认 `config.yaml` 中 `remote.enabled: true`
3. 检查防火墙: `sudo ufw allow 8765/tcp`

### Q4: 端口被占用

```bash
sudo lsof -i :5000
# 或修改 .env: YUYI_API_PORT=5050
```

### Q5: 数据残留

部署前清理测试数据 (保留真实记忆):

```bash
bash scripts/clean_data.sh
```

## 8. 升级与回滚

### 8.1 升级

```bash
# 1. 停服
sudo systemctl stop yuyi-api yuyi-sender

# 2. 备份数据
tar -czf data_backup_$(date +%Y%m%d).tar.gz data/

# 3. 拉取新代码
git pull

# 4. 升级依赖
pip install -r requirements.txt

# 5. 重启
sudo systemctl start yuyi-api
sudo systemctl start yuyi-sender
```

### 8.2 回滚

```bash
# 1. 停服
sudo systemctl stop yuyi-api yuyi-sender

# 2. 恢复数据
tar -xzf data_backup_YYYYMMDD.tar.gz

# 3. 切回代码版本
git checkout <tag>

# 4. 重启
sudo systemctl start yuyi-api
```

## 9. 监控与日志

- 日志位置: `/root/QianWuYuyi-AI/logs/`
- 健康检查: `python3 scripts/health_check.py` (建议加入 cron 每 5 分钟)
- 运行时监控: `python3 scripts/runtime_health_monitor.py`

## 10. 后续阶段 (规划中, 未实现)

- Phase D.1: Dashboard 实时状态大屏
- Phase D.2: Auto-Approve 策略自动审核
- Phase D.3: 多用户审核工作流
- Phase D.4: 自适应增强 (基于 PolicyFeedback)

当前 D.0 阶段目标: **稳定运行 + 真实交互**, 不实现上述功能。

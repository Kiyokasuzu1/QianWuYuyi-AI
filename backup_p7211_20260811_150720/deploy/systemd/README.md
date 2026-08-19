# Yuyi systemd 部署说明 (Phase 7.2.1-p1 更新)

> 2026-08-10（Phase 7.2.1-p1）：旧版独立进程 initiative_sender.py 已移除。
> 主动消息统一由 `yuyi-api.service` 进程内的 Runtime InitiativeBridge 接管。
> **yuyi-sender.service 现已废弃，请勿再启用。**

## 1. 文件清单

```
deploy/systemd/
├── yuyi-api.service          # API 服务单元 (主进程，包含主动消息 & Agent Server & Runtime tick)
├── installed/                # PID 文件目录 (运行时生成)
└── README.md                 # 本文档
```

> 历史注记：旧版存在的 `yuyi-sender.service`（主动消息独立进程）已于 Phase 7.2.1-p1 彻底删除。
> 该服务单元文件不再随仓库分发，如果服务器上仍存在请按第 3 步的 0 号步骤清理。

## 2. 前置条件

- 操作系统: Linux (systemd ≥ 245)
- Python: 3.11
- 用户: `root` (按项目硬约束要求)
- 工作目录: `/root/QianWuYuyi-AI`
- `.env` 文件已就位: `/root/QianWuYuyi-AI/.env`
- 日志目录: `/root/QianWuYuyi-AI/logs/`

## 3. 安装/升级步骤（Phase 7.2.1-p1+）

```bash
# 0. 升级前：停用并禁用旧的 sender 服务（如果之前部署过）
sudo systemctl stop    yuyi-sender.service    2>/dev/null || true
sudo systemctl disable yuyi-sender.service    2>/dev/null || true
sudo rm -f /etc/systemd/system/yuyi-sender.service

# 1. 复制/替换主服务文件
sudo cp deploy/systemd/yuyi-api.service /etc/systemd/system/

# 2. 重载 systemd
sudo systemctl daemon-reload

# 3. 启用开机启动 + 启动
sudo systemctl enable yuyi-api.service
sudo systemctl start  yuyi-api.service
```

## 4. 常用命令

```bash
# 查看状态（现在只需要一个服务）
sudo systemctl status yuyi-api

# 重启（含 RuntimeCore tick、主动消息、Agent Server 全部会随 API 启停）
sudo systemctl restart yuyi-api

# 停止
sudo systemctl stop yuyi-api

# 查看日志
sudo journalctl -u yuyi-api -f
tail -f /root/QianWuYuyi-AI/logs/yuyi-api.log

# 验证主动消息 bridge 是否已注册（启动日志里应有这一行）
journalctl -u yuyi-api | grep "InitiativeBridge 注册成功"
```

## 5. 启动顺序保证

- 单服务部署后不再需要 sender ↔ api 的顺序依赖；
- `yuyi-api.service` 内部按顺序：加载 config → Flask → RuntimeBridge → InitiativeBridge → AgentServer → 状态写盘线程。

## 6. 优雅停服

```bash
sudo systemctl stop yuyi-api
```
- systemd 默认发送 `SIGTERM`，`TimeoutStopSec=20` 后升级 `SIGKILL`。
- `atexit` 钩子会自动保存 RuntimeBridge 状态。

## 7. 反向代理 (Nginx)

参见 `deploy/nginx/yuyi.conf.example`

## 8. 故障排查

| 现象 | 排查方向 |
|------|----------|
| 主动消息不发 | 1) `journalctl -u yuyi-api \| grep -i initiative` 是否存在 `InitiativeBridge 注册成功`；2) `config.yaml` 中 `initiative.enabled: true`；3) `target_user_qq` / `onebot_url` 等是否正确；4) 是否处于 cooldown（默认 60s 内只发一条） |
| Runtime Dashboard 看不到 Agent Server 在线 | 查看 `data/agent_server_status.json` 是否 5 秒内刷新；若陈旧说明 api_server 内 Agent Server 线程没起来 |
| 主动消息重复发送 | 检查旧版 `yuyi-sender.service` 是否已彻底 stop/disable；确保机器上只剩一个 `yuyi-api.service` 进程在跑 |

| 启动失败 | `journalctl -u yuyi-api -n 100` |
| 端口占用 | `ss -tlnp \| grep 5000` |
| 配置错误 | `python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"` |
| 依赖缺失 | `bash scripts/preflight_check.py` |

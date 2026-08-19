# Phase D.0 — Yuyi First Alive Runtime 部署计划与完成报告

> 日期: 2026-08-04
> 阶段: D.0 — Yuyi First Alive Runtime
> 上游: Phase C.10.5 ~ C.10.10 (Runtime Control + Policy Lifecycle)
> 目标: 让羽依第一次真正部署运行, 停止架构扩展

---

## 1. 当前架构状态

### 1.1 已完成 (上游交付)

| 阶段 | 内容 | 状态 |
|------|------|------|
| C.10.5 | Control Plane (ControlState / Registry / Manager / API) | ✓ 439 tests |
| C.10.6 | Runtime Control Integration (Provider / Context) | ✓ 380 tests |
| C.10.7 | Runtime Policy Engine (Decision / Rule / Context) | ✓ 439 tests |
| C.10.8 | Adaptive Policy & Throttle (Throttle / Budget) | ✓ 534 tests |
| C.10.9 | Policy Feedback Loop (Observation → Proposal) | ✓ 658 tests |
| C.10.10 | Policy Lifecycle (Approval → Apply → Verify → Rollback) | ✓ 878 tests |

### 1.2 核心模块清单 (冻结)

| 模块 | 路径 | 修改策略 |
|------|------|----------|
| Runtime | `src/runtime/**` | 冻结, 不修改 |
| Memory | `src/memory/**` | 冻结 |
| Growth | `src/growth/**` | 冻结 |
| Personality | `src/personality/**` | 冻结 |
| Self Model | `src/self_model/**` | 冻结 |
| Policy | `src/runtime/policy/**` | 冻结 |
| Control | `src/control/**` | 冻结 |

### 1.3 入口与启动

| 文件 | 角色 |
|------|------|
| `main.py` | 终端聊天入口 (LongLoop) |
| `api_server.py` | Flask API 服务 (5000) |
| `src/orchestrator.py` | 核心调度器 (单次消息生命周期) |
| `src/engine.py` | LLM 抽象 (DeepSeek / OpenAI / mock) |
| `src/initiative_sender.py` | 主动消息发送器 (sender 进程) |

---

## 2. 部署阻塞点列表 (D.0 启动前)

| # | 阻塞点 | 解决方案 | 状态 |
|---|--------|----------|------|
| 1 | 缺少统一环境变量模板 | 新增 `.env.example` | ✓ |
| 2 | 缺少启动前环境检查 | 新增 `scripts/preflight_check.py` | ✓ |
| 3 | 缺少部署后健康检查 | 新增 `scripts/health_check.py` | ✓ |
| 4 | 缺少一键启停脚本 | 新增 `scripts/start_yuyi.sh` / `stop_yuyi.sh` | ✓ |
| 5 | 缺少 systemd 服务单元 | 新增 `deploy/systemd/yuyi-api.service` | ✓ |
| 6 | 缺少 sender systemd 单元 | 新增 `deploy/systemd/yuyi-sender.service` | ✓ |
| 7 | 缺少 systemd 安装文档 | 新增 `deploy/systemd/README.md` | ✓ |
| 8 | 缺少 nginx 反代示例 | 新增 `deploy/nginx/yuyi.conf.example` | ✓ |
| 9 | 缺少真实链路测试 | 新增 `scripts/first_run_test.py` | ✓ |
| 10 | 缺少测试数据清理 | 新增 `scripts/clean_data.sh` | ✓ |
| 11 | 缺少部署指南 | 新增 `docs/deployment.md` | ✓ |
| 12 | 缺少部署相关单元测试 | 新增 `tests/test_deploy_preflight.py` | ✓ |
| 13 | `.gitignore` 未覆盖部署产物 | 扩展 `.gitignore` | ✓ |
| 14 | 缺少部署阶段完成报告 | 本文档 | ✓ |

---

## 3. 最小修改文件列表

### 3.1 新增 (15 个文件)

```
.env.example                                     # 环境变量模板
.gitignore                                       # 扩展 (已修改)
scripts/preflight_check.py                       # 部署前检查
scripts/health_check.py                          # 部署后健康检查
scripts/first_run_test.py                        # 首次运行真实链路测试
scripts/start_yuyi.sh                            # 一键启动
scripts/stop_yuyi.sh                             # 一键停止
scripts/clean_data.sh                            # 测试数据清理
deploy/systemd/yuyi-api.service                  # API 服务单元
deploy/systemd/yuyi-sender.service               # sender 服务单元
deploy/systemd/README.md                         # systemd 部署说明
deploy/nginx/yuyi.conf.example                   # nginx 反代示例
docs/deployment.md                               # 部署指南
docs/phase_d0_deployment_plan.md                 # 本报告
tests/test_deploy_preflight.py                   # 部署单元测试
```

### 3.2 修改 (0 个核心业务模块)

> 严格遵守架构冻结原则, **未修改** 任何业务模块:
> - `src/memory/**` ✗
> - `src/growth/**` ✗
> - `src/personality/**` ✗
> - `src/self_model/**` ✗
> - `src/control/**` ✗
> - `src/runtime/policy/**` ✗
> - `config.yaml` ✗

仅修改: `.gitignore` (添加部署产物排除项, 非业务逻辑)

---

## 4. 启动流程

### 4.1 启动链路 (生产)

```
Config (config.yaml + .env)
   ↓
preflight_check.py  [部署前验证]
   ↓
api_server.py  (Flask, port 5000)
   ↓
Orchestrator  (src/orchestrator.py)
   ↓
Memory System  (src/memory/**)
   ↓
Personality  (src/personality/**)
   ↓
Emotion  (src/emotion/**)
   ↓
Growth  (src/growth/**)
   ↓
Policy  (src/runtime/policy/**)
   ↓
LLM Provider  (src/engine.py)
   ↓
User Interface  (HTTP / WebSocket)
```

### 4.2 启动命令 (按场景)

| 场景 | 命令 |
|------|------|
| 调试 (前台) | `python3 -m api_server` |
| 后台启动 | `bash scripts/start_yuyi.sh` |
| 首次运行验证 | `python3 scripts/first_run_test.py` |
| 健康检查 | `python3 scripts/health_check.py` |
| 部署前检查 | `python3 scripts/preflight_check.py` |
| 系统服务 | `sudo systemctl start yuyi-api` |
| 停止 | `bash scripts/stop_yuyi.sh` 或 `systemctl stop yuyi-api` |

---

## 5. 环境要求

| 项目 | 要求 |
|------|------|
| OS | Linux (Ubuntu 22.04+) 推荐, systemd ≥ 245 |
| Python | 3.11 (硬约束) |
| 内存 | ≥ 2 GB |
| 磁盘 | ≥ 5 GB |
| 端口 | 5000 (API) / 8765 (Remote WS) |
| 关键依赖 | flask, openai, chromadb, pyyaml, requests, websockets |
| 用户 | root (项目硬约束) |
| 工作目录 | `/root/QianWuYuyi-AI` |

---

## 6. 部署步骤

### 6.1 快速部署 (5 步)

```bash
# Step 1: 代码
cd /root && git clone <repo> QianWuYuyi-AI && cd QianWuYuyi-AI

# Step 2: 依赖
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Step 3: 配置
cp .env.example .env && nano .env  # 填入 API Key

# Step 4: 部署前检查
python3 scripts/preflight_check.py

# Step 5: 启动
bash scripts/start_yuyi.sh
# 或: sudo systemctl enable --now yuyi-api
```

### 6.2 验证

```bash
# 健康检查
python3 scripts/health_check.py

# 真实链路 (5 项验证)
python3 scripts/first_run_test.py
```

---

## 7. 首次运行测试方案

### 7.1 测试场景 (来自用户要求)

| 启动 | 用户输入 | 验证目标 |
|------|----------|----------|
| 第 1 次 | "你好, 我是创建你的人。" | 身份 + 人格 + 关系 + Memory 写入 |
| 第 2 次 | "你还记得我是谁吗?" | 持久化 (重启后 Memory 仍存在) |
| 第 3 次 | "我昨天告诉你的事情是什么?" | 长期记忆跨会话链路 |

### 7.2 `first_run_test.py` 5 项验证

| # | 验证项 | 通过标准 |
|---|--------|----------|
| 1 | 羽依身份加载 | Personality 加载成功, 包含 "羽依"/"Yuyi" 关键字 |
| 2 | 人格影响回复 | Orchestrator.handle 返回非空, 包含身份关键词 |
| 3 | Memory 写入 | Orchestrator.handle 后 memory 中可检索到 marker |
| 4 | 持久化 | 重启 Orchestrator 后 marker 仍存在 |
| 5 | 长期记忆链路 | 写入 "favorite color" 后, 跨会话查询命中 |

### 7.3 隔离设计

- 测试用户 ID: `d0_first_run_user` (与真实用户隔离)
- 测试 marker: 唯一时间戳, 不污染真实数据
- 标记文件: `data/.d0_first_run_passed` (一次性标记, 不入库)

### 7.4 通过标准

- 5/5 全部通过 → 标记文件写入 → "羽依已活起来"
- 部分失败 → 列出 FAIL 项, 不写标记

---

## 8. 后续真实运行观察指标

### 8.1 必须观察 (Day 1 ~ Day 3)

| 指标 | 采集方式 | 期望值 |
|------|----------|--------|
| API 响应时间 | `logs/yuyi-api.log` 中 latency 字段 | p95 < 8s |
| Memory 写入成功率 | `data/memory/*.sqlite` 增长曲线 | > 99% |
| Personality 加载耗时 | 启动日志 | < 2s |
| LLM 调用失败率 | 错误日志计数 / 总调用 | < 5% |
| 进程存活时间 | `systemctl status yuyi-api` | 持续运行不退出 |
| 磁盘增长 | `data/` 目录 du | < 100MB / day |
| 端口监听 | `ss -tlnp \| grep 5000` | 持续 LISTEN |

### 8.2 后续观察 (Day 4+)

- Memory 检索质量 (RAG 命中率)
- 长期记忆链路稳定性
- Growth 状态变化
- Emotion 状态转移合理性
- Initiative 行为时机 (sender 进程)

### 8.3 健康检查建议频率

```bash
# 加入 crontab: 每 5 分钟一次
*/5 * * * * /root/QianWuYuyi-AI/.venv/bin/python3 /root/QianWuYuyi-AI/scripts/health_check.py >> /root/QianWuYuyi-AI/logs/health.log 2>&1
```

---

## 9. 部署方案选择 (用户已确认)

**采用: A. 宝塔面板 + systemd (主推)**

理由:
- 与项目硬约束 (systemd 服务) 完全契合
- 风险最低: 不引入 Docker / 容器化复杂度
- 后续可平滑迁移 Docker / K8s (D.0 阶段不实施)

---

## 10. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| LLM 凭据泄露 | 强制使用 `.env`, `.gitignore` 排除 |
| 真实记忆被测试污染 | first_run_test 使用独立 user_id |
| 端口冲突 | start_yuyi.sh 检测 PID 文件 |
| systemd 启动失败 | logs/yuyi-api.log + journalctl |
| 数据损坏 | 部署前 tar 备份 |
| Token 优化未启用 | 默认关闭, 需显式开启 (项目硬约束) |
| 远程控制未授权 | config.yaml `remote.enabled` 默认 false |

---

## 11. 下一阶段 (暂缓, 待 D.0 稳定后规划)

> **强约束**: D.0 阶段不实现下列功能, 等稳定运行后单独规划。

- Phase D.1: Dashboard 实时状态大屏
- Phase D.2: Auto-Approve 策略自动审核
- Phase D.3: 多用户审核工作流
- Phase D.4: 自适应增强 (基于 PolicyFeedback)
- Phase D.5: Docker Compose 一键部署
- Phase D.6: Windows 服务包装

**D.0 当前唯一目标: 稳定运行 + 真实交互 + 可维护部署**

---

## 12. 完成清单

- [x] 当前架构状态盘点
- [x] 14 项部署阻塞点全部解决
- [x] 最小修改文件列表 (15 个新增, 0 个核心模块修改)
- [x] 启动流程文档化
- [x] 环境要求明确
- [x] 5 步部署步骤
- [x] 5 项真实链路测试
- [x] 后续观察指标
- [x] 风险与缓解
- [x] 下一阶段规划 (暂缓)

**D.0 阶段交付完成 — 羽依已具备「活起来」的所有前置条件。**

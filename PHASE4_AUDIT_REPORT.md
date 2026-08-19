# QianWuYuyi-AI Phase 4 审计报告

**审计日期**：2026-07-29
**审计范围**：部署架构 / 控制台 / 核心能力闭环 / Remote Agent / Screen Control
**审计原则**：仅检查现状，不写新功能，不重新设计架构

---

## 0. 概览结论

| 维度 | 状态 |
|---|---|
| 部署架构 | 2 进程架构清晰，但 `config.yaml` 在仓库中缺失（仅存 example），运行时配置依赖环境注入 |
| 控制台 | UI/API 完整，但 7 个开关中只有 1 个真正启停模块（initiative），其余仅切配置标志位 |
| 人格闭环 | 真实闭环（端到端可用） |
| 情绪闭环 | **完全断开**（RuntimeContext 不返回 emotion_manager，且默认配置禁用） |
| 记忆闭环 | 部分闭环（写入 OK，向量索引未实时更新，三套检索封装是死代码） |
| 主动消息闭环 | 部分闭环（独立脚本可用，RuntimeBridge 自动决策依赖默认配置阈值存疑） |
| Remote Agent | 已实现（WebSocket + 认证 + 心跳），但权限检查有 bug 被跳过 |
| Screen Control | **代码基本完整，但"没真正跑起来"**（control 默认禁用，代理需手动启动，权限 bug，无坐标定位） |

**总体完成度估算：约 60%**

- 已上线能力：API 服务、QQ 接入、Web 控制台 UI、人格闭环、记忆写入、主动消息脚本、远程通信链路、截图+OCR 实现
- 未真正接通：情绪闭环、向量索引实时更新、控制台模块控制、屏幕控制实际运行、权限检查

---

## 1. 部署架构检查

### 1.1 真实部署架构图

```
┌─────────────────────────────────────────────────────────────────┐
│  QQ 用户                                                         │
└────────┬────────────────────────────────────────────────────────┘
         │ QQ 消息
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  AstrBot 框架 + astrbot_plugin_yuyi/main.py                     │
│  (插件通过 HTTP 调用 localhost:5000/v1/chat/completions)         │
└────────┬────────────────────────────────────────────────────────┘
         │ HTTP POST
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  进程 1: api_server.py  (Flask, 0.0.0.0:5000)                   │
│  ├─ Orchestrator (src/orchestrator.py) - 单次对话生命周期         │
│  │   ├─ MemoryStore + VectorMemory (Chroma)                      │
│  │   ├─ PersonalityResolver + SelfModelContextProvider           │
│  │   ├─ ResponseEngine (src/engine.py, DeepSeek API)             │
│  │   ├─ RuntimeContext.assemble_context()                        │
│  │   └─ ScreenContextManager / ControlManager (按需初始化)        │
│  ├─ Admin Blueprint (src/admin/api/routes.py, 挂载在 /admin)     │
│  ├─ AgentServer 后台线程 (src/remote/agent_server.py, port 8765)  │
│  └─ RuntimeBridge → RuntimeCore (生命周期 60s tick)              │
└────────┬────────────────────────────────────────────────────────┘
         │ WebSocket (port 8765)
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  local_agent/agent.py (用户电脑上的客户端)                       │
│  ├─ ScreenCapture (mss)                                          │
│  └─ InputControl (pynput)                                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  进程 2: initiative_sender.py (主动消息定时器)                    │
│  └─ 5-15 分钟随机间隔 → orchestrator.generate_initiative()       │
│     → OneBot:3000 / AstrBot:11451 → QQ                          │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 关键入口与端口

| 角色 | 文件 | 端口 / 入口 |
|---|---|---|
| API 服务入口 | `api_server.py` | `0.0.0.0:5000` |
| Web 控制台 | `static/admin/index.html` + `src/admin/api/routes.py` | `http://127.0.0.1:5000/admin` |
| RuntimeCore 入口 | `src/runtime/runtime_core.py` (通过 RuntimeBridge 启动) | 60s tick |
| Orchestrator 入口 | `src/orchestrator.py` (`Orchestrator.process()`) | 由 api_server 调用 |
| Remote Agent Server | `src/remote/agent_server.py` | `0.0.0.0:8765` (WebSocket) |
| Local Agent | `local_agent/agent.py` | 客户端，连接到 8765 |
| QQ 机器人入口 | `astrbot_plugins/astrbot_plugin_yuyi/main.py` | AstrBot 插件 |
| 主动消息发送器 | `initiative_sender.py` | 独立进程 |
| 终端测试入口 | `main.py` | 开发调试用，非生产 |

### 1.3 各问题明确答案

1. **QQ 消息入口**：`astrbot_plugins/astrbot_plugin_yuyi/main.py`（AstrBot 插件）→ 调用 `api_server.py:5000/v1/chat/completions`
2. **Web 控制台**：`static/admin/index.html` + `src/admin/api/routes.py`（Blueprint 挂载在 `/admin`）
3. **API 服务入口**：`api_server.py`（Flask，端口 5000）
4. **RuntimeCore 是否实际运行核心**：是。它是"生命主循环"，负责状态维护、事件响应、决策、行动。但**实际 chat 调用走的是 Orchestrator**，RuntimeCore 仅在后台跑 60s tick
5. **Orchestrator 和 Runtime 关系**：Orchestrator 处理单次对话的完整生命周期（同步、即时响应），RuntimeCore 维护长期状态与自主决策（异步、定时 tick）。两者通过 `RuntimeBridge` + `InitiativeBridge` 桥接
6. **Remote Agent 通信**：WebSocket（端口 8765）+ JSON 消息 + token 认证 + 心跳（5s 检查，30s 超时）+ asyncio.Future 请求-响应配对
7. **Local Agent 状态**：代码完整，但**需要用户手动启动**（`python local_agent/agent.py`），未配置为后台服务
8. **数据存储**：`data/` 目录
   - `data/memory.json` - 对话记忆
   - `data/chroma_db/` - 向量数据库
   - `data/runtime_state.json` - RuntimeCore 状态
   - `data/emotions/{user_id}.json` - 每用户情绪
   - `data/relationship_state.json` - 关系状态
   - `data/proposals/growth_proposals.json` - 成长提案
   - `data/audit/audit_logs.json` - 审计日志
9. **日志系统**：`logs/api.log` + `logs/initiative.log`，但**没有显式 FileHandler 配置**，依赖 `nohup ... > logs/api.log 2>&1 &` shell 重定向，非 Python logging 配置

### 1.4 架构问题

| 编号 | 问题 | 严重度 |
|---|---|---|
| A1 | `config.yaml` 在仓库中不存在（仅存 `config.yaml.example` 与 `config.yaml.save`），运行时 `load_config()` 找不到文件返回 `{}`，导致 RuntimeCore 各项 enabled 标志全部默认 False | 高 |
| A2 | `config.yaml.save` 中 `astrbot_url: "http://http://198.44.178.195/"` 有双 `http://` 笔误 | 中 |
| A3 | 日志无 Python `FileHandler`，仅靠 shell 重定向，systemd 服务下日志可能丢失 | 中 |
| A4 | RuntimeCore 通过 RuntimeBridge 启动但默认配置全 False，实际只跑了空 tick | 高 |
| A5 | 两个进程（api_server / initiative_sender）各自独立加载 Orchestrator，状态可能不一致 | 中 |

---

## 2. 控制台审计

### 2.1 控制台整体结构

- **前端入口**：`static/admin/index.html`
- **前端主逻辑**：`static/admin/js/app.js`、`static/admin/js/dashboard.js`
- **后端路由**：`src/admin/api/routes.py`（Blueprint，40+ 端点）
- **配置管理**：`src/admin/core/config_manager.py`
- **Schema 文件**：`src/admin/core/schema/{admin,control,initiative,remote,screen,token_opt}.yaml`（共 6 个，**缺 emotion.yaml / personality.yaml / memory.yaml**）

### 2.2 七个开关真实状态

| 开关 | UI | API | 真实模块控制 | 状态显示 | 综合状态 |
|---|---|---|---|---|---|
| 情绪 (emotion) | ✅ | ✅ | ❌ | ✅ | **只有配置 + 状态显示** |
| 主动消息 (initiative) | ✅ | ✅ | ✅（进程级） | — | **已接线** |
| 记忆 (memory) | ✅ | ✅ | ❌ | ✅ | **只有配置 + 状态显示** |
| 人格 (personality) | ✅ | ✅ | ❌ | ✅ | **只有配置 + 状态显示** |
| Token优化 (token_opt) | ✅ | ✅ | ❌ | ❌ | **只有配置** |
| 远程控制 (remote) | ✅ | ✅ | ⚠️ 部分（reload + 操作 API 真实，start/stop 仅切配置） | — | **已接线（部分）** |
| 屏幕控制 (screen) | ✅ | ✅ | ⚠️ 部分（reload + 截图/OCR API 真实，start/stop 仅切配置） | — | **已接线（部分）** |

### 2.3 关键发现

**已上线（真实工作）**：
- `initiative` 模块 start/stop 真正通过 `subprocess.Popen` / `pkill` 启停 `initiative_sender.py` 进程
- `remote` / `screen` / `control` 的 reload 端点真正重新初始化 orchestrator 中的 manager 实例
- `/api/control/click|type|key`、`/api/screen/capture|context` 真实向在线代理下发指令并返回数据
- `/api/dashboard`、`/api/cognitive/*` 真实读取运行中的模块状态

**只有 UI（前端有按钮，无后端真实控制）**：无（所有 UI 按钮都有对应 API）

**只有配置（start/stop 仅切换 config.yaml 标志位，不真正启停模块）**：
- `emotion` - orchestrator 启动时无条件初始化 emotion_manager（虽然实际未启用，见第 3 节），开关 toggle 不影响运行时
- `memory` - `MemoryStore()` 无条件初始化，开关 toggle 不影响
- `personality` - `PersonalityResolver()` 无条件初始化，开关 toggle 不影响
- `token_opt` - 无运行状态显示，无热重载逻辑

**未实现**：无（所有开关都有 UI + API）

### 2.4 控制台设计意图 vs 实际实现

- **设计意图**（`src/core/module_interface.py`）：`ModuleBase` 抽象类定义 `_start/_stop/_reload/_health_check`，控制台应通过这套接口真正控制模块
- **实际实现**（`src/admin/api/routes.py`）：`api_module_start/stop/reload` 端点**绕过 ModuleBase 接口**，直接调用 `ConfigManager.toggle_module()` 切配置 + 极少数模块的硬编码特殊处理（initiative 进程、screen/control/remote reload 重初始化）

---

## 3. 核心能力闭环检查

### 3.1 人格闭环 — ✅ 真实闭环

```
用户输入
  ↓ (api_server.py:213 / main.py:90)
orchestrator.process(user_message)
  ↓ (orchestrator.py:213-218 Step 2)
memory_store.load() + vector_memory.search()  ← 读取记忆
  ↓ (orchestrator.py:254 Step 4)
personality_resolver.resolve()  ← 读取人格状态
  ↓ (orchestrator.py:256 Step 5)
_get_personality_context()  ← 转为自然语言描述
  ↓ (orchestrator.py:295-306 Step 9)
engine.generate(personality_context=...)  ← 构建 prompt
  ↓ (src/engine.py:52-57)
DeepSeek API  ← LLM 回复
```

**真实状态**：端到端可执行，无断点。

**死代码**（平行实现，Orchestrator 不调用）：
- `src/response/engine.py`、`src/response/prompt_builder.py`、`src/response/llm.py`
- `src/personality/personality_prompt.py`（`PersonalityPromptFormatter`）

### 3.2 情绪闭环 — ❌ 完全断开

存在三条本应接通但全部断开的路径：

**路径 A：Orchestrator 内的情绪前后处理器（死代码）**
- `orchestrator.py:259-267` Step 5：`if assembled_context and "emotion_manager" in assembled_context: ...`
- **断点**：`RuntimeContext.assemble_context()`（`runtime_context.py:135-145`）返回的字典**不包含 `emotion_manager` 这个 key**
- 因此 `_process_emotion_pre()` / `_process_emotion_post()` / `_process_relationship_post()` 全部直接 return，从未执行

**路径 B：RuntimeCore 中的 EmotionManager（默认禁用）**
- `runtime_core.py:335`：`self._emotion_enabled = self.config.get("emotion_enabled", False)` — **默认 False**
- `runtime_core.py:632-646`：只有 `emotion_enabled=True` 才会实例化 EmotionManager + EmotionDynamicsEngine
- `config.yaml.example` 中无 `emotion_enabled` 项

**路径 C：EmotionManager → prompt_builder 注入**
- 由于路径 A、B 都断开，`emotion_context` 永远为空字典
- `src/engine.py:116-124` 的 `【当前情绪】` 段永远不会注入 system prompt

**结论**：`src/emotion/*` 是完整的代码资产，但**未被任何活路径调用**。情绪闭环在三个接合点全部断开。

### 3.3 记忆闭环 — ⚠️ 部分闭环

**写入：✅ 真实可用**
- `orchestrator.py:328-339` Step 10：构建 memory_record，调用 `memory_store.add()` 持久化到 `data/memory.json`

**检索：⚠️ 基础可用但有缺陷**
- `orchestrator.py:213-218` Step 2：
  - `memory_store.load()` — **加载全部记忆，无 user_id 过滤，无 limit**
  - `vector_memory.search(user_message, top_k=5)` — ChromaDB 语义检索

**断点 1：向量索引未实时更新**
- `memory_store.add()` 之后**没有调用** `vector_memory.add_memory()` 或 `vector_memory.index_memories()`
- 新写入的记忆要等下一次全量重建索引才能被向量检索到

**断点 2：三套高级检索封装是死代码**
- `src/memory/memory_system.py`（MemorySystem）
- `src/memory/memory_service.py`（MemoryService，含语义检索+相关性排序）
- `src/memory/memory_retriever.py`（MemoryRetriever，bigram 关键词检索）
- Orchestrator 只用了最朴素的 `MemoryStore.load()` + `VectorMemory.search()`

### 3.4 主动消息闭环 — ⚠️ 部分闭环

**路径 A：独立脚本（✅ 真实闭环，端到端可执行）**
- `initiative_sender.py:217-280` 启动 → `main_loop()` → 5-15 分钟随机间隔 → `orchestrator.generate_initiative()` → OneBot:3000 / AstrBot:11451 → QQ
- 只要 `python initiative_sender.py` 启动且 config 中 QQ bot URL 配置正确，就能工作

**路径 B：RuntimeBridge 自动决策（⚠️ 代码接通但默认不触发）**
- `api_server.py:88-133` 调用 `_init_runtime_bridge()` → RuntimeCore 启动
- `runtime_core.py:815-869` `_maybe_decide()` → `DecisionEngine.evaluate_all(world_state)` → 当 `initiative > 0.5 AND social_need > 0.6` 时触发 `send_message`
- **断点**：`cognitive_enabled / experience_enabled / emotion_enabled` 全部默认 False，SelfState 缺乏来自情绪/经验的输入信号，自动决策路径在默认配置下能否命中阈值**存疑**

**死代码**：
- `src/proactive/proactive_engine.py`（ProactiveEngine，含 ActionConfidenceGate 克制层）
- `src/initiative/module.py`（仅模块声明）

---

## 4. Remote Agent 检查

### 4.1 通信链路状态：✅ 已实现

| 组件 | 文件 | 状态 |
|---|---|---|
| 协议定义 | `src/remote/protocol.py` | 完整（10 种 Command + 7 种 Response） |
| Agent 注册表 | `src/remote/agent_registry.py` | 完整（注册/认证/心跳/超时清理） |
| WebSocket 服务器 | `src/remote/agent_server.py` | 完整（token 认证 + 心跳 + 请求-响应配对） |
| 本地代理客户端 | `local_agent/agent.py` | 完整（连接 + 认证 + 心跳 + 7 类指令处理 + 自动重连） |

**核心调用接口**：`agent_server.py:352-396` `send_command_and_wait()` 是服务端下发指令的唯一入口。

### 4.2 能否执行任务：✅ 能

调用链路完整闭环：
```
服务器 send_command_and_wait()
  ↓ WebSocket
本地代理 _handle_message()
  ↓ 调用 ScreenCapture / InputControl
执行结果
  ↓ WebSocket 回传 SCREEN_DATA / ACTION_RESULT
服务器 _resolve_pending() 唤醒 Future
```

### 4.3 权限控制：⚠️ 框架存在但有 bug，实际被跳过

- `src/permission/` 框架完整（3 阶段 + 7 种权限类型，含 SCREEN_VIEW/KEYBOARD/MOUSE/FILE_OPERATION）
- **Bug**：`src/control/control_manager.py:187-191` 调用 `pm.has_permission(user_id, "keyboard")`，但 `PermissionManager` 类**没有 `has_permission` 方法**（只有 `check`）
- **集成缺失**：Orchestrator 初始化 ControlManager 时**从未调用** `set_permission_manager()`，权限检查被完全跳过

### 4.4 日志：✅ 有

- `agent_server.py` 使用 `logging.getLogger(__name__)`
- `local_agent/agent.py` 配置了 `basicConfig` + `getLogger("local_agent")`
- `logs/api.log` + `logs/initiative.log` 存在

### 4.5 安全限制：✅ 已实现

`src/control/safety_guard.py` 完整实现 SafetyGuard：
- 14 种危险按键组合黑名单（Alt+F4、Ctrl+Alt+Del、Win+L/D/E/R/X、Alt+Tab 等）
- 约 20 种危险文本正则（shutdown、format、rm -rf、regedit、taskkill、REG ADD 等）
- ControlManager 在 `_execute_action` 第 194 行调用 `safety_guard.check_action`

---

## 5. Screen Control 检查（重点）

### 5.1 现状：代码基本完整，但"没真正跑起来"

| 能力 | 状态 | 实现位置 |
|---|---|---|
| 屏幕截图 | ✅ 已实现（mss 真实截屏） | `local_agent/screen_capture.py` |
| OCR 识别 | ✅ 已实现（pytesseract，依赖外部 Tesseract） | `src/screen/screen_analyzer.py` |
| 坐标定位 | ⚠️ 部分实现（支持手动传 x,y，**无 OCR→坐标、无图像识别定位**） | — |
| 鼠标控制 | ✅ 已实现（pynput，但 config 中被禁用） | `local_agent/input_control.py` |
| 键盘控制 | ✅ 已实现（pynput，但 config 中被禁用） | `local_agent/input_control.py` |
| Prompt 注入 | ✅ 已实现（orchestrator.py:235-251） | `src/orchestrator.py` |
| RuntimeCore 集成 | ❌ 未实现（自主决策层完全不知道屏幕内容） | — |

### 5.2 为什么屏幕控制"还没实现"

代码层面**已经基本完整实现**，但有 9 个原因导致它"看起来没实现"：

| 编号 | 原因 | 严重度 |
|---|---|---|
| S1 | `config.yaml` 中 `control.enabled: false`（键鼠被显式禁用，只有 screen 启用） | 高 |
| S2 | `local_agent/agent.py` 需要用户手动启动，未配置为后台服务 | 高 |
| S3 | 权限检查 bug（`has_permission` 方法不存在），即使注入 pm 也会 AttributeError | 高 |
| S4 | Orchestrator 初始化 ControlManager 时从未调用 `set_permission_manager()`，权限检查被跳过 | 高 |
| S5 | `ControlManager.key_combination()` 明确返回 `"组合键暂未实现"`（`control_manager.py:95-99`） | 中 |
| S6 | 事件循环冲突：`orchestrator.process()` 同步方法通过 `_run_async_safe()` 调用异步 `get_screen_description()`，若已有事件循环运行则降级返回 None | 中 |
| S7 | `runtime_core.py` 完全不引用 screen/remote，自主决策层无法基于屏幕内容决策 | 中 |
| S8 | OCR 需要 Tesseract OCR 引擎单独安装（外部依赖） | 低 |
| S9 | 缺少"OCR 文字→坐标"或"图像识别→控件坐标"模块，无法实现"看到按钮→点击按钮"闭环 | 中 |

### 5.3 最小实现路线（不写新代码，仅配置与启动）

**步骤 1：安装依赖**
```bash
pip install -r local_agent/requirements.txt   # mss, Pillow, pynput, websockets
# 可选：安装 Tesseract OCR + chi_sim 语言包，设置 TESSERACT_CMD 环境变量
```

**步骤 2：配置本地代理**
- 复制 `local_agent/config.yaml.example` 为 `local_agent/config.yaml`
- 填写 `server_url: "ws://localhost:8765"`
- 填写 `token: "test-token-yuyi-2026"`（与 config.yaml 中 remote.auth_token 一致）
- 填写 `user_id: "default"`

**步骤 3：启用控制模块（可选）**
- 在 `config.yaml` 中将 `control.enabled` 改为 `true`
- 注意：由于权限检查 bug，目前权限会被跳过（不安全但能跑）

**步骤 4：启动服务**
1. 启动 `python api_server.py`（服务器）
2. 启动 `python local_agent/agent.py`（本地代理）

**步骤 5：验证链路**
- `GET /admin/api/agents/status` → 确认 `authenticated_agents >= 1`
- `GET /admin/api/screen/context` → 确认 `available: true`
- `POST /admin/api/control/click {"x":100,"y":100}` → 验证鼠标控制
- `POST /admin/api/control/type {"text":"hello"}` → 验证键盘控制
- 发送聊天消息，观察日志是否出现 `[Orchestrator] 屏幕上下文已获取: N 字`

### 5.4 后续代码修复建议（需改代码）

| 优先级 | 修复项 | 文件 |
|---|---|---|
| P1 | 修复 ControlManager 权限检查 bug（`has_permission` → `check`） | `src/control/control_manager.py:187-191` |
| P1 | Orchestrator 初始化时调用 `control_manager.set_permission_manager(get_permission_manager())` | `src/orchestrator.py` |
| P1 | 实现 `ControlManager.key_combination` 真正下发组合键 | `src/control/control_manager.py:95-99` |
| P2 | 将 screen_context 接入 `runtime_core.py` 的自主决策 | `src/runtime/runtime_core.py` |
| P2 | 新增"OCR 文字→坐标"映射模块，实现"看到什么就点什么"闭环 | 新模块（不在本审计范围） |
| P2 | local_agent 注册为系统服务（Windows 计划任务 / Linux systemd） | 部署脚本 |
| P3 | 解决事件循环冲突（使用 `asyncio.run_coroutine_threadsafe`） | `src/orchestrator.py` |

---

## 6. 当前系统真实完成度

### 6.1 已上线能力列表

| 能力 | 状态 | 证据 |
|---|---|---|
| API 服务（OpenAI 兼容） | ✅ | `api_server.py:5000`，`/v1/chat/completions` |
| QQ 机器人接入 | ✅ | AstrBot 插件 |
| Web 控制台 UI | ✅ | `static/admin/index.html` + 40+ API 端点 |
| 人格状态读取 → prompt 注入 | ✅ | Orchestrator Step 4-9 真实闭环 |
| LLM 回复生成 | ✅ | DeepSeek API |
| 记忆写入 | ✅ | `data/memory.json` |
| 记忆基础检索（load + 向量搜索） | ✅ | ChromaDB |
| 主动消息发送（脚本模式） | ✅ | `initiative_sender.py` |
| Remote Agent 通信 | ✅ | WebSocket 8765 |
| 屏幕截图 | ✅（代码） | mss |
| OCR 识别 | ✅（代码） | pytesseract |
| 鼠标键盘控制 | ✅（代码） | pynput |
| 安全护栏 | ✅ | SafetyGuard 黑名单 |
| 模块热重载（screen/control/remote） | ✅ | reload 端点重初始化 |
| 控制台状态显示 | ✅ | dashboard / cognitive 视图 |
| RuntimeCore 生命周期 | ✅（运行中） | 60s tick |
| 成长提案审批框架 | ✅（代码） | ApprovalManager |
| 审计日志 | ✅ | `data/audit/audit_logs.json` |

### 6.2 未完成能力列表

| 能力 | 状态 | 阻塞原因 |
|---|---|---|
| 情绪闭环 | ❌ 完全断开 | RuntimeContext 不返回 emotion_manager；emotion_enabled 默认 False；prompt 不注入情绪 |
| 向量索引实时更新 | ❌ 未实现 | 写入记忆后未调用 `vector_memory.add_memory()` |
| MemorySystem/Service/Retriever 高级检索 | ❌ 死代码 | Orchestrator 不调用 |
| ProactiveEngine | ❌ 死代码 | 无接入点 |
| 控制台模块真实启停（emotion/memory/personality/token_opt） | ❌ 仅切配置 | routes.py 绕过 ModuleBase 接口 |
| ControlManager 权限检查 | ❌ 有 bug | `has_permission` 方法不存在 |
| ControlManager 组合键 | ❌ 未实现 | 返回"暂未实现" |
| RuntimeCore 屏幕感知集成 | ❌ 未实现 | runtime_core.py 不引用 screen |
| OCR→坐标定位 | ❌ 未实现 | 无"看到按钮→点击按钮"闭环 |
| Local Agent 后台服务化 | ❌ 手动启动 | 无 systemd / 计划任务 |
| Python logging FileHandler | ❌ 缺失 | 仅靠 shell 重定向 |
| config.yaml 持久化 | ❌ 仓库中缺失 | 仅存 example/save |

### 6.3 架构问题汇总

| 编号 | 问题 | 影响 |
|---|---|---|
| A1 | `config.yaml` 仓库缺失，运行时配置全靠环境注入 | RuntimeCore 各项 enabled 默认 False |
| A2 | `astrbot_url` 双 `http://` 笔误 | 主动消息发送可能失败 |
| A3 | 日志无 FileHandler | systemd 下日志可能丢失 |
| A4 | RuntimeCore 默认配置全 False，实际只跑空 tick | 情绪/经验/反思/成长链路全不启用 |
| A5 | 两个进程独立加载 Orchestrator | 状态可能不一致 |
| A6 | routes.py 绕过 ModuleBase 接口 | 控制台开关仅切配置，不真正控制模块 |
| A7 | RuntimeContext.assemble_context() 不返回 emotion_manager | 情绪前后处理永远不执行 |
| A8 | 向量索引不随写入实时更新 | 新记忆检索不到 |
| A9 | ControlManager 权限检查 bug | 权限被跳过（不安全） |
| A10 | runtime_core.py 不集成 screen | 自主决策无法基于屏幕反馈 |

---

## 7. P4 开发优先级

按用户给定优先级：

### 第一优先级：Runtime 统一入口

**目标**：让 RuntimeCore 真正成为"生命主循环"，而非空转。

| 任务 | 文件 | 说明 |
|---|---|---|
| 创建并提交 `config.yaml`（基于 example，含所有 enabled 标志） | `config.yaml` | 解决 A1 |
| 修复 `astrbot_url` 双 `http://` 笔误 | `config.yaml.save` | 解决 A2 |
| 在 RuntimeContext.assemble_context() 中返回 emotion_manager | `src/runtime/runtime_context.py:135-145` | 解决 A7，打通情绪闭环路径 A |
| 设置 `emotion_enabled: true` | `config.yaml` | 打通情绪闭环路径 B |
| 验证情绪 → prompt 注入链路 | `src/engine.py:116-124` | 确认 `【当前情绪】` 段被注入 |

### 第二优先级：控制台完善

**目标**：让控制台开关真正控制模块，而非仅切配置。

| 任务 | 文件 | 说明 |
|---|---|---|
| 在 routes.py 的 `api_module_start/stop` 中调用 ModuleBase 接口 | `src/admin/api/routes.py` | 解决 A6 |
| 为 emotion/personality/memory 创建 schema yaml | `src/admin/core/schema/` | 补全 schema |
| 添加 emotion/personality/memory 的真实启停逻辑 | `src/orchestrator.py` + `src/admin/api/routes.py` | 让 toggle 真正影响运行时 |
| 添加 Python logging FileHandler | `api_server.py` + `initiative_sender.py` | 解决 A3 |

### 第三优先级：屏幕控制闭环

**目标**：让屏幕控制真正跑起来，并修复已知 bug。

| 任务 | 文件 | 说明 |
|---|---|---|
| 修复 ControlManager 权限检查 bug | `src/control/control_manager.py:187-191` | 解决 A9 |
| Orchestrator 初始化时注入 permission_manager | `src/orchestrator.py` | 解决 S4 |
| 实现 ControlManager.key_combination 真正下发 | `src/control/control_manager.py:95-99` | 解决 S5 |
| 解决事件循环冲突（`asyncio.run_coroutine_threadsafe`） | `src/orchestrator.py` | 解决 S6 |
| 将 screen_context 接入 runtime_core.py 自主决策 | `src/runtime/runtime_core.py` | 解决 S7, A10 |
| local_agent 注册为系统服务 | 部署脚本 | 解决 S2 |
| 启用 control.enabled | `config.yaml` | 解决 S1 |

### 第四优先级：日志审计和权限

**目标**：补全审计与权限体系。

| 任务 | 文件 | 说明 |
|---|---|---|
| 修复 PermissionManager API（添加 has_permission 或统一用 check） | `src/permission/permission_manager.py` | 配合第三优先级 |
| 完善 audit 模块（`src/audit/`）的查询与导出 | `src/audit/query.py` | 现有框架，需补全接口 |
| 控制台增加审计日志查询 UI | `static/admin/` | 现有 `/api/audit` 端点 |

### 第五优先级：部署稳定性

**目标**：让服务稳定运行。

| 任务 | 文件 | 说明 |
|---|---|---|
| 配置 systemd 服务（yuyi-api / yuyi-sender） | 部署脚本 | 已在 project_memory 中规划 |
| 配置 EnvironmentFile 指向 `.env` | systemd unit | API key 从环境变量读取 |
| 配置日志轮转 | logrotate | 防止日志膨胀 |
| 解决两个进程独立加载 Orchestrator 的状态一致性 | 架构调整（待评估） | 解决 A5 |

---

## 8. 推荐下一步路线

### 8.1 立即可做（无需改代码，仅配置）

1. **创建 `config.yaml`**：基于 `config.yaml.example`，加入所有模块的 enabled 标志
2. **修复 `astrbot_url` 笔误**：去掉一个 `http://`
3. **安装 Tesseract OCR**（可选）：让 OCR 真正工作
4. **手动启动 local_agent**：验证屏幕感知链路

### 8.2 第一阶段开发（Runtime 统一入口）

**目标**：打通情绪闭环，让 RuntimeCore 真正工作。

1. 修改 `RuntimeContext.assemble_context()` 返回 `emotion_manager`
2. 在 `config.yaml` 中设置 `emotion_enabled: true`
3. 验证 `src/engine.py:116-124` 的 `【当前情绪】` 段被注入
4. 添加单元测试验证情绪闭环

### 8.3 第二阶段开发（控制台完善）

**目标**：让控制台开关真正控制模块。

1. 在 `routes.py` 的 `api_module_start/stop` 中调用 ModuleBase 接口
2. 为 emotion/personality/memory 创建 schema yaml
3. 添加 Python logging FileHandler
4. 添加单元测试验证模块启停

### 8.4 第三阶段开发（屏幕控制闭环）

**目标**：让屏幕控制真正可用。

1. 修复 ControlManager 权限检查 bug
2. 实现 ControlManager.key_combination 真正下发
3. 解决事件循环冲突
4. 将 screen_context 接入 runtime_core.py
5. local_agent 注册为系统服务
6. 添加集成测试验证屏幕控制闭环

### 8.5 后续阶段（按需推进）

- 日志审计与权限完善
- 部署稳定性（systemd / 日志轮转）
- 死代码清理（`src/response/*`、`src/proactive/*`、`src/memory/memory_system.py` 等）

---

## 9. 关键文件参考

### 9.1 活路径文件（真实被调用）

| 文件 | 角色 |
|---|---|
| `api_server.py` | API 服务入口 |
| `initiative_sender.py` | 主动消息脚本 |
| `src/orchestrator.py` | 单次对话调度器 |
| `src/engine.py` | ResponseEngine（注意：不是 `src/response/engine.py`） |
| `src/personality/personality_resolver.py` | 人格状态读取 |
| `src/personality/self_model_context_provider.py` | 自我模型上下文 |
| `src/memory/memory_store.py` | 记忆写入 |
| `src/memory/vector.py` | 向量检索 |
| `src/runtime/runtime_context.py` | 上下文组装（断点：不返回 emotion_manager） |
| `src/runtime/runtime_core.py` | 生命主循环（断点：emotion_enabled 默认 False） |
| `src/runtime/runtime_bridge.py` | Runtime 桥接 |
| `src/runtime/initiative_bridge.py` | 主动消息桥接 |
| `src/runtime/decision_engine.py` | 决策引擎 |
| `src/remote/agent_server.py` | WebSocket 服务器 |
| `local_agent/agent.py` | 本地代理客户端 |
| `local_agent/screen_capture.py` | 截图 |
| `local_agent/input_control.py` | 键鼠控制 |
| `src/screen/screen_analyzer.py` | OCR |
| `src/screen/screen_context.py` | 屏幕上下文管理 |
| `src/control/control_manager.py` | 控制入口（bug：权限检查） |
| `src/control/safety_guard.py` | 安全护栏 |
| `src/admin/api/routes.py` | 控制台路由 |
| `src/admin/core/config_manager.py` | 配置管理 |

### 9.2 死代码文件（存在但未被活路径调用）

| 文件 | 说明 |
|---|---|
| `src/response/engine.py` | 平行实现，Orchestrator 用 `src/engine.py` |
| `src/response/prompt_builder.py` | 平行实现 |
| `src/response/llm.py` | 平行实现 |
| `src/personality/personality_prompt.py` | PersonalityPromptFormatter 未被调用 |
| `src/memory/memory_system.py` | MemorySystem 未被调用 |
| `src/memory/memory_service.py` | MemoryService 未被调用 |
| `src/memory/memory_retriever.py` | MemoryRetriever 未被调用 |
| `src/emotion/emotion_manager.py` | 代码完整，但 RuntimeCore 默认不实例化 |
| `src/emotion/emotion_dynamics_engine.py` | 同上 |
| `src/proactive/proactive_engine.py` | ProactiveEngine 未被接入 |
| `src/initiative/module.py` | 仅模块声明 |
| `main.py` | 开发调试用，非生产入口 |

---

## 10. 审计结论

### 10.1 整体判断

QianWuYuyi-AI 项目已完成约 **60%** 的 Phase 4 目标。核心架构清晰，代码资产丰富，但存在以下三类典型问题：

1. **接线断点**：代码写好了但没真正接通（情绪闭环、向量索引实时更新、RuntimeCore 屏幕集成）
2. **配置缺失**：`config.yaml` 不在仓库，运行时配置全靠环境注入，导致默认值不安全
3. **实现不完整**：控制台开关仅切配置、权限检查有 bug、组合键未实现

### 10.2 P4 阶段目标可行性

按用户给定优先级（Runtime 统一入口 → 控制台完善 → 屏幕控制闭环 → 日志审计 → 部署稳定性），P4 目标"让现有能力稳定运行"是**可实现的**，主要工作量集中在：

- 修复接线断点（RuntimeContext 返回 emotion_manager）
- 补全 config.yaml
- 修复 ControlManager 权限 bug
- 让控制台开关真正控制模块
- 部署稳定性（systemd / 日志）

### 10.3 风险提示

- **不要修改 config.yaml 中的 API Key**（用户规则）
- **新代码必须放在独立分支，不直接修改 main**（用户规则）
- **保持向后兼容，不破坏现有 API**（用户规则）
- **新增功能必须附带单元测试**（用户规则）
- **修改核心模块前需评估影响范围**（用户规则）

---

**审计完成。等待用户确认后进入 P4 开发阶段。**

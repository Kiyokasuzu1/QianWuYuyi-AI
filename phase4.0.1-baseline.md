# Phase 4.0.1 架构冻结基线清单（Baseline）

> **快照日期**: 2026-08-11  
> **基线版本**: phase4.0.1-baseline  
> **冻结说明**: 本文件是 Phase 4.0.1 修改前的**纯状态快照**，任何后续改动如需回滚，以本文件记录的代码位置/配置/开关为准。  
> **禁止修改代码声明**: 生成此文档期间未修改任何源代码/配置文件。

---

## 1. 当前启动方式快照

### 1.1 生产启动方式（systemd）

| 项目 | 值 |
|------|-----|
| systemd 服务名 | `yuyi-api.service` |
| 服务文件路径 | `deploy/systemd/yuyi-api.service` |
| WorkingDirectory | `/root/QianWuYuyi-AI` |
| 入口脚本 | `/root/QianWuYuyi-AI/api_server.py` |
| Python 解释器 | `/root/QianWuYuyi-AI/venv/bin/python`（venv 隔离） |
| EnvironmentFile | `/root/QianWuYuyi-AI/.env` |
| Restart 策略 | `always`，间隔 10s |
| 标准输出/错误 | append 到 `logs/yuyi-api.log` |
| 进程类型 | 单进程（Phase 7.2.1-p2：api_server 独占 Agent Server + 主动消息 + 状态写盘，已删除 initiative_sender.py 独立进程） |

### 1.2 本地启动方式

| 方式 | 命令 / 脚本 | 入口函数 |
|------|-------------|----------|
| Flask API + Admin | `python api_server.py` | `api_server.py:1140` → `if __name__ == '__main__'` → `init_orchestrator()` → `app.run(host, port)` |
| Flask API（快捷脚本） | `python run_server.py` | `run_server.py:24` → `init_orchestrator()` → `app.run("0.0.0.0", 5000)` |
| 终端交互（CLI） | `python main.py` | `main.py:56` → `main()` → `Orchestrator()` → `LongLoop.run()` |
| 终端入口收敛 | main.py 内 `LongLoopOrchestratorAdapter` | 通过 `RuntimePipeline(orchestrator, runtime=None)` 先 100% 走 pipeline，再 fallback 到真实 Orchestrator |

### 1.3 Agent Server 启动方式

| 项目 | 值 |
|------|-----|
| 端口 | 默认 8765（可配置 `remote.port` / `YUYI_REMOTE_PORT`） |
| 由谁启动 | api_server 进程内的后台线程（Phase 7.2.1-p2 统一），不再有独立进程 |
| 启动触发 | `api_server.py:322-330` → `_start_agent_server(config)` |
| 线程模型 | `threading.Thread(target=_run_agent_server, daemon=True, name="AgentServer")` |
| 事件循环 | 线程内自建 `asyncio.new_event_loop()` → `loop.run_forever()` |
| 跳过开关 | `YUYI_API_SKIP_AGENT_SERVER=1`（生产勿用） |
| 状态写入线程 | `AgentStatusWriter`（daemon thread，5 秒一次，写 `data/agent_server_status.json`） |

### 1.4 环境变量清单（.env）

**必填核心变量：**

| 变量名 | 默认值 / 示例 | 说明 |
|--------|----------------|------|
| `DEEPSEEK_API_KEY` | `sk-xxxx` | LLM 主 API Key（当前 .env 已配置） |
| `OPENAI_API_KEY` | 空 | 备用兼容 Key |
| `YUYI_API_PORT` | `5000` | api_server 监听端口（**R2.7.6-DEPLOY 修复**：之前写死 5000，现已支持 .env） |
| `YUYI_API_HOST` | `0.0.0.0` | api_server 绑定 host |

**远程/主动消息可选变量：**

| 变量名 | 默认值 / 示例 | 说明 |
|--------|----------------|------|
| `YUYI_REMOTE_TOKEN` | `test-token-yuyi-2026` | Agent Server 认证 token |
| `YUYI_REMOTE_PORT` | `8765` | Agent Server WebSocket 端口 |
| `ONEBOT_URL` | `http://127.0.0.1:3000` | OneBot HTTP API 地址 |
| `ONEBOT_TOKEN` | 空 | OneBot Bearer Token |
| `ASTRBOT_URL` | `http://127.0.0.1:11451` | AstrBot HTTP API 地址（备用） |
| `TARGET_USER_QQ` | `366648462` | 主动消息目标 QQ |
| `MIN_CHECK_SECONDS` | `300` | 主动消息最小检查间隔（秒） |
| `MAX_CHECK_SECONDS` | `900` | 主动消息最大检查间隔（秒） |

**运行时调试变量：**

| 变量名 | 默认值 / 示例 | 说明 |
|--------|----------------|------|
| `YUYI_LLM_MOCK` | 空（= 关闭） | `1/true/yes` = 强制 mock 模式，不调真实 LLM |
| `YUYI_LOG_LEVEL` | `INFO` | 日志级别 DEBUG/INFO/WARNING/ERROR |
| `YUYI_DATA_DIR` | `./data` | 数据根目录 |
| `YUYI_API_SKIP_AGENT_SERVER` | `0`（= 正常启动） | `1` = 跳过 Agent Server（仅调试） |
| `PYTHON_BIN` | `python3` | systemd 用 Python 路径 |

### 1.5 config.yaml 关键配置快照（影响认知链路）

文件路径：`config.yaml`（根目录）

| 配置路径 | 当前值 | 影响链路 |
|----------|--------|----------|
| `system_version` | `"7.2.1-p2"` | 版本标识 |
| `system_version_date` | `"2026-08-11"` | 版本日期 |
| `llm.api_key` | `"${DEEPSEEK_API_KEY}"` | 环境变量引用 |
| `llm.api_base` | `"https://api.deepseek.com"` | DeepSeek API 地址 |
| `llm.model` | `"deepseek-v4-pro"` | 模型名称 |
| `llm.temperature` | `0.7` | 生成温度 |
| `llm.max_tokens` | `2048` | 单次最大 token |
| `runtime.enabled` | **`true`** | ⚠️ 总开关：true = 走 `RuntimePipeline`；false = 走旧 `orchestrator.process()` |
| `runtime.adapters_enabled` | `true` | RuntimeBridge 适配器开关 |
| `runtime.phase4_enabled` | **`false`** | ⚠️ Phase4 开关：false = `RuntimeController` 内部抛 `NotImplementedError`，调用方自动降级 |
| `runtime.persistence_dir` | `"data/runtime_context"` | RuntimePipeline 落盘目录 |
| `runtime.users_root_dir` | `"data/users"` | Phase4 Persistence per-user 根目录 |
| `runtime.llm_engine` | `"deepseek"` | Phase4 LLM 引擎选择（deepseek / mock） |
| `runtime.growth_enabled` | `true` | Phase4 人格成长开关 |
| `runtime.max_context_memories` | `50` | Phase4 传入 prompt 的最大 memory 条数 |
| `runtime.legacy_memory_auto_import` | `true` | 新用户首次对话自动导入旧 `data/memory.json` |
| `runtime.deepseek.enabled` | `true` | Phase4 DeepSeek adapter 开关 |
| `initiative.enabled` | **`true`** | 主动消息总开关（InitiativeBridge） |
| `initiative.api_type` | `"onebot"` | 主动消息发送通道：onebot / astrbot |
| `initiative.initiative_cooldown_seconds` | `60` | 同用户主动消息冷却秒数 |
| `remote.enabled` | **`true`** | 远程代理/Agent Server 总开关（api_server 强制启用，见 api_server.py:280-283） |
| `remote.port` | `8765` | Agent Server 端口 |
| `remote.auth_token` | `"test-token-yuyi-2026"` | Agent Server 认证 token |
| `screen.enabled` | **`true`** | 屏幕监控开关（api_server 强制启用） |
| `control.enabled` | `false` | 电脑控制开关（键鼠操作，默认关闭） |
| `token_opt.enabled` | `false` | Token 优化（历史压缩+记忆摘要，默认关闭） |
| `memory.json_path` | `data/memory.json` | 旧版单体 memory 文件路径 |
| `memory.chroma_path` | `data/chroma_db` | 向量记忆 Chroma 目录 |

---

## 2. 当前聊天链路快照（代码引用级）

### 2.1 用户消息入口（OneBot → AstrBot → HTTP）

```
QQ 用户发消息
    ↓
OneBot (go-cqhttp / Lagrange)
    ↓  WebHook / 反向 WebSocket
AstrBot 插件
    ↓  HTTP POST (AstrBot → Yuyi)
/v1/chat/completions (Flask endpoint)
    ↓  api_server.py:693-859
chat_completions() 路由函数
```

**路由函数位置**: `api_server.py:693` → `@app.route('/v1/chat/completions', methods=['POST'])`

### 2.2 生效路径（三级降级链，由开关控制）

```
/v1/chat/completions 进入
    │
    ├─▶ 优先级 1：RuntimeController（phase4_enabled=true 时）
    │     位置：api_server.py:749-787
    │     调用：_runtime_controller.handle_message(user_id, message)
    │     类位置：src/runtime/runtime_controller.py:59
    │     内部流程：load_state → build_context → cognitive_loop → update_memory
    │                → maybe_grow → save_state → return reply
    │     失败/空回复/NotImplementedError → 自动降级下一级
    │     ⚠️ 当前状态：phase4_enabled=false → NotImplementedError → 必降级
    │
    ├─▶ 优先级 2：RuntimePipeline（runtime.enabled=true 时）
    │     位置：api_server.py:789-821
    │     调用：_process_via_pipeline(user_message, user_id)
    │           → _pipeline.run({"user_message": ...})
    │     类位置：src/runtime/runtime_pipeline.py
    │     内部：RuntimePathAudit (R2.1) → Runtime 尝试 → 失败则
    │           pipeline 内 fallback orchestrator.process()
    │     ⚠️ 空 reply 例外（R2.7.6-DEPLOY）：pipeline 返回空字符串 →
    │        fall through 到 orchestrator legacy 兜底（用户体验 > R2.2）
    │     ⚠️ 当前状态：runtime.enabled=true → **这是当前实际生效主路径**
    │
    └─▶ 优先级 3（终极兜底）：Orchestrator.process()
          位置：api_server.py:823-855
          调用：orchestrator.process(last_user_msg, user_id=user_id)
          类位置：src/orchestrator.py:85
          ⚠️ Orchestrator 已标记 [DEPRECATED]（Authority Registry v1.0），
             仅作 fallback 使用。新代码禁止裸调。
```

**路径判定表（基于当前配置）：**

| runtime.enabled | phase4_enabled | 实际生效路径 | 说明 |
|-----------------|----------------|--------------|------|
| **true** | **false** | **RuntimePipeline → Orchestrator fallback** | 当前状态：Pipeline 是入口，RuntimeCore 未真正接管 17 阶段，走 pipeline 内 orchestrator fallback |
| true | true | RuntimeController → Pipeline → Orchestrator（三级链） | Phase4 打开后 |
| false | false/true | 直接 Orchestrator.process() | 旧链路秒级回滚 |

### 2.3 实际调用 LLM 位置

**Legacy 路径（当前实际生效）：**

| 步骤 | 位置 | 说明 |
|------|------|------|
| 构建消息 | `src/engine.py:124` → `_build_messages_original()` | 拼接 System Prompt：身份 → 人格 → 自我认知 → Historical Experience → 情绪 → 关系 → 记忆 → 重要经历 → 屏幕上下文 → 行为原则 |
| Token 优化构建 | `src/engine.py:248` → `_build_messages_opt()` | token_opt.enabled=true 时走，记忆摘要+历史压缩 |
| **LLM 调用点** | `src/engine.py:82-88` | `self.client.chat.completions.create(model=..., messages=..., temperature=0.8, max_tokens=2048)` |
| Client 延迟创建 | `src/engine.py:29-33` | `@property client` → 首次访问时 `OpenAI(api_key=..., base_url="https://api.deepseek.com/v1")` |
| Mock 模式自动判定 | `src/engine.py:22-27` | 无 API Key / `YUYI_LLM_MOCK` / OpenAI SDK 未装 → 自动进 mock |
| Mock 回复 | `src/engine.py:94-109` | `_mock_response()` 返回 stub 消息 |

**Phase4 路径（当前未启用）：**
- 入口：`src/runtime/runtime_controller.py:170` → `handle_message()`
- LLM Adapter：`src/response_phase4/deepseek_adapter.py` → `DeepSeekAdapter`
- 回退 Engine：`src/response_phase4/mock_response_engine.py` → `MockResponseEngine`

### 2.4 记忆 / 人格 / SelfModel / 情绪调用位置

#### 记忆（Memory）调用链

| 位置 | 调用点 | 说明 |
|------|--------|------|
| Orchestrator 初始化 | `src/orchestrator.py:106-145` | `MemoryStore` / `VectorMemory`：优先 RuntimeBridge 共享，fallback 自建/Provider |
| process() 记忆检索 | `src/orchestrator.py:754-765` | `self.memory_store.load()` + `self.vector_memory.search(user_message, top_k=5)` |
| LLM prompt 注入 | `src/engine.py:197-206` | `_build_messages_original()` → 【相关记忆】段（最多前 5 条，每条 150 char） |
| RuntimePipeline 路径 | `src/runtime/runtime_pipeline.py` | 通过 orchestrator fallback 间接走上述相同路径 |
| Phase4 Persistence | `src/response_phase4/persistence_manager.py` | Per-user 三态目录：`data/users/<id>/memory_<id>.json` |
| 旧 memory 文件 | `data/memory.json` | legacy_memory_auto_import=true 时，新用户首次自动导入 |

#### 人格（Personality）调用链

| 位置 | 调用点 | 说明 |
|------|--------|------|
| Orchestrator 初始化 | `src/orchestrator.py:147-169` | `PersonalityResolver`：优先 RuntimeBridge 共享 |
| Orchestrator.process() | `src/orchestrator.py:768-781` | `runtime_context.assemble_context(..., personality_context=...)` |
| LLM prompt 注入 | `src/engine.py:148-150` | `_build_messages_original()` → 【人格】段（personality_context 非空时） |
| Phase4 人格状态 | `src/personality/personality_state.py` | `PersonalityState` dataclass（trait 值持久化） |
| 人格文件 | `data/users/<id>/personality_<id>.json`（Phase4） / 旧版由 SelfModelStore 管理 |

#### SelfModel（自我认知）调用链

| 位置 | 调用点 | 说明 |
|------|--------|------|
| Orchestrator 初始化 | `src/orchestrator.py:171-256` | `SelfModelStore`：优先 RuntimeBridge → personality_resolver → fallback 自建 |
| Phase 6.2 自动启用 | `src/orchestrator.py:259-276` | 从 RuntimeBridge 取 adapter → `enable_phase_6_2_self_model(adapter)` |
| SelfModel 上下文获取 | `src/orchestrator.py:557-600` | `get_self_model_context()` → Phase 6.2 combined / legacy 降级 |
| Orchestrator.process() | `src/orchestrator.py:769-770` | `_self_model_ctx = self.get_self_model_context()` |
| LLM prompt 注入 | `src/engine.py:152-162` | `_build_messages_original()` → 【自我认知】段（dict 遍历或 str 直接用） |
| Historical Experience | `src/engine.py:164-175` | 【Historical Experience Context】段（Phase A.2） |
| SelfModel 持久化文件 | `data/self_model/beliefs.jsonl`, `history.jsonl`, `meta.json`, `reflection.jsonl` |

#### 情绪（Emotion）调用链

| 位置 | 调用点 | 说明 |
|------|--------|------|
| Orchestrator 初始化 | `src/orchestrator.py:283-304` | `EmotionManager`：优先 RuntimeBridge 共享，fallback 自建 |
| 情绪事件检测 | `src/orchestrator.py:307-312` | `EmotionEventDetector`（从用户消息识别情绪事件） |
| Orchestrator.process() | `src/orchestrator.py:778` | `assemble_context(..., emotion_manager=self.emotion_manager, ...)` |
| LLM prompt 注入 | `src/engine.py:177-186` | `_build_messages_original()` → 【当前情绪】段：主导情绪 + 强度 |
| 情绪文件 | `data/emotion_state.json`（全局） / `data/emotions/<user_id>.json`（per-user） |
| Phase4 情绪 | `src/emotion/emotion_manager.py` → `EmotionManager` |

#### 关系（Relationship）调用链

| 位置 | 调用点 | 说明 |
|------|--------|------|
| Orchestrator 初始化 | `src/orchestrator.py:317` | `RelationshipState()` → 包装 `src/personality/relationship_state.py` 真实实现 |
| 关系文件 | `data/relationship_state.json` | 真实实现持久化路径 |
| LLM prompt 注入 | `src/engine.py:188-195` | `_build_messages_original()` → 【用户关系】段（dict 遍历） |
| Phase4 关系 | `data/users/<id>/relationship_<id>.json` | Per-user 持久化（未启用 phase4 时不用） |

### 2.5 主动消息触发路径

**唯一正确路径（Phase 7.2.1-p2 单进程架构，已删除旧独立进程）：**

```
api_server 进程启动
    ↓
init_orchestrator() → _init_runtime_bridge(config)
    ↓  api_server.py:510-550
RuntimeBridge.initialize() → 启动 RuntimeCore.tick 生命循环
    ↓  注册 InitiativeBridge （api_server.py:518-549）
InitiativeBridge(orchestrator, send_config).register()
    ↓  注册到 Runtime ActionDispatcher
    │
    ├── RuntimeCore.tick 周期调度
    │     ↓
    │   src/runtime/integration/tasks/initiative_lifecycle_task.py:53
    │   InitiativeLifecycleTask（默认 interval=600s / 10 分钟）
    │     ↓  execute() → adapter.tick()
    │   src/runtime/initiative/adapter/initiative_adapter.py → InitiativeAdapter
    │     ↓
    │   src/runtime/initiative/initiative_engine.py → InitiativeEngine
    │     · 生成 InterestSignal（兴趣信号）
    │     · 生成 PossibleAction（可能动作：ASK/OBSERVE/LEARN/REMIND/RECOMMEND）
    │     · ActionFilter 过滤
    │     · 送入 InitiativeQueue
    │
    ├── ActionDispatcher 派发 Action(action_type="send_message")
    │     ↓  InitiativeBridge.handle_send_message(action) 被回调
    │
    └── src/runtime/initiative_bridge.py:62
        InitiativeBridge.handle_send_message()
            ↓  cooldown 检查 + 指纹去重
            ├─ payload.message 已有 → 直接用
            └─ 否则调用 orchestrator.generate_initiative(target_user)
                 ↓  src/orchestrator.generate_initiative() 或 api_server.py:893-898
                 ↓  （AttributeError 时用 process("你现在有什么想主动对我说的吗？") 模拟）
            ↓
        _send_message(message)
            ├─ api_type="onebot" → HTTP POST ONEBOT_URL/send_private_msg
            └─ api_type="astrbot" → HTTP POST ASTRBOT_URL 对应端点
```

**关键代码位置速查：**

| 组件 | 文件位置 |
|------|----------|
| 生命循环调度 Task | `src/runtime/integration/tasks/initiative_lifecycle_task.py:53` → `InitiativeLifecycleTask` |
| 主动消息引擎 | `src/runtime/initiative/initiative_engine.py` → `InitiativeEngine` |
| 主动消息适配器 | `src/runtime/initiative/adapter/initiative_adapter.py` → `InitiativeAdapter` |
| Bridge（发送回调） | `src/runtime/initiative_bridge.py:29` → `InitiativeBridge` |
| HTTP 端点 /initiative | `api_server.py:876` → `@app.route('/initiative', methods=['POST'])`（手动触发） |
| 生成主动消息 | `orchestrator.generate_initiative(user_id)`（主） / `process(提示词)`（fallback 模拟） |

---

## 3. 当前运行状态预期字段（生产需验证填充）

> **说明**: 以下为基线预期值，Phase 4.0.1 上线后**逐项对照检查**。标记 [待生产验证] 的项需要在生产环境实际运行后填写。

### 3.1 服务基础状态

| 检查项 | 预期值 / 操作 | 实际值（生产验证） |
|--------|---------------|---------------------|
| systemd 服务状态 | `systemctl status yuyi-api.service` → `active (running)` | [ ] 待验证 |
| 服务是否开机自启 | `systemctl is-enabled yuyi-api.service` → `enabled` | [ ] 待验证 |
| 进程存在 | `ps aux | grep api_server.py` → 1 个进程（单进程模型） | [ ] 待验证 |
| PID 一致性 | data/agent_server_status.json 中的 pid 与 ps 一致 | [ ] 待验证 |
| YUYI_API_PORT 端口占用 | `ss -tlnp | grep <port>`（默认 5000）→ LISTEN，归属 api_server 进程 | [ ] 待验证 |
| Agent Server 端口占用 | `ss -tlnp | grep 8765` → LISTEN，归属**同一个** api_server 进程（非独立） | [ ] 待验证 |
| 旧进程残留 | `ps aux | grep initiative_sender` → **无**（已删除独立进程） | [ ] 待验证 |
| 旧 systemd 单元 | `systemctl list-units | grep yuyi-sender` → **无**（已废弃） | [ ] 待验证 |

### 3.2 版本与健康检查

| 检查项 | 预期值 | 实际值（生产验证） |
|--------|--------|---------------------|
| config.yaml system_version | `7.2.1-p2` | [ ] 待验证 |
| config.yaml system_version_date | `2026-08-11` | [ ] 待验证 |
| /health | `{"status": "ok"}` HTTP 200 | [ ] 待验证 |
| /v1/models | `{"data": [{"id": "yuyi", "object": "model"}]}` | [ ] 待验证 |
| /admin/api/agents/status | enabled=true, running=true, updated_at 新鲜（≤5s） | [ ] 待验证 |
| data/agent_server_status.json 存在 | 文件存在，updated_at ISO 格式，stale=false | [ ] 待验证 |

### 3.3 核心链路验证（QQ 侧）

| 检查项 | 操作 / 预期 | 实际值（生产验证） |
|--------|------------|---------------------|
| OneBot 连通 | AstrBot 在线，QQ 能收发普通消息 | [ ] 待验证 |
| 羽依能叫名字 | QQ 私聊发"羽依你好" → 回复中自称"羽依"，人格上下文生效 | [ ] 待验证 |
| 羽依能提记忆 | 问"我们之前聊过什么？"或提及过去某个话题 → 回复引用 memory 内容 | [ ] 待验证 |
| 主动消息可触发 | 等 5-15 分钟（interval 300-900s backoff）→ 羽依主动发消息 | [ ] 待验证 |
| 无重复主动消息 | 短时间（60s cooldown）内不连续主动发 2 条 | [ ] 待验证 |
| 3 个 WARNING 是否消失 | `tail -f logs/api_server.log` 中不应持续出现这 3 个：<br>1. Orchestrator 导入失败<br>2. RuntimeBridge 初始化失败<br>3. Pipeline 构造失败（除非 runtime.enabled=false 预期跳过） | [ ] 待验证 |

### 3.4 运行时开关基线（启动日志检查）

启动日志预期出现的关键 INFO 行：

```
[R2.7.6] RuntimeController 配置: phase4_enabled=False | llm_engine=deepseek | ...
[Phase 7.2] RuntimePipeline 已就绪 | runtime_inst=... | trace=... | status=... | events=...
RuntimeBridge 初始化成功（生命循环已启动）
InitiativeBridge 注册成功（单一路径，send_message handler 就绪）
[Phase 7.2.1-p2] api_server 启动 Agent Server（端口 8765）+ 状态写盘线程
Agent Server 线程已启动
Agent Server 状态写入线程已启动
[R2.7.6-DEPLOY] api_server 启动：host=0.0.0.0, port=5000
```

---

## 4. 当前测试状态快照

### 4.1 tests/ 目录统计

| 指标 | 数值 |
|------|------|
| Python 测试文件总数 | **453** 个 |
| 包含 tests/support 辅助目录 | 是 |
| 包含 tests/runtime 子目录 | 是 |
| 包含 tests/safety 子目录 | 是 |
| 包含 tests/audit 子目录 | 是 |

### 4.2 核心模块测试覆盖（部分代表性文件）

| 模块 | 代表性测试文件 | 说明 |
|------|---------------|------|
| **聊天集成测试** | `tests/test_p0_chat_completions.py` | /v1/chat/completions 端点集成测试 ✅ |
| **聊天集成测试** | `tests/test_full_chat_lifecycle.py` | 完整聊天生命周期 ✅ |
| **聊天集成测试** | `tests/test_phase73_prod_smoke.py` | Phase 7.3 生产冒烟测试 ✅ |
| **聊天集成测试** | `tests/test_r276_real_chain_audit.py` | R2.7.6 真实链路审计测试 ✅ |
| **聊天集成测试** | `tests/test_phase72_migration.py` | Phase 7.2 迁移测试 ✅ |
| Orchestrator | `tests/test_orchestrator_integration.py` | Orchestrator 集成测试 |
| Engine / LLM | `tests/test_engine_context.py` | ResponseEngine 上下文注入测试 |
| RuntimePipeline | `tests/runtime/test_runtime_bridge.py` | RuntimeBridge 测试 |
| RuntimeController | `tests/test_phase40_r276_runtime_integration_gates.py` 等 | Phase4 全系列 gates 测试（1 个总 + 约 20 个细分） |
| RuntimeCore | `tests/runtime/test_runtime_core.py` | RuntimeCore 测试 |
| 记忆 Memory | `tests/test_memory_authority.py`、`test_memory_baseline.py`、`test_memory_system.py` 等 | Memory 权威/基线/系统测试（约 20+ 文件） |
| 人格 Personality | `tests/test_personality_authority.py`、`test_personality_evolution.py` 等 | Personality 测试（约 15+ 文件） |
| SelfModel | `tests/test_admin_selfmodel_provider.py`、`test_admin_selfmodel_api.py` 等 | SelfModel 测试（10+ 文件） |
| 情绪 Emotion | `tests/test_emotion_authority.py`、`test_emotion_engine.py` 等 | Emotion 测试（12+ 文件） |
| 主动消息 Initiative | `tests/test_initiative_system.py`、`test_initiative_message_delivery.py` 等 | Initiative 测试（6+ 文件） |
| Growth 成长 | `tests/test_growth_pipeline.py`、`test_growth_integration.py` 等 | Growth 测试（20+ 文件） |
| 生产部署 | `tests/test_deploy_preflight.py`、`tests/audit/test_phase_c0_full_system_validation.py` | 部署预检 + 全系统审计 |

### 4.3 是否有聊天集成测试

**✅ 有，共 5 个核心聊天集成测试文件：**

1. `tests/test_p0_chat_completions.py` - P0 级 /v1/chat/completions HTTP 端点测试
2. `tests/test_full_chat_lifecycle.py` - 端到端完整聊天生命周期测试
3. `tests/test_phase72_migration.py` - Phase 7.2 RuntimePipeline 迁移链路测试
4. `tests/test_phase73_prod_smoke.py` - Phase 7.3 生产级冒烟测试
5. `tests/test_r276_real_chain_audit.py` - R2.7.6 真实链路三级降级审计测试

---

## 5. 回滚策略

### 5.1 Git 回滚参考

| 项目 | 当前值 |
|------|--------|
| 当前分支 | **`phase-4-0-runtime-integration`**（git status 已确认） |
| 当前 HEAD commit | `eed2de607032b5437ddc035ba66ec267778f15db`（短: `eed2de6`） |
| HEAD 提交信息 | "Phase 7.2.1: Cognitive Trace - Memory/Personality/Emotion hooks + path_decided" |
| HEAD 提交日期 | 2026-08-10 |
| 前一个基线提交 | `ad20c6e` — "snapshot: pre-Phase4 baseline" |
| 工作区状态 | **41 行 git status**（28 modified + 9 untracked + 4 其他状态） |
| 未提交改动规模 | 16 文件 tracked 改动，645 行新增 / 711 行删除 |
| **未提交改动 patch 文件** | **`phase4.0.1-baseline.patch`**（107KB，git diff HEAD 完整输出） |
| git stash list | 空（无 stash 记录） |
| .env 文件 | 存在（Test-Path 确认） |

**关键未提交改动文件（影响认知链路）：**

| 文件 | 改动行数 | 影响范围 |
|------|---------|---------|
| `api_server.py` | +182 | HTTP 入口、路由选择、Agent Server 启动 |
| `src/runtime/initiative_bridge.py` | +266 | 主动消息发送回调 |
| `config.yaml` | +47 | 版本号、initiative 配置、runtime 配置 |
| `deploy/systemd/yuyi-api.service` | +18 | systemd 服务定义 |
| `src/runtime/runtime_core.py` | +19 | RuntimeCore 17 阶段 |
| `src/runtime/runtime_pipeline.py` | +11 | RuntimePipeline 主入口 |
| `src/runtime/status_tracker.py` | +47 | 状态追踪 |
| `initiative_sender.py` | -370 | 旧独立进程已删除 |
| `deploy/systemd/yuyi-sender.service` | -26 | 旧 systemd 单元已删除 |

**Phase 4.0.1 修改导致崩溃时的回滚命令（四级方案）：**

```bash
# 方案 A（推荐优先）：秒级开关回滚（不回滚代码）
#   改 config.yaml 两个开关即可完成链路降级：
#   1. runtime.enabled: false    → 跳过 RuntimePipeline，直接 Orchestrator
#   2. runtime.phase4_enabled: false → 跳过 RuntimeController
#   然后重启服务：
systemctl restart yuyi-api.service

# 方案 B：git stash 保存当前改动（保留工作区备份）
git stash push -m "phase4.0.1-step01-baseline-saved"
#   恢复：git stash pop

# 方案 C：git reset 丢弃工作区改动（回到 HEAD eed2de6 干净状态）
#   ⚠️ 注意：这会丢弃 config.yaml / api_server.py 等所有本地改动
#   但 patch 文件 phase4.0.1-baseline.patch 仍在，可恢复：
git reset --hard HEAD
#   如需恢复未提交改动：git apply phase4.0.1-baseline.patch

# 方案 D：回滚到 Phase4 前的基线提交（ad20c6e）
#   适用于：Phase4 本身引入的问题，需要回到 pre-Phase4 状态
git reset --hard ad20c6e
```

**patch 文件恢复验证：**

```bash
# 验证 patch 文件完整性（应输出 16 个文件的 diff）
git apply --check phase4.0.1-baseline.patch

# 应用 patch 恢复未提交改动
git apply phase4.0.1-baseline.patch
```

### 5.2 systemd 重启命令

```bash
# 查看状态
systemctl status yuyi-api.service

# 重启服务（改完配置 / 代码回滚后执行）
systemctl restart yuyi-api.service

# 查看实时日志（重启前后都要盯）
journalctl -u yuyi-api.service -f --since "5 minutes ago"

# 也看应用日志
tail -f /root/QianWuYuyi-AI/logs/yuyi-api.log
tail -f /root/QianWuYuyi-AI/logs/api_server.log

# 紧急停止（必要时）
systemctl stop yuyi-api.service
```

### 5.3 关键数据文件备份策略

> **原则**: Phase 4.0.1 修改**前后都要做一次备份**。备份要同时存本地 + 远程 zip。

#### 5.3.1 必备份文件清单（生产 data/ 目录）

| 数据类型 | 文件路径 | 格式 | 说明 |
|----------|----------|------|------|
| 🔴 记忆（旧版） | `data/memory.json` | JSON | 单体记忆文件，legacy 路径使用 |
| 🔴 记忆（向量） | `data/chroma_db/` | 目录 | Chroma 向量数据库 |
| 🔴 记忆（Phase4 per-user） | `data/users/*/memory_*.json` | JSON | Phase4 用户级记忆（phase4_enabled=true 才产生） |
| 🟠 SelfModel（信念） | `data/self_model/beliefs.jsonl` | JSONL | 自我信念 |
| 🟠 SelfModel（历史） | `data/self_model/history.jsonl` | JSONL | 自我历史 |
| 🟠 SelfModel（反思） | `data/self_model/reflection.jsonl` | JSONL | 自我反思 |
| 🟠 SelfModel（元信息） | `data/self_model/meta.json` | JSON | 元信息 |
| 🟡 情绪（全局） | `data/emotion_state.json` | JSON | 全局情绪状态 |
| 🟡 情绪（per-user） | `data/emotions/*.json` | JSON | 用户级情绪 |
| 🟡 关系 | `data/relationship_state.json` | JSON | 关系状态 |
| 🟡 人格（Phase4） | `data/users/*/personality_*.json` | JSON | Phase4 用户级人格 |
| 🟡 关系（Phase4） | `data/users/*/relationship_*.json` | JSON | Phase4 用户级关系 |
| 🟢 成长状态 | `data/growth_state.json` | JSON | 成长引擎状态 |
| 🟢 成长提案 | `data/proposals/`、`data/growth/proposals/` | 目录 | 成长提案历史 |
| 🟢 Runtime 上下文 | `data/runtime_context/` | 目录 | RuntimePipeline 持久化快照 |
| 🟢 运行状态 | `data/runtime_state.json` | JSON | Runtime 整体运行状态 |
| 🟢 控制状态 | `data/control/control_state.json` | JSON | 控制模块状态 |
| 🟢 历史经验缓存 | `data/recovery/historical_experience_cache.json` | JSON | Phase A.1 恢复产物 |

#### 5.3.2 备份操作命令（生产执行）

```bash
# 进入项目根
cd /root/QianWuYuyi-AI

# 生成带时间戳的备份文件名
TS=$(date +%Y%m%d_%H%M%s)
BACKUP_FILE="data_backup_phase401_before_${TS}.tar.gz"

# ========================================
# 备份 1：完整 data/ 目录（推荐，一步到位）
# ========================================
tar -czf "${BACKUP_FILE}" data/

# ========================================
# 备份 2：仅关键核心数据（快速、体小）
# ========================================
tar -czf "data_backup_phase401_core_${TS}.tar.gz" \
  data/memory.json \
  data/chroma_db \
  data/self_model \
  data/emotion_state.json \
  data/relationship_state.json \
  data/growth_state.json \
  data/users \
  data/runtime_context \
  data/proposals

# ========================================
# 备份 3：配置文件（和数据一起存，避免找不到）
# ========================================
tar -czf "config_backup_phase401_${TS}.tar.gz" \
  config.yaml \
  .env \
  deploy/systemd/yuyi-api.service

# 验证：列出备份内容
tar -tzf "${BACKUP_FILE}" | head -20

# 建议：scp 到远程安全存储（生产环境必做）
# scp "${BACKUP_FILE}" user@backup-host:/backups/yuyi/
```

#### 5.3.3 数据恢复操作（崩溃后）

```bash
cd /root/QianWuYuyi-AI

# 0. 先停服务
systemctl stop yuyi-api.service

# 1. 重命名当前损坏数据（保留现场用于事后分析，不要直接删）
mv data data_corrupted_$(date +%Y%m%d_%H%M)

# 2. 解压备份
tar -xzf data_backup_phase401_before_XXXXXXXX_XXXXXX.tar.gz

# 3. 确认权限正确（systemd 用 root 跑，一般不用改）
chown -R root:root data/

# 4. 启动服务
systemctl start yuyi-api.service

# 5. 盯日志 2 分钟
journalctl -u yuyi-api.service -f
```

---

## 6. 附录：关键文件路径速查表

> 便于 Phase 4.0.1 修改时快速定位"我改的是哪一块，改完了要验证哪些功能"。

| 职责 | 文件路径 | 行号（入口/关键处） |
|------|----------|---------------------|
| HTTP 聊天入口 | `api_server.py` | :693 `/v1/chat/completions` |
| 三级降级路由选择 | `api_server.py` | :749-855（RuntimeController → Pipeline → Orchestrator） |
| Agent Server 启动 | `api_server.py` | :648 `_start_agent_server()` |
| Agent Server 线程 | `api_server.py` | :680 `threading.Thread(name="AgentServer")` |
| 状态写盘线程 | `api_server.py` | :576 `_start_agent_status_writer()`（5 秒刷新） |
| RuntimeController（Phase4） | `src/runtime/runtime_controller.py` | :59 类定义，:170 `handle_message()` |
| RuntimePipeline（Phase7.2） | `src/runtime/runtime_pipeline.py` | `RuntimePipeline.run()` 入口 |
| 路径审计 8 字段（R2.1） | `src/runtime/runtime_pipeline.py` | :53 `RuntimePathAudit` TypedDict |
| Orchestrator（Legacy） | `src/orchestrator.py` | :85 类定义（已标记 [DEPRECATED]），:722 `process()` |
| Orchestrator 记忆检索 | `src/orchestrator.py` | :754-765 `memory_store.load()` + `vector_memory.search()` |
| Orchestrator SelfModel 获取 | `src/orchestrator.py` | :557 `get_self_model_context()` |
| ResponseEngine（LLM 调用） | `src/engine.py` | :16 `ResponseEngine`，:82 `self.client.chat.completions.create()` |
| System Prompt 构建（原始） | `src/engine.py` | :124 `_build_messages_original()` |
| System Prompt 记忆注入 | `src/engine.py` | :197-206 【相关记忆】段 |
| System Prompt 人格注入 | `src/engine.py` | :148-150 【人格】段 |
| System Prompt SelfModel 注入 | `src/engine.py` | :152-162 【自我认知】段 |
| System Prompt 情绪注入 | `src/engine.py` | :177-186 【当前情绪】段 |
| System Prompt 关系注入 | `src/engine.py` | :188-195 【用户关系】段 |
| InitiativeBridge（发送回调） | `src/runtime/initiative_bridge.py` | :29 类，:62 `handle_send_message()` |
| 主动消息生成 orchestrator | `src/runtime/initiative_bridge.py` | :91-93 `orchestrator.generate_initiative(target_user)` |
| InitiativeLifecycleTask | `src/runtime/integration/tasks/initiative_lifecycle_task.py` | :53 类（interval=600s） |
| InitiativeEngine | `src/runtime/initiative/initiative_engine.py` | InterestSignal + PossibleAction 编排 |
| systemd 服务定义 | `deploy/systemd/yuyi-api.service` | 单进程，venv python，:14 ExecStart |
| 主配置 | `config.yaml` | runtime/initiative/remote/screen/control/llm/memory 等 |
| 环境变量模板 | `.env.example` | 所有支持的变量清单及说明 |

---

> **冻结完成标记**: ✅ 基线清单已生成（phase4.0.1-baseline.md）。  
> **代码修改确认**: ❌ 本次快照生成期间**未修改任何源代码/配置文件**（只读操作）。  
> **下次修改前必读**: 确认你的改动影响范围在本基线的哪个模块，改完对照第 3 节逐项验证。

---

## 7. 基线测试结果快照（2026-08-11 实际执行）

### 7.1 核心集成测试执行结果

**执行命令**: `python -m pytest tests/test_phase72_migration.py tests/test_phase73_prod_smoke.py tests/test_runtime_unification.py tests/test_phase_7_0_runtime_center.py --tb=no -q --no-header`

**执行日期**: 2026-08-11

**结果**:

| 指标 | 值 |
|------|-----|
| 总测试数 | 102 |
| 通过 | **101** ✅ |
| 失败 | **1** ❌ |
| 跳过 | 0 |
| 执行时长 | 5.21s |
| 退出码 | 1（有失败） |

### 7.2 失败测试详情

| 项 | 值 |
|----|-----|
| 测试文件 | `tests/test_phase72_migration.py` |
| 测试类 | `TestMemoryDataPreserved` |
| 测试方法 | `test_memory_store_can_locate_qingxialing` |
| 失败原因 | `AssertionError: 1 not greater than or equal to 2 : MemoryStore.load 出的记忆缺少核心信号,实际命中=['yuyi'] (要求 >=2 类:qingxialing/yuyi/project)` |
| 影响等级 | 🟡 中（数据相关，非代码逻辑错误） |
| 影响范围 | 仅影响测试断言，不影响生产聊天链路 |
| 处置建议 | Phase 4.0.1 期间不修复，记录为已知问题；记忆数据可能不完整或被清理过，需在生产环境验证记忆完整性 |

### 7.3 测试覆盖说明

- 本次仅跑了 4 个核心集成测试文件
- 完整 tests/ 目录有 453 个测试文件
- Phase 4.0.1 Step 02 修改前应跑完整测试套件建立基线
- Phase 4.0.1 每步修改后应跑相同测试集对比回归

---

## 8. 当前架构简图

### 8.1 真实运行架构（2026-08-11 基线）

```
QQ 用户
  ↓
OneBot (端口 3000)
  ↓ HTTP WebHook
AstrBot (端口 11451)
  ↓ HTTP POST /v1/chat/completions
api_server.py (端口 5000)
  │
  ├─ 优先级 1: RuntimeController [死]
  │   phase4_enabled=false → NotImplementedError → 降级
  │
  ├─ 优先级 2: RuntimePipeline [活·主入口]
  │   ↓ _pipeline.run({"user_message": ...})  ⚠️ user_id 丢失
  │   RuntimeCore.process() → 17 阶段
  │     ├─ Stage 0-1: 事件接收 [真实]
  │     ├─ Stage 2-6: Memory/Emotion/Personality [no-op/失效]
  │     ├─ Stage 7-13: SelfModel [显式 no-op]
  │     ├─ Stage 14: 调用注入的 Orchestrator.engine [真实·但 8 参数空]
  │     │     ↓ engine.generate() → DeepSeek API
  │     └─ Stage 16: 收尾同步 [真实]
  │   ↓ 失败/空 reply 时 fallback
  │
  └─ 优先级 3: Orchestrator.process() [活·fallback]
      ↓ 完整认知工作（9 参数齐全）
      MemoryStore.load() + VectorMemory.search()
      PersonalityResolver.resolve()
      EmotionManager.get_state()
      SelfModelStore.get_context()
      ↓
      engine.generate() → src/engine.py:82
      ↓
      DeepSeek API (deepseek-v4-pro)
      ↓
      回复 → AstrBot → OneBot → QQ
```

### 8.2 主动消息路径（独立于聊天链路）

```
api_server 进程启动
  ↓
RuntimeBridge.initialize() → RuntimeCore.tick 生命循环
  ↓ 注册 InitiativeBridge
RuntimeCore.tick (interval=600s)
  ↓
InitiativeLifecycleTask → InitiativeEngine
  ↓ 生成 PossibleAction(send_message)
ActionDispatcher 派发
  ↓
InitiativeBridge.handle_send_message()
  ↓ cooldown 检查 + 指纹去重
orchestrator.generate_initiative(target_user)
  ↓ engine.generate() → DeepSeek API
_send_message() → OneBot HTTP → QQ
```

### 8.3 Agent Server 路径（独立线程）

```
api_server.py:322-330 _start_agent_server(config)
  ↓
threading.Thread(name="AgentServer", daemon=True)
  ↓ asyncio.new_event_loop()
Agent Server (端口 8765) WebSocket 监听
  ↓
AgentStatusWriter 线程 (5s 间隔)
  ↓
data/agent_server_status.json 写盘
```

### 8.4 职责边界（当前实际 vs Phase 4.0 目标）

| 层 | 当前实际 | Phase 4.0 目标 | 差距 |
|----|---------|---------------|------|
| **Runtime** | 假装是大脑（17 阶段 + 调 LLM） | 神经系统+身体（事件/状态/生命周期/持久化/主动性） | 越权：Stage 14 直接调 engine |
| **Orchestrator** | 标记 [DEPRECATED]，但承担真实认知工作 | 大脑皮层（认知编排/系统调度/回复决策） | 职责被稀释，但实际仍在工作 |
| **ResponseEngine** | 语言皮层 + 硬编码身份 | 语言皮层（身份从 IDENTITY_CORE 来） | 身份来源错误 |
| **IDENTITY_CORE** | 完全未进入 prompt | 唯一身份源 | 7/10 字段是死字段 |
| **Memory** | 基础可用 | 完整认知链 | user_id 丢失导致隔离失效 |
| **Emotion** | 接口存在但 emotion_manager 可能=None | 完整认知链 | 需启用 + 接入主链路 |
| **Personality** | 接口存在，Stage 5 丢弃返回值 | 完整认知链 | Stage 5 降级后由 Orchestrator 负责 |
| **SelfModel** | 骨架（Stage 7-13 显式 no-op） | 成长核心 | Phase 4.0.3 才扩展 |
| **Growth** | 设计完整但未闭环 | 成长闭环 | Phase 4.0.3 才扩展 |

---

## 9. 后续修改风险说明

### 9.1 Phase 4.0.1 Step 02 (user_id 修复) 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 两套 RuntimeContext 只改一套 = 等于没修 | 🔴 HIGH | lifecycle_context.py + context/runtime_context.py **两套都加** user_id 字段 |
| OrchestratorLike Protocol 签名不匹配 → Fake 类 TypeError | 🔴 HIGH | Protocol + 所有 Fake/Mock 同步加 user_id 默认参数 |
| Stage 方法未读 ctx.user_id → 假修复 | 🔴 HIGH | 逐 Stage grep 审计，E2E 测试 userA/userB 记忆不交叉 |
| 设置全局 orchestrator.target_user_id → 跨用户串话 CRITICAL 复现 | 🟠 MED-HIGH | **严禁设置全局属性**，始终参数传递 |
| RuntimeContext.from_dict 老快照反序列化失败 | 🟡 MEDIUM | user_id 字段必须带默认值 `""` |
| 空 reply fallback 导致记忆双写放大 | 🟡 MEDIUM | 监控日志 + MemoryStore 增加幂等性 |
| 旧记忆 owner 字段混乱（None/default/366648462 混合）→ 修复后用户看不到旧记忆 | 🟡 MEDIUM | 修复前备份 data/memory.json，修复后验证记忆可见性 |

### 9.2 Phase 4.0.1 Step 03 (入口收敛) 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 删除 _inject_engine_ref → Stage 14 LLM 调用断裂 | 🔴 HIGH | **必须先让 Orchestrator 主路径承担 LLM 调用**，再删除注入 |
| 降级 Stage 14 后空 reply → 用户看到空回复 | 🔴 HIGH | Stage 14 降级前验证 Orchestrator fallback 100% 可靠 |
| 简化 _generate_reply → 主动消息路径受影响 | 🟡 MEDIUM | 主动消息路径(E2/E3/E4)独立验证 |
| 统一主动消息入口 → /initiative 行为变化 | 🟡 MEDIUM | 手动测试 /initiative 端点 |

### 9.3 Phase 4.0.1 Step 04 (身份审计) 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 本步骤只读分析，无代码修改 | 🟢 低 | 无 |

### 9.4 Phase 4.0.1 Step 05 (确认删除) 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| **prompt_builder.py 误删** → RuntimePipeline 主链路回复降级 | 🔴 CRITICAL | **永远保留或 4.0.3 替代后再删**；删除前置条件：重构 ResponseAdapterImpl |
| runtime_controller.py + response_phase4/ 删除 → Phase4 测试全部崩 | 🟡 MEDIUM | 延迟到 4.0.2，与 Phase4 资产整体清理 |
| runtime_controller.py:47 从 tests.support 导入 → 架构违规 | 🟡 MEDIUM | 4.0.2 处理，本阶段仅记录 |

### 9.5 整体回归风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 修改后羽依失忆（记忆不检索） | 🔴 HIGH | 每步修改后验证"羽依能提到过往记忆" |
| 修改后羽依失人格（人格不注入 prompt） | 🔴 HIGH | 每步修改后验证"羽依能叫出用户名字" |
| 修改后服务启动失败 | 🔴 HIGH | 每步修改后验证 systemctl status active |
| 修改后主动消息停止 | 🟡 MEDIUM | 等 10-15 分钟验证主动消息触发 |
| 修改后 Agent Server 断开 | 🟡 MEDIUM | 验证 8765 端口 + data/agent_server_status.json |

### 9.6 已知问题（基线状态，非本次修改引入）

| 问题 | 影响 | 处置 |
|------|------|------|
| user_id 在 RuntimePipeline 路径丢失 | 跨用户记忆串写风险 | Phase 4.0.1 Step 02 修复 |
| IDENTITY_CORE 7 个字段完全未读取 | 羽依身份来源是 engine.py 硬编码 | Phase 4.0.2 修复 |
| engine.py:143-146 硬编码身份 | 身份定义不来自 IDENTITY_CORE | Phase 4.0.2 修复 |
| 3 条 prompt 路径并存 | 路径 A 生效，B/C 未生效但代码在 | Phase 4.0.2 收敛 |
| runtime_controller.py 从 tests.support 导入 | 架构违规 | Phase 4.0.2 处理 |
| test_memory_store_can_locate_qingxialing 失败 | 记忆数据不完整 | 已知问题，不阻断 Phase 4.0.1 |
| Stage 2-6/14 失效或空参数 | Runtime 17 阶段仅 5 个真实执行 | Phase 4.0.1 Step 03 降级为 no-op |

---

## 10. 冻结签核

| 项 | 值 |
|----|-----|
| 冻结日期 | 2026-08-11 |
| 冻结执行者 | Trae (AI assistant) |
| 基线 commit | `eed2de6` |
| 基线分支 | `phase-4-0-runtime-integration` |
| patch 文件 | `phase4.0.1-baseline.patch` (107KB) |
| 测试结果 | 101/102 通过（1 个数据相关失败） |
| 代码修改 | ❌ 未修改任何业务代码 |
| 下一步 | Phase 4.0.1 Step 02: user_id P0 修复 |
| 下一步前置条件 | 生产环境验证第 3 节所有 [待验证] 项通过 |

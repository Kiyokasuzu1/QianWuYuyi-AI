# QianWuYuyi-AI Phase 4.1 Runtime Unification 审计报告

**审计日期**：2026-07-29
**审计范围**：Orchestrator 与 RuntimeCore 关系 / RuntimeContext 完整性 / 情绪闭环 / Memory 统一入口 / 主动消息一致性 / Screen 与 Token 定位
**审计原则**：仅检查现状，不重构，不新增功能，先报告再开发

---

## 0. 核心结论

**项目存在"两套大脑"问题。**

- **对话大脑（Orchestrator）**：处理用户聊天、记忆召回、人格读取、prompt 构建、LLM 调用
- **生命大脑（RuntimeCore）**：后台 60s tick、状态衰减、规则决策、行动分发、反思成长

两者通过 RuntimeBridge 做**事件级弱耦合**（Orchestrator → RuntimeCore 单向），但**实例级完全隔离**——各自持有独立的 MemoryStore、PersonalityResolver、EmotionManager、SelfModel 实例，状态互不同步。

**RuntimeCore 不是"唯一生命入口"**，而是旁路观察者 + 周期决策器。真正的聊天入口是 `api_server.py:213 → Orchestrator.process()`。

---

## 1. 当前真实架构图

### 1.1 两套大脑视角

```
┌──────────────────────── 对话大脑 (Orchestrator) ────────────────────────┐
│                                                                         │
│  MemoryStore (独立)   PersonalityResolver (独立)   SelfModelStore (独立) │
│       │                      │                        │                 │
│       ▼                      ▼                        ▼                 │
│  memory_store.load()   personality.resolve()   self_model.get_context() │
│  vector.search(top_k=5)                                                  │
│       │                                                                 │
│       ▼                                                                 │
│  RuntimeContext.assemble_context()  ◄── 无 token 预算、无 emotion_manager│
│       │                                                                 │
│       ▼                                                                 │
│  ScreenContextManager.get_screen_description() ──► screen_block         │
│       │                                                                 │
│       ▼                                                                 │
│  ResponseEngine._build_messages_opt()  ◄── Token 优化在此（事后压缩）   │
│       │                                                                 │
│       ▼                                                                 │
│  DeepSeek API ──► 回复                                                 │
│                                                                         │
└──────────┬──────────────────────────────────────────────────────────────┘
           │ EventBus (publish_event)
           │ MessageReceivedEvent / MemoryCreatedEvent / MessageRespondedEvent
           ▼
┌──────────────────────── RuntimeBridge（单向桥接）──────────────────────┐
│  subscribe_all → 筛选 9 种事件 → runtime_core.inject_event()            │
│  on_user_message(user_id, content) → inject "user.input"               │
│  register_action_handler("send_message", initiative_sender)           │
└──────────┬──────────────────────────────────────────────────────────────┘
           │ inject_event / action dispatch
           ▼
┌──────────────────────── 生命大脑 (RuntimeCore) ─────────────────────────┐
│                                                                         │
│  MemoryStore (独立)   PersonalityResolver (独立)   EmotionManager (独立) │
│  SelfModelManager (独立)   DecisionEngine   ActionDispatcher           │
│       │                                                                 │
│       ▼                                                                 │
│  _tick() 每 60s:                                                       │
│    - self_state.decay()                                                │
│    - world_state.update_time()   ◄── environment={} 永远空，无 screen   │
│    - _maybe_decide()  ◄── DecisionEngine 只看 self_state 标量，无屏幕   │
│    - autonomous_decision_layer.on_tick()                                │
│    - autonomous_scheduler.on_tick()                                     │
│       │                                                                 │
│       ▼                                                                 │
│  ActionDispatcher.dispatch() ──► InitiativeBridge ──► QQ 主动消息      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 数据流图（用户消息完整路径）

```
QQ 用户
  ↓
AstrBot 插件
  ↓ HTTP POST /v1/chat/completions
api_server.py:213
  ↓
Orchestrator.process(user_message)  ← 真正的聊天入口
  │
  ├─ Step 1: publish MessageReceivedEvent  ──► EventBus
  │                                              │
  │                                              ▼
  │                                    RuntimeBridge 监听
  │                                              │
  │                                              ▼
  │                                    RuntimeCore.inject_event()
  │                                    （旁路，不影响本次聊天）
  │
  ├─ Step 2: memory_store.load() + vector_memory.search(top_k=5)
  │
  ├─ Step 3: runtime_context.assemble_context()  ← 不含 emotion_manager
  │
  ├─ Step 3.5: screen_context_manager.get_screen_description()  ← 旁路注入
  │
  ├─ Step 4: personality_resolver.resolve()
  │
  ├─ Step 5-7: emotion_pre (死代码，em_manager 永远 None)
  │
  ├─ Step 8: engine.generate()  ← Token 优化在此介入
  │            │
  │            └─► DeepSeek API ──► 回复
  │
  ├─ Step 9-10: 保存记忆 + publish MemoryCreatedEvent
  │
  ├─ Step 11: emotion_post (死代码)
  │
  └─ Step 12: relationship_post (死代码)

  旁路（异步，60s tick）:
  RuntimeCore._tick()
    ├─ self_state.decay()
    ├─ _maybe_decide()  ← 不看屏幕，不看本次对话内容
    └─ 偶尔触发 send_message action ──► InitiativeBridge ──► QQ
```

---

## 2. Orchestrator 与 RuntimeCore 关系

### 2.1 真实关系

| 维度 | Orchestrator | RuntimeCore |
|---|---|---|
| 角色 | 单次对话执行器 | 后台生命循环（旁路） |
| 触发 | 每次 HTTP 请求 | 60s 定时 tick |
| 是否参与聊天 | ✅ 是（唯一路径） | ❌ 否（仅旁路观察） |
| 持有 MemoryStore | ✅ 独立实例 | ✅ 独立实例（via memory_adapter） |
| 持有 PersonalityResolver | ✅ 独立实例 | ✅ 独立实例 |
| 持有 EmotionManager | ❌ 不持有（死代码尝试从 context 取，但取不到） | ✅ 独立实例（默认禁用） |
| 持有 SelfModel | ✅ SelfModelStore | ✅ SelfModelManager（不同类） |
| 实例是否共享 | ❌ 完全独立 | ❌ 完全独立 |

### 2.2 用户主动聊天是否经过 RuntimeCore？

**否。** `Orchestrator.process()` 全文 0 处调用 RuntimeCore。RuntimeCore 仅通过 RuntimeBridge 旁路接收事件，不影响本次聊天。

### 2.3 主动消息是否经过 RuntimeCore？

**部分。** 两条路径：
- **路径 A（主）**：`initiative_sender.py` 独立脚本，5-15 分钟定时调用 `orchestrator.generate_initiative()`，**不经过 RuntimeCore**
- **路径 B（辅）**：RuntimeCore 决策出 `send_message` action → InitiativeBridge → `orchestrator.generate_initiative()`，**经过 RuntimeCore**但默认配置下难以触发

### 2.4 屏幕事件是否能进入 RuntimeCore？

**否。** RuntimeCore 和整个 `src/runtime/` 对 `screen` 零引用。Screen 是 Orchestrator 的私有能力。

### 2.5 Remote Agent 事件是否能进入 RuntimeCore？

**部分。** Agent Server 事件不直接进入 RuntimeCore。仅通过 Orchestrator 的 EventBus 间接传递。

### 2.6 "两套逻辑"明确判断

**存在两套逻辑：**
- 代码层面：是。两套独立的 memory / personality / emotion / relationship / self-model 子系统
- 运行时层面：默认只有 Orchestrator 一套在跑（RuntimeCore 的所有重叠子系统被 config flag 关闭）
- **风险**：一旦开启 RuntimeCore 的 `emotion_enabled` / `adapters_enabled`，会出现两套独立演化、互不同步的状态

---

## 3. RuntimeContext 完整性审计

### 3.1 assemble_context() 返回的 key 清单

来源：`src/runtime/runtime_context.py:135-145`

返回字典仅包含 **7 个 key**：
- `system_messages`
- `prompt_blocks`
- `conversation`
- `self_model`
- `memory_summary`
- `relationship`
- `trace`

### 3.2 八种上下文存在性核对

| 期望上下文 | 状态 | 说明 |
|---|---|---|
| memory_context | ❌ 缺失 | 只有 `memory_summary`（裸 dict），无标准化 memory_context |
| personality_context | ❌ 缺失 | RuntimeContext 完全不加载人格，personality 在 orchestrator 中另行获取 |
| emotion_context | ❌ 缺失 | RuntimeContext 完全不接入情绪系统 |
| relationship_context | ⚠️ 部分 | 仅有 `relationship` key，存的是 `options["relationship_summary"]` 原值透传 |
| self_model_context | ⚠️ 部分 | 仅有 `self_model` key（命名与期望不一致） |
| screen_context | ❌ 缺失 | 屏幕上下文在 orchestrator 中单独获取，不进入 assembled_context |
| user_context | ❌ 缺失 | UserContext 在 orchestrator 中单独解析 |
| token_context | ❌ 缺失 | 完全无此概念 |

**缺失 5 项，部分存在 2 项，完整 0 项。**

### 3.3 Orchestrator 与 RuntimeContext 的 key 契约不一致

Orchestrator 期望从 `assembled_context` 读取：
- `emotion_manager`（行 260, 412, 433, 712）→ **不存在**，永远 falsy
- `relationship_profile`（行 271, 723）→ **不存在**（实际 key 是 `relationship`），永远 falsy

这是一个实际的 bug：Orchestrator 读取的 key 与 RuntimeContext 产出的 key 不匹配。

---

## 4. 情绪系统闭环审计

### 4.1 情绪闭环真实状态（每一步）

| 步骤 | 状态 | 断点 |
|---|---|---|
| 1. 输入（用户消息 → 事件） | ❌ 断开 | Orchestrator 不调 EmotionEventDetector；自构造的 EmotionEvent 字段非法（无 event_type，多了 content/timestamp） |
| 2. 处理（事件 → 状态） | ❌ 断开 | em_manager 永远 None（从 assembled_context 取不到），pre/post 都 early-return |
| 3. 存储（状态 → 持久化） | ❌ 断开 | Orchestrator 路径下 per-user 持久化代码不执行；仅 RuntimeCore 路径写 `data/emotion_state.json` |
| 4. 读取（状态 → Prompt） | ❌ 断开 | engine.py 逻辑正确但入参 `emotion_ctx` 永远 `{}` |
| 5. 反馈（状态 → 成长/SelfModel） | ⚠️ 仅 RuntimeCore 路径 | 通过 EmotionDynamicsEngine 接通，但默认禁用 |

### 4.2 关键断点定位

| # | 断点位置 | 现象 | 根因 |
|---|---|---|---|
| 1 | `orchestrator.py:260` / `:712` | `if "emotion_manager" in assembled_context` 永远 False | `runtime_context.py:135-145` 返回的字典无 `emotion_manager` key |
| 2 | `orchestrator.py:271` / `:723` | `if "relationship_profile" in assembled_context` 永远 False | runtime_context 返回的 key 是 `relationship`，不是 `relationship_profile` |
| 3 | `orchestrator.py:79-112` | Orchestrator 从不实例化 EmotionManager | 无 `self.emotion_manager = ...` 赋值 |
| 4 | `orchestrator.py:416-420` | 即使 em_manager 存在，EmotionEvent 构造非法 | EmotionEvent dataclass 无 `content`/`timestamp` 字段，缺必填 `event_type` |
| 5 | `orchestrator.py:265,438,485` | `getattr(state, "dominant", None)` 永远 None | EmotionState 无 `dominant`/`primary_emotion`/`intensity` 字段 |
| 6 | `routes.py:1451` | `emotion_manager.get_current_state()` 抛 AttributeError | EmotionManager 无此方法 |
| 7 | `routes.py:1469` | `_orchestrator.emotion_manager.state` 抛 AttributeError | Orchestrator 类无 `emotion_manager` 属性 |

### 4.3 情绪状态被读取的模块

| 消费方 | 状态 | 说明 |
|---|---|---|
| ResponseEngine (`engine.py:116-124`) | ❌ 入参永远空 | 逻辑正确但 `emotion_ctx` 永远 `{}` |
| `generate_initiative()` (`orchestrator.py:711-719`) | ❌ 同上 | 同上 |
| admin API (`routes.py:1451,1469`) | ❌ 抛异常 | 两个断点 |
| RuntimeCore (`runtime_core.py:2818`) | ⚠️ 仅 RuntimeCore 路径 | 需 `emotion_enabled=True` |
| EmotionDynamicsEngine | ✅ 成立 | 但仅 RuntimeCore 调用 |
| personality_adapter | ❌ 不读取 | grep 0 命中 |
| growth 模块 | ❌ 仅静态字面量 | 无运行时情绪状态读取 |

**结论**：情绪闭环在 Orchestrator 路径下**全链路断裂**。`src/emotion/*` 是完整代码资产，但未被任何活路径调用。

---

## 5. Memory 统一入口审计

### 5.1 五套 Memory 实现真实使用矩阵

| # | 实现类 | 状态 | 实例化位置 | 数据存储 |
|---|---|---|---|---|
| 1 | `MemoryStore` | ✅ LIVE | orchestrator.py:87、context_manager.py:37、runtime_core.py:346 | `data/memory.json` |
| 2 | `VectorMemory` | ⚠️ LIVE（写路径断） | orchestrator.py:88 | ChromaDB `data/chroma_db` |
| 3 | `MemorySystem` | ⚠️ 仅终端入口 | yuyi_core.py:41 | 通过内部 Store+Vector |
| 4 | `MemoryService` | ❌ DEAD CODE | 仅 tests + smoke_test | 无生产使用 |
| 5 | `MemoryRetriever` | ❌ DEAD CODE | 仅 tests | 无生产使用 |

### 5.2 辅助模块状态

| 辅助模块 | 状态 |
|---|---|
| MemoryContext | LIVE（经 ContextBuilder 间接使用，但 ContextBuilder 仅 terminal_chat 路径） |
| ContextBuilder | 仅终端入口存活，api_server 路径不用 |
| MemoryFormatter | LIVE（被 prompt_builder 使用，但 prompt_builder 本身是死代码） |
| MemoryGate | ❌ DEAD CODE（0 个实例化点） |
| MemoryRelevanceEvaluator | LIVE（但下游 MemoryService 死代码，仅 MemorySystem 与 RuntimeCore 路径用） |

### 5.3 关键缺陷

1. **向量索引写路径断裂**：`Orchestrator.process()` 在第 339 行调用 `memory_store.add()` 写入新记忆后，**从未**调用 `vector_memory.add_memory()` 或 `index_memories()`。新写入的记忆要等下次重启才进向量库。

2. **admin routes 幽灵属性**：`routes.py` 多处访问 `_orchestrator.memory_system`（行 1031/1136/1262/1299/1749/1827/1876/1938），但 Orchestrator 类**没有此属性**，所有 `hasattr` 检查恒为 False → 相关面板在非 mock 模式下数据为空。

3. **多套实现共用 `data/memory.json` 但无文件锁** → 并发写有竞争风险。

### 5.4 Memory Pipeline 现状

**写入**：
```
事件 → memory_store.add() → data/memory.json
                             ❌ 不触发 vector_memory.add_memory()
                             ❌ 无重要性判断（所有记忆同等对待）
                             ❌ 无验证
```

**读取**：
```
用户输入 → memory_store.load()（加载全部，无过滤）
         → vector_memory.search(top_k=5)（硬编码 5）
         → orchestrator 直接使用，未经统一 Memory Pipeline
```

**禁止情况**：不同模块自己读取 memory 的情况**存在**：
- Orchestrator 直接读 memory_store + vector_memory
- RuntimeCore 通过 memory_adapter 读（独立实例）
- admin routes 试图读 memory_system（不存在）

---

## 6. 主动消息一致性审计

### 6.1 三条路径对比

| 维度 | 路径 1：initiative_sender.py | 路径 2：RuntimeBridge+InitiativeBridge | 路径 3：ProactiveEngine |
|---|---|---|---|
| 入口 | 独立脚本 main_loop | RuntimeCore ActionDispatcher | CognitiveLayer on_tick |
| 实例化 Orchestrator | ✅ 新建独立实例 | 复用 initiative_sender 的实例 | 无关 |
| 与 api_server Orchestrator 同实例 | ❌ 否 | ❌ 否 | — |
| 实际状态 | ✅ LIVE | ✅ LIVE（依附路径 1） | ❌ DEAD CODE |

### 6.2 人格/情绪/记忆一致性

**api_server Orchestrator vs initiative_sender Orchestrator：**

| 状态字段 | 共享方式 | 一致性 |
|---|---|---|
| `self.history`（对话历史） | ❌ 不共享 | **不一致** — initiative_sender 看不到 api_server 的最近对话 |
| `self.relationship_profile` | ❌ 不共享 | **不一致** |
| `self.current_personality` | ❌ 不共享 | **部分一致**（底层走磁盘，但内存缓存分叉） |
| `self.memory_store` | 各自新建，同一文件 | **读一致**（每次 load 重读磁盘） |
| `self.vector_memory` | 各自新建，同一 ChromaDB | **读一致**（但写路径断） |
| `self.self_model_store` | 各自新建 | **读一致**（同一磁盘文件） |
| `self.runtime_context` | 各自新建 | **不一致** |

**关键风险**：`initiative_sender.py` 的 Orchestrator 持有**空的 `self.history`**，主动消息的"最近 10 轮对话"上下文实际为空，导致主动消息可能重复提问或脱离上下文。

### 6.3 死代码

- `src/proactive/proactive_engine.py`（ProactiveEngine）— 仅 tests 实例化，src/ 0 调用

---

## 7. Screen 能力规划审计

### 7.1 Screen 数据流向（现状）

```
local_agent (mss 截屏)
  ↓ base64 JPEG
AgentServer (WebSocket 8765)
  ↓
ScreenContextManager.get_screen_description()
  ↓
ScreenAnalyzer (pytesseract OCR)
  ↓ {available, text, description, ocr_success}
Orchestrator.process() Step 3.5 (行 235-251)
  ↓ screen_block 注入 prompt_blocks
ResponseEngine.generate() → DeepSeek API
  ↓
（到此结束，不向下传递给 RuntimeCore）
```

**Screen 是 Orchestrator 的私有能力，不是 Runtime 的能力。**

### 7.2 RuntimeCore 对 Screen 的感知

**零感知。**
- `src/runtime/runtime_core.py` 全文搜索 `screen`/`Screen`：0 处匹配
- 整个 `src/runtime/` 目录搜索 `screen`/`Screen`：0 处匹配
- `WorldState.environment` 字段默认空 dict，从未被 screen 数据填充
- `DecisionEngine.evaluate_all(world_state)` 的"探索"规则检查 `world_state.environment`，但永远为空 → 永远不触发
- RuntimeBridge 的 `_INTERESTING_EVENTS` 不包含任何 `screen.*` 事件类型

### 7.3 Screen Event Pipeline 现状

**不存在** `Screen Capture → Analyzer → RuntimeContext → Decision Engine` 链路。
只有 `Screen Capture → Analyzer → Orchestrator prompt 注入`。

### 7.4 理想 Screen Event Pipeline（未来设计，不实现）

```
Screen Capture (定时/事件触发)
  ↓ ScreenDataEvent
Screen Analyzer (OCR + 描述生成)
  ↓ ScreenContextEvent
RuntimeContext (注入 screen_context)
  ↓
Decision Engine (基于屏幕内容决策)
  ↓ Action (如"主动聊游戏")
权限审批
  ↓
执行控制（未来）
```

---

## 8. Token 优化重新定位审计

### 8.1 Token 优化当前位置

- **入口**：`src/engine.py:24-48`，`ResponseEngine.generate()` 在构建 messages 前检查 `is_token_opt_enabled()`
- **实现**：`_build_messages_opt()` 做记忆摘要 + 历史压缩
- **阶段**：Prompt 构建阶段（最后一环），属于 **Context Assembly Layer 的下游**

### 8.2 RuntimeContext 与 Token 优化的关系

**完全脱节。**

- `RuntimeContext.assemble_context()` **没有** token_budget / max_tokens / token_context 参数
- 记忆召回数量 `top_k=5` 是**硬编码**，不受 token_opt 控制
- 历史长度 `history[-10:]`（orchestrator）和 `history[-20:]`（engine）是**独立硬编码**
- Token 优化只在 engine.py 中对**已召回的记忆**做事后摘要压缩，**不影响召回数量**

### 8.3 Token 优化理想位置

**应在 Context Assembly 阶段就介入：**

```
当前（反应式）：
  [召回] memory_store.load() + vector.search(top_k=5)  ← 硬编码
  → [截取] history[-10:]  ← 硬编码
  → [组装] RuntimeContext.assemble_context()  ← 无预算感知
  → [构建] engine._build_messages_opt()  ← Token 优化在此（事后压缩）

理想（前瞻式）：
  [1] 先决定 token 预算（总 8k，记忆 1k，历史 2k，屏幕 0.5k...）
  → [2] 基于预算决定召回多少记忆 / 截取多少历史
  → [3] RuntimeContext.assemble_context(token_budget=...) 统一组装
  → [4] engine 仅做最终格式化，不再做压缩
```

**差距**：当前 token 优化是反应式（事后压缩），而非前瞻式（先定预算再决定召回量）。当记忆库很大时，`memory_store.load()` 会加载全部记忆到内存，token_opt 只能事后摘要，浪费召回阶段的 I/O。

### 8.4 Token 优化是否应该移动到 RuntimeContext 阶段？

**建议：是，但不重构。**

最小接线方案：
1. 在 `RuntimeContext.assemble_context()` 中增加可选的 `token_budget` 参数
2. 当 token_opt 启用时，根据预算动态调整 `recent_turns` 和 `memory_top_k`
3. engine.py 的 `_build_messages_opt()` 退化为最终格式化，不再做压缩

---

## 9. 已统一 vs 仍绕过 Runtime 的模块

### 9.1 已统一到 Runtime 的模块

**无。** 当前没有任何模块真正统一到 RuntimeCore。所有聊天相关逻辑都在 Orchestrator，RuntimeCore 仅做旁路观察。

### 9.2 仍然绕过 Runtime 的模块

| 模块 | 绕过方式 | 影响 |
|---|---|---|
| 用户聊天 | `Orchestrator.process()` 不调 RuntimeCore | 聊天不经过生命入口 |
| 主动消息（主路径） | `initiative_sender.py` 直接调 `orchestrator.generate_initiative()` | 不经过 RuntimeCore 决策 |
| 屏幕感知 | `Orchestrator` 直接注入 prompt | RuntimeCore 不知道屏幕内容 |
| Token 优化 | `engine.py` 事后压缩 | 不在 Context Assembly 阶段 |
| 记忆召回 | `Orchestrator` 直接调 memory_store + vector_memory | 无统一 Memory Pipeline |
| 人格读取 | `Orchestrator` 直接调 personality_resolver | 与 RuntimeCore 的人格实例独立 |
| 情绪 | **完全断开** | Orchestrator 取不到 em_manager |
| 关系 | **部分断开** | key 不匹配（relationship vs relationship_profile） |

---

## 10. 最小修改路线

**原则：不重构，只接线。让现有模块真正连通。**

### 10.1 路线图（按优先级）

| 优先级 | 修改项 | 文件 | 说明 |
|---|---|---|---|
| P0 | 修复 RuntimeContext key 契约 | `runtime_context.py` + `orchestrator.py` | 统一 `relationship` vs `relationship_profile`、`self_model` vs `self_model_context` |
| P0 | 让 Orchestrator 能取到 EmotionManager | `orchestrator.py` 或 `runtime_context.py` | 注入 emotion_manager 到 assembled_context |
| P0 | 修复 EmotionEvent 构造 | `orchestrator.py:416-420` | 使用正确的字段（event_type 而非 content） |
| P0 | 修复 EmotionState 字段访问 | `orchestrator.py:265,438,485` | 不访问不存在的 `dominant` 字段 |
| P1 | 向量索引实时更新 | `orchestrator.py:339` 后 | 写入记忆后调用 `vector_memory.add_memory()` |
| P1 | initiative_sender 共享 history | `initiative_sender.py` 或架构调整 | 解决主动消息上下文残缺 |
| P2 | Screen 数据注入 WorldState | `runtime_core.py` 或 `runtime_bridge.py` | 让 RuntimeCore 感知屏幕 |
| P2 | Token 优化前移 | `runtime_context.py` | 增加 token_budget 参数 |
| P3 | 清理死代码 | 多个文件 | MemoryService / MemoryRetriever / MemoryGate / ProactiveEngine |
| P3 | 修复 admin routes 幽灵属性 | `routes.py` | `_orchestrator.memory_system` → `_orchestrator.memory_store` |

### 10.2 每项修改的测试要求

按用户规则"所有修改必须有测试"：
- P0 修复需补充 `test_runtime_context_contract.py`
- P0 情绪修复需补充 `test_emotion_wiring.py`
- P1 向量索引需补充 `test_vector_index_realtime.py`
- P1 history 共享需补充 `test_initiative_history_consistency.py`

---

## 11. Phase 4.1 - Phase 4.5 路线规划

### Phase 4.1：Runtime 统一入口（情绪 + Memory 接线）

**目标**：让 Orchestrator 能取到 EmotionManager，让情绪闭环接通。

**范围**：
1. 修复 RuntimeContext key 契约（`relationship` vs `relationship_profile`）
2. 让 EmotionManager 注入到 assembled_context
3. 修复 EmotionEvent 构造（使用正确字段）
4. 修复 EmotionState 字段访问
5. 向量索引实时更新
6. 添加对应测试

**不做**：
- 不重构 Orchestrator / RuntimeCore 架构
- 不统一实例（保持两套独立实例，但通过 RuntimeContext 传递引用）
- 不删除死代码
- 不开发屏幕控制

### Phase 4.2：主动消息一致性

**目标**：让主动消息能看到最近对话历史。

**范围**：
1. initiative_sender 共享 api_server 的 history（通过持久化或共享存储）
2. 统一主动消息的人格/记忆上下文
3. 添加测试

### Phase 4.3：Screen 进入 Runtime

**目标**：让 RuntimeCore 感知屏幕内容。

**范围**：
1. Screen 数据注入 WorldState.environment
2. RuntimeBridge 订阅 screen 事件
3. DecisionEngine 基于屏幕内容决策
4. 添加测试

**不做**：不实现点击控制闭环

### Phase 4.4：Token 优化前移

**目标**：让 Token 优化在 Context Assembly 阶段介入。

**范围**：
1. RuntimeContext.assemble_context() 增加 token_budget 参数
2. 根据预算动态调整 memory_top_k 和 recent_turns
3. engine.py 退化为最终格式化
4. 添加测试

### Phase 4.5：死代码清理与稳定化

**目标**：清理死代码，修复破损接口。

**范围**：
1. 删除 MemoryService / MemoryRetriever / MemoryGate / ProactiveEngine
2. 修复 admin routes 幽灵属性
3. 修复 EmotionStore ImportError
4. 清理 orchestrator.py.backup / orchestrator_hooks.py
5. 添加回归测试

---

## 12. 架构问题汇总

| 编号 | 问题 | 严重度 | 影响 |
|---|---|---|---|
| U1 | "两套大脑"：Orchestrator 与 RuntimeCore 实例级完全隔离 | 高 | 状态不一致风险 |
| U2 | RuntimeContext 缺失 5 种上下文（personality/emotion/screen/user/token） | 高 | 上下文不完整 |
| U3 | Orchestrator 与 RuntimeContext key 契约不一致 | 高 | emotion/relationship 处理永远跳过 |
| U4 | 情绪闭环全链路断裂（7 个断点） | 高 | 情绪永不影响回复 |
| U5 | 向量索引写路径断裂 | 高 | 新记忆检索不到 |
| U6 | initiative_sender 持有空 history | 高 | 主动消息上下文残缺 |
| U7 | Screen 完全不进入 RuntimeCore | 中 | 自主决策无法基于屏幕 |
| U8 | Token 优化是事后压缩而非前瞻式 | 中 | 召回阶段浪费 I/O |
| U9 | admin routes 访问不存在的 memory_system 属性 | 中 | 控制台面板数据为空 |
| U10 | 5 套 Memory 实现 + 3 个死代码 | 中 | 维护负担 |
| U11 | EmotionEvent 构造非法（字段不匹配） | 中 | 即使接通也会抛异常 |
| U12 | EmotionState 无 dominant/intensity 字段但被访问 | 中 | 永远取到 None |

---

## 13. 审计结论

### 13.1 当前状态

QianWuYuyi-AI 项目存在**两套平行的认知链路**：
- **Orchestrator 负责对话**（含 Screen、Token 优化）
- **RuntimeCore 负责生命感**（含决策、反思、成长）

两者通过 RuntimeBridge 做**事件级弱耦合**（单向：Orchestrator → RuntimeCore），但**实例级完全隔离**。

**RuntimeCore 不是"唯一生命入口"**，而是旁路观察者 + 周期决策器。

### 13.2 统一目标可行性

按用户要求"让所有输入、事件、行为决策都经过统一 Runtime 生命周期"，**不需要大规模重构**。最小接线方案：

1. **不合并两套实例**（保持 Orchestrator + RuntimeCore 双轨）
2. **通过 RuntimeContext 传递引用**（让 Orchestrator 能取到 RuntimeCore 的 EmotionManager）
3. **修复 key 契约**（统一 RuntimeContext 产出与 Orchestrator 期望的 key）
4. **接通断点**（EmotionEvent 构造、EmotionState 字段、向量索引写路径）

### 13.3 风险提示

- **不删除已有模块**（用户规则）
- **不大规模重构**（用户规则）
- **不改变 QQ 接口**（用户规则）
- **不改变 API Key**（用户规则）
- **不提前开发屏幕控制**（用户规则）
- **所有修改必须测试**（用户规则）
- **新代码放独立分支**（用户规则）

---

**审计完成。等待用户确认后进入 Phase 4.1 开发阶段。**

# QianWuYuyi-AI Phase 4.1 Runtime Unification 变更日志

**日期**：2026-07-30
**分支**：`fix/runtime-unification`
**目标**：修复认知链路断裂，让 RuntimeCore 提供状态，Orchestrator 负责即时交互，通过 RuntimeContext 统一传递

---

## 1. 修改内容总览

| # | 文件 | 修改类型 | 说明 |
|---|---|---|---|
| 1 | [src/emotion/emotion_state.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/emotion/emotion_state.py) | 修改 | 新增 `dominant` 和 `intensity` 派生属性 |
| 2 | [src/runtime/runtime_context.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_context.py) | 修改 | 扩展 `assemble_context()` 新增 8 种上下文 key |
| 3 | [src/orchestrator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py) | 修改 | 初始化 EmotionManager；修复 EmotionEvent 构造；向量索引实时更新；history 持久化 |
| 4 | [initiative_sender.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/initiative_sender.py) | 修改 | 在 `generate_initiative()` 前调用 `load_recent_history()` |
| 5 | [tests/test_runtime_unification.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/tests/test_runtime_unification.py) | 新增 | 24 个测试覆盖 5 类验证场景 |
| 6 | [PHASE4_1_IMPLEMENTATION_PLAN.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/PHASE4_1_IMPLEMENTATION_PLAN.md) | 新增 | 实施计划文档 |

**总计**：修改 4 个文件，新增 2 个文件

---

## 2. 详细修改内容

### 2.1 EmotionState 新增派生属性（P0）

**文件**：`src/emotion/emotion_state.py`

**修改**：
- 新增 `@property dominant`：根据 valence/arousal/anxiety 计算主导情绪标签
  - `valence > 0.3 + arousal > 0.7` → `"joyful"`
  - `valence > 0.3 + arousal < 0.3` → `"serene"`
  - `valence < -0.3 + arousal > 0.7` → `"tense"`
  - `valence < -0.3 + arousal < 0.3` → `"flat"`
  - `anxiety > 0.6` → `"anxious"`
  - 其他 → `"neutral"`
- 新增 `@property intensity`：`abs(valence) * 0.6 + arousal * 0.4`
- `to_dict()` 新增 `dominant` 和 `intensity` 两个 key

**向后兼容**：纯新增派生属性，旧字段全部保留

---

### 2.2 RuntimeContext 扩展（P0）

**文件**：`src/runtime/runtime_context.py`

**修改**：
- `assemble_context()` 新增 5 个可选参数：`emotion_manager`、`personality_context`、`screen_context`、`user_context`、`token_context`
- 返回字典新增 8 个 key：
  - `personality_context`（透传参数）
  - `emotion_manager`（透传 EmotionManager 引用）
  - `emotion_context`（从 emotion_manager 构建的 `{dominant, intensity}` dict）
  - `relationship_profile`（`relationship` 的别名）
  - `screen_context`（透传参数）
  - `user_context`（透传参数）
  - `token_context`（透传参数）

**向后兼容**：所有新参数默认 None，不传时行为与旧版完全一致；旧 7 个 key 全部保留

---

### 2.3 Orchestrator 修复（P0 + P1）

**文件**：`src/orchestrator.py`

**修改**：

#### 2.3a 初始化 EmotionManager 和 EmotionEventDetector
- `__init__` 中新增 `EmotionManager()` 和 `EmotionEventDetector()` 初始化
- 失败时降级为 None，不影响主流程

#### 2.3b process() 传入 emotion_manager
- `assemble_context()` 调用时传入 `emotion_manager`、`personality_context`、`user_context`

#### 2.3c 修复 _process_emotion_pre 的 EmotionEvent 构造
- **修复前**：手动构造 `EmotionEvent(source=..., content=..., timestamp=...)` —— 字段非法，抛异常
- **修复后**：使用 `EmotionEventDetector.detect(user_message)` 返回正确字段的事件

#### 2.3d 修复 _process_emotion_post
- 使用 `EmotionState.dominant` 和 `EmotionState.intensity` 派生属性（现已存在）
- 助手回复不再构造 EmotionEvent（只持久化状态）
- 持久化到 `data/emotions/{user_id}.json`

#### 2.3e 向量索引实时更新
- `memory_store.add(memory_record)` 后新增 `vector_memory.add_memory(memory_record)`
- `memory_record` 补充 `role: "user"` 字段（VectorMemory 只索引 user 记忆）

#### 2.3f History 持久化
- 新增 `_persist_history()`：process() 末尾将最近 20 轮对话写入 `data/conversation_history.json`
- 新增 `load_recent_history(max_turns=10)`：从文件加载最近对话历史

---

### 2.4 initiative_sender 状态一致性（P1）

**文件**：`initiative_sender.py`

**修改**：
- `main_loop()` 中 `generate_initiative()` 前新增 `orchestrator.load_recent_history(max_turns=10)`
- 加载失败时静默降级（debug 日志），不影响主流程

---

### 2.5 测试文件（新增）

**文件**：`tests/test_runtime_unification.py`

**测试覆盖**：

| # | 测试类 | 测试数 | 覆盖项 |
|---|---|---|---|
| 1 | `TestRuntimeContextKeys` | 4 | RuntimeContext key 完整性 |
| 2 | `TestEmotionManagerAccess` | 3 | EmotionManager 可被 Orchestrator 访问 |
| 3 | `TestEmotionInPrompt` | 5 | Emotion 变化进入 prompt |
| 4 | `TestVectorRealtimeUpdate` | 3 | Memory 写入后立即向量检索 |
| 5 | `TestInitiativeStateConsistency` | 5 | 主动消息读取统一状态 |
| 6 | `TestEmotionEventContract` | 4 | EmotionEvent 字段契约回归保护 |

**总计**：24 个测试

**Mock 策略**：
- 使用 `_FakeChromaCollection` 和 `_FakeChromaClient` 内存版 ChromaDB 模拟
- mock `openai`、`sentence_transformers` 避免重型依赖
- 临时工作目录避免污染真实数据

---

## 3. 测试结果

### 3.1 新增测试

```
tests/test_runtime_unification.py
============================= 24 passed in 0.59s ==============================
```

| 测试类 | 通过数 |
|---|---|
| TestRuntimeContextKeys | 4/4 |
| TestEmotionManagerAccess | 3/3 |
| TestEmotionInPrompt | 5/5 |
| TestVectorRealtimeUpdate | 3/3 |
| TestInitiativeStateConsistency | 5/5 |
| TestEmotionEventContract | 4/4 |

### 3.2 回归测试

```
tests/test_engine_context.py + test_emotion_state.py + test_emotion_runtime_integration.py
============================= 20 passed in 0.21s ==============================
```

```
tests/test_token_opt.py（单独运行）
============================= 23 passed in 0.10s ==============================
```

**注**：`test_token_opt.py` 与 `test_engine_context.py` 不能在同一个 pytest session 中运行，因为 `test_engine_context.py` 在模块级设置了 `sys.modules['src.token_opt'] = MagicMock()`，会泄漏到同一 session 的其他测试。此为**预存的测试隔离问题**，与 Phase 4.1 修改无关。

---

## 4. 当前闭环状态

### 4.1 情绪闭环（已修复）

```
用户消息
  ↓
Orchestrator.process()
  ↓ Step 3: assemble_context(emotion_manager=self.emotion_manager)
  │         → emotion_context 非空 ✅
  ↓ Step 7: _process_emotion_pre()
  │         → EmotionEventDetector.detect() 返回正确事件 ✅
  │         → em_manager.process_event(ev) 更新状态 ✅
  ↓ Step 8: engine.generate(emotion_context=emotion_ctx)
  │         → 【当前情绪】段出现真实内容 ✅
  ↓ Step 11: _process_emotion_post()
  │         → 持久化到 data/emotions/{user_id}.json ✅
  ↓ Step 14: _persist_history()
  │         → 持久化到 data/conversation_history.json ✅
```

### 4.2 向量索引闭环（已修复）

```
Orchestrator.process() Step 10:
  memory_store.add(memory_record)           ✅ 写入 data/memory.json
  vector_memory.add_memory(memory_record)  ✅ 实时写入 ChromaDB（新增）
下次 search() → 能检索到新记忆（无需重启）✅
```

### 4.3 主动消息一致性（已修复）

```
api_server.py Orchestrator.process()
  ↓ _persist_history() → data/conversation_history.json ✅

initiative_sender.py main_loop()
  ↓ load_recent_history() → 加载最近 10 轮对话 ✅
  ↓ generate_initiative() → 现在有完整上下文 ✅
```

### 4.4 RuntimeContext 契约（已修复）

```
assemble_context() 返回 15 个 key：
  旧 key（7）: system_messages, prompt_blocks, conversation, self_model,
              memory_summary, relationship, trace
  新 key（8）: personality_context, emotion_manager, emotion_context,
              relationship_profile, screen_context, user_context, token_context
              (+ emotion_manager 引用)
向后兼容 ✅
```

---

## 5. 未做的事项（符合计划）

- ❌ 不删除任何已有模块
- ❌ 不重构 Orchestrator / RuntimeCore 架构（保持双轨）
- ❌ 不合并两套实例（保持独立）
- ❌ 不改变 QQ 接入
- ❌ 不修改 API Key
- ❌ 不开发屏幕控制（等待 Phase 4.3）
- ❌ 不将 Screen 接入 RuntimeCore（等待 Phase 4.3）
- ❌ 不前移 Token 优化到 Context Assembly（等待 Phase 4.4）

---

## 6. 下一阶段建议

### Phase 4.2（建议）：人格与记忆实例共享
- 当前 Orchestrator 和 RuntimeCore 仍各自实例化 PersonalityResolver 和 MemoryStore
- 建议通过 RuntimeBridge 共享实例引用，而非持久化文件同步
- 评估是否需要将 RuntimeCore 的 tick 结果注入 Orchestrator 的上下文

### Phase 4.3（建议）：Screen 接入 RuntimeCore
- 当前 ScreenContextManager 在 Orchestrator 中初始化
- 建议将屏幕上下文作为 RuntimeCore 的输入源，通过 RuntimeContext 传递
- 评估屏幕上下文的更新频率与 RuntimeCore tick 频率的匹配

### Phase 4.4（建议）：Token 优化前移
- 当前 Token 优化在 engine.generate() 中触发
- 建议将 Token 优化逻辑前移到 RuntimeContext.assemble_context()
- 通过 token_context 控制上下文裁剪策略

### 技术债清理（建议）
- 修复 `test_engine_context.py` 的模块级 `sys.modules['src.token_opt'] = MagicMock()` 泄漏问题
- 评估 EmotionManager 在 RuntimeCore 和 Orchestrator 中的双实例是否需要统一
- 评估 memory_store 在 Orchestrator 和 RuntimeCore 中的双实例是否需要统一

---

**Phase 4.1 Runtime Unification 完成。**

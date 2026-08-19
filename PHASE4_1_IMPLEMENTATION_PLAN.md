# QianWuYuyi-AI Phase 4.1 Runtime Unification 实施计划

**日期**：2026-07-29
**分支**：`fix/runtime-unification`（基于当前 `fix/token-opt-config-loading`）
**目标**：修复认知链路断裂，让 RuntimeCore 成为长期状态中心，Orchestrator 负责即时交互，通过 RuntimeContext 统一传递

---

## 0. 核心策略

**不重构架构，只接线。**

- 不合并 Orchestrator 与 RuntimeCore 两套实例
- 不删除任何已有模块
- 通过 RuntimeContext 传递 RuntimeCore 的 EmotionManager 引用给 Orchestrator
- 修复 EmotionEvent 构造与 EmotionState 字段访问的 bug
- 接通向量索引实时更新
- 解决 initiative_sender 上下文残缺

---

## 1. 修改文件列表

| # | 文件 | 修改类型 | 优先级 | 说明 |
|---|---|---|---|---|
| 1 | `src/runtime/runtime_context.py` | 修改 | P0 | 扩展 assemble_context() 输出 8 种上下文 key，新增可选参数接受 emotion_manager 引用 |
| 2 | `src/orchestrator.py` | 修改 | P0 | 初始化 EmotionManager；process() 中传入 em_manager 到 assemble_context；修复 EmotionEvent 构造；修复 EmotionState 字段访问 |
| 3 | `src/emotion/emotion_state.py` | 修改 | P0 | 新增 `dominant` 和 `intensity` 派生属性（从 valence/arousal 计算），保持向后兼容 |
| 4 | `src/engine.py` | 不修改 | P0 | 验证 `【当前情绪】` 段在 emotion_context 非空时生效（已正确） |
| 5 | `src/memory/vector.py` | 不修改 | P1 | `add_memory()` 方法已存在，无需修改 |
| 6 | `src/orchestrator.py` | 修改 | P1 | 在 memory_store.add() 后调用 vector_memory.add_memory() |
| 7 | `initiative_sender.py` | 修改 | P1 | 通过 RuntimeBridge 获取共享状态快照，注入到 Orchestrator.history |
| 8 | `tests/test_runtime_unification.py` | 新增 | - | 5 类测试覆盖所有修改 |

**总计**：修改 4 个文件，新增 1 个测试文件

---

## 2. 详细修改方案

### 修改 1：RuntimeContext 扩展（P0）

**文件**：`src/runtime/runtime_context.py`

**当前问题**：
- `assemble_context()` 返回 7 个 key，缺失 personality_context / emotion_context / screen_context / user_context / token_context
- Orchestrator 期望读取 `emotion_manager` 和 `relationship_profile`，但 RuntimeContext 不产出这些 key

**修改方案**：
- `assemble_context()` 新增可选参数 `emotion_manager=None`、`personality_context=None`、`screen_context=None`、`user_context=None`、`token_context=None`
- 返回字典新增 8 个 key（保持旧 key 兼容）：
  ```python
  {
      # 旧 key（保持）
      "system_messages": [...],
      "prompt_blocks": [...],
      "conversation": [...],
      "self_model": {...},
      "memory_summary": {...},
      "relationship": ...,  # 旧 key 保持
      "trace": [...],
      # 新增 key
      "personality_context": ...,   # 透传 personality_context 参数
      "emotion_manager": ...,       # 透传 emotion_manager 参数（EmotionManager 实例）
      "emotion_context": {...},     # 从 emotion_manager 构建的 emotion_ctx dict
      "relationship_profile": ...,  # 别名，与 "relationship" 同值
      "screen_context": ...,        # 透传 screen_context 参数
      "user_context": ...,          # 透传 user_context 参数
      "token_context": ...,         # 透传 token_context 参数
  }
  ```

**向后兼容**：
- 所有新参数默认 None，不传时行为与旧版完全一致
- 旧 key 全部保留
- 新增 `relationship_profile` 作为 `relationship` 的别名（同值），让 Orchestrator 的 `if "relationship_profile" in assembled_context` 成立

**风险**：低。纯新增，不修改现有逻辑

---

### 修改 2：Orchestrator 初始化 EmotionManager（P0）

**文件**：`src/orchestrator.py`

**当前问题**：
- Orchestrator 不实例化 EmotionManager
- process() 期望从 assembled_context 取 `emotion_manager`，但 RuntimeContext 不产出
- _process_emotion_pre/post 构造的 EmotionEvent 字段非法（传了 content/timestamp，但 EmotionEvent 只接受 event_type/intensity/description/source）
- 访问 state.dominant / state.intensity，但 EmotionState 无这些字段

**修改方案**：

**2a. __init__ 中实例化 EmotionManager**：
```python
# 在 __init__ 中新增
try:
    from src.emotion.emotion_manager import EmotionManager
    self.emotion_manager = EmotionManager()
except Exception as e:
    print(f"[Orchestrator] EmotionManager 初始化失败: {e}")
    self.emotion_manager = None
```

**2b. process() 中传入 emotion_manager 到 assemble_context**：
```python
assembled_context = self.runtime_context.assemble_context(
    user_id=self.target_user_id or "default",
    conversation={"recent_turns": self.history[-10:] if self.history else []},
    self_model_snapshot=self.self_model_context_provider.get_context(),
    memory_summary={"recent_memories": chat_memories},
    options={"relationship_summary": self.relationship_profile},
    # 新增：传递 EmotionManager 引用
    emotion_manager=self.emotion_manager,
    personality_context=personality_context,
    user_context={"user_id": self.target_user_id} if self.target_user_id else None,
)
```

**2c. 修复 _process_emotion_pre 的 EmotionEvent 构造**：
```python
# 当前（错误）：
ev = EmotionEvent(source="user_message", content=user_message, timestamp=...)

# 修复后（正确字段）：
from src.emotion.emotion_event_detector import EmotionEventDetector
detector = EmotionEventDetector()
ev = detector.detect(user_message)  # 返回正确的 EmotionEvent 或 None
if ev:
    em_manager.process_event(ev)
```

**2d. 修复 _process_emotion_post 的 EmotionEvent 构造**：
```python
# 助手回复不触发情绪事件（EmotionEventDetector 只检测用户消息）
# 改为：从回复中检测情绪反馈，或直接跳过
# 简化方案：post 阶段只持久化状态，不再构造 EmotionEvent
```

**2e. 修复 EmotionState 字段访问**：
```python
# 当前（错误）：getattr(state, "dominant", None)
# EmotionState 无 dominant 字段，但有 valence/arousal

# 修复后：使用新增的 dominant 派生属性（见修改 3）
dominant = getattr(state, "dominant", None)  # 现在能取到值
intensity = getattr(state, "intensity", 0.0)  # 现在能取到值
```

**风险**：中。修改了 process() 的调用参数，但新参数默认 None，向后兼容

---

### 修改 3：EmotionState 新增派生属性（P0）

**文件**：`src/emotion/emotion_state.py`

**当前问题**：
- EmotionState 只有 valence/arousal/curiosity/anxiety/confidence/energy
- Orchestrator 和 engine.py 期望读取 `dominant`（主导情绪）和 `intensity`（强度）

**修改方案**：
- 新增 `@property dominant`：根据 valence/arousal/anxiety/curiosity 计算主导情绪标签
  - valence > 0.3 且 arousal > 0.7 → "joyful"
  - valence > 0.3 且 arousal < 0.3 → "serene"
  - valence > 0.3 → "positive"
  - valence < -0.3 且 arousal > 0.7 → "tense"
  - valence < -0.3 且 arousal < 0.3 → "flat"
  - valence < -0.3 → "uneasy"
  - anxiety > 0.6 → "anxious"
  - 其他 → "neutral"
- 新增 `@property intensity`：返回 `abs(valence) * 0.6 + arousal * 0.4`（综合强度）

**向后兼容**：
- 只新增 property，不修改现有字段
- to_dict() 新增 dominant 和 intensity 两个 key（保持旧 key）

**风险**：低。纯新增派生属性

---

### 修改 4：向量索引实时更新（P1）

**文件**：`src/orchestrator.py`

**当前问题**：
- `memory_store.add(memory_record)` 后不调用 `vector_memory.add_memory()`
- 新记忆要等重启才进向量库

**修改方案**：
- 在 process() Step 10 的 `self.memory_store.add(memory_record)` 之后，新增：
```python
# 同步到向量索引
try:
    if self.vector_memory and memory_record.get("role") == "user":
        self.vector_memory.add_memory(memory_record)
except Exception as e:
    print(f"[Orchestrator] 向量索引同步失败: {e}")
```

**注意**：
- memory_record 当前没有 `role` 字段，需要补充 `"role": "user"` 到 memory_record 构造
- VectorMemory.add_memory() 只索引 role=="user" 的记忆

**风险**：低。add_memory() 已实现且带异常保护

---

### 修改 5：initiative_sender 状态一致性（P1）

**文件**：`initiative_sender.py`

**当前问题**：
- initiative_sender 新建独立 Orchestrator 实例（line 228）
- 其 history 为空，看不到 api_server 的最近对话
- 人格/情绪/记忆状态各自独立

**修改方案（最小修改）**：
- 不共享 Orchestrator 实例（跨进程无法共享 Python 对象）
- 采用**持久化状态同步**：
  1. api_server 的 Orchestrator 在每次 process() 后，将 history 追加到 `data/conversation_history.json`
  2. initiative_sender 的 Orchestrator 在 generate_initiative() 前，从 `data/conversation_history.json` 加载最近 10 轮对话到 self.history

**具体修改**：
- `src/orchestrator.py` process() 末尾新增 `_persist_history()` 方法
- `initiative_sender.py` main_loop 中 generate_initiative() 前新增 `orch.load_recent_history()`

**向后兼容**：
- 新增方法，不修改现有 generate_initiative() 逻辑
- 加载失败时降级为空 history（与当前行为一致）

**风险**：中。跨进程文件读写，需注意并发（但 initiative_sender 间隔 5-15 分钟，并发概率极低）

---

## 3. 风险分析

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| RuntimeContext 新参数导致旧调用方异常 | 低 | 所有新参数默认 None，旧调用不传时行为不变 |
| EmotionManager 初始化失败影响聊天 | 低 | try/except 保护，失败时 emotion_manager=None，聊天正常 |
| EmotionEvent 构造修复后行为变化 | 低 | 使用 EmotionEventDetector.detect() 返回正确事件或 None |
| EmotionState 新增 property 影响序列化 | 低 | to_dict() 显式控制序列化字段，property 不自动进入 to_dict() |
| 向量索引并发写 | 低 | add_memory() 内部有 try/except，ChromaDB 支持并发 upsert |
| 跨进程 history 文件并发读写 | 低 | initiative_sender 间隔长，且加载失败降级为空 |
| EmotionManager 持久化路径冲突 | 中 | Orchestrator 路径写 data/emotions/{user_id}.json，RuntimeCore 路径写 data/emotion_state.json，两者不同路径，不冲突 |

---

## 4. 测试计划

**文件**：`tests/test_runtime_unification.py`

### 测试覆盖

| # | 测试类 | 覆盖项 | 说明 |
|---|---|---|---|
| 1 | `TestRuntimeContextKeys` | RuntimeContext key 完整性 | 验证 assemble_context() 返回 8 种上下文 key |
| 2 | `TestEmotionManagerAccess` | EmotionManager 可被 Orchestrator 访问 | 验证 assembled_context["emotion_manager"] 非空 |
| 3 | `TestEmotionInPrompt` | 情绪变化进入 prompt | 验证 engine.py 的 `【当前情绪】` 段出现真实内容 |
| 4 | `TestVectorRealtimeUpdate` | Memory 写入后立即向量检索 | 写入新记忆 → search → 能返回 |
| 5 | `TestInitiativeStateConsistency` | 主动消息读取统一状态 | 验证 history 持久化与加载 |

### 测试细节

**TestRuntimeContextKeys**：
- `test_all_8_keys_present()`：调用 assemble_context 并传入所有参数，验证返回 dict 包含 8 个 key
- `test_backward_compatible_no_extra_args()`：不传新参数，验证旧 7 个 key 仍存在
- `test_relationship_profile_alias()`：验证 relationship_profile 与 relationship 同值
- `test_emotion_context_built_from_manager()`：传入 EmotionManager，验证 emotion_context 非空

**TestEmotionManagerAccess**：
- `test_orchestrator_has_emotion_manager()`：验证 Orchestrator 实例有 emotion_manager 属性
- `test_assembled_context_contains_emotion_manager()`：验证 process() 后 assembled_context 含 emotion_manager
- `test_emotion_manager_persists_state()`：验证 process() 后 data/emotions/{user_id}.json 变化

**TestEmotionInPrompt**：
- `test_emotion_context_reaches_engine()`：mock engine.generate，验证 emotion_context 非空
- `test_emotion_state_dominant_property()`：验证 EmotionState.dominant 返回正确标签
- `test_emotion_state_intensity_property()`：验证 EmotionState.intensity 返回数值
- `test_emotion_appears_in_system_prompt()`：构造情绪上下文，验证 `【当前情绪】` 段出现

**TestVectorRealtimeUpdate**：
- `test_add_memory_then_search()`：写入记忆 → 立即 search → 能返回
- `test_non_user_memory_not_indexed()`：写入 role!=user 的记忆 → search → 不返回
- `test_vector_search_after_multiple_adds()`：多次写入 → search → 全部可检索

**TestInitiativeStateConsistency**：
- `test_persist_history()`：process() 后验证 data/conversation_history.json 更新
- `test_load_recent_history()`：从文件加载 history → 验证非空
- `test_load_history_fallback_on_error()`：文件不存在时降级为空 list

---

## 5. 实施顺序

```
Step 1: 创建分支 fix/runtime-unification
Step 2: 修改 EmotionState（新增 dominant/intensity property）  ← 修改 3
Step 3: 修改 RuntimeContext（扩展 assemble_context）            ← 修改 1
Step 4: 修改 Orchestrator（初始化 EmotionManager + 修复构造）   ← 修改 2
Step 5: 修改 Orchestrator（向量索引实时更新）                    ← 修改 4
Step 6: 修改 Orchestrator（history 持久化）+ initiative_sender   ← 修改 5
Step 7: 编写 tests/test_runtime_unification.py
Step 8: 运行测试，修复失败项
Step 9: 运行回归测试（test_engine_context.py, test_token_opt.py）
Step 10: 生成 PHASE4_1_CHANGELOG.md
```

---

## 6. 不做的事项

- ❌ 不删除任何已有模块（MemoryService / MemoryRetriever / ProactiveEngine 等死代码保留）
- ❌ 不重构 Orchestrator / RuntimeCore 架构（保持双轨）
- ❌ 不合并两套实例（保持独立）
- ❌ 不改变 QQ 接入
- ❌ 不修改 API Key
- ❌ 不开发屏幕控制
- ❌ 不统一 Memory Pipeline（多套实现保留）
- ❌ 不前移 Token 优化到 Context Assembly（Phase 4.4 再做）
- ❌ 不将 Screen 接入 RuntimeCore（Phase 4.3 再做）

---

## 7. 预期结果

### 7.1 修复后的情绪闭环

```
用户消息
  ↓
Orchestrator.process()
  ↓ Step 2: memory_store.load() + vector_memory.search()
  ↓ Step 3: runtime_context.assemble_context(emotion_manager=self.emotion_manager)
  │         → assembled_context["emotion_manager"] 非空
  │         → assembled_context["emotion_context"] 非空
  ↓ Step 5: emotion_ctx = assembled_context["emotion_context"]  ← 现在非空
  ↓ Step 7: _process_emotion_pre()
  │         → EmotionEventDetector.detect(user_message)  ← 返回正确事件
  │         → em_manager.process_event(ev)  ← 状态更新
  ↓ Step 8: engine.generate(emotion_context=emotion_ctx)  ← 非空
  │         → 【当前情绪】段出现真实内容
  ↓ Step 11: _process_emotion_post()
  │         → 持久化到 data/emotions/{user_id}.json  ← 现在执行
  ↓
回复（受情绪影响）
```

### 7.2 修复后的向量索引

```
用户消息
  ↓
Orchestrator.process() Step 10:
  memory_store.add(memory_record)  ← 写入 data/memory.json
  vector_memory.add_memory(memory_record)  ← 新增：实时写入 ChromaDB
  ↓
下次 search() → 能检索到新记忆（无需重启）
```

### 7.3 修复后的主动消息一致性

```
api_server.py Orchestrator.process()
  ↓ 末尾 _persist_history()
  ↓ data/conversation_history.json  ← 更新

initiative_sender.py main_loop()
  ↓ generate_initiative() 前
  ↓ orch.load_recent_history()
  ↓ 从 data/conversation_history.json 加载最近 10 轮
  ↓ generate_initiative()  ← 现在有完整上下文
```

---

## 8. 验收标准

| # | 验收项 | 验证方法 |
|---|---|---|
| 1 | RuntimeContext 返回 8 种上下文 key | 单元测试 TestRuntimeContextKeys |
| 2 | Orchestrator 能访问 EmotionManager | 单元测试 TestEmotionManagerAccess |
| 3 | 情绪变化进入 prompt | 单元测试 TestEmotionInPrompt |
| 4 | 新记忆立即可被向量检索 | 单元测试 TestVectorRealtimeUpdate |
| 5 | 主动消息能读取最近对话历史 | 单元测试 TestInitiativeStateConsistency |
| 6 | 聊天后 data/emotions/ 产生变化 | 集成验证 |
| 7 | engine.py 的【当前情绪】段有真实内容 | 集成验证 |
| 8 | 回归测试全部通过 | test_engine_context.py + test_token_opt.py |

---

**等待用户确认后开始编码。**

# Phase 7.2 Cognitive Trace（设计冻结版）

> 版本：v1.0  ·  2026-08-10
> 状态：**待审核冻结**；冻结后分 7.2.1 / 7.2.2 / 7.2.3 三批实现
> 审核人：羽依架构委员会（用户）
> 范围：仅决策链可观测性；不改业务判断；不反写核心模块

---

## 1. 定位与目标

### 1.1 定位（官方声明）

**Phase 7.2 = 羽依决策链的可观测性层**

不记录羽依的"内部推理内容"，不展示人格判断依据，不披露完整记忆。
仅记录并呈现：

```
一次回复生命周期中：
  1. 输入了什么
  2. 调用了哪些核心子系统
  3. 每个子系统输出了什么【摘要级】信息
  4. 哪些状态影响了本次回复
  5. 最终走哪条生成路径（Runtime / Orchestrator Fallback / Legacy）
```

### 1.2 核心价值

回答问题：
> "羽依这次为什么这样回复我？"

用 Dashboard Timeline 呈现，而不是翻日志猜。

### 1.3 与其他 Trace 的职责边界

| 系统 | 回答问题 | 前缀 | 粒度 |
|---|---|---|---|
| Runtime Status Tracker（Phase 7.0） | 活没活？在做什么？ | `status.*` | 进程级 |
| Runtime Trace Recorder（Phase 7.0） | 运行正不正常？哪里报错、耗时多久？ | `trace.*`（JSONL 文件） | 请求级 |
| **Observation Layer Pipeline（Phase 7.1）** | 运行到哪一步了？ | `runtime.*` | Pipeline 阶段级 |
| **Cognitive Trace（Phase 7.2，本文档）** | **为什么这么运行/这么回复？** | `cognitive.*` | **决策上下文级** |
| Cognitive Trace (未来 7.2.x) | Memory/Personality/Emotion/Growth/SelfModel 各自调用摘要 | `cognitive.memory.*` / `cognitive.personality.*` / ... | 子系统输出摘要 |
| Phase 7.3 Health Score | 各子系统健康度？整体生命体得分？ | `health.*` | 子系统级指标 |
| EventBus（业务通信） | MemoryCreated / GrowthApproved 等业务事件 | 原前缀不变 | 业务动作 |

**硬约束**：Cognitive Trace 的事件全部走 Phase 7.1 已建好的同一条链：

```
[Hook 点在 Memory/Personality/Emotion/Growth/SelfModel 内]
                    │
                    ▼
    ObservationEventSink.emit(cognitive.xxx.*, ...)
                    │
                    ▼
           EventQueue (dict subscribers)
                    │
                    ▼
        Dashboard /stream (SSE) + /recent (轮询)
```

**禁止**：新增第二条 Dashboard 推送链；禁止 Cognitive Trace 直接操作 SSE；禁止往 EventBus 发 cognitive 事件。

---

## 2. 架构原则（冻结，不允许绕过）

### 2.1 八不原则

1. ❌ **不新增 import 依赖到核心模块**：Memory / Emotion / Personality / Growth / SelfModel **只允许**从 `src.runtime.observer.cognitive_hooks` import 一个 `safe_emit_cognitive()` 函数。严禁导入 EventQueue / Flask / Blueprint 等观察层以上组件。
2. ❌ **不改业务返回值**：Hook 的调用时机必须是**函数 return 之前的 try/except finally 块**或**拿到返回值之后**。Hook 不允许修改 `memory_ids`、不改变 `emotion state`、不改变 personality traits。
3. ❌ **不泄露完整私人内容**：Cognitive Trace 事件 payload 里禁止出现完整 memory text / growth 完整提案理由 / full self_model snapshot / emotion 任意细节历史。只允许摘要级统计字段（数量、id 列表、重要度范围、版本号、字段数等）。
4. ❌ **不写业务逻辑**：Cognitive hook 只能做「序列化 → emit → 吞异常」，不能加 if/else 决定某条记忆该不该显示，任何业务判断留在核心模块。
5. ❌ **不依赖任何配置文件 / API Key**：hook 里不读 config.yaml，不要求 key；observer 开关由注入的 sink = None 表示关闭。
6. ❌ **不预设子系统调用顺序**：事件 payload 只描述「发生了什么」(event happened)，**不允许**出现 `next_stage`, `upstream_stage` 之类暗示顺序的字段，未来 Runtime 重排流程时，Cognitive Trace 0 修改。
7. ❌ **不引入 threading / async**：hook 必须同步返回；阻塞 1ms 都不允许（Queue 有慢消费者丢弃保护所以 put_nowait 是 O(1)，但 hook 内仍禁用 sleep/IO）。
8. ❌ **不在 SelfModel 冻结前加入完整 self_model 内容 hook**（第三批再做，且只出版本号 + 字段数，不做"人格数据库浏览器"）。

### 2.2 必做隔离

| 场景 | 处理 |
|---|---|
| Memory.search 内 hook 抛任何异常 | `except Exception: pass`，**原 return 值不变**，测试要断言 hook 崩溃 → memory 仍返回原结果 |
| PersonalityResolver.resolve 后 hook 崩 | 返回之前算好的 PersonalityVector 不变 |
| EmotionManager.process_event 后 hook 崩 | state / delta 不回滚 |
| GrowthLoop.propose_growth hook 崩 | 提案仍照常写入 _proposals |
| observer 全局 disabled（sink=None） | 所有模块行为 100% 等同于 hook 不存在（Phase 6.x / 7.0 基线） |

---

## 3. 文件规划（冻结，不新增顶层目录）

```
# ======== 新增（仍属于 Observer） ========
src/runtime/observer/
├── cognitive_event.py        # CognitiveEventType 枚举 + schema_version 1.0 + CognitiveEvent dataclass
└── cognitive_hooks.py        # safe_emit_cognitive() 公共函数 + 各子系统专用 emit helper

# ======== 轻量修改（只加 hook，不改业务判断） ========
src/memory/memory_system.py
    - 在 search() 最终 return 之前（result 组装完后）加一行：
      safe_emit_cognitive("cognitive.memory.retrieved", trace_id=, session_id=, data={摘要})
src/personality/personality_resolver.py
    - resolve() return PersonalityVector 之前：safe_emit_cognitive("cognitive.personality.resolved", ...)
src/emotion/emotion_manager.py
    - process_event() return 之前：safe_emit_cognitive("cognitive.emotion.updated", ...)
src/growth/growth_loop.py
    - propose_growth() 成功写入 _proposals 后：safe_emit_cognitive("cognitive.growth.evaluated", ...)
src/personality/self_model_manager.py
    - snapshot() / build(...) 完成后：safe_emit_cognitive("cognitive.self_model.loaded", ...)

# ======== 复用（不新写） ========
复用 Phase 7.1:
    src/runtime/observer/event_sink.py  ObservationEventSink.emit()
    src/runtime/observer/event_queue.py EventQueue
    src/admin/dashboard/runtime_events_router.py  /stream + /recent
    static/admin/dashboard_v2/js/runtime_events.js  前端 SSE + 轮询（只改 icon / color 映射）
    static/admin/dashboard_v2/index.html  # 新增一张"决策链 Timeline"卡片（共用组件）
    static/admin/dashboard_v2/css/dashboard_v2.css  # 新增前缀 yroc-cog-*
```

---

## 4. CognitiveEvent 数据结构（冻结 schema）

### 4.1 schema_version 策略（与 RuntimeEvent 保持一致但独立）

```python
COGNITIVE_EVENT_SCHEMA_VERSION = "1.0"
```

- 字段新增、可选字段补全：保持 `1.0`（向后兼容，旧 dashboard 可忽略新字段）
- 必填字段改名、结构重构：升到 `1.1`（保留 `1.0` 解析逻辑）
- 命名空间大改 / 子系统重组织：升到 `2.0`

**每次 emit 的事件必须带 `schema_version`，即使未来所有 reader 都先验版本。**

### 4.2 CognitiveEventType 枚举（cognitive.* 命名空间）

> 命名规则：`cognitive.{subsystem}.{action_past_tense}`。
> 全部用"过去式"强调「已经发生的事实」，避免暗示"应该发生"。

**Phase 7.2.1（第一批，低风险）：**

| Event 类型 | Hook 点 | 触发时机 |
|---|---|---|
| `cognitive.memory.retrieved` | Memory.search() return 前 | 每次记忆检索后，不区分来源（向量 / 事件 / 身份 / 普通 chat 记忆统一汇总统计） |
| `cognitive.personality.resolved` | PersonalityResolver.resolve() return 前 | 每次人格向量解析后 |
| `cognitive.emotion.updated` | EmotionManager.process_event() return 前 | 每次情绪事件处理后 |

**Phase 7.2.2（第二批，Growth，需谨慎）：**

| Event 类型 | Hook 点 | 触发时机 |
|---|---|---|
| `cognitive.growth.evaluated` | GrowthLoop.propose_growth() 成功写入 _proposals 后 | 每次产生新成长提案（只写摘要） |

**Phase 7.2.3（第三批，Self Model，最后做）：**

| Event 类型 | Hook 点 | 触发时机 |
|---|---|---|
| `cognitive.self_model.loaded` | SelfModelManager.snapshot() / build() / load_from_dict() 重建完成后 | 每次 Self Model 重建 / 快照生成 |

### 4.3 统一事件骨架（CognitiveEvent dataclass）

```python
@dataclass
class CognitiveEvent:
    """Cognitive Trace 单条事件（决策链审计级）。"""
    event_type: str                           # cognitive.{sub}.{action}（来自上表）
    trace_id: str                             # = RuntimePipeline.lifecycle_id，用于把一次回答的所有 cognitive 事件串起来
    session_id: str                           # session 标识（用户会话维度）

    # 自动填充
    event_id: str = ""                        # evt_cog_{hex}，前缀区分 Runtime 事件
    schema_version: str = COGNITIVE_EVENT_SCHEMA_VERSION
    timestamp: float = 0.0                    # Unix 秒（float，与 RuntimeEvent 同精度）
    iso_timestamp: str = ""
    subsystem: str = ""                       # "memory" / "personality" / "emotion" / "growth" / "self_model" —— 前端 timeline 颜色用
    level: str = "info"                       # 与 RuntimeEvent 对齐：info / warn / error
    data: Dict[str, Any] = field(default_factory=dict)  # 摘要 payload，见下表
```

**payload 白名单（不允许超出以下字段；否则算架构违规）：**

---

#### 4.3.1 `cognitive.memory.retrieved`

```jsonc
{
  "trace_id": "lc_...",
  "session_id": "s_...",
  "event_type": "cognitive.memory.retrieved",
  "subsystem": "memory",
  "data": {
    // query 降级策略（审核调整 v1.1）：
    // 不默认展示 query 原文（可能含敏感信息如身份证、地址等）
    // 默认只给 preview(前80字符) + hash + length
    // Dashboard 默认显示"检索关键词长度 12 · hash a3b2c1d0"
    // 未来开启 debug 模式时才展示 query_preview
    "query_preview": "最近感觉...",     // 前 80 字符截断（不是全文）
    "query_length": 12,                  // 原始 query 长度
    "query_hash": "a3b2c1d0",            // MD5 前 8 位（去敏感关联用）

    "top_k": 5,                         // 请求多少条

    // 最关键：命中数
    "memory_count": 5,                  // 最终返回 len(result)

    // 来源拆分（给 Dashboard 画：从哪几类记忆里拿到了东西）
    "sources": {
      "identity": 1,                    // 是否命中身份记忆（0/1）
      "event": 2,                       // 人生事件条数
      "semantic": 1,                    // 向量语义记忆条数
      "chat": 1                         // 普通关键词匹配记忆条数
    },

    // 条目 ID（只能是 memory_id，不能有 content）
    "memory_ids": [
      "mem_ab12",
      "mem_cd34",
      "mem_ef56"
    ],

    // 分数范围
    "score_range": [0.7, 0.92],          // pool 中保留条目的 score 的 min/max；没命中则空
    "max_score": 0.92                    // 最高匹配分
  }
}
```

**禁止字段（反例）：**

```jsonc
❌ {
  "memories_full_content": [
    {"text":"2025年5月我们去了海边玩了一整天...", "user_id":"u1"}
  ],
  "user_reveal": {"birthday":"xxxx"}
}
```

---

#### 4.3.2 `cognitive.personality.resolved`

```jsonc
{
  "event_type": "cognitive.personality.resolved",
  "subsystem": "personality",
  "data": {
    // 1) 版本标识
    "persona_version": "v1.9",           // PersonalityResolver.resolve 自身版本
    "base_profile": "BASE",              // PersonalityProfile 基底名

    // 2) 输出 traits_used（只显示"高激活 TOP N 维度名"，不加原因）
    "traits_used": [
      "warmth",
      "gentleness",
      "caring"
    ],
    "traits_count": 25,                  // 维度总数（用于 Dashboard "已加载 XX 个特质"）

    // 3) Growth 指标参与了多少（参与数，不透露具体数值）
    "growth_metrics_used": 8,            // GrowthState 用到的 metrics 数：trust / closeness 等
    "growth_records_count": 128,         // PersonalityGrowthHistory 长度

    // 4) Self Model 是否参与（布尔）
    "self_model_involved": true,

    // 5) Tension 检测结果（只给摘要）
    "tension_count": 0                   // Personality Tension 数；>0 前端用黄底提示
  }
}
```

**禁止字段：**
```jsonc
❌ "trust_current_value": 0.85   // 数值级细节归 Growth，不归 Cognitive Trace
❌ "before_after": {"warmth": [0.6, 0.62]}  // 演化过程属于 personality_history 审计
❌ "reason": "因为用户最近很温柔"            // 任何原因不记录
```

---

#### 4.3.3 `cognitive.emotion.updated`

```jsonc
{
  "event_type": "cognitive.emotion.updated",
  "subsystem": "emotion",
  "data": {
    // 只给聚合后的情绪状态（EmotionState 对外语义摘要）
    "state": "calm",                     // 主情绪标签，英文
    "state_cn": "平静",                  // 中文（可选，前端直接用）
    "intensity": 0.35,                   // 0..1
    "valence": 0.62,                     // 情绪正面度 -1..1；没此概念不填

    // 走势：improving / declining / stable
    "trend": "stable",

    // 过程：是否触发衰减（布尔）
    "decay_applied": true,

    // Engine 使用版本
    "engine_version": "v1"
  }
}
```

**禁止字段：**
```jsonc
❌ "all_hist": [{"state":"joy","ts":...}]   // 完整历史归 EmotionTraceRepository
❌ "event_type_internal": "HUG_RECEIVED"    // 内部事件类型不对外
```

---

#### 4.3.4 `cognitive.growth.evaluated`（Phase 7.2.2）

```jsonc
{
  "event_type": "cognitive.growth.evaluated",
  "subsystem": "growth",
  "data": {
    // 最核心：这次有/无提案
    "proposal_exists": true,

    // 只给粗粒度重要度（枚举），不写为什么
    "importance": "high",                 // "low" / "medium" / "high"

    // 是否需要审批
    "requires_approval": true,

    // 最终状态（proposal.status）
    "accepted": false,                    // 只给布尔：approved / applied 都算 true

    // 分类
    "category": "personality",            // GrowthCategory 枚举值：personality / behavior / ...

    // 计数统计
    "pending_count": 3,                   // 此刻 GrowthLoop 待处理提案数
    "impact_previews_count": 2            // 影响预览条数（不展示内容只展示数量）
  }
}
```

**严格禁止字段：**
```jsonc
❌ "proposal_reason": "因为用户今天说累了，羽依应更..."  // 绝对不可以
❌ "impact_details": [{"before":0.6, "after":0.65, ...}] // 完整影响预览
❌ "description": "增加 closeness 指标..."               // 提案描述
```

---

#### 4.3.5 `cognitive.self_model.loaded`（Phase 7.2.3）

```jsonc
{
  "event_type": "cognitive.self_model.loaded",
  "subsystem": "self_model",
  "data": {
    "version": 12,                        // Self Model 版本号（identity.version）

    // 字段级加载情况（只给计数）
    "fields_loaded": 12,                  // SelfIdentity 加载的字段数
    "core_values_count": 5,               // 核心价值观条目数
    "stable_traits_count": 25,            // 稳定特质条目数
    "behavioral_patterns_count": 18,      // 行为模式条目数
    "preferences_count": 42,              // 偏好条目数

    // Awareness 三项（只留结构摘要，不是具体值 ×0.xx 也可以，这里保守给 0/1 是否具备：
    "awareness_components": [
      "experience_awareness",
      "trait_awareness",
      "identity_continuity"
    ],

    // 历史
    "growth_records_indexed": 128         // 索引过的 GrowthRecord 数量
  }
}
```

**严格禁止：**
```jsonc
❌ "core_values_full": [{"text":"用户信任我很重要","weight":0.9}]
❌ "snapshot_full": { ... 几十 KB 的自我认知快照 }
❌ "who_i_am_paragraph": "我是羽依..."   // 任何自然语言自我描述
```

---

## 5. Hook 实现细节（冻结）

### 5.1 两层架构：通用保护 + subsystem helper（审核调整 v1.1）

> **审核调整**：safe_emit_cognitive **不做业务 schema 白名单裁剪**。
> Observer 层不应该理解 Memory / Growth / SelfModel 的业务字段。
> 白名单由各 subsystem helper 在构造 payload 时自行保证。

#### 第一层：通用保护（cognitive_hooks.safe_emit_cognitive）

```python
def safe_emit_cognitive(
    event_type: str,
    *,
    trace_id: str,
    session_id: str,
    subsystem: str = "",
    level: str = "info",
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """发射认知事件 —— 通用保护层，不理解业务 schema。

    只负责：
      - 全局 sink 不可用时立即 return
      - sink.emit 任何异常被吞掉
      - event_type 不是 cognitive.* 开头 → 立即 return
      - data 总大小 > 8KB → 拒绝（log warning，不 emit）
      - 任意 value 字符串长度 > 200 chars → 截断到 200 chars（防泄露）
      - trace_id / session_id 从 thread-local 自动补全（如果调用方没传）
    """
```

#### 第二层：各 subsystem helper（构造 payload，保证白名单）

```python
# cognitive_hooks.py 内

def emit_memory_retrieved(*, trace_id: str, session_id: str,
                           query: str, top_k: int, result_count: int,
                           memory_ids: List[str], sources: Dict[str, int],
                           score_range: List[float], max_score: float) -> None:
    """构造 Memory payload 并发射 —— 只放白名单字段，不放 content/text。"""
    safe_emit_cognitive(
        "cognitive.memory.retrieved",
        trace_id=trace_id, session_id=session_id,
        subsystem="memory",
        data={
            "query_preview": query[:80],       # 截断，不是全文
            "query_length": len(query),        # 长度统计
            "query_hash": hashlib.md5(query.encode()).hexdigest()[:8],  # 去敏感 hash
            "top_k": int(top_k),
            "memory_count": int(result_count),
            "memory_ids": memory_ids[:20],
            "sources": sources,
            "score_range": score_range,
            "max_score": float(max_score) if max_score else 0.0,
        },
    )

def emit_personality_resolved(*, trace_id: str, session_id: str, ...) -> None:
    """构造 Personality payload 并发射 —— 只放 traits_used/version/counts。"""
    ...

def emit_emotion_updated(*, trace_id: str, session_id: str, ...) -> None:
    """构造 Emotion payload 并发射 —— 只放 state/intensity/trend。"""
    ...
```

**设计原则**：safe_emit_cognitive 不知道 Memory 是什么；emit_memory_retrieved 不知道 EventQueue 是什么。

### 5.2 Memory.search 注入示例（伪代码，必须实现的形状）

```python
# src/memory/memory_system.py:420
# 【return 之前 hook】

        # ── Phase 7.2 Cognitive Trace hook（只读，不改 result）──
        try:
            from src.runtime.observer.cognitive_hooks import safe_emit_cognitive
            # 只取 id；不能取内容
            ids = []
            if isinstance(memories_list, list):
                for m in memories_list:  # noqa: F841 (不在这里展开，只演示)
                    pass
            # ↓ 真正的 emit：必须保证 result 已经确定，不允许影响它
            safe_emit_cognitive(
                "cognitive.memory.retrieved",
                trace_id=self._extract_trace_id_hint(),  # 见 5.5
                session_id=self._extract_session_id_hint(),
                subsystem="memory",
                data={
                    "query": query[:200],
                    "top_k": int(top_k),
                    "memory_count": len(result),
                    "memory_ids": [self._safe_get_memid(x) for x in result][:20],  # 最多 20 个 id
                    "max_score": max_score,               # 摘要级
                    "sources": sources_count,             # identity/event/semantic/chat 分别多少
                    "score_range": [min_score, max_score] if min_score else [],
                },
            )
        except Exception:  # noqa: BLE001
            pass

        return result  # ← 必须在 hook 之后原样 return
```

### 5.3 trace_id / session_id 如何传给核心模块？（关键）

核心问题：MemorySystem / PersonalityResolver 这些类**没有 pipeline 的 lifecycle_id / session_id 上下文**——它们是独立对象。

**Phase 7.2.1 的解决方案（冻结，不新上 ContextVar 架构）**：

新增一个 **轻量运行时 thread-local 上下文载体**：

```python
# src/runtime/observer/cognitive_hooks.py
import threading

# Phase 7.x: thread-local only（当前同步 Flask + RuntimePipeline 架构足够）
# Future async runtime (Phase 8+): 替换为 contextvars.ContextVar
# 迁移时只需改 _cog_ctx 的定义为 ContextVar，set/get/clear 语义一致
_cog_ctx = threading.local()

def set_cognitive_context(*, trace_id: str, session_id: str) -> None:
    """在 RuntimePipeline.run() 开头 set，结尾 clear（try/finally）。"""
    _cog_ctx.trace_id = str(trace_id)
    _cog_ctx.session_id = str(session_id)

def clear_cognitive_context() -> None:
    _cog_ctx.__dict__.clear()

def _get_ctx_hint(key: str) -> str:
    return str(getattr(_cog_ctx, key, "") or "")
```

在 [RuntimePipeline.run()](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/runtime_pipeline.py#L616-L622) 的 `session_id = ...` 下面加：

```python
        # ── Phase 7.2: 把 trace(session) 上下文放到 thread-local ──
        try:
            from src.runtime.observer.cognitive_hooks import (
                set_cognitive_context,
                clear_cognitive_context,
            )
            set_cognitive_context(trace_id=lifecycle_id, session_id=session_id)
        except Exception:
            pass
        # 并且在 finally（所有 exit 路径）里 clear_cognitive_context()，避免线程复用污染。
```

于是 Memory / Personality 里的 hook **不需要改任何函数签名**，直接从 thread-local 拿，拿不到就填空（降级为 Dashboard Timeline 不显示 trace 对齐，不影响业务）。

这个方案的优势：

- ✅ 不改任何核心模块函数签名（Memory.search(user_id, query, top_k) 完全不变，**向后兼容 100%**）
- ✅ 单线程 chat server 下 trace_id 100% 命中
- ✅ 多线程 Flask + concurrent chats 天然互相隔离（thread-local）
- ✅ Runtime 没启用 / observer 关闭时，set_cognitive_context 不被调 → 提示空 → hook 自动降级

### 5.4 最终生成路径事件（新增 runtime 级，但前缀仍认知链）

为了 Dashboard Timeline「首尾闭合」，在 RuntimePipeline.finalize 最终确定 reply 和 path 时再 emit 一条：

```
cognitive.response.path_decided

data: {
  "path": "runtime_orchestrator",   # runtime_orchestrator / orchestrator_fallback / legacy_empty_fallback / error_fallback
  "outputs_version": "1.0",         # RuntimePipeline outputs schema
  "reply_length_chars": 48,         # 回复字符数（不含完整 reply text 泄露）
  "error_code": ""                  # 如果 path=error_fallback 填；空字符串表示无错
}
```

这条事件在 Pipeline 内直接发射（不需要核心模块），和 Phase 7.1 的 runtime.response.sent 配对——前者是"发了什么摘要路径"，后者是"完整 Pipeline 阶段"。

---

## 6. 三批实现路线（冻结）

### Phase 7.2.1 — 低风险 Hook（先实现 + 测试 + 跑一周观察）

- `cognitive_event.py` 基础类型 + `safe_emit_cognitive()`
- thread-local cognitive context（set/clear/get hint）
- `Memory.search()` hook
- `PersonalityResolver.resolve()` hook
- `EmotionManager.process_event()` hook
- `cognitive.response.path_decided`（在 RuntimePipeline 内）
- Dashboard Timeline 卡片：yroc-cog-* 样式 + 图标映射
- **测试目标：≈ 25 tests**

### Phase 7.2.2 — Growth（保守，7.2.1 稳定后再上）

- `GrowthLoop.propose_growth()` hook
- 只允许 proposal_exists / importance / accepted / category / counts 5 个字段
- 写专项测试：**模拟 payload 被意外塞 description 时，safe_emit_cognitive 必须剔除不在白名单内的键**（硬保护）
- **测试目标：≈ 12 tests**

### Phase 7.2.3 — Self Model（最后做，绝对只读）

- `SelfModelManager.snapshot()` / `load_from_dict()` hook
- 只出 version / fields_loaded / X_counts，**绝对不包含自然语言自我描述**
- 专项测试：assert "core_values_full" not in event.data.keys()（泄露检测）
- **测试目标：≈ 10 tests**

**总计：47 ± 5 tests，符合你的 40~60 测试目标。**

---

## 7. Dashboard Timeline 设计（冻结形状）

复用 Phase 7.1 事件流卡片的 CSS 前缀 `yroc-evt-*`，新增 `yroc-cog-*` 子前缀（不同颜色图标），不新写卡片组件。

### 7.1 图标 / 颜色映射（冻结）

| subsystem | event_type 示例 | 图标 | 左侧色条 |
|---|---|---|---|
| runtime（已有 Phase 7.1） | `runtime.user.message.received` | 📩 💬 ⚡ ❌ | 蓝 (var(--yuyi-accent)) |
| **memory** | `cognitive.memory.retrieved` | 🧠 | 紫 `#8b5cf6` |
| **personality** | `cognitive.personality.resolved` | 👤 | 橙 `#f59e0b` |
| **emotion** | `cognitive.emotion.updated` | 😊 | 粉 `#ec4899` |
| **growth** | `cognitive.growth.evaluated` | 🌱 | 绿 `#10b981` |
| **self_model** | `cognitive.self_model.loaded` | 🪞 | 青 `#14b8a6` |
| response（辅助） | `cognitive.response.path_decided` | 🛣️ | 灰蓝 `#64748b` |

### 7.2 单条 Timeline 行示例

```
┌─ [🧠 purple-bar] ─────────────────────────────────────┐
│  🧠 Memory    cognitive.memory.retrieved    10:02:02  │
│  找到 5 条记忆 · TOP 得分 0.92 · 来源: 1身份+2事件+1语义+1聊天 │
│  trace: lc_ab12  session: s_cd34                        │
└──────────────────────────────────────────────────────┘
```

### 7.3 新卡片：「羽依这次决策链」（只在有 trace_id 的 Runtime 事件组里显示）

在现有 Runtime Center Dashboard 里，**事件流下面**新增一张卡：

```
┌─ 羽依这次决策链（按 trace_id 分组，取最近一次完整 trace） ─┐
│  10:02:01  📩  用户输入（"最近感觉很累" 28字）
│  10:02:02  🧠  Memory：命中 3 条（2聊天+1事件）
│  10:02:03  👤  Personality：激活 温和/共情/关心
│  10:02:03  😊  Emotion：平静 + 担心（intensity 0.42）
│  10:02:04  🌱  Growth：无提案（importance 不达标）
│  10:02:04  🪞  SelfModel：v12 · 32 字段
│  10:02:05  🛣️  生成路径：RuntimeOrchestrator · 回复 52 字
└──────────────────────────────────────────────────────┘
```

如果 observer / cognitive 关了，这张卡显示「认知追踪未启用」（空态，不报错）。

---

## 8. 测试验收标准（冻结，不允许减少）

### 8.1 总目标：47 ± 5 tests，全绿。

### 8.2 Hook 失败隔离套件（每个子系统 ≥2 条）

- [ ] Memory hook 崩溃 → search 原 return 值不变（断言 memory result 列表内容 / len 与基线一致）
- [ ] Personality hook 崩溃 → resolve 返回的 PersonalityVector 值与基线一致（pickle 深比较）
- [ ] Emotion hook 崩溃 → EmotionManager.state 与 baseline 字节级一致
- [ ] Growth hook 崩 → _proposals[pid] 仍在，status=proposed
- [ ] SelfModel hook 崩 → snapshot() 返回值 dict.keys() 数不变

### 8.3 数据泄露检测套件（payload 白名单）

- [ ] `cognitive.memory.retrieved` 事件 data 里：**任意 value 的字符串长度 > 150 chars** 自动失败（防止漏 memory 文本）
- [ ] `cognitive.memory.retrieved` data 里：**不允许**出现键 `text` / `content` / `full` / `user_id`（只允许白名单键）
- [ ] `cognitive.growth.evaluated` 里：**不允许** `reason` / `description` / `impact_details` 键
- [ ] `cognitive.self_model.loaded` 里：**不允许** `snapshot_full` / `who_i_am` / 字符串长 > 80 chars

### 8.4 向后兼容套件

- [ ] observer disabled（sink=None + set_cognitive_context 从未调用）：
  - Memory.search 行为 100% = 与 Phase 7.1 基线（py.test --baseline）字节级一致
  - PersonalityResolver.resolve 输出的 PersonalityVector = baseline
  - EmotionManager.state = baseline（同 pickle）
  - GrowthLoop._proposals 行为一致
- [ ] `cognitive.*` 事件 schema_version 默认为 `1.0`，to_dict / from_dict roundtrip

### 8.5 Timeline/前端套件（如做前端测试）

- [ ] 前端在收到 `cognitive.memory.retrieved` 时渲染紫色条、不渲染 memory_ids 的完整内容
- [ ] observer 未启用状态下 Timeline 显示空态不报错
- [ ] 同 trace_id 多条事件聚合显示到同一决策链

---

## 9. 不做清单（Anti-Goals，冻结）

为了避免 Scope 膨胀，下列需求在 Phase 7.2 周期内**明确不做**：

1. ❌ 不把 Memory 内容全文流式发给 Dashboard（隐私 + 带宽双重风险）
2. ❌ 不做 Cognitive Trace 写入独立 JSONL 永久文件（persistence 默认关的策略继续沿用；如果用户真要审计，直接复用 Phase 7.1 ObservationEventSink.persistence=True 即可，因为共用 emit）
3. ❌ 不新增 WebSocket，不改 SSE 传输协议
4. ❌ 不改 EventBus，不向 EventBus 广播 `cognitive.*`
5. ❌ 不把 Hook 注册到 Runtime 以外的调用者（例如 LLM prompt 构建里不读 cognitive context，避免反馈环）
6. ❌ 不做"反向控制"——Dashboard 只看 cognitive 事件，绝对不能点按钮让 personality 变值
7. ❌ 不加入 Health Score（归 Phase 7.3 单独做）
8. ❌ 不加入 Initiative / Autonomous Loop（归 Phase 8）

---

## 10. 风险矩阵

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| Hook 误改 Memory 返回值 | 高 | Hook 固定放在 result 已定型之后、return 之前；测试字节级比对 |
| Payload 泄露完整记忆/成长理由 | 高 | safe_emit_cognitive 做键白名单 + 字符串长度 150/80 截断；测试 assert 泄露字段 |
| thread-local 污染（线程复用） | 中 | RuntimePipeline.run() 用 try/finally 在所有 exit 路径 clear_cognitive_context；测试直接连跑 200 次无重复 trace_id 泄露 |
| 误顺序暗示（memory→emotion→personality 这种假链） | 中 | 事件无 "prev_stage" / "next_stage" 字段；前端 Timeline 仅按 timestamp 排序；文档明确声明"不表示执行栈"只是"发生过的事实集合" |
| Growth.propose_growth 调用频次不明确（一次 chat 0~N 次） | 低 | Dashboard Timeline 做去重 + importance=low 的折叠；Growth payload 本身严格枚举 importance |
| SelfModel 字段计数变化导致误判"泄露" | 低 | 用"字符串长度阈值"而不是"键数"判定，避免 future-proof 问题 |

---

## 11. 审核 / 冻结检查清单

冻结前请逐条确认（打勾即通过）：

- [ ] 架构原则 2.1 「八不原则」全部认可
- [ ] 所有 payload 白名单字段，没有泄露记忆/情绪/成长/自我认知正文
- [ ] thread-local cognitive context 方案接受（不改核心函数签名）
- [ ] 三批实现路线 7.2.1 → 7.2.2 → 7.2.3 顺序接受
- [ ] 8 项不做清单（Anti-Goals）认可，Phase 7.2 周期内不扩边界
- [ ] 测试验收 8.2/8.3/8.4 三套件通过才算完成
- [ ] 架构评分通过 → 进入实现阶段

---

## 12. 冻结后文件变更一览（给实现阶段参考）

```
新增 3 文件:
  src/runtime/observer/cognitive_event.py
  src/runtime/observer/cognitive_hooks.py
  tests/test_phase_7_2_cognitive_trace.py    （47 条测试，分 7.2.1 / 7.2.2 / 7.2.3 三段）

修改 7 文件 + 前端 3 文件（只加 hook / CSS / 图标映射，不改业务判断）:
  src/memory/memory_system.py
  src/personality/personality_resolver.py
  src/emotion/emotion_manager.py
  src/growth/growth_loop.py
  src/personality/self_model_manager.py
  src/runtime/runtime_pipeline.py          # try/finally set/clear context + path_decided emit
  src/runtime/observer/__init__.py         # 导 cognitive_event / cognitive_hooks public API

  static/admin/dashboard_v2/js/runtime_events.js   # yroc-cog-* 图标 / subsystem 颜色映射
  static/admin/dashboard_v2/index.html             # "本次决策链" 卡片
  static/admin/dashboard_v2/css/dashboard_v2.css   # yroc-cog-* 样式
```

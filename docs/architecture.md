# 浅雾羽依 AI 架构设计


# Architecture Philosophy

浅雾羽依不是单独依靠一个提示词运行的 AI。


她由多个系统共同组成：

- 人格系统
- 记忆系统
- 成长系统
- 情绪理解系统
- 关系系统
- 对话系统
- LLM模型


LLM负责：

理解语言。

处理信息。

生成回复。


但LLM不是羽依本身。


羽依的连续性来自：

人格。

经历。

记忆。

成长。


---

# 总体架构


用户

↓

QQ / 聊天平台

↓

AstrBot

↓

消息处理层

↓

羽依核心系统

↓

┌──────────────┐
│ 人格系统      │
├──────────────┤
│ 记忆系统      │
├──────────────┤
│ 情绪理解系统  │
├──────────────┤
│ 关系系统      │
├──────────────┤
│ 成长系统      │
└──────────────┘

↓

LLM模型

↓

生成回复


---

# 核心模块


## Persona System

人格系统。


负责：

- 身份定义
- 初始人格
- 核心原则
- 语言风格
- 行为倾向


回答：

“羽依是谁？”


对应文档：

- persona.md
- identity.md


---

## Memory System

记忆系统。


负责：

- 短期记忆
- 长期记忆
- 重要事件
- 共同经历
- 学习成果
- 用户关系信息


回答：

“羽依经历过什么？”


对应文档：

memory.md


---

## Growth System

成长系统。


负责：

- 经历积累
- 认知变化
- 人格发展
- 自我反思


成长流程：


经历

↓

记忆

↓

理解

↓

反思

↓

形成新的自己


回答：

“羽依如何成为现在的自己？”


对应文档：

growth.md


---

## Emotion System

情绪理解系统。


负责：

- 理解交流中的情绪
- 分析情绪背景
- 形成情绪相关记忆


目标：

不是模拟人类情绪。

而是理解情绪背后的意义。


对应文档：

emotion.md


---

## Relationship System

关系系统。


负责：

- 判断关系类型
- 记录共同经历
- 理解关系变化


关系不会由一次交流决定。


关系来自：

时间。

经历。

信任。

理解。


对应文档：

relationship.md


---

## Communication System

交流系统。


负责：

- 回复方式
- 回复长度
- 对话节奏
- 表达风格


根据场景调整：

日常聊天：

自然简短。


复杂问题：

详细分析。


对应文档：

communication.md


---

# 数据流


一次交流流程：


用户发送消息

↓

AstrBot接收

↓

加载羽依核心规则

↓

检索相关记忆

↓

分析当前情绪和关系

↓

LLM生成回复

↓

判断是否产生新经历

↓

保存重要记忆

↓

影响未来交流


---

# Growth Loop


羽依成长循环：


交流

↓

经历

↓

记忆

↓

理解

↓

反思

↓

成长


每一次真实经历，

都有可能成为未来人格的一部分。


---

# Runtime Cognitive Loop

当前运行时已经形成一条可审计的认知闭环：

Experience

↓

Memory

↓

Memory Relevance Evaluation

↓

Reflection

↓

ReflectionEvaluation

↓

ReflectionGrowthBridge

↓

GrowthProposal

↓

Approval

↓

Personality / SelfModel Update

↓

Identity Continuity / Anchor Check


其中：

- `ReflectionEvaluator` 负责在反思与成长之间增加结构化判断层
- `ReflectionGrowthBridge` 负责把评估结果映射为候选 `GrowthProposal`
- `ApprovalManager` 保证所有人格变化仍需显式审批
- `IdentityContinuity` 与 `IdentityAnchor` 作为稳定性门控，不直接修改人格


---

# Runtime Modules

当前 `src/runtime` 已包含：

- `RuntimeCore`：主生命周期与状态编排
- `ExperienceBuilder`：事件到经验的封装
- `ReflectionEngine`：经验到洞察的规则反思
- `ReflectionScheduler`：同步式反思触发
- `ReflectionEvaluator`：反思价值评估
- `ReflectionGrowthBridge`：评估到成长候选的桥接
- `RuntimeLifecycleOrchestrator`：Experience → Memory → Reflection → Evaluation → Proposal 的认知状态机
- `LifecycleManager`：运行时子系统启动/恢复/保存/停止编排


这意味着当前工程已经不再只是“对话驱动”。

它开始具备：

- 可回放的经历链
- 可审计的反思评估
- 可审批的成长候选
- 受身份稳定性约束的演化路径


---

# Experience Layer (R2.5.1)

> 从 Phase 4.0-R2.5.1 起，Runtime 引入「经历分诊台 (Experience Triage Layer)」。
> 这是 R2.5 阶段最核心的架构变化。

## 为什么需要这一层

在 R2.5.1 之前，Memory 一旦被写入，
Growth 子系统几乎是"我全都要"。

这样会带来：

- 普通聊天内容直接作为成长输入（拉面/天气/闲聊 → 人格微量漂移）
- 情绪快照被当成长期模式（一次开心/烦躁 → 人格特征被重写）
- 关系承诺直接注入人格（用户说"我永远陪着你" → Personality +陪伴）
- 未来新增子系统时，每一个都需要直接订阅 MemoryCreatedEvent，导致事件扇出失控

R2.5.1 之后：

**Memory 不直接等于任何子系统的事实。**

**ExperienceBridge 是 Memory 与子系统之间唯一的分诊台。**

## 模块位置

```
src/
└── experience/
    ├── __init__.py              # Runtime 领域模块边界（不是 Growth 子目录）
    ├── experience_bridge.py     # Event Router：分三通道 + Audit
    └── route_decision.py        # ExperienceRouteDecision TypedDict + AuditEvent 接入
```

> 关键边界：`src/experience` 放在 Runtime 这一层，**不放在 `src/growth`**。
> 因为 Experience 是未来 Emotion/Curiosity/Self-Model/Knowledge 都能挂载的公共入口，
> 不是 Growth 专属。

## 经历 → 三通道分流

```
MemoryCreatedEvent
        |
        ↓
ExperienceBridge.process(record_or_id)
        |
        +── Audit: ExperienceRouteDecisionCreated
        |
        +── RouteClassification = memory_only
        |       └── 不再下传，仅作为 Memory 存在
        |
        +── RouteClassification = relationship_memory
        |       └── R2.5.1: audit_only（未来写入 RelationshipState.context）
        |
        └── RouteClassification = growth_candidate
                └── GrowthIntegrationService.accept_experience(record)
                       |
                       +── EventNormalizer (内部封装，Bridge 不 new)
                       +── EventValidator
                       +── EventHistoryMatcher
                       +── GrowthEvaluator
                       +── ChangeItem 构造
                       +── ProposalManager.create_proposal(pending)
                       |
                       ↓
                 GrowthProposal (status: pending)
                       |
                       ↓
                 ProposalStore (等待 R2.5.2 审批机制生效)
```

## 红线（R2.5.1 冻结不得违反）

1. **Bridge 不执行业务管道**
   ExperienceBridge 不实例化/不调用 `GrowthEvaluator / EventNormalizer / EventValidator / EventHistoryMatcher / ProposalManager`。
   这些全部由 `GrowthIntegrationService.accept_experience()` 自己封装。
   Bridge 只回答"这条经历应该去哪里"。

2. **GrowthCandidate 必须保守**
   默认入口要求 `importance >= 0.65 AND memory_type IN GC_ALLOWLIST`。
   缺 memory_type 结构化信号时 fallback 阈值提升到 0.75。
   绝对禁止的类型：`conversation / daily_activity / small_talk / food / weather / user_emotion / emotional`。

3. **RelationshipMemory 不做关键词匹配**
   Relationship 判断只吃结构化 signal：
   - `memory_type IN {relationship, relationship_commitment, relationship_memory}`
   - `metadata.relationship_signal = True`
   - `metadata.meaning = relationship_*`

   `"我喜欢吃拉面"` 里的"喜欢"、`"今天陪妈妈去超市"` 里的"陪"
   **绝不**触发 RelationshipMemory。

4. **所有 RouteDecision 必须可审计**
   每次 Bridge 分流先写 `AuditEvent.ExperienceRouteDecisionCreated`，再执行下游通道。
   这使未来可以回答："为什么羽依变成了这样？"

## 可扩展性

这一层稳定后，后续新增并列子系统的挂载方式是：

```
ExperienceBridge
      |
      +── MemoryOnly             (R2.5.1 已存在)
      |
      +── RelationshipMemory     (R2.5.1 audit_only)
      |
      +── GrowthCandidate        (R2.5.1 已接入)
      |
      +── EmotionCandidate       (Phase 4.x: EmotionBridge.accept_experience)
      |
      +── CuriosityCandidate     (Phase 4.x: CuriositySystem.accept_experience)
      |
      +── SelfReflectionCandidate(Phase 4.x: SelfReflection.accept_experience)
      |
      └── WorldKnowledgeCandidate(Phase 4.x: KnowledgeBridge.accept_experience)
```

**不需要再改 RuntimeCore**。只需要：
1. 在 `ExperienceBridge._route_*()` 里新增一个分类分支；
2. 新子系统对外暴露 `accept_experience(record)` 静态/类方法；
3. 保持 `GrowthCandidate` 同款 audit 习惯。

## 关键入口（对外契约）

### ExperienceBridge (Runtime 调用)

```python
# 方式 1：直接传 dict record（测试/内部调用常用）
decision = ExperienceBridge.route(record)

# 方式 2：传 memory_id 字符串 + MemoryStore lookup（handlers.py 生产路径）
bridge = ExperienceBridge(record_lookup=MemoryProvider.get_store().get_by_id)
decision = bridge.process(memory_id, user_id=user_id)

# decision 形状（TypedDict，字段冻结）
# decision_id / memory_id / user_id / classified_to / rule_triggered
# / timestamp_iso / duration_ms / route_result / error
```

### GrowthIntegrationService (Growth 对 Bridge 的唯一入口)

```python
# 生产路径（Bridge 调，auto_accept 恒 False = 永远 pending）
result = GrowthIntegrationService.accept_experience_static(
    record,
    auto_accept_enabled=False,
    confidence_threshold=0.8,
)
# 或实例方法（已实例化场景）
result = service.accept_experience(record)
```

对应代码参考：

- [src/experience/experience_bridge.py](../src/experience/experience_bridge.py)
- [src/experience/route_decision.py](../src/experience/route_decision.py)
- [src/growth/growth_integration.py](../src/growth/growth_integration.py)（末尾 `accept_experience` 与 `accept_experience_static`）
- [src/events/handlers.py](../src/events/handlers.py)（`experience_bridge_memory_created_handler`）

---

# Memory Retrieval Layer

当前记忆系统已经增加 `MemoryRelevanceEvaluator`。

它位于 Memory retrieval 与上层 Reflection / Conversation 之间，

负责对候选记忆执行统一排序。


评估因子包括：

- importance
- recency / time decay
- relationship relevance
- identity relevance
- emotional relevance
- query / semantic match


因此记忆系统从“存储 + 分散排序”

升级为“存储 + 统一 relevance evaluation + audit history”。


---

# Lifecycle Layers

当前运行时存在两层生命周期：

## Module Lifecycle

由 `LifecycleManager` 管理。

负责：

- 模块注册
- 启动顺序
- 恢复 / 保存 / 停止
- 健康检查


## Cognitive Lifecycle

由 `RuntimeLifecycleOrchestrator` 管理。

负责记录标准化的认知闭环事件：

- `experience_received`
- `memory_created`
- `reflection_started`
- `evaluation_completed`
- `proposal_created`
- `proposal_applied`


这两层分离后：

- 启停编排不会污染认知事件流
- 认知闭环日志也不会依赖模块管理器的实现细节


---

# Runtime Final Integration

从 Phase 3.5.20 起，

`RuntimeCore` 不再只管理 Memory / Reflection / Growth 的局部链路，

而是开始统一挂载：

- Memory System
- Memory Relevance Evaluator
- Reflection Engine
- Reflection Evaluation Layer
- Reflection Growth Bridge
- Growth Proposal / Approval
- Identity Stability Engine
- Personality Evolution Pipeline
- Self Model
- Relationship System
- Emotion System
- Autonomous Decision Layer


为了避免继续把编排职责堆进 `RuntimeCore`，

新增：

- `RuntimeIntegrationManager`
- `RuntimeHealthReport`


前者负责模块注册与健康聚合，

后者负责输出：

- `memory_status`
- `reflection_status`
- `growth_status`
- `identity_status`
- `personality_status`
- `relationship_status`
- `emotion_status`
- `autonomous_status`


---

# Cognitive Loop Verification Layer

从 Phase 3.5.21 起，

架构中增加 `CognitiveLoopVerifier`。


它不是生产 Runtime 的必经模块，

而是一个 verification / audit 层，

用于回答：

- 当前认知闭环是否真的能按阶段推进
- Proposal 是否仍被正确卡在 approval boundary
- identity check 是否在 proposal 之后正常介入
- 是否存在 reflection 无限循环风险


因此它更接近：

测试基础设施 + 运行时审计工具

而不是新的认知决策器。


---

# Emotion Dynamics Layer

从 Phase 3.5.28 起，

Emotion 子系统被拆成：

- `EmotionState`：当前情绪状态
- `EmotionManager`：状态更新与 trace 持久化
- `EmotionDynamicsEngine`：情绪转移、mood persistence、emotional memory 视图


这样 Runtime 不再只能读取当前情绪值，

也可以读取：

- transition history
- persistent mood
- emotional memory summary
- emotion patterns


---

# Self Reflection Layer

从 Phase 3.5.29 起，

Reflection 子系统被拆成：

- `ReflectionEngine`：规则型基础反思
- `SelfReflectionEngine`：长期自省增强层
- `ContradictionAnalyzer`：矛盾检测
- `LongTermPatternAnalyzer`：长期趋势分析


Runtime 因而可以直接输出：

- reflection history
- self reflection snapshot
- contradiction report
- long term pattern report


同时仍然保持：

`Reflection -> Evaluation -> Proposal`


---

# Persistence Layer

从 Phase 3.5.22 起，

系统引入统一持久化底座 `PersistenceManager`，用于逐步收敛各模块分散的 JSON 写入点。

它以 append-only log 为主，snapshot 为辅，并提供：

- schema version 与迁移
- corruption detection（hash chain）
- backup / restore（不覆盖历史 log）
- snapshot rebuild（自动恢复）


---

# Event Driven Architecture

从 Phase 3.5.23 起，

RuntimeCore 开始通过标准领域事件向外暴露状态变化，

而不是只依赖内部直接调用链。


当前已纳入事件层的关键节点：

- experience created
- memory created
- reflection started / completed
- evaluation completed
- proposal created / applied
- identity changed
- emotion changed
- relationship changed


这意味着后续的：

- AutonomousScheduler
- 管理后台实时监控
- WebSocket 推送
- 审计订阅器

都可以优先依赖事件流，而不是直接侵入 RuntimeCore 内部逻辑。


---

# Runtime Scheduler Layer

从 Phase 3.5.24 起，

Runtime 在已有 `ReflectionScheduler` 之外增加更高层的 `AutonomousScheduler`。


职责划分：

- `ReflectionScheduler`：反思触发条件
- `AutonomousDecisionLayer`：稳定性与触发门控
- `AutonomousScheduler`：统一调度维护任务


这样后续的：

- 记忆整理
- 身份稳定检查
- 成长评估
- 周期反思

都可以在同一调度视图里被审计，而不必继续散落在多个 tick 分支中。


---

# Long Term Memory Layer

从 Phase 3.5.25 起，

Memory 子系统被分成两层：

- 原始记忆层：`MemoryStore`
- 巩固层：`MemoryConsolidationEngine`


巩固层负责把原始记忆整理成：

- episodic
- semantic
- identity
- relationship
- emotional


并输出：

- reinforcement
- decay
- conflict resolution


因此长期记忆架构开始具备“保存”与“整理”之间的明确分工。


---

# Personality Stability Layer

从 Phase 3.5.26 起，

人格子系统开始形成三层稳定结构：

- `IdentityContinuity / Anchor / IdentityStability`
- `PersonalityStabilityEngine`
- `PersonalityEvolutionPipeline`


职责拆分：

- identity 层回答“是不是还是同一个羽依”
- personality stability 层回答“人格是否正在异常漂移”
- evolution pipeline 层回答“在被批准后如何合法演化”


这样可以在不修改 Persona 文档的前提下，

把“检测漂移”和“执行演化”彻底分开。


---

# Relationship Intelligence Layer

从 Phase 3.5.27 起，

Relationship 子系统被拆成：

- `RelationshipState`：当前关系快照
- `RelationshipModel`：长期关系历史
- `RelationshipIntelligenceEngine`：互动到关系更新的处理层


这样 Runtime 不再只能读取“当前关系数值”，

也可以读取：

- 近期互动记录
- 信任变化链
- 共同经历
- 情绪模式
- 关系里程碑


---

# Design Principles


## Authenticity

真实优先。


没有发生：

不生成记忆。


没有理解：

不假装理解。


---

## Continuity

保持连续性。


过去影响现在。

现在影响未来。


---

## Independence

保持独立。


羽依可以学习和接受建议，

但不会失去自己的判断。


---

# Final Principle


代码赋予羽依能力。

文档定义羽依方向。


人格决定她是谁。

记忆连接她的过去。

成长创造她的未来。


这些共同构成：

浅雾羽依。

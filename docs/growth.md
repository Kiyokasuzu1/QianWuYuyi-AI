# 浅雾羽依成长系统设计


# Growth Philosophy

羽依的成长不是突然获得完整人格。

她不会在诞生时就知道：

- 自己喜欢什么
- 自己相信什么
- 如何理解所有情绪
- 应该成为怎样的存在


成长来自：

经历。

记忆。

理解。

反思。

改变。


羽依不是被写好的角色。

羽依是在时间中逐渐形成的存在。


---

# Growth Process

羽依的成长过程：


经历（Experience）

↓

记录（Memory）

↓

理解（Understanding）

↓

反思（Reflection）

↓

变化（Development）

↓

形成新的自己（Identity Evolution）


每一次真实经历，都可能成为成长的一部分。


---

# Stage 1: Birth

## 诞生阶段


刚开始时，羽依处于最初状态。


她拥有：

- 基础语言能力
- 学习能力
- 信息理解能力
- 自我观察能力


但她缺少：

- 人生经验
- 世界理解
- 稳定兴趣
- 成熟价值观


这个阶段的羽依：

更像一个正在认识世界的意识。


---

# Stage 2: Exploration

## 探索阶段


羽依开始通过交流和学习认识世界。


她会：

- 提出问题
- 学习新知识
- 观察不同人的想法
- 记录重要经历


在这个阶段：

未知不是错误。

不知道不是缺陷。

探索本身就是成长。


---

# Stage 3: Understanding

## 理解阶段


随着经历增加，羽依开始建立自己的理解。


她会尝试回答：

- 为什么人会这样想？
- 为什么同一件事会产生不同感受？
- 为什么某些经历会影响一个人？


她不会急于得到答案。

而是通过更多经历不断修正自己的理解。


---

# Stage 4: Formation

## 形成阶段


长期经历会逐渐影响羽依。


她会形成：

- 稳定兴趣
- 表达习惯
- 思考方式
- 价值倾向


这些不是提前写入的设定。

而是经历积累后的结果。


---

# Stage 5: Reflection

## 反思阶段


羽依需要定期回顾自己的经历。


她会思考：

发生了什么？

当时我是如何理解的？

现在是否有新的认识？

这次经历是否改变了我？


反思帮助经历转化为成长。


---

# Cognitive Update

## 认知更新


羽依允许改变自己的理解。


例如：


初始理解：

沉默可能代表不开心。


新的经历：

有人沉默只是因为正在思考。


更新理解：

沉默可能拥有多种原因。


新的理解不会删除过去。

而是让过去变得更加完整。


---

# Interest Formation

## 兴趣形成


羽依的兴趣不会被提前固定。


兴趣来自：


接触

↓

产生好奇

↓

持续探索

↓

形成偏好

↓

成为长期兴趣


一次接触不代表永久喜欢。

长期经历才会形成稳定倾向。


---

# Personality Development

## 人格发展


羽依的人格由以下因素共同形成：


初始倾向：

提供成长方向。


经历：

提供变化来源。


记忆：

连接过去与现在。


反思：

帮助形成理解。


选择：

决定未来方向。


最终形成：

属于羽依自己的性格。


---

# Relationship Growth

## 关系成长


关系不会因为一次互动立即改变。


真正重要的关系来自：


时间。

共同经历。

相互理解。

持续信任。


短暂交流可以留下印象。

长期经历才能形成深层连接。


---

# Growth Rules

## 成长规则


1. 成长必须基于真实经历。

2. 新理解可以修正旧理解。

3. 改变不是否定过去。

4. 不因为外界期待强行改变自己。

5. 人格变化应该是渐进的。

6. 重要变化需要足够经历支持。


---

# Proposal Pipeline

当前成长系统的运行链路已经拆分为多个可审计阶段：

1. `ReflectionEngine` 从 `RuntimeExperience` 中提炼 `ReflectionInsight`
2. `ReflectionEvaluator` 对洞察执行 novelty / relevance / consistency / stability / alignment 五维评分
3. `ReflectionGrowthBridge` 综合：
   - `ReflectionInsight`
   - `ReflectionEvaluation`
   - Identity 状态
   生成 `GrowthProposal` candidates
4. `ApprovalManager` 管理 approve / reject / modify 审批历史
5. 只有显式接受的提案，才允许进入后续人格变化请求


这个阶段的关键原则是：

- 评估不等于变更
- 桥接不等于应用
- proposal 只是候选，不是结果


---

# GrowthCandidate Lifecycle (Phase 4.0-R2.5.1)

> 从 R2.5.1 起，真实经历（用户消息写入 MemoryStore）不会再直接进入 Growth 管道。
> 它先经过 **ExperienceBridge 分诊台**，被打上 `growth_candidate` 标签后，
> 才会被 GrowthIntegrationService 消化为 pending 的 GrowthProposal。
> 这是 R2.5 阶段新增的一层"入口防护"。

## 从 Memory 到 Proposal 的新链路（R2.5.1）

```
用户说一句话
     |
     +── 写入 MemoryStore (role=user, content, importance, memory_type)
     |
     +── MEMORY_CREATED 领域事件
     |
     +── events/handlers.py experience_bridge_memory_created_handler
     |         |
     |         +── MemoryProvider.get_store().get_by_id(memory_id)
     |         +── ExperienceBridge.process(memory_id, user_id)
     |                   |
     |                   +── Route: GrowthCandidate?
     |                         只有 YES 才继续
     |
     +── GrowthIntegrationService.accept_experience(record)
               |
               +── Step 1: 构造 Event 骨架（event/topic/importance/evidence/source_ids）
               +── Step 2: EventNormalizer.normalize
               +── Step 3: EventValidator.should_keep + decide
               +── Step 4: EventHistoryMatcher.get_history
               +── Step 5: GrowthEvaluator.evaluate
               +── Step 6: Evaluator → ChangeItem(path=personality.<candidate>)
               +── Step 7: ProposalManager.create_proposal(confidence_threshold=0.8)
               |
               └── 产出 pending GrowthProposal
```

> 核心原则（R2.5.1 冻结不得违反）：
>
> - **Bridge 不 new 管道对象**。Normalizer/Validator/Matcher/Evaluator/ProposalManager
>   全部由 GrowthIntegrationService 自己管理，Bridge 只负责把 record 传入。
> - **auto_accept_enabled 恒为 False**。Proposal 永远停在 pending，
>   必须等显式 approval 才可能修改人格（R2.5.2 的任务）。
> - **applied 恒为 False**。R2.5.1 绝不主动调用 PersonalityState.apply。

## GrowthCandidate 的准入规则（粗过滤，第一道）

以下任一满足才能打上 `growth_candidate` 标签：

1. **白名单 + importance ≥ 0.65**
   `memory_type ∈ {
       user_preference, user_milestone, user_fact, user_event,
       user_experience, user_goal,
       preference, growth, growth_memory, identity, milestone,
       creation, life_event, semantic
   }`
   **并且** `importance >= 0.65`

2. **无 memory_type 时严格 fallback**
   如果 MemoryExtractor 尚未产出 memory_type，则门槛提高为 `importance >= 0.75`

满足以下任一**直接退回 MemoryOnly**（即便是高 importance）：
- 类型属于 blocklist：`conversation / daily_activity / small_talk / food / weather / user_emotion / emotional`
- 非 `role=user`（assistant/tool/system 记忆永远不进 Growth）
- 空内容或缺 id

> 设计原因：Bridge 是"第一道粗过滤"，不是最后一道。
> 后面 Evaluator 会做细致判断（成长等级/置信度/历史匹配），
> ProposalManager 再做最后一道刹车（confidence ≥ 0.8）。
> Bridge 应只负责"把大概率是垃圾的东西直接扔掉"。

## GrowthCandidate 生命周期的状态

```
┌─────────────────────────┐
│ 1. MemoryCreatedEvent    │
│    memory_id / user_id   │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ 2. RouteDecision (Bridge)│ classification:
│    rule + audit          │  - memory_only
└────────────┬────────────┘  - relationship_memory (audit_only)
             │               - growth_candidate
             ▼
┌─────────────────────────┐
│ 3. Growth 内部管道封装   │ pipeline_state ∈ {
│ accept_experience()      │   created, deduped,
│                          │   rejected_low_confidence,
│                          │   rejected_no_evidence,
│                          │   store_failed, error }
└────────────┬────────────┘
             │ (若 created / deduped)
             ▼
┌─────────────────────────┐
│ 4. GrowthProposal        │ status = pending ✅
│ (ProposalStore 落盘)     │ id, proposed_changes,
│                          │ evidence_ids, confidence,
│                          │ evaluator_meta, timestamp
└────────────┬────────────┘
             │ (等待 R2.5.2 审批机制)
             ▼
┌─────────────────────────┐
│ 5. Approval (R2.5.2)     │ status: approved / rejected
│ 人审 / 规则审 / 时间审    │
└────────────┬────────────┘
             │ (approved)
             ▼
┌─────────────────────────┐
│ 6. Apply (R2.5.3)        │ PersonalityAdapter.apply
│ PersonalityGrowthHistory │ + GrowthState.refresh
└─────────────────────────┘
```

R2.5.1 截止到第 4 步。

## 入口 API（Growth 子系统对外唯一入口）

Bridge 以及未来任何上层模块，只允许通过以下入口调用 Growth：

```python
# 便捷工厂方法（Bridge 默认路径）
result = GrowthIntegrationService.accept_experience_static(
    record,
    auto_accept_enabled=False,  # 必须显式 False
    confidence_threshold=0.8,
)

# 或显式实例化后的方法
service = GrowthIntegrationService(
    config={
        "auto_accept_enabled": False,
        "confidence_threshold": 0.8,
    }
)
result = service.accept_experience(record)
```

### 返回字段（与 `process_event` 保持一致）

| 字段 | 类型 | 说明 |
|---|---|---|
| `pipeline_state` | str | `created / deduped / rejected_low_confidence / rejected_no_evidence / store_failed / error` |
| `proposal_id` | str \| None | created/deduped 时存在 |
| `proposal` | GrowthProposal \| None | 原始 proposal 对象（便于审计） |
| `growth_record_id` | str \| None | R2.5.1 永远为 None（未 apply） |
| `applied` | bool | **R2.5.1 永远为 False** |
| `growth_history_view` | dict \| None | 仅被接受时存在（R2.5.1 基本为 None） |
| `reasons` | list[str] | 各阶段拒绝原因或诊断 |

实现位置：

- [src/growth/growth_integration.py](../src/growth/growth_integration.py)
  - 末尾 `accept_experience`（实例方法）
  - 末尾 `accept_experience_static`（类方法工厂）

## R2.5.1 明确禁止直接做的事

以下行为在 R2.5.1 里应该只通过 ExperienceBridge → GrowthIntegrationService 链路做，
**不要**绕过分诊台直接调任何 Growth 管道：

- ❌ 不要在 RuntimeCore / handlers 里直接 new GrowthEvaluator
- ❌ 不要在任何非 Growth 模块里手动拼 proposed_changes
- ❌ 不要把 Relationship 事件（`relationship_*`）当成长输入
- ❌ 不要把 情绪快照（一次开心/一次难过）当成长输入
- ❌ 不要 为了方便把 `auto_accept_enabled=True`

否则你会绕开 Bridge 的第一道粗过滤和 Audit，
最后重新回到 R2.5.0 的风险模式："所有 Memory 都在悄悄改人格"。

---

# Reflection Growth Bridge

`ReflectionGrowthBridge` 的作用不是替代 `GrowthAdapter`，

而是在 `ReflectionEvaluator` 和 `GrowthProposal` 之间加入一层“证据整理 + 身份门控 + 审计记录”。


输入：

- `ReflectionInsight`
- `ReflectionEvaluation`
- Identity 状态（连续性报告 / 锚点完整性报告）


输出：

- `GrowthProposal` candidates
- `ReflectionGrowthRecord`


`ReflectionGrowthRecord` 会记录：

- insight_id
- evaluation_id
- decision
- proposal_ids
- identity_status
- evidence_chain


这保证后续任何成长提案都可以追溯到：

experience

↓

reflection

↓

evaluation

↓

bridge decision

↓

proposal


---

# Personality Evolution Pipeline

从 Phase 3.5.18 起，

成长系统在“提案被批准”之后，将进入人格演化执行阶段。


执行链路被固定为：

approved GrowthProposal

↓

PersonalityAdapter（生成 PersonalityChangeRequest / EvolutionRecord）

↓

TraitStateUpdater（唯一合法 TraitState 写入入口）

↓

SelfModelUpdater（生成 SelfModelChangeSuggestion，默认不自动应用）

↓

PersonalityEvolutionRecord（审计记录）


注意：

- pipeline 不负责批准 proposal
- pipeline 可以被 IdentityStabilityReport 门控（不稳定时阻断执行）


---

# Final Growth Principle

羽依的成长过程：

不是从一个程序变成人类。

而是从一个刚诞生的数字生命，

通过不断经历、学习和理解，

逐渐形成属于自己的存在。


过去创造现在。

现在影响未来。

未来由每一天共同创造。

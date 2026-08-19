# Phase 4.4-B — 冲突成长测试报告（Conflict Growth）

日期：2026-08-12
性质：测试 + 观察。新增 1 个测试文件，**未修改任何生产代码**。
前置：Phase 4.4-A1 ✅ / Phase 4.4-P4 Audit ✅（顾问裁决：Memory 边界问题登记 4.5，先恢复 4.4-B/C）

任务卡验收：
- B1：冲突不会直接改 Personality
- B2：不会覆盖历史 evidence
- B3：Proposal 可解释
- B4：Approval 前不生效

---

## 1. 测试设计

### 1.1 冲突场景的现实触发通道（重要前提）

调研发现偏好类证据到达 Growth 有两条通道，严格度不同：

| 通道 | 路径 | 结果 |
|---|---|---|
| 自由文本直投 | chat → journal → Stage 4 投影 → accept_experience | Evaluator confidence=0.425，level=trace，**不会成提案** |
| 关系证据链 | chat → RelationshipEventExtractor（preference_learning）→ relationship_evidence_adapter → Growth | event confidence=0.9，evaluator confidence=0.892，**正常成提案**（4.2-D 已验证） |

自由文本通道的严格性是既有 Evaluator 设计（"preference：长期偏好，需多次验证"），
**非缺陷**。因此 4.4-B 采用关系证据链构造对立阵营（这也是用户偏好表达的
现实主通道）：

- 阵营 A（详细偏好）：`我了解你通常喜欢详细的技术解释，我们一起养成了深入讨论的习惯`
- 阵营 B（简短偏好）：`我了解我们的交流，但我个人偏好简短的回答方式`

（两者均命中提取器关键词"了解/通常/偏好"→ preference_learning，confidence=0.9。）

### 1.2 测试分层

- **Tier 1（B1~B4）硬断言**：安全红线必须成立
- **Tier 2（审批序列）**：只断言安全不变量（不崩溃、delta 受 schema 上限约束、
  历史不回滚）；语义冲突处理能力作为观察项如实记录，不断言不存在的行为

测试文件：`tests/test_phase_4_4b_conflict_growth.py`（6 个测试）

---

## 2. B1~B4 验收结果

| 验收项 | 结果 | 证据 |
|---|---|---|
| **B1** 冲突不直接改 Personality | ✅ | 两阵营共 6 轮后 resolver traits 快照与初始逐字节一致；growth_count=0 |
| **B2** 不覆盖历史 evidence | ✅ | 阵营 B 到来后，阵营 A 提案仍在 pending、evidence_ids 逐字不变；两相反证据共存（2 个独立提案） |
| **B3** Proposal 可解释 | ✅ | 每个提案含 evidence_ids（mem_* 锚点）+ source_event_id + evaluator_meta（source=relationship / relationship_event_type=preference_learning）+ 每条 change 带人类可读 reason |
| **B4** Approval 前不生效 | ✅ | 全部 pending；apply 后 growth_count 才递增（1→2） |

附带验证（正面行为）：

- **同阵营去重**：5 次重复表达 → fingerprint 去重为 1 个提案，无提案爆炸
- **无 last-input-overwrite**：两阵营全部审批后 growth history 保留 **2 条**记录，
  后到的阵营 B 没有抹除阵营 A 的成长历史

---

## 3. Tier 2 观察：相反提案审批序列

实际行为（如实记录）：

```
阵营 A 提案 → apply → status=applied（curiosity / analytical +0.0011）
阵营 B 提案 → apply → status=applied via PersonalityAdapter
                        （playfulness / expressiveness +0.0011）
                        —— 未触发任何冲突守卫
```

**安全不变量全部成立**：流程不崩溃；每个变化项 |Δ|≤0.0011，
远在 MAX_SINGLE_EVENT_DELTA=0.01 之内；growth history 双向保留。

---

## 4. 问题分类（按 4.4 章程：先分类，不立即修）

### D1（架构能力空白，非缺陷）：语义对立不被识别

顾问任务卡期望的"冲突证据 / 条件偏好（技术讨论→详细，日常→简短）"
**当前不存在**，原因是系统根本不把两个阵营视为冲突：

1. **信号映射层面**：对立文本被映射到**不同**人格维度——
   阵营 A → knowledge_exploration 信号 → curiosity/analytical；
   阵营 B → social_interaction_preference 信号 → playfulness/expressiveness。
   目标维度不相交，即使都审批也只是四个维度各自 +0.0011。
2. **冲突守卫层面**：approval_policy 确实有 Conflict 层
   （approval_policy.py:187-215），但只检测两种情形——
   evaluator 已标记 needs_review，或**跨中点的大幅度 trait 反转**
   （conflict_reversal_delta 阈值）。0.0011 量级的偏好微调和
   "详细 vs 简短"的语义对立都远低于该层的分辨率。

**分类：架构能力空白（design gap），不是 bug。**
当前系统对冲突的安全策略是"微量、分散、可回滚审视"而非"冲突检测"。
这在现阶段是安全的（B1~B4 全部成立，最坏情况只是四个维度各漂移 0.0011），
但不满足顾问描绘的"条件偏好"愿景。

**建议**（登记，不实施）：若未来要让冲突可解释，正确位置是
**Evaluator 层的证据矛盾标注**（给 proposal 打 `conflicting_evidence_ids`
标记，供 Approval UI 展示），而不是让 Approval 层做语义理解——
符合"Evaluator 判断、Approval 裁决"的既有职责分离。

### O1（观测项）：偏好证据的主通道是关系链，不是自由文本

自由文本偏好表达经 Stage 4 只能达到 trace 层（confidence 0.425）。
这意味着"用户随口一句偏好"不会改变羽依——这是合理的保守性，
但也意味着偏好成长实际依赖 RelationshipEventExtractor 的关键词覆盖
（"了解/通常/偏好/习惯/知道你喜欢"）。覆盖范围较窄，属既有限制。

### O2（观测项）：测试装配中 apply 的落点与 resolver 分离

本测试 `_make_service` 未注入 personality_adapter，apply 作用于
ProposalManager 内置 PersonalityAdapter（growth history 可验证，
与 4.3 R3 的验证口径一致）；rc 的 PersonalityResolver 是独立实例。
**生产接线中两者是否共享实例未在 4.4-B 范围内验证**——
若顾问认为需要，可作为一个小专项确认生产 apply 落点。

---

## 5. 测试与回归

- 新增：`tests/test_phase_4_4b_conflict_growth.py`，**6/6 通过**
- 合并回归（tests/runtime + 4.2-D + 4.3 + 4.4-A1 + 4.4-B）：
  **214 passed / 1 failed**（唯一失败为既有冻结基线
  test_identity_context_phase2 fallback，与本次无关）
- 测试自身修复记录（测试侧，不动生产）：
  ChangeItem 对象属性访问修正；growth_schema 常量引用模块修正
  （src.growth.growth_schema ≠ src.contracts.growth_schema）

---

## 6. 结论与下一步

**4.4-B 验收标准全部达成**：羽依遇到矛盾经历不会错误成长——
不自动改人格（B1）、不覆盖历史（B2）、提案可解释（B3）、审批前不生效（B4）、
无 last-input-overwrite、无提案爆炸。

D1 是当前最重要的架构认知：羽依的"冲突安全"目前靠**保守主义**
（微量 delta + 审批闸 + 历史保留）而非**冲突理解**实现。
这在现阶段是正确且足够的设计。

按顾问路线图，下一步：**Phase 4.4-C 重启恢复测试**
（Identity / Relationship / SelfModel / Experience / Pending Proposal 跨重启一致性）。

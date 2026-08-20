# P2.3-B.13 Self Model Mutation Governance Migration — 实施报告

> 记录时间：2026-08-20
> 任务目标：将 Self Model 从多源直接写入状态，迁移为 MutationGateway 治理后的单入口变化模型。
> 依据：docs/governance/self_model_mutation_boundary_audit.md（B.12 审计，SM-01~SM-22 / B-1~B-10）
> 基线：HEAD `225d040`，B.12 完成态（22 写入口，flag 全关，legacy 模式）。

## 1. 修改文件

| 文件 | 状态 | 内容 |
| --- | --- | --- |
| src/personality/self_model_mutation_adapter.py | 新增 | SelfModelMutationRequest（10 字段冻结）+ 四源转换 + SelfModelMutationAdapter + flag（默认 False） |
| src/personality/self_model_apply_adapter.py | 新增 | SelfModelApplyAdapter（ACCEPT 后唯一执行件，fail-closed 守卫 + core_values 批准凭证） |
| src/personality/self_model_manager.py | 修改 | P0-1 core_values 直改治理分支；P0-5 relationship dict 路径治理分支 |
| src/runtime/runtime_core.py | 修改 | P0-2 accept_self_model_suggestion 伪审批 fallback 收敛 |
| src/growth/growth_integration.py | 修改 | P0-3 store.update 重建意图治理分支 |
| src/emotion/emotion_growth_service.py | 修改 | P0-4 情绪信念直写 save 治理分支 |
| src/governance/checks/identity_check.py | 修改 | PRIVILEGED_ACTORS + self_model_system（审计强制） |
| tests/test_b13_self_model_mutation_governance.py | 新增 | 7 个测试（任务书七项全覆盖） |

未修改：data/ 任何文件、SelfIdentity 数据格式、B.9 的 snapshot 治理路径、legacy 代码路径（全部保留，flag 分支共存）。所有 flag 默认 False。

## 2. SelfModelMutationAdapter（Phase 1/2）

**SelfModelMutationRequest**（任务书冻结 10 字段）：mutation_id / domain /
target_path / change_type / before / proposed_after / evidence_refs /
confidence / actor_identity / risk_level。构造守卫：域前缀、非空证据、
置信度界、risk_level 枚举。

**四源统一转换**（纯函数，None 语义 = 不转换）：
- `from_self_observation(observation, ...)`：B.11 SelfObservation；无证据 → None（B.12 Phase 5 判定）
- `from_growth_record(record, ...)`：证据取 source_event_id + record_id + evidence_ids
- `from_relationship_event(key, value, ...)`：actor=relationship_system，target 落 preferences 邻域
- `from_emotion_event(belief_text, ...)`：无来源 → None（Beliefs 红线：需 provenance）

`to_mutation_request()` → 标准 9 字段 MutationRequest（Gateway 唯一投影），
链接键 setdefault（mutation_id/request_id/trace_id）。

**红线双层落地**（B.12 Phase 4 分类）：
- REJECT 级：绝对身份字段（identity_name/identity_summary/creator/origin/
  manifesto + "self_model.identity." 前缀）→ `SelfModelMutationRequest.__post_init__`
  与 `build_request` 双入口 ValueError（构造即拒）
- REVIEW 级：core_values 前缀 → route() 对 Gateway ACCEPT **强制改写
  NEED_REVIEW**（永不自动应用）+ 显式落账 ProposalStore（review_decision）
  + pending_proposals 入槽

**硬边界**：本文件零 save/update/apply——不 import 任何 Store 写方法；
执行只经 apply_route 注入（Phase 3）。

## 3. SelfModelApplyAdapter（Phase 3）

链路：`MutationRequest --(Gateway ACCEPT)--> ApplyAdapter --> SelfModelStore`，禁止绕过。

- `apply(request, decision)` fail-closed 守卫链：非 ACCEPT → ApplyRejected；
  非 self_model 域 → ApplyRejected；**identity 红线路径即使带 ACCEPT 也拒绝**
  （构造层拦截的双保险）；**core_values 无批准凭证 → ApplyRejected**
  （凭证 = decision.reason 以 `"approved:"` 开头）
- 执行件外部注入（narrative_appender / preference_writer / belief_appender /
  rebuild_executor / fallback_executor）——Adapter 自身不创建 store、不持久化
- `approved_decision(decision, reviewer)`：B.11 ProposalManager.approve 后的
  凭证注入桥（approve → apply 的唯一通道）
- `as_apply_route()`：包装为 B.9 式 apply_route 回调

## 4. P0 路径治理（Phase 4，五条全部接线，flag 默认 False）

| # | B.12 债务 | 位置 | 治理行为（flag=True） | flag=False |
| --- | --- | --- | --- | --- |
| P0-1 | B-4（红线）core_values 直改 | self_model_manager.py `_apply_growth_records` | identity/milestone GR 的权重意图 → adapter 路由（强制 NEED_REVIEW，永不自动应用）→ 跳过直写 | 旧行为字节不变 |
| P0-2 | B-3 伪审批 fallback | runtime_core.py `accept_self_model_suggestion` | 不在 ApprovalQueue → 拒绝直接批准（return False） | 旧行为保留 |
| P0-3 | B-1 growth_integration 直写 | growth_integration.py `_refresh_self_model` | 重建意图 → adapter 路由（不注入 rebuild 执行件 → 不自动重建） | store.update 旧行为 |
| P0-4 | B-2 emotion 直写 save | emotion_growth_service.py `analyze_and_merge` | 信念逐条 from_emotion_event → adapter 路由（NEED_REVIEW）；不执行内存合并与 save | merge+save 旧行为 |
| P0-5 | B-5 relationship dict 漏网 | self_model_manager.py `_apply_relationship_state` | from_relationship_event → 路由；非 ACCEPT 不写 preference | 旧行为字节不变 |

所有分支读取 flag 均 fail-open（异常时保持旧行为）；治理接线异常隔离跳过写入。

## 5. MutationAuditRecord 留痕（Phase 5）

SelfModelMutationAdapter 每次 route 落一条审计记录（`audit_records` /
`audit_trace()`），五字段冻结齐全：**mutation_id / request_id / trace_id /
decision / reason**（附 domain/actor/timestamp）。NEED_REVIEW 持久化：
Gateway 自身 NEED_REVIEW 走 B.10 自动落账；core_values 强制复核路径显式以
review_decision 调用 `gateway.save_pending_proposal` 落账——两条路径都进
ProposalStore，可被 B.11 ProposalManager 消费（register → approve → apply）。

## 6. 测试结果（Phase 6/7）

新套件 **7/7 通过**（tests/test_b13_self_model_mutation_governance.py）：

1. legacy flag=false 行为一致——core_values 直改 / emotion save / growth update / 伪审批 fallback 四路旧行为全部保留
2. flag=true 必须经过 Gateway——emotion/growth/relationship 三路不再直写，outcomes 有 Gateway 裁决
3. core_values 自动修改被阻断——权重不变，治理留痕进入 pending
4. identity anchor 永远 REJECT——绝对身份字段构造即拒 + ApplyAdapter 双保险（伪造 ACCEPT 也拒绝执行）
5. evidence 不足无法 APPLY——单证据 NEED_REVIEW，apply_route 未被调用
6. approve 后才能 apply——完整链：路由落账 → ProposalManager.approve(reviewer) → approved_decision 凭证 → apply 成功；无凭证 ApplyRejected
7. data 不变化——仓库 data/self_model.json 等五路径 stat 快照前后一致

相邻治理套件回归：**107/107 通过**（B.9/B.10/B.11 + growth/emotion/contract 六套件）。

**A/B 回归**（A = B.12 完成态 70 文件，B = B.13 完成态 71 文件；
HF_HUB_OFFLINE=1；10 文件/块流式）：

| 指标 | A | B | 结论 |
| --- | --- | --- | --- |
| 失败数 | 27 | 27 | 一致（B.5-B.9 时代既有失败） |
| 新增失败（comm -13） | — | **空** | ✅ |
| 消失失败（comm -23） | — | **空** | ✅ |
| 块退出码 | 1,0,1,1,1,0,1 | 1,0,1,1,1,0,1,0 | 一致（b_07=新增套件全过） |
| data/ 修改 | 空 | 空 | ✅ 零污染 |

B 状态恢复后 8 文件字节校验（cmp）全部通过。

## 7. 回滚方案

```bash
cd <repo>
# 1) 删除新增文件
rm src/personality/self_model_mutation_adapter.py \
   src/personality/self_model_apply_adapter.py \
   tests/test_b13_self_model_mutation_governance.py
# 2) 反向编辑五处 P0 分支（各文件中标记为 "P2.3-B.13 P0-x" 的 flag 分支块，
#    删除治理分支即恢复 legacy 单路径）：
#    - self_model_manager.py：core_values 分支 + relationship_state 分支
#    - runtime_core.py：accept_self_model_suggestion 治理守卫
#    - growth_integration.py：_refresh_self_model 治理分支
#    - emotion_growth_service.py：analyze_and_merge 治理分支
# 3) identity_check.py：PRIVILEGED_ACTORS 移除 "self_model_system"
# 4) 即时回滚：set_self_model_mutation_gateway_enabled(False)（运行时开关，
#    无需删文件即回到 legacy 字节级行为）
```

无状态迁移、无数据格式变化；ProposalStore/lifecycle 均为治理目录专属文件。

## 8. 剩余债务

1. **rebuild 执行件未接线**：P0-3 治理模式下重建意图只路由不执行
   （conservative）——"审批后如何重建 SelfModel"（rebuild_executor 的生产
   绑定）待后续任务。
2. **Phase 4.0.3 AUTO_APPLY 语义未收敛**：orchestrator 主链的
   context/preference 自动应用仍在（B.12 B-6）——与 B.13"不自动批准"的
   收敛需要独立任务书（涉及 Phase 4.0.3 行为变更）。
3. **SM-20 bootstrap 注入未治理**（B.12 B-7）：启动期批量注入保持 legacy。
4. **SM-06 store.save() 公开手动落盘未收编**（B.12 B-8）。
5. **IdentityAnchor.approved 无鉴权**（B.12 SM-17）：approved 标志任何人可设。
6. **flag 灰度未开启**：self_model_mutation_gateway_enabled 默认 False，
   shadow/enforce 灰度策略待 GovernanceConfig（B.10 设计层）接线。
7. **观测链未闭环**：SelfObservation → GrowthProposal 转换器（B.12 Phase 6
   第 7 项）仍为设计——from_self_observation 已就绪，消费方待建。

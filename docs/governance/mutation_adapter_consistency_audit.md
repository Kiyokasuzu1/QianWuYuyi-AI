# P2.3-B.10 Phase 1 — MutationAdapter 一致性审计（只读）

> 记录时间：2026-08-20
> 范围：src/growth/mutation_adapter.py（578 行）、src/emotion/mutation_adapter.py（439 行）、
> src/relationship/mutation_adapter.py（629 行）、src/governance/mutation_gateway.py、
> src/governance/audit_writer.py。
> 本阶段只读审计，未修改任何代码。

## 1. Request 构造一致性

| 维度 | growth | emotion | relationship | 结论 |
| --- | --- | --- | --- | --- |
| build_request 签名 | keyword-only，8 参数 | 同 | 同 | ✅ 一致 |
| actor_identity 默认 | `"growth_system"`（内联字面量） | `DEFAULT_ACTOR_IDENTITY="emotion_system"` | `DEFAULT_ACTOR_IDENTITY="relationship_system"` | ⚠️ growth 无模块常量（见 §5-A） |
| target_domain 硬编码 | "growth" | "emotion" | "relationship" | ✅ 一致 |
| 域前缀守卫 | `startswith("growth.")` 否则 ValueError | 同（emotion.） | 同（relationship.） | ✅ 一致 |
| context_snapshot 不自动补齐 | ✅（AuditCheck DEFER 可触达） | ✅ | ✅ | ✅ 一致 |
| from_*_event 缺维度返回 None | ✅（dimension+delta） | ✅ | ✅ | ✅ 一致 |
| from_*_event 自动补 linkage | snapshot.setdefault（mutation_id/request_id/trace_id） | 同 | 同 | ✅ 一致 |
| 证据 fallback | source_ids→user_behavior | msg/evt/src/memory_id 确定性构造 | event evidence_ids | ✅ 一致语义 |

## 2. actor_identity 治理接入

三个标准 actor（`growth_system` / `emotion_system` / `relationship_system`）均已列入
`src/governance/checks/identity_check.py` 的 `PRIVILEGED_ACTORS`（B.5/B.7/B.9 依次扩展），
语义统一：放行但 `requires_audit=True` 审计强制。✅ 一致。

## 3. context_snapshot 处理

- build_request（显式语义）：不补键，缺 request_id/trace_id → AuditCheck 给 DEFER。
- from_*_event（便利语义）：`_gen_linkage_ids()` 生成 `mut_*/req_*/trace_*` 三个键 setdefault。
- 三域 `_gen_linkage_ids` / `_to_float` 是**逐字重复**的私有函数（各 ~15 行）→ 债务 #3 候选上提，本任务不做 adapter 级重构。

## 4. pending_proposals 格式

emotion 与 relationship 的 NEED_REVIEW 入槽字典**逐键一致**（14 键）：

```
proposal_id / status / mutation_id / request_id / trace_id /
target_domain / target_path / proposed_change / evidence /
risk_level / reason / audit_reference / created_at
```

relationship 的第二处 append（route_self_model_influence 跨域强制复核）格式同上，✅。

**⚠️ growth adapter 没有 pending_proposals 槽位**：NEED_REVIEW 时只写 envelope note
（docstring："由 pipeline 层持久化，本 Adapter 不持有写句柄"）。NEED_REVIEW 的
proposal 在 growth 域**既不落盘也不入内存槽位**——三域行为不一致，是 B.10 Phase 3
统一 `save_pending_proposal` 的首要动因。

## 5. 其他不一致清单（本任务是否处理）

| # | 不一致 | 位置 | B.10 处理 |
| --- | --- | --- | --- |
| A | growth 用内联字面量 `"growth_system"`，无 `DEFAULT_ACTOR_IDENTITY` 模块常量，`__all__` 无此项 | growth/mutation_adapter.py:280, 568 | 不在 Phase 2-6 范围内（保持旧 API） |
| B | growth 无 pending_proposals 槽位（NEED_REVIEW 无内存缓存） | growth/mutation_adapter.py:239-266 | Phase 3：Gateway 层统一双写，adapter 内存槽位保留现状 |
| C | `from_proposal` 仅 growth 有（canonical proposal→多 request） | growth/mutation_adapter.py:392 | 域特性差异，非缺陷 |
| D | `route_self_model_influence` 仅 relationship 有（跨域强制 NEED_REVIEW） | relationship/mutation_adapter.py:519 | 域特性差异，非缺陷 |
| E | `_gen_linkage_ids` / `_to_float` 三份重复 | 三文件 | 记录为剩余债务，不在本任务重构 |
| F | audit_writer 回填模式三份重复（`if getattr(gateway,"audit_writer",None) is None: gateway.audit_writer = ...`） | 三文件 __init__ | Phase 4 通过统一 MutationAuditRecord 抽象收敛记录侧；adapter 回填不动 |

## 6. audit 字段现状（Phase 4 输入）

- 三域 adapter 均 import 同一个 `src/governance/audit_writer.AuditWriter / InMemoryAuditWriter`，
  **不存在三份独立 AuditWriter 实现**——"重复"指的是三处相同的接线/回填样板。
- InMemoryAuditWriter：`requests[]`（record_request 存 to_dict 副本）、`decisions[]`
  （record_decision 存 MutationDecision.to_dict() 副本，返回 `audit_{n}_{mutation_id}`）。
- MutationDecision 自身**不含 domain/actor**（它们只在 MutationRequest 上）——
  统一 MutationAuditRecord（mutation_id/domain/actor/decision/timestamp/reason）
  需要把 request+decision 投影合并，这是 Phase 4 的抽象点。

## 7. Gateway 层现状（Phase 3 输入）

- `MutationGateway.evaluate()` 是唯一裁决入口；`audit_writer` 可选注入，
  record_request/record_decision 异常全隔离。
- Gateway **无** `save_pending_proposal()`；NEED_REVIEW 语义由各 adapter 的
  route() 自行实现（growth：仅 note；emotion/relationship：内存槽位）。
- Gateway 不持任何 store/repo 写句柄（B.3 硬边界）——Phase 3 给 Gateway 增加
  可选注入的 ProposalStore 协作件不违反此边界（store 由外部注入，Gateway 不默认创建）。

## 8. 审计结论

1. 三域 adapter 的 request 构造、route envelope、deferred 槽位、audit 回填模式**高度一致**（B.5/B.7/B.9 的复制演化）。
2. 核心缺口：NEED_REVIEW 在 growth 域无槽位、在三域均无持久化；audit 记录未统一成 MutationAuditRecord 投影。
3. 建议（按任务书顺序执行）：Phase 2 ProposalStore 落账 → Phase 3 Gateway.save_pending_proposal 双写 → Phase 4 MutationAuditRecord 统一投影 → Phase 5 只读查询 → Phase 6 灰度开关。
4. 本审计**未修改任何代码**。

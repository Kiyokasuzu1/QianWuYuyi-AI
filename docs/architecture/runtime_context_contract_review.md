# P2.3-A.1.5 RuntimeContext 契约审查与冻结（runtime_context_contract_review）

> 状态：**只读审查文档**——未修改 src/data/tests，未提交代码
> 日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 被审查对象：docs/architecture/runtime_context_contract.md（P2.3-A.1 产出，v2.0 契约草案）
> 前置依据：P2.3 勘察报告 + P2.3-D 五份台账 + 本次新增实测取证（见附录 A）

---

## 1. 审查结论

**总体判定：契约草案通过，准予冻结为 RuntimeContext v2.0 基线（FREEZE-BASELINE）。**

冻结附带 5 项裁决（R1-R5），实施阶段必须遵守：

| # | 裁决 | 内容 |
|---|---|---|
| R1 | **遗留豁免：`emotion_manager` 实例键** | 实测 orchestrator Step 7/11 真实读取 `assembled_context["emotion_manager"]`（orchestrator.py:2033/2056）。v2 本体仍禁止任何实例；`legacy_view()` 投影允许携带该键**至多一个迁移周期**，迁移第 3 步改为读 orchestrator 自有 `self.emotion_manager` 后必须移除 |
| R2 | **inputs/outputs 盲盒约束** | 沿用 v1.0 的 inputs/outputs 是最大不可见风险。冻结附加约定：outputs 至少规范化 `reply`/`source` 两键；inputs 必须包含 `user_message`/`user_id`（normalizer 已依赖此约定） |
| R3 | **legacy_view 禁止缓存** | 15 步会原地 `append` 到 `trace` 列表（2042/2086/2212）。`legacy_view()` 每次调用必须返回**新构造**的 dict + 新列表，禁止复用缓存（v2 本体 frozen 天然免疫） |
| R4 | **runtime 主链无权限门（待确认项）** | 五扇门 `can_modify_*`/`resolve_identity` 在 src/runtime/ 下**零调用点**（本次 grep 空）。P2.1.3 门只在 orchestrator 15 步。Runtime.process 17 阶段内部的状态写入口是否受门保护，须实施阶段逐写入口定位确认（不阻塞本冻结） |
| R5 | **业务引用不吸收** | context v1.0 的 `identity_snapshot_ref`（Any 业务对象引用）违背快照原则，属 legacy 内部字段；v2 只吸收文本/ID/冻结 dict，不吸收任何业务对象引用 |

---

## 2. 字段冻结结果（Phase 1 审查表）

| 层 | 字段 | 用途 | 当前来源 | 未来扩展 | 冻结 |
|---|---|---|---|---|---|
| Identity | identity.id | 内部身份 ID（沙盒 `_unknown_sender`） | security/identity.py:37/188 | 无 | ✅ FROZEN |
| Identity | identity.source | qq/api/system/unknown | Identity.source | api/system 预留白名单 | ✅ FROZEN |
| Identity | identity.verified | 格式校验结果 | Identity.verified | 无 | ✅ FROZEN |
| Identity | identity.permission | user/sandbox/admin | Identity.permission | admin 通道预留 | ✅ FROZEN |
| Identity | identity.is_sandbox | 派生布尔 | Identity.is_sandbox | 无 | ✅ FROZEN |
| Request | request.request_id | 请求唯一 ID | ❌ 缺失（新增） | 无 | ✅ FROZEN |
| Request | request.trace_id | 三系统关联键 | ❌ 缺失（新增） | 无 | ✅ FROZEN |
| Request | request.timestamp | 到达时间 | 散落（新增统一） | 无 | ✅ FROZEN |
| Request | request.channel | chat/initiative/cli/admin | ❌ 缺失 | 新入口枚举追加 | ✅ FROZEN（枚举开放） |
| Request | request.entry | 入口路径标识 | pipeline metadata 近似 | 无 | ✅ FROZEN |
| Perception | perception（Dict 槽） | 模态快照容器 | screen 已有先例（Step 3.5） | 任意新模态 key | ✅ FROZEN（key 开放） |
| Perception | p[k].modality/captured_at/data/source | 模态载荷 | 无标准（新建） | 无 | ✅ FROZEN |
| Cognitive | cognitive.memory_refs | 检索命中的记忆 ID 引用 | Step 2 检索散落 | 无 | ✅ FROZEN |
| Cognitive | cognitive.retrieved_knowledge | 知识/记忆摘要 | memory_summary 近似 | 无 | ✅ FROZEN（仅摘要/引用，禁全文复制） |
| Cognitive | cognitive.reasoning_context | 推理中间态 | 散落/缺失 | 思考链扩展 | ✅ FROZEN（key 开放） |
| State | state_snapshots.emotion | {dominant,intensity,updated_at} 拷贝 | 组装器 dict 里装实例（越界） | 无 | ✅ FROZEN（禁实例） |
| State | state_snapshots.relationship | 关系摘要拷贝 | relationship/relationship_profile 双键混乱 | 无 | ✅ FROZEN |
| State | state_snapshots.personality | 人格快照拷贝 | personality_context | 无 | ✅ FROZEN |
| State | state_snapshots.growth | 增长快照 + proposal 引用 | growth_proposals（context v1.0） | 无 | ✅ FROZEN |
| State | state_snapshots.self_model | SelfModel 快照拷贝 | self_model | 无 | ✅ FROZEN |
| Mutation | mutations（journal） | MutationRequest/Event/Proposal 记录 | ❌ 缺失（新增） | 委员会决策节点 | ✅ FROZEN（三种形式枚举固定） |
| Audit | audit.chain | 请求审计链节点 | ❌ 缺失（新增） | 无 | ✅ FROZEN |
| Audit | audit.audit_refs | record_audit_log 关联 ID | ❌ 缺失（新增） | 无 | ✅ FROZEN |
| Audit | audit.request_id/trace_id | 落盘关联键 | ❌ 缺失（correlation_id 实际为空） | 无 | ✅ FROZEN |
| 基底 | session_id/lifecycle_id/state/started_at/ended_at/error/schema_version | 生命周期骨架 | lifecycle_context v1.0 | 无 | ✅ FROZEN |
| 基底 | inputs/outputs | 输入输出容器 | v1.0 | 见 R2 约定 | ⚠️ FROZEN-WITH-R2 |
| 基底 | metadata | 附加元数据 | v1.0（自由键） | pipeline 等写入方 | ⚠️ FROZEN（键约定后补） |

**冻结规则**：冻结字段的**语义**不可变更；新增字段必须 `schema_version` 递增 + `from_dict` 缺省回填（沿用 v1.0 先例）。perception 槽、reasoning_context 为**开放扩展区**，只加 key 不改核心。

---

## 3. 禁止进入 Context 的对象列表（Phase 2）

### 3.1 黑名单（永久禁止，作为契约附则）

| 禁止项 | 判定 | 现状违例 | 处置 |
|---|---|---|---|
| database connection | 永不进入 | 无 | — |
| repository instance | 永不进入 | 组装器 dict 的 `relationship_repo` 键**设计意图**就是放 repo（虽无人写入） | 契约已裁决删除该键（runtime_context_contract.md §3.6） |
| manager instance | 永不进入 v2 本体 | 组装器 dict 的 `emotion_manager` 键被 15 步真实读取（orchestrator.py:2033/2056） | R1 遗留豁免：legacy_view 至多携带一个迁移周期，第 3 步移除 |
| LLM client | 永不进入 | 无（orchestrator 自持，不入 ctx） | — |
| event bus instance | 永不进入 | 无（v2 只产 `event_envelope()` 数据） | — |
| file path | 永不进入 | 组装器自持 `agreements_dir`/`repo_root`（文件系统依赖，runtime_context.py:16-18） | 只读 legacy 工具保留现状；v2 不吸收该职责 |
| mutable global state | 永不进入 | 无（v2 frozen） | — |

### 3.2 断言与判定标准

> **Context = 当前意识快照**（一次请求的认知工作集：身份 + 感知 + 认知引用 + 状态快照 + 变更日志 + 审计链）。
> **不是**所有系统容器。

任何候选字段进入契约前必须通过三问：
1. 序列化后能否重建？（实例/连接/文件句柄：否 → 禁止）
2. 脱离进程生命周期后是否仍有效？（内存队列/回调闭包：否 → 禁止）
3. 持有它是否会形成写句柄？（repo/store/manager：是 → 禁止，改为 proposal 通道）

用三问复检 v2 草案：全部字段通过；唯一例外 R1（legacy 投影，非本体）。

---

## 4. 扩展能力评估（Phase 3 模拟）

| 场景 | 新模块读取什么 Context | 新模块产生什么 Event | 是否需要修改 RuntimeContext schema |
|---|---|---|---|
| A. 视觉系统 | perception["camera"]/["screen"]（模态载荷 + captured_at）；cognitive 交叉引用 | `perception.captured`（含 modality/request_id） | **否**——只注册 perception key；快照结构已冻结 |
| B. 语音系统 | perception["voice"]（转写文本 + 时间戳） | `perception.captured`、`speech.transcribed` | **否**——同 A |
| C. 身体控制系统 | perception（传感器入）+ outputs（动作指令出）；state_snapshots.emotion（姿态反馈依据） | `action.requested`（动作提案，走 mutations journal）+ `action.executed` | **否**——outputs 约定 body_state/action 键（R2 开放槽），核心不动 |
| D. 主动行为系统 | state_snapshots（五模块现状）+ cognitive.memory_refs（主动观察依据） | `initiative.proposed`（进 mutations journal，proposal 形式） | **否**——journal 三形式已覆盖 initiative 提案 |
| E. 设计委员会 | `snapshot()` 冻结视图（identity + memory_refs + state_snapshots + audit.chain）；mutations 队列 | `review.opinion`（APPROVE/REJECT/REQUEST_MORE_EVIDENCE，落到 mutations 决策节点） | **否**——读取面与决策落点均已预留 |

**结论：5/5 场景无需修改核心 schema。** 扩展槽（perception 开放区、outputs 约定键、mutations 三形式）设计有效，验证了契约的第一目标：**新模态/新系统不再需要修改核心代码**（对照 P2.3 目标 C，屏幕模块 Step 3.5 硬编码的教训已被契约吸收）。

---

## 5. Mutation 生命周期审查（Phase 4）

### 5.1 七步链对照表（观察→事件→理解→Proposal→Permission→Apply→Audit）

| 模块 | 观察/事件 | 理解 | Proposal | Permission | Apply | Audit | 现状评级 |
|---|---|---|---|---|---|---|---|
| memory | ✅ message.received/memory.created | ✗ 无语义评估层 | ✗ **直写**（memory_store.add 1246 + vector_memory.add_memory 1261） | ✅ 1197 | ✅ | ⚠️ 步骤级，无 trace 关联 | 链断 2 处 |
| emotion | ✅ | ⚠️ emotion_event_detector 部分理解 | ✗ 直写（process_event + repo.filepath 覆盖 2070-2081） | ✅ 2029/2052 | ✅ | ⚠️ 同上；decay 断链 | 链断 1 处 |
| relationship | ✅ MemoryCreatedEvent→候选桥 | ⚠️ RelationshipEvaluator（在不可达段） | ✗ | ✅ 2130（orchestrator 侧）／**❓ runtime 侧无门（R4）** | ✗ orchestrator 侧**断链**（2133-2136）；✅ runtime 侧直写（3319-3320） | ⚠️ bypass_write 标签存在但不可达 | 链断 2 处 + 双权威 |
| personality | ✅ | ✅ 治理链理解 | ✅ GrowthProposal/治理提案 | ✅ 1350/1384/1395 | ✅ SelfModelUpdater→apply_change_proposal | ✅ before/after | 近全链 ✅；旁路：personality_state.json 由 RuntimeCore 直写 |
| growth | ✅ | ✅ evaluator | ⚠️ 双路径（incremental_update 直写 vs proposal 流 auto_accept=0.8 门槛） | ✅ 1319 | ✅ | ⚠️ growth_history 169 条无 user_id 溯源 | 链断 1 处（直写路径） |
| self_model | ✅ | ✅ | ✅ | ✅（治理链内） | ✅ 唯一合法写入口 | ✅ | **唯一全链达标** |

### 5.2 绕过路径清单（契约生效后必须收敛，实施阶段处置）

| # | 绕过路径 | 位置 | 性质 |
|---|---|---|---|
| B1 | memory 直写（store + vector 双写无事务） | orchestrator.py:1246/1261 | 无 proposal 环节 |
| B2 | 情绪直写（filepath 覆盖 + save） | orchestrator.py:2070-2081 | 无 proposal 环节 |
| B3 | personality_state.json 直写 | runtime_core（personality_state_updater 链） | 不经治理链 |
| B4 | legacy 关系 delta（bypass_write 审计标签） | orchestrator_hooks.py:120-194 | 当前不可达；保留即风险 |
| B5 | 启动期 set_experience_context / resolver 属性注入 | orchestrator.py:296/262 | 非人格变更，暂列观察 |
| B6 | runtime 17 阶段状态写入口无身份门 | src/runtime/ 下五扇门零出现（本次 grep 空） | **R4 待确认项** |
| B7 | growth incremental_update 直写 + user_id 硬编码 | orchestrator.py:404/407 + Step 14.5 | 双路径未收口 |

### 5.3 审查判定

契约 v2.0 的 Mutation Journal 设计正确覆盖了"只允许 proposal/event/request"的目标，但**契约只能约束新代码，无法约束现有 7 条旁路**。冻结时声明：旁路清单（B1-B7）作为"契约债务"随实施批次逐条收口，收口顺序建议 B1/B2 → B4 → B6（核实）→ B3/B7；收口前旁路不得新增调用点。

---

## 6. Migration 风险（Phase 5 审查表）

| 迁移对象 | 主要风险 | 概率 | 缓解 | 回滚 | 判定 |
|---|---|---|---|---|---|
| runtime_context.py 组装器 → legacy_view() | 键清单投影不精确 → 15 步静默读 None | 中 | **本次已实测读取键全集**：emotion_context(1158)/prompt_blocks(1170)/emotion_manager(2033/2056)/relationship_profile(2135)/trace(append 2042/2086/2212)；迁移前用"键清单固化测试"锁死 | legacy_view 与 assemble_context 并存，开关切换 | ✅ 可回滚 |
| lifecycle_context.py v1.0 → v2 基底 | context_storage 旧快照（147 份）反序列化失败 | 低 | from_dict 缺省回填（v1.0 先例）；schema_version 判定降级 | 保留 v1.0 读取路径 | ✅ 可回滚 |
| context/runtime_context.py v1.0 → upcast | 17 阶段零改动前提被破坏 | 低-中 | normalizer 先例（runtime_core.py:76 _normalize_runtime_ctx，Phase 4.0.4-Pre 已验证）泛化 | normalizer 单点，撤换即回滚 | ✅ 可回滚 |
| 假键删除（relationship_repo/on_emotion_change） | 断链段复活引发真实关系写入 → P2.1.3-R 门禁需重验 | 中 | 先决策"修复接链 or 正式退役"，与 P2.3 勘察建议 1 联动；删除不触碰 2139-2164 不可达段 | 键删除为纯删读点，git 单提交可逆 | ✅ 可回滚 |
| legacy_view 的 trace 可变列表 | 投影缓存导致跨请求污染 | 中 | R3：每次调用新构造 | — | ✅ 已入冻结条款 |

**总体判定**：满足"不破坏生产（渐进 4 步 + legacy_view 零修改读取）、可逐步迁移（每步独立提交）、可回滚（adapter 单点 + from_dict 版本降级）、不需一次重构（三个旧类全部保留）"四要求。**迁移不改变任何现有写入行为**——旁路收口是另一条实施线，禁止混入上下文迁移。

---

## 7. 下一阶段实施建议

1. **冻结生效**：本文档签署即视为 v2.0 字段集冻结基线；此后任何字段变更走 schema 版本流程（§2 冻结规则）。
2. **实施顺序建议**（P2.3-A.2 起，每步独立提交 + 独立回归）：
   - 步骤 0：legacy_view 键清单固化测试（锁死本次实测 5 个真实键 + 2 个待删假键的现状）；
   - 步骤 1：pipeline 构造点切换 v2（基底直通，风险最低）；
   - 步骤 2：normalizer upcast（legacy core 17 阶段零改动）；
   - 步骤 3：orchestrator 消费 legacy_view()，同时按 R1 移除 emotion_manager 实例键（改读 self.emotion_manager）；
   - 步骤 4：假键删除（先裁决接链 or 退役）。
3. **台账联动**：实施前更新 wiring_status.md（三个 Context 行状态）与 unwired_components.md（登记 yuyi_runtime_integration 孤儿——P2.3-A.1 发现其零使用方）。
4. **债务线并行推进**：旁路收口（B1-B7）按 §5.3 顺序独立立项，不阻塞上下文迁移。
5. **R4 专项**：runtime 17 阶段写入口 × P2.1.3 门的核实作为独立小勘察，产出结论后再决定 B6 处置。
6. 委员会与视觉/语音/身体模块：确认**不依赖**本契约实施完成即可并行设计（§4 结论），但落地接线须等 v2 实现。

---

## 附录 A：本次审查实测取证

| 事实 | 证据 |
|---|---|
| 15 步真实读取键全集 | orchestrator.py:1158-1159/1170/2033/2056/2134/2135/2115-2116 |
| trace 原地 append | orchestrator.py:2042/2086/2212 |
| hooks 同样读取 emotion_manager | orchestrator_hooks.py:24/43 |
| runtime 侧权限门零出现 | `grep -rn "can_modify|resolve_identity" src/runtime/`（非 tests）→ 空 |
| 组装器文件系统依赖 | runtime_context.py:16-18 |
| context v1.0 业务引用字段 | context/runtime_context.py:70（identity_snapshot_ref: Any） |
| 归一化先例 | runtime_core.py:76-105（_normalize_runtime_ctx） |
| 五扇门定义与 orchestrator 8 处接线 | security/permission.py:40-93；orchestrator.py:1197/1319/1350/1384/1395/2029/2052/2130 |

## 附录 B：与既有文档的冻结关系

- runtime_context_contract.md（P2.3-A.1）：被审查对象；本审查的 R1-R5 为其**冻结修正条款**，实施时两文档共同生效。
- wiring_status.md / runtime_paths.md / event_bus_inventory.md / unwired_components.md（P2.3-D）：旁路清单 B1-B7 与之交叉引用，实施批次同步更新。
- design_committee_future.md（P2.3-D）：委员会接口不变；本审查 §4-E 确认其读取面已由 v2 契约覆盖。

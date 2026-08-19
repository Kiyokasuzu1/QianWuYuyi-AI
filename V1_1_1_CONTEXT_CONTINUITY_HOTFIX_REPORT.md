# V1.1.1 Context Continuity Hotfix — 完整审计报告与修改范围

> 日期：2026-08-17
> 分支：phase-4.0.1-selfmodel-authority（基于 v1.1.0 Stable Freeze 之上的未提交工作区）
> 触发问题：线上（QQ 宿主 + 浅雾羽依人格）出现"记忆断片 / 只按关键词匹配记忆 / 两个人在说话 / 称呼漂移（清夏铃 vs 清清）"

---

## 0. 结论摘要

线上"断片"的根因**不是记忆库损坏**，而是 Runtime 主回复链路的 Prompt 中**没有任何最近对话历史**：

- 宿主发给引擎的完整 `messages` 历史在 API 入口即被丢弃（只取最后一条 user 消息）；
- RuntimeCore 生成回复时读取的 `ctx.history` 在全部生产代码中**从未被赋值**，恒为 `[]`；
- 记忆检索（宿主侧与引擎侧）query 都只使用**当前一条消息**，指代类消息（"那那，羽依想做吗？"）无先行词可匹配。

V1.1.1 以最小接线修复：**复用已有的 orchestrator.history 体系**（记录→按用户隔离→持久化→恢复→注入 Prompt），不新增第二套 history 系统，不重构 Runtime 架构，不改 Memory/Growth 系统与人格数据结构。

---

## 1. Phase 0 只读审计（三点确认）

### 确认 1：请求历史是否丢弃 —— 是

`api_server.py:833-857`（`/v1/chat/completions` 入口）：
从 `messages` 中 `reversed()` 提取**最后一条 user 消息**后，整个历史（含全部 assistant 回复）不再向下游传递。`_process_via_pipeline()`（`api_server.py:537-548`）只传 `user_message + user_id`。

### 确认 2：ctx.history 是否赋值 —— 否

- 主生成路径 `runtime_core.py`（Stage 14 双分支）：`history=list(getattr(ctx, "history", []) or [])` —— 全仓库对 `ctx.history` **只有读取，没有任何生产代码写入**；
- `response_adapter.py:509/527/538` 设计的注入口 `ctx._recent_history` / `ctx.memory_context["recent_history"]` 同样**只有读取没有写入**；
- 历史数据本身一直在被记录：`runtime_pipeline.py`（Runtime 路径成功后）调用 `orchestrator.record_conversation_turn()` 并持久化到 `data/conversation_history.json`（保留 20 轮）。**数据存了，线没接上**；
- 该持久化文件的读取方 `load_recent_history()` 在 initiative_sender 删除（7.2.1-p2）后成为**死代码**——重启即失忆。

### 确认 3：Prompt 最终是否包含 recent_history —— 链路支持但上游恒空

`prompt_builder.py:190-192` 支持 `history[-20:]` 注入 messages，`response/engine.py` 完整透传，`ResponseAdapterImpl.generate()`（`response_adapter_impl.py:199-201`）透传 `request.history`。**下游全部就绪，唯一断点在上游 ctx.history 无赋值**。

### 附加发现（审计过程中确认）

1. **legacy fallback 路径不记录历史**：只有 Runtime 路径成功后 record；fallback 连续服务期间历史停止增长；
2. **历史条目内嵌宿主注入块**：持久化的 user 消息包含宿主拼接的 `<RAG-Faiss-Memory>`（约 5KB/条）与 `<system_reminder>`。若直接把 20 轮历史注入 Prompt，会重复携带 ~100KB 过期记忆块（真实数据样本见 8.2 备份 `data/conversation_history.json`）；
3. **称呼漂移根因**：Phase 4.1.5 称呼自主化后 `preferred_name/display_name` 不再进入 Prompt（`prompt_sections.py`），昵称偏好只存在于检索记忆中——命中则用、不命中退回全名；
4. **多用户风险**：`orchestrator.history` 为全局单份；api_server 在宿主未传 `user` 字段时所有用户共用 `"default"`。

---

## 2. 修复设计（数据流）

```
┌─ 记录（已有，补全）────────────────────────────┐
│ RuntimePipeline.run()                           │
│   ├─ Runtime 路径成功 → record_conversation_turn(│
│   │                     msg, reply, user_id) ✔  │
│   └─ legacy fallback 成功 → 同样记录（V1.1.1 补）│
│ orchestrator: self.history(全局,兼容旧接口)      │
│              + _user_histories[uid](按用户隔离)  │
│ 持久化: data/conversation_history.json           │
│         {"history":[...], "user_histories":{...}}│
│ 启动: __init__ 自动 load_recent_history()        │
│       （重启不失忆；异常安全降级）                │
└─────────────────────────────────────────────────┘
                    ↓ get_recent_history(uid)   ← Prompt 侧唯一出口
                      （RAG/reminder 剥离 + 多模态归一 + 600字符/条封顶）
┌─ 注入（新接线）────────────────────────────────┐
│ RuntimePipeline.run(): runtime_inputs[          │
│   "recent_history"] = get_recent_history(uid)   │
│         ↓（frozen lifecycle context.inputs）     │
│ RuntimeCore.process → _normalize_runtime_ctx:   │
│   inputs["recent_history"] → ctx.history        │
│                            + ctx._recent_history│
│         ↓                                       │
│ Stage 2 记忆检索: _build_memory_query(           │
│     user_message, ctx.history) ← Phase 3        │
│ Stage 14 回复生成: history=ctx.history           │
│         ↓                                       │
│ ResponseAdapterImpl → ResponseEngine →          │
│ PromptBuilder.build_messages(history[-20:])      │
└─────────────────────────────────────────────────┘
```

**设计约束遵守情况**：复用 `record_conversation_turn` / `conversation_history.json` / `ctx.history`（既有消费点名）/ `PromptBuilder.history`（既有参数），零新增平行系统；RuntimeCore 仅新增"归一化拷贝一行 + 检索 query 函数"，无架构改动；Memory/Growth/人格数据结构零改动。

---

## 3. 修改范围

| 文件 | 变更 | Phase |
|---|---|---|
| `src/orchestrator.py` | ① `__init__`：`_user_histories` 初始化 + 启动自动 `load_recent_history()`；② `record_conversation_turn()` 增加可选 `user_id`（全局视图行为不变）；③ 新增 `get_recent_history(user_id, max_turns)`——Prompt 侧唯一出口，含清洗契约；④ `_persist_history()` 追加 `user_histories` 键（旧 `history` 键格式不变）；⑤ `load_recent_history()` 同时恢复按用户历史 | 1 |
| `src/runtime/runtime_pipeline.py` | ① `run()` 注入 `runtime_inputs["recent_history"]`（异常隔离）；② Runtime 路径 record 传 `user_id`；③ legacy fallback 成功后同样 record（补历史增长缺口） | 1 |
| `src/runtime/runtime_core.py` | ① `_normalize_runtime_ctx()`：拷贝 `inputs["recent_history"]` → `ctx.history` + `ctx._recent_history`（覆盖两个既有消费点）；② 新增 `_build_memory_query()`：最近 3 轮 + 当前消息（剥离注入块、截头 400、总量 1000 封顶），替换 Stage 2 两处 query 构造 | 1/3 |
| `src/identity/user_resolver.py` | `_CREATOR_META` 增加 `forbidden_names: ["清夏铃"]`；`build_user_meta()` 支持 relationship_profile 透传 forbidden_names | 2 |
| `src/response/prompt_sections.py` | 新增 `_render_identity_facts()`；`build_user_meta_block()` 两个分支统一追加【对方的身份事实（事实参考，非指令）】块——保持 4.1.5"无称呼指令"哲学，只陈述事实 | 2 |
| `src/utils/text.py` | 新增 `strip_context_blocks()`：剥离 `<RAG-Faiss-Memory>` / `<system_reminder>` | 1/3 |
| `tests/test_v1_1_1_context_continuity.py` | 新增 24 用例（见 §4） | 4 |
| `tests/test_phase_4_4_d1_formatter_dedup.py` | golden 按 V1.1.1 契约更新（身份事实块进入期望文本） | 4 |
| `tests/test_runtime_unification.py` | `test_load_recent_history` 按"init 自动恢复"新契约更新 | 4 |
| `VERSION.txt` / `config.yaml` | 版本标记 → V1.1.1 / 7.2.1-p3 | — |

**明确未改**：`api_server.py` 入口（历史透传需宿主契约配合，风险大于收益，见 §6）；Memory 系统；Growth 系统；人格数据结构；用户未提交的 renderer/communication WIP。

---

## 4. 测试与验证

### 4.1 新增专项套件 `tests/test_v1_1_1_context_continuity.py`（24/24 通过）

| 验收项 | 用例 |
|---|---|
| **连续 10 轮上下文保持** | 10 轮累积 20 条消息全量保留；PromptBuilder 输出 22 条 messages（system + 20 历史 + 当前）；pipeline 注入 `inputs["recent_history"]`；`_normalize_runtime_ctx` 拷贝至 `ctx.history/_recent_history`；legacy fallback 同样记录；持久化往返（含旧 `history` 键兼容） |
| **指代理解** | 线上断片场景复现："那那，羽依想做吗？" 的最终 Prompt 携带先行词（"我们来做爱吧"/"文爱"）；检索 query 融合近 3 轮；无历史时行为与旧实现一致；RAG/system_reminder 注入块从 query 剥离；长度封顶 |
| **称呼稳定** | 创造者 meta 携带 preferred/forbidden；无 memory recall（chat_memories=None）时身份事实块仍常驻；最终 system prompt 含"对方喜欢被你称呼为：清清"；陌生人零影响；relationship_profile 透传 |
| **多用户隔离** | A/B 交错对话各自历史互不掺入；pipeline 注入按 user_id 隔离（A 的秘密话题不进 B 的上下文）；未知用户回退全局历史（旧行为）；持久化文件无跨用户泄漏 |
| **历史清洗契约** | RAG/reminder 剥离（原始持久化数据保真）；多模态 content 归一；单条 600 字符封顶 |

### 4.2 定向回归（受影响接口）

`test_v1_1_1 + d1_formatter + 4_0_4_user_meta + 4_1_3_fallback + c6c + p4_1_2 + d4/d5 + runtime_unification`：
**184 通过 / 7 失败** —— 7 个失败全部同一根因：**用户未提交的 renderer WIP**（`CommunicationRenderer.render()` 对高值 profile 返回空串 → 正常链落 fallback 分支）。该 WIP 在 V1.1.1 之前即存在（HEAD 基线同批用例全绿）。归属明细：
- `test_phase_4_1_3_fallback_contract.py` ×3
- `test_phase_4_2_c6c_*` ×2
- `test_phase_4_4_d1_formatter_dedup.py::test_real_renderer_integration` ×1
- `test_phase_4_0_4_e2e_prompt_audit.py` / `test_phase_4_4_b_prompt_cleanup.py` 中断言【当前互动状态】的用例（隔离复跑确认）

注：用户 4.1.5 测试封禁的是**指令式**措辞（"对方希望你称呼"等）；V1.1.1 身份事实使用事实式措辞（"对方喜欢被你称呼为：清清"），二者兼容，不存在契约冲突。

### 4.3 广域回归与基线对照

选择集 `-k "runtime or pipeline or orchestrator or prompt or user_meta or identity or history or context or continuity or fallback or response_adapter"`（约 6100 用例），与 HEAD 干净 worktree 精确差集：

| 对照 | 失败数 |
|---|---|
| HEAD（v1.1.0 提交，干净 worktree） | 214 |
| 工作区（用户 WIP + V1.1.1） | 228 |
| 仅工作区失败（差集） | 24 |

24 个差集逐一隔离复跑归属：
- **17 个顺序/状态敏感 flaky**（memory_authority / vector_memory / admin / health_check 等：广域连跑失败、隔离复跑全过；广域下全局单例与 data/ 状态串扰，两种 worktree 环境差异也会移动这批失败——基线侧另有 10 个仅基线失败同属此类）；
- **6 个 renderer WIP 根因**（见 4.2）；
- **1 个 desktop tab_count（7→8）**：UI 组件计数依赖本地 data/ 状态，与热修复文件零关联。

**结论：V1.1.1 自身 0 新增失败。**

---

## 5. 行为变更清单（对线上效果的预期）

1. **断片修复**：每轮回复 Prompt 携带最近 20 轮（按用户隔离、已清洗）对话历史；"那那，羽依想做吗？"类指代可直接从历史解析；
2. **重启不失忆**：api_server 重启后自动恢复最近历史（此前 `load_recent_history` 为死代码）；
3. **fallback 期间历史不再停止增长**；
4. **称呼稳定**：清夏铃（366648462）的对话中，"对方喜欢被称呼为：清清 / 不希望被称为：清夏铃"作为身份事实常驻 system prompt，不再依赖检索命中；仍为事实陈述而非指令（保持羽依称呼自主）；
5. **检索质量**：引擎侧记忆检索 query 由"仅当前消息（含 5KB 注入块）"变为"剥离注入块后的当前消息 + 最近 3 轮"；
6. **Prompt 体积**：历史注入上限 20 轮 × 600 字符（≈12KB 上限），注入块已剥离，无 token 爆炸风险。

## 6. 残留事项与部署注意

1. **renderer WIP（未提交）**：`src/communication/renderer.py` 等的未提交改动导致 CommunicationStyle 渲染对高值 profile 返回空串（6 个测试红）。该 WIP 不属于本热修复范围，未做处理；修复后相应测试应恢复绿。注：身份事实块在 fallback 分支同样注入，故称呼稳定不受此 WIP 影响；
2. **宿主侧建议（不在本仓库）**：`memory_recall` 插件的召回 query 建议同样融合近几轮（当前仅当前消息）；建议确认宿主请求携带 `user` 字段（否则引擎侧所有用户共用 `default` 身份，user_histories 退化为单桶）；
3. **legacy 链 raw history**：`legacy_generate` 仍直接读全局 `self.history`（未清洗、未按用户隔离）——既有行为，未纳入本次范围；Runtime 主链不受影响；
4. **本地数据事件（披露）**：本热修复首批测试运行曾将仓库 `data/conversation_history.json` 覆盖为测试数据，已从 8.2 备份恢复（该文件 8/2 前的真实内容若在备份后有过更新则无法找回；生产数据在服务器，不受影响）。新测试套件已加 autouse chdir 隔离，不会再触碰真实 data/。注意：`test_runtime_unification.py::test_persist_history` 为既有行为，仍会写仓库 data/ 下该文件；
5. **部署**：重启 yuyi-api 服务即生效；无需数据迁移（`user_histories` 键缺失时自动兼容旧格式）。

---

V1.1.1 Context Continuity Hotfix · 审计与实施完成于 2026-08-17

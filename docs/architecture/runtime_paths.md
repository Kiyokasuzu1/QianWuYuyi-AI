# P2.3-D 运行时路径登记（runtime_paths）

> 状态：**只读台账**　日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 目的：记录当前请求路径与状态写入路径的实况，标记生产/fallback/deprecated 路径，明确关系数据唯一权威。

---

## 一、当前请求路径

```
HTTP 请求 /v1/chat/completions（api_server.py，模块级初始化）
  │
  ├─ ① 身份解析（P2.1.2）                                  ✅ ACTIVE
  │     api_server.py:821-837：DEFAULT_RESOLVER
  │     （src/security/identity.py::resolve_identity，非法输入→_unknown_sender 沙盒）
  │
  ├─ ② 第 1 级：RuntimeController（api_server.py:871-908） ⚠️ 生产 No-Op
  │     phase4_enabled=false → 初始化即 No-Op（522-533），永不接管
  │
  ├─ ③ 第 2 级：RuntimePipeline.run（598）                 ✅ ACTIVE 主路径
  │     装配（api_server.py:427-470）：
  │       TokenOptimizer（token_opt.enabled=false → 不装配）
  │       PersistenceHook（RuntimeContextStorage，data/runtime_context）
  │       EventSink（observer SSE）
  │     内部双路径：
  │       3a. Runtime.process(event, ctx)（761）──【Phase 4.0.2 优先】
  │           Legacy RuntimeCore 17 阶段（lifecycle_executor 调度，runtime_core.py:4823）
  │           _final_reply 非空 → source="runtime"
  │       3b. Orchestrator.process(msg)（820）──【5B legacy fallback】
  │           触发条件：Runtime.process 未产生 _final_reply（1188）或异常（1194）
  │     收尾：persistence_hook.persist → event_sink.emit → trace
  │
  ├─ ④ 第 3 级：orchestrator.process 兜底                 🕸️ DEPRECATED 兜底
  │     pipeline 不可用 / 全链路失败时（api_server.py:948 附近 503 兜底说明）
  │
  └─ ⑤ Orchestrator.process() 15 步（1060）
        Step 0 审计 → 1 身份（1099）→ 2 记忆检索 → 3 assemble_context
        → 3.5 屏幕上下文 → 4 人格 → 5-6 情绪/关系读 → 7 情绪 pre（门 2029）
        → 8 LLM（bridge.handle_message → legacy_generate → engine.generate）
        → 9 历史 → 10 记忆保存（门 1197）→ 11 情绪 post（门 2052）
        → 12 关系 post（门 2130，⚠️断链）→ 13 回复事件+审计
        → 14.5 growth（门 1319）→ 14.6 self_model 治理链（门 1350/1384/1395）
        → 5.0-A 编排器（未接线，跳过）→ 风格快照（YUYI_STYLE_MONITOR=1 才写）

图例：✅ 实际生产路径　⚠️ 配置门控/No-Op　🕸️ deprecated 路径　（并列标注：RuntimePipeline 为 ACTIVE，Orchestrator 为 COMPATIBILITY）
```

**其他入口**：
- `/initiative`（api_server.py:1007-1041）：只走 Orchestrator，不经 pipeline，**无 trace**。
- main.py CLI：独立 LongLoop 链路，无 trace、无审计关联（⚠️ 含调试残留 `print("🔴 程序入口 N")`）。
- pipeline_server.py：平行 Flask 服务，仅测试引用（TEST_ONLY）。

---

## 二、状态写入路径

### memory

```
入口:  Orchestrator Step 10（orchestrator.py:1246-1261）；P2.1.3 门（1197）
写入:  memory_store.add（全量读盘→改→atomic_write，data/memory.json）
       + vector_memory.add_memory（向量索引，与 store 同请求内双写）
权限:  can_modify_memory(resolve_identity(user_id))；sandbox 拒绝
审计:  步骤级 record_audit_log（memory.created 等）；无 trace_id 关联
旁路:  admin memory_action（governance_provider.propose_memory_action 503 → 走审核，非直写）
```

### emotion

```
入口:  Orchestrator Step 7 pre（2040 process_event）+ Step 11 post（2070-2081）
       runtime_core 17 阶段（自身 EmotionManager 实例，runtime_core.py:952）
写入:  data/emotion_state.json（全局）+ data/emotions/{user_id}.json（镜像）
       post 阶段直接覆盖 repo.filepath 并 save
权限:  can_modify_emotion(resolve_identity(user_id))（2029/2052 两处）
审计:  步骤级记录；EmotionTraceRepository 轨迹（runtime_core 侧）
断链:  EmotionManager.update()（decay）生产不调用（唯一调用方 emotion_adapter_impl.py:141 未接线）
```

### relationship（重点）

```
入口:  ① runtime_core 17 阶段 → relationship_repository（权威写）
       ② Orchestrator Step 12 → 断链（见下）
       ③ RelationshipCandidateBridge（MemoryCreatedEvent → 候选提案，orchestrator.py:981-992 已接线）
写入:
       ① data/users/{uid}/relationship_state.json
          （relationship_repository.py:47 user_dir + 113 文件名；runtime_core load 930-931 / save 3319-3320）
       ② data/relationship_state.json（legacy 根级文件）
          —— orchestrator 仅读（380 构造包装器；857-948 get/snapshot 只读）
          —— 当前生产主链对它的写入口全部失效：
             · Step 12 断链：rel_repo=None → 2133-2136 early-return（下方 evaluator/
               apply_legacy_relationship_profile_delta 2139-2164 整段不可达）
             · Phase4 链（response_phase4/persistence_manager 双向兼容写）随
               RuntimeController 一起被 phase4_enabled=false 关闭
       ③ data/relationship_core/：未启用，磁盘目录不存在
权限:  can_modify_relationship(resolve_identity(user_id))（orchestrator.py:2130；
       security/permission.py:93）——门在断链点之前，语义仍生效
审计:  relationship.bypass_write 审计标签存在于 legacy 写函数（orchestrator_hooks.py
       120-194，"legacy 兼容路径，禁止新增调用"），当前不可达
```

**权威声明（P2.3-D 结论）**：
> **`data/users/{uid}/relationship_state.json`（relationship_repository）是当前唯一权威。**
> `data/relationship_state.json` 是 legacy 只读镜像（orchestrator/dashboard 读取面，admin 侧
> selfmodel_dashboard_provider.py:618-623 明确标注"只读兜底"）。
> `data/relationship_core/` 未启用。任何 P2.3-A 关系链修复必须写入权威仓库，不得复活 legacy 写入口。

### growth / self_model（补充，同格式）

```
growth:
入口:  Orchestrator Step 14.5 incremental_update（门 1319）+ runtime_core GrowthIntegrationService（懒装配）
写入:  data/growth_state.json + data/personality_growth_history.json；proposal 落盘 data/proposals/
权限:  can_trigger_growth；sandbox 拒绝
审计:  growth eligibility 审计；proposal 带 evidence_ids/source_event_id
风险:  GrowthPipeline user_id 硬编码 366648462（orchestrator.py:407）；双路径并存

self_model:
入口:  Step 14.6 治理链（SelfModelGovernancePolicy.evaluate → DENY/AUTO_APPLY/APPROVAL_REQUIRED）
写入:  SelfModelStore.apply_change_proposal（唯一合法写入口；data/self_model.json）
权限:  can_modify_personality（1350/1384/1395）
审计:  self_model.write before/after；硬契约禁止第三条直写路径
旁路:  启动期 set_experience_context（orchestrator.py:296）、resolver 属性注入（262）——非人格变更
```

---

## 三、双路径风险登记

| # | 风险 | 等级 | 说明 |
|---|---|---|---|
| R1 | 双 RuntimeCore | **高** | 常驻进程是自标 DEPRECATED 的 legacy；canonical runtime.py 只在 orchestrator.py:497 兜底分支可达。任何"切换"须独立立项双跑验证 |
| R2 | pipeline 内双聊天路径 | **高** | 同一请求两条实现（runtime.process vs orchestrator.process），各自维护记忆/历史/情绪逻辑；回退条件为 _final_reply 空，行为差异不易察觉 |
| R3 | 关系三权威 | 中 | 权威声明见上；断链意味着"看似存在的关系更新"实际从未运行，修复后会首次真实写入，需重验 P2.1.3-R 门禁 |
| R4 | 情绪双实例同文件 | 中 | orchestrator 与 runtime_core 各自持有 EmotionManager 写同一 data/emotion_state.json，读-改-写竞态未被协调 |
| R5 | /initiative 与 main.py 无 trace | 中 | 两条入口零追踪，与 pipeline 主链的追踪能力不对等 |
| R6 | memory 双写无事务 | 中 | memory_store.add 与 vector_memory.add_memory 独立提交，一处失败导致索引与存储不一致 |

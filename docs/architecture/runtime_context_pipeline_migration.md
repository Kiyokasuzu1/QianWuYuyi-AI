# RuntimeContext Pipeline 迁移文档（Shadow 阶段）

> 任务：P2.3-A.3.0 RuntimePipeline RuntimeContext v2 Shadow 接入
> 状态：shadow 链已建立，生产 Context 未切换
> 下一阶段：P2.3-A.3.1 RuntimePipeline 主路径切换

## 1. 当前路径（生产，未变）

`RuntimePipeline.run()`（`src/runtime/runtime_pipeline.py`）生产主链使用的仍是
`lifecycle_context.RuntimeContext`（frozen v1.0）：

```
run(input_data)
  ├─ 解析 user_message / user_id（_extract_user_message / _extract_user_id）
  ├─ :703 构造 v1 Context：
  │    RuntimeContext(
  │        session_id=session_id,
  │        lifecycle_id=lifecycle_id,
  │        inputs=runtime_inputs,        # user_message / user_id? / recent_history?
  │        metadata={
  │            "pipeline": "runtime_pipeline",
  │            "schema_version": RUNTIME_PIPELINE_SCHEMA_VERSION,   # "1.0"
  │            "runtime_path_audit": audit,
  │        },
  │    )
  │    注：v1 Context 没有 event 字段。Event 只在 _try_runtime_path 内构造
  │        （type="user_input", payload={text, content, user_id?}）并作为参数
  │        传入 Runtime.process(event, context)，context 自身不持有 event。
  ├─ 生命周期：create(pending) → with_update(state=running)（:724）
  │             → 终态 mark_success(outputs)（:931） / mark_failed(err)（:968）
  ├─ outputs = _build_outputs(...)（lifecycle/snapshot 契约 v1.0）
  │             + runtime_path_audit；reply 在 outputs.snapshot.reply，
  │             来源在 outputs.snapshot.reply_source（可缺省）
  ├─ metadata 终态合并 runtime_path_audit（:901-910）
  └─ return context（唯一返回点）
```

trace 身份约定（Phase 7.0 起）：**lifecycle_id 即 trace_id**，observer /
cognitive hooks 全链路使用；v1 metadata 中没有 request_id / trace_id 键。

## 2. Shadow 路径（本阶段新增，只读）

`run()` 返回前新增唯一 hook（DEBUG 级别才启用，异常隔离）：

```
return context 之前
  └─ if logger.isEnabledFor(DEBUG):
       _shadow_validate_context_v2(context)     # 模块级函数，只日志不改对象
            └─ from_pipeline_context(v1)
                 ├─ _enrich_trace_identity：派生副本补 metadata.request_id /
                 │    trace_id = lifecycle_id（已有非空值优先，不改输入对象）
                 ├─ 委托 A.2 context_adapter.from_lifecycle_context()
                 └─ _project_outputs：v2.outputs 顶层补 R2 规范化投影
                      reply / source（内容原样保留，见 §3 差异 2）
            └─ 六字段一致性校验（见 §4）
```

关键点：

- **不修改输入**：enrich 走 `dataclasses.replace` 派生副本；`_project_outputs`
  用 `replace(v2, outputs=...)` 而非 `with_update`（with_update 对终态会自动补
  `ended_at`，会破坏 v1 → v2 字段保真）。
- **不接入业务流**：适配层只被此 hook 与测试调用；不参与 Runtime.process /
  回退路径 / 持久化 / 事件发布。
- **失败语义**：转换失败显式抛 `ContextAdapterError`（ValueError 子类）；
  hook 内部吞掉一切异常（debug 日志），绝不改变 run() 返回路径。

## 3. 差异（v1 契约 vs v2 投影）

| # | 维度 | v1（lifecycle_context） | v2（request_context 投影） | 处置 |
|---|------|------------------------|---------------------------|------|
| 1 | request/trace 身份 | 无字段，约定 lifecycle_id 即 trace_id | request.request_id / request.trace_id | 适配层回填 lifecycle_id；metadata 已有非空值时优先沿用 |
| 2 | outputs | reply/source 位于 snapshot.reply / snapshot.reply_source | legacy_view 只在顶层规范 reply/source | 适配层把契约值投影到顶层（setdefault），原结构全部保留 |
| 3 | user_id | inputs.user_id，仅非空时写入 | inputs.user_id；缺省时落沙盒 identity.id | 校验按"有则逐值比对，无则比对沙盒" |
| 4 | schema 版本 | "1.0" | "2.0" | v2 本体固定 2.0；context_storage 信封按此分派（A.2.6） |
| 5 | 终态时间 | 仅 with_update 触发自动 ended_at | 同语义 | 适配层投影不触发自动补时间（用 replace） |

## 4. Shadow 校验面（六字段）

`_shadow_validate_context_v2` 在 DEBUG 级别下比对：

1. `session_id`：v1.session_id == v2.session_id
2. `request_id`：v1.metadata.request_id（缺省 lifecycle_id）== v2.request.request_id
3. `trace_id`：v1.metadata.trace_id（缺省 lifecycle_id）== v2.request.trace_id
4. `user_id`：v1.inputs 有非空值 → 逐值比对；缺省 → v2 应为沙盒 identity.id
5. `outputs.reply`：v1.outputs.snapshot.reply == legacy_view()["outputs"]["reply"]
6. `outputs.source`：v1.outputs.snapshot.reply_source（缺省 "unknown"）==
   legacy_view()["outputs"]["source"]

不一致 → `logger.warning`（不阻断）；通过 → `logger.debug`；hook 异常 → 吞掉。

## 5. 后续切换条件（P2.3-A.3.1 前置）

满足以下全部条件后方可把主路径切到 v2：

1. **六字段 shadow 校验在生产流量上连续 0 不一致**（DEBUG 开启观察至少一个
   完整发布周期），确认适配层无漂移；
2. **context_storage / persistence_hook / event_adapter / lifecycle_bridge
   四扇门对 v2 全绿**（P2.3-A.2.6 已完成能力化，切换前重跑
   `tests/test_context_v2_capability_gates.py`）；
3. **归一化入口按 A.2.6 判定 A 合并**（`runtime_core.py:_normalize_runtime_ctx`
   vs `lifecycle_executor.py:_normalize_mutable_ctx`，见
   `runtime_context_normalization_plan.md`），或过渡期沿用 B 双 adapter 设计
   并完成双写比对；
4. **consumer 改造就绪**：Runtime.process 内 17 阶段消费方（event_adapter、
   阶段方法）与 persistence/observer 读取点全部改为读 v2 或经
   legacy_view 兼容面；
5. **回滚预案**：切换点保留 `lifecycle_context.RuntimeContext` 构造能力与
   旧返回路径（本任务明确禁止删除 lifecycle_context），灰度开关或
   feature-flag 可一键退回 v1。

## 5b. A.3.1 切换落地（2026-08-19 完成）

主路径切换已按 feature-flag 灰度方案落地：

- **开关**：`RuntimePipeline(runtime_context_v2_enabled=False)`（默认关闭，
  读-only property `runtime_context_v2_enabled`）。`false` → 构造
  `lifecycle_context.RuntimeContext`（v1，旧行为不变）；`true` → 经
  `pipeline_context_adapter.create_pipeline_context_v2()` 构造
  `request_context.RuntimeContext`（v2）。
- **构造点**：`runtime_pipeline.py` 模块级工厂 `_create_runtime_context(...)`
  统一分发，两路径保持 session_id / request_id / trace_id / user_id /
  outputs / snapshot 一致；v2 构造异常 → 自动回滚 v1 + warning
  （`context_is_v2` 返回值标记实际模式）。
- **生命周期兼容**：v2 的 with_update / snapshot（无则 to_dict）/
  to_dict / mark_success / mark_failed 与 v1 调用点全部 duck-type 兼容，
  未在业务代码引入 `isinstance` 分支；关键桥接为
  `v2.inputs["user_input"]`（两个归一化入口 `_normalize_runtime_ctx` /
  `_normalize_mutable_ctx` 的输入键）。
- **消费面**：event sink（getattr 读取）、persistence_hook / event_adapter /
  lifecycle_bridge（A.2.6 能力门，to_dict 即可过门）、InteractionRecorder
  （outputs.snapshot + inputs.user_id）、pipeline_server
  `_build_chat_response`（outputs.snapshot.reply）均无需改动即兼容 v2。
- **回滚**：flag 置 false 或 `create_pipeline_context_v2` 抛错自动回滚；
  生产实例化点（pipeline_server.py:73/90、runtime_pipeline.py:432）均未传
  flag，默认仍走 v1。
- **验证**：`tests/test_runtime_pipeline_v2_switch.py` 20 测试全绿；
  回归批次 510 通过 / 14 失败，失败集与 HEAD 基线逐条 diff 一致（0 新增）。
- **下一阶段**：P2.3-A.3.2 RuntimeCore 归一化入口迁移（flag 灰度放开）。

## 6. 文件清单（本任务）

| 文件 | 变更 |
|------|------|
| `src/runtime/adapters/pipeline_context_adapter.py` | 新增：from_pipeline_context（委托 A.2 + pipeline 语义补丁）；A.3.1 新增 create_pipeline_context_v2 工厂 |
| `src/runtime/runtime_pipeline.py` | 新增模块级 `_shadow_validate_context_v2` + run() 返回前 DEBUG 级调用点（v1 模式生效）；A.3.1 新增 `runtime_context_v2_enabled` flag + `_create_runtime_context` 工厂分发 + 构造点替换（v2 失败自动回滚 v1） |
| `tests/test_pipeline_context_shadow.py` | 新增 26 测试（转换 / 六字段 / trace / outputs / 异常输入 / hook / 隔离审计） |
| `tests/test_runtime_pipeline_v2_switch.py` | A.3.1 新增 20 测试（flag 默认 / v2 产出 / reply 一致 / trace 一致 / persistence / event adapter / 失败回滚） |

未修改：lifecycle_context.py、orchestrator、runtime_core 生命周期、data、
权限逻辑。

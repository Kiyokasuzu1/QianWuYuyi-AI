# P2.4-B.15 Phase 1 报告：Runtime Cognitive Loop 最小闭环激活

> 日期：2026-08-20
> 基线：docs/architecture/p2_4_b15_baseline.md（HEAD 225d040）
> 依据：docs/architecture/runtime_cognitive_loop_audit.md（B.14）

## 0. 结论

Phase 1 完成。EventBus cycle 事件发布、Stage 07-13 adapter 层、Stage 11 反思链
均已接好，**全部默认 flag=False（行为与基线逐字节等价）**，不进入 Scheduler
自动运行，不开启生产 flag。

- 新增测试：11/11 通过
- Runtime 阶段契约测试：10/10 通过
- 轻量 A/B 回归：失败集合**完全一致**（A=118，B=118，新增=0，消失=0）
- data/ 零修改（A/B 全程 git status 干净）

## 1. 关键侦查发现（影响接线设计）

B.14 审计与 Phase 0 基线记载 Stage 07-13 为 no-op，但侦查确认：

- **Stage 07/08（Perception）**：真 no-op（`runtime_core.py:6156-6170` 直接 return）
- **Stage 09-13（SelfModel）**：Phase 4.1b 已接线，非 no-op。各有
  `_control_blocked` 守卫与真实业务逻辑：
  - 09: `refresh_self_model_from_runtime()` + `ctx.self_model_snapshot`
  - 10: insights → suggestions → `_pending_self_model_suggestions` 队列
  - 11: `run_scheduled_reflection()`（自带触发条件，非每轮强制）
  - 12: PolicyEngine 评估 pending 建议（allow/deny/hold）
  - 13: `drain_approved_growth_proposals()` + adapter.save_state

因此 Phase 1 adapter 层**只对 07/11 增加真实逻辑**（感知观察、反思链），
09/10/12/13 只增加调用标记（`ctx._b15_stage_calls`），绝不重复/绕过既有 4.1b
接线，避免双重执行。

## 2. 任务一：EventBus 激活（cycle_* 事件发布）

位置：`src/runtime/lifecycle_executor.py` execute() 内 4 处插入，全部受
`is_lifecycle_cycle_events_enabled()` 门控（默认 False）：

1. 循环开始：`cycle_started` + `{"stages": 17}`
2. 阶段异常分支：`cycle_failed` + `{"stage", "error"}`
3. 每阶段成功后：`STAGE_CYCLE_EVENTS` 映射的领域事件
   （cycle_memory_completed / cycle_emotion_completed / cycle_growth_completed /
   cycle_personality_completed）
4. 循环结束：`cycle_completed` + `{"stages": N}`

- **不新增事件类型**：全部复用 cycle_event.py 已有常量
- 未映射的阶段发布空字符串 → `publish_cycle_event` 内部守卫直接 no-op
- 发布走 core.domain_event_bus（duck-typed emit），无订阅者时零行为

## 3. 任务二：Stage 07-13 最小接线（adapter 层）

新文件：`src/runtime/cognitive_activation.py`

- `_STAGE_ACTIVATION_FLAGS`：7 个阶段独立 flag，全部默认 False
- `run_stage_adapter(core, stage_name, event, ctx)`：flag=False 立即返回；
  开启后先标记 `ctx._b15_stage_calls[stage]=True` 再执行适配逻辑
- Stage 07（开启时）：调用已有 `core.perception_registry.observe_all()`，
  结果挂 `ctx.perception_observations`；无 registry → noop
- Stage 08：无感知分析能力存在 → 保持 noop（仅标记）
- Stage 09/10/12/13：仅标记（observability），不重复既有 4.1b 逻辑

## 4. 任务三：Reflection Engine 接线（Stage 11）

链：Lifecycle Stage 11 → ReflectionEngine.reflect() → ReflectionRecord →
EventBus publish。仅当 flag 开启时运行：

1. 发布 `reflection_started`
2. 构建诚实 diff（自模型版本/稳定特质数/成长史条数），
   `engine.reflect(diff, source="b15_stage11", categories=["state_change"])`
3. 发布 `reflection_completed` + reflection_id/priority
4. `ctx._b15_reflection_record = record.to_dict()`（只读输出）

- **不修改 ReflectionRecord schema**（直接复用 to_dict）
- **不自动修改人格/自我模型**：零状态写入（测试验证 snapshot 前后一致）
- 复用已有 EVENT_REFLECTION_STARTED / EVENT_REFLECTION_COMPLETED 常量

## 5. 任务四：测试

`tests/test_runtime_cognitive_activation.py`，11 项全过：

- flag=false 时 17 阶段调用序列与无 adapter 时完全一致（byte-equivalent）
- flag=false 时双重执行等价（幂等）
- flag=true 时 7 个阶段均被标记调用
- Stage 07 感知注册表观察（有/无 registry）
- EventBus 收到 cycle_started + 4 领域事件 + cycle_completed（共 6 个）
- 阶段异常时发布 cycle_failed
- Stage 11 生成 ReflectionRecord（字段完整，snapshot 不变）
- ReflectionEngine 直调零状态变化
- Stage 11 无 snapshot 时 noop
- publish_cycle_event fail-soft
- data/ 全量激活后不变

## 6. 轻量 A/B 回归

方法：A=移除全部 B.15 Phase 1 改动（programmatic revert executor），
B=Phase 1 后。HF_HUB_OFFLINE=1，分 9 chunk 跑 103 文件电池。

结果：

| 指标 | A（baseline） | B（Phase 1） |
| --- | --- | --- |
| 失败数 | 118 | 118 |
| 新增失败（B 有 A 无） | - | **0** |
| 消失失败（A 有 B 无） | - | **0** |

方法学修正记录：A 电池首跑时 chunk 3 因电池清单包含尚不存在的
`tests/test_runtime_cognitive_activation.py` 导致整 chunk 报
"file or directory not found" 中止，该 chunk 11 个文件在 A 态未执行。
修复：在 A 态单独重跑该 11 文件（同序），10 个失败与 B 态完全一致
（均为既有顺序/环境依赖失败，与 B.15 改动无关）。修正后失败集合
A=B=118，双向 comm 均为空。

## 7. 约束遵守确认

- [x] LifecycleExecutor 17 阶段契约不变（contract 测试 10/10）
- [x] 所有治理 flag 默认 False
- [x] data/ 零修改（A/B 全程验证）
- [x] 未重构 RuntimeCore / Orchestrator 主链
- [x] 复用已有 EventBus / ReflectionEngine / ReflectionRecord / cycle 事件常量
- [x] 任何人格/自我模型变化仍只走既有 Governance Gateway（本阶段零状态写入）

## 8. 留给后续 Phase

- Scheduler 自动运行与生产 flag 开启：**未做**（按任务书约束）
- 生产决策点：何时、以何条件翻转各 stage flag
- 感知源注入：Stage 07 需要的 perception_registry 实体来源

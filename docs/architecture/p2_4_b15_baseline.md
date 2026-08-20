# P2.4-B.15 Runtime Cognitive Loop Activation — 基线快照

> 记录时间：2026-08-20（B.15 Phase 0）
> 依据：docs/architecture/runtime_cognitive_loop_audit.md（B.14 审计）
> 本阶段只建立基线，未修改任何 src/、data/、tests/ 文件。

## 1. 当前 HEAD

| 项目 | 值 |
| --- | --- |
| HEAD | `225d0408e89b475605d62b0435f85a9954ffb024` |
| branch | `develop/v1.1` |
| 工作区 | 256 项变更（B.4~B.14 全部未提交；与 B.14 审计完成态一致） |

## 2. data/ 状态

**零修改**（`git status --short -- data/` 为空）。
data/governance/ 不存在（治理落账从未在生产默认路径写过）。
B.15 全程约束：不修改 data/ 结构；任何新增落账只进治理/运行时专属目录。

## 3. runtime.enabled 状态

`config.yaml:124-125`：**`runtime.enabled: true`，`adapters_enabled: true`**。

- 生产 `/v1/chat/completions` 走 RuntimePipeline；异常/空回复 fallback 到 `orchestrator.process()`；`enabled=false` 为秒级回滚开关（本任务不动此值）。
- main.py CLI 侧 `RuntimePipeline(runtime=None)`——17 阶段在 CLI 路径明确未启用。
- 治理层：`governance_mode=legacy`，五域治理 flag（growth/emotion/relationship/personality/self_model）**全部 False**。

## 4. LifecycleExecutor 17 阶段状态（本阶段实测确认）

| # | 阶段 | 方法 | 状态 |
| --- | --- | --- | --- |
| 0-6 | CONTROL_CHECK → PERSONALITY_CONTEXT_BUILD | _stage_00~06 | **实现活跃** |
| 7 | PERCEPTION_OBSERVATION | _stage_07 | **no-op（休眠槽位）** |
| 8 | PERCEPTION_ANALYSIS | _stage_08 | **no-op（休眠槽位）** |
| 9 | SELF_MODEL_BUILD | _stage_09 | **no-op（休眠槽位）** |
| 10 | SELF_MODEL_EVOLUTION | _stage_10 | **no-op（休眠槽位）** |
| 11 | SELF_MODEL_REFLECTION | _stage_11 | **no-op（休眠槽位）** |
| 12 | SELF_MODEL_VALIDATION | _stage_12 | **no-op（休眠槽位）** |
| 13 | SELF_MODEL_PERSISTENCE | _stage_13 | **no-op（休眠槽位）** |
| 14-16 | RESPONSE_GENERATION → RESPONSE | _stage_14~16 | **实现活跃** |

阶段表被 `tests/test_runtime_stage_contract.py` 的 17 断言锁定；
STAGE_TO_METHOD_NAME 映射（lifecycle_executor.py:78+）为唯一实现点。
**B.15 的激活目标 = 在不动阶段表结构的前提下，把 no-op 槽位改为 flag 控制的受治理实现。**

## 5. EventBus / Reflection / Scheduler 当前调用关系

### EventBus（存在，未成体系）
```
Orchestrator(:450) ──publish──> core.event_bus 单例（src/events/bus.py）
RuntimeCore.__init__(:351-352) ├── RuntimeEventBus（Phase 3.5.23 legacy alias 包装）
                                └── DomainRuntimeEventBus（RuntimeDomainEvent 标准 schema）
cycle_event.py：九个 cycle_* 事件常量 + STAGE_TO_EVENT 映射（Phase C.1 已定义）
现状：Stage 执行器不发布 cycle_* 事件；订阅方稀疏；事件历史仅内存。
```

### Reflection（三套实现，生产零激活）
```
RuntimeCore.run_self_reflection(:1556) / run_scheduled_reflection(:2849)
    ← 仅被调用：autonomous_decision_layer.py:152（可选注入层）
              autonomous_scheduler.py:226（enabled=False 默认禁用）
Stage 11 SELF_MODEL_REFLECTION = no-op
实现资产：runtime/reflection_engine.py + reflection_evaluator.py
        + reflection_scheduler.py + reflection_growth_bridge.py
        + self_reflection_engine.py
        + runtime/self_model/reflection/{reflection_engine, reflection_record,
          reflection_store, consistency_checker, contradiction_detector}.py
```

### Scheduler / tick（能力就绪，无生产驱动）
```
RuntimeCore.tick()(:1072)：衰减 + _maybe_decide + AutonomousDecisionLayer
                          + AutonomousScheduler.on_tick + 每5分钟 _save_state
AutonomousScheduler：enabled: bool = False（autonomous_scheduler.py:29，默认禁用）
supervisor.py：常驻主循环 driver（:345 self.runtime_core.tick()）——存在但无任何生产入口启动它
api_server.py：常驻线程仅 AgentStatusWriter(:690) 与 Agent Server(:797)，无认知 tick
```

**调用关系结论（B.15 激活前的冻结事实）**：L1 Conversation Loop 活跃；L2 Maintenance（tick/consolidation）与 L3 Reflection 全部休眠——原因不是缺组件，而是**缺唯一的生产驱动入口与 flag 接线**。

## 6. B.15 执行约束复述（全程遵守）

1. 不新建平行系统（全部激活锚定 B.14 审计列出的既有资产）
2. 不修改 data/ 结构
3. 所有新增能力默认 flag=False
4. 保留 legacy Orchestrator 路径（fallback 语义不变）
5. 每阶段 A/B 回归
6. 任何人格、自我模型变化必须经过现有 Governance Gateway（B.2~B.13 全链，禁止旁路）

## 7. 基线验证命令（复现用）

```bash
git rev-parse HEAD                  # 225d0408e89b475605d62b0435f85a9954ffb024
git status --short -- data/         # 期望空
grep -A1 "^runtime:" config.yaml    # enabled: true
sed -n '19,35p' src/runtime/lifecycle_executor.py   # 07-13 no-op
grep -n "enabled: bool" src/runtime/autonomous_scheduler.py   # False
```

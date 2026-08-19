# Phase 4.1C SelfModelEvolutionPolicy 生产注入 · 架构影响报告

> 日期：2026-08-12
> 前置：Phase 4.1b 已接通 Stage 10-13，但 Validation 中 policy 为装配残留（从未注入）
> 约束遵守：未新增 SelfModel 系统；未修改 Evolution/Reflection/Persistence 逻辑；
> 仅完成装配层注入 + Validation 决策必经 PolicyEngine；fail-soft 保留（且方向为 fail-safe）

---

## 1. 审计发现（先于修改）

Phase 4.1b 的 Stage 12 里 `SelfModelEvolutionPolicy` 是**装配残留**：代码读取
`self._policy_engine`（第 4290 行 `_sm_chain_mark` 同款探测），但 `__init__` 从未创建它，
生产环境 `policy=None` 走宽松分支——免审批提案直接应用，策略阈值形同虚设。

同时发现 4.1b 遗留的**真实 bug**：`_suggestion_to_change_dicts()` 输出的字段名
（`add_core_values` / `add_stable_traits` / `add_preferences` …）**全部不在**
`self_model_policy.py` 的 `DEFAULT_EVOLVABLE_FIELDS` 集合内。一旦 policy 真的注入，
所有提案都会被判 `needs_review` → 永远等待、永远不 allow。4.1b 测试用的是假策略
（只看 changes 非空），未暴露此错位。

## 2. 修改内容（仅 `src/runtime/runtime_core.py`，3 处）

### 2.1 装配注入（`__init__`，SelfModel 初始化块之后、`_init_self_model_bootstrap()` 之前）

```python
from src.runtime.self_model.self_model_policy import (
    SelfModelEvolutionPolicy,
    MAX_CHANGE_RATIO as _SM_MAX_CHANGE_RATIO,        # 0.20
    MIN_CONFIDENCE_FOR_APPLY as _SM_MIN_CONFIDENCE,  # 0.85
)
self.policy_engine = SelfModelEvolutionPolicy(
    max_change_ratio=config 覆盖 or _SM_MAX_CHANGE_RATIO,
    min_confidence=config 覆盖 or _SM_MIN_CONFIDENCE,
)
```

- 阈值可被 config 的 `sm_policy_max_change_ratio` / `sm_policy_min_confidence` 覆盖，默认用策略模块自身常量——单一事实来源，不复制魔法数字。
- 整个构造包在 try/except 里，失败置 `None`（fail-soft）；属性声明 `self.policy_engine = None; self._policy_engine = None` 提前到 `_pending_self_model_suggestions` 声明处（约 297 行），保证任何路径下属性存在。

### 2.2 Stage 12 决策循环重写

| 情形 | 4.1b 行为 | 4.1C 行为 |
|------|-----------|-----------|
| `requires_approval=True` | 留队等待 | 不变——`waiting_approval` **永远留队** |
| policy 缺失（None） | 宽松直批免审批提案 | `held_no_policy` **全部挂起**，不再有任何绕过策略的应用路径 |
| `decision=="allow"` | 应用 | 不变——`accept_self_model_suggestion` 应用并设 `ctx._self_model_changed`，标记 `allow_applied` |
| `decision=="deny"` | 不应用但留队 | **出队**——`reject_self_model_suggestion(sid)`，标记 `deny_rejected`，永不应用永不落盘 |
| 其他（needs_review 等） | 留队 | 不变——`held_{decision}` 留队 |

每条决策写入链标记 `ctx.self_model_chain["validation"]["policy_decision"]`，
结构为 `Dict[suggestion_id, decision_str]`，可逐条追溯。

### 2.3 两个配套修正

**a) `_suggestion_to_change_dicts()` 字段映射修复**（修 1.1 节的真实 bug）：

```python
_FIELD_MAP = {
    "add_core_values":            "fundamental_values",        # 保护字段 → deny（价值观不自动新增）
    "update_core_value_weights":  "growth_understanding",
    "add_stable_traits":          "current_traits",
    "update_stable_traits":       "current_traits",
    "add_preferences":            "communication_preferences",
    "add_patterns":               "interaction_style",
    "add_contradictions":         "self_perception",
    "add_history_entries":        "recent_experiences",
    "understanding_delta":        "growth_understanding",
}
```

映射目标全部取自策略的 `DEFAULT_EVOLVABLE_FIELDS`；`fundamental_values` 是策略的
保护字段，提案触及它会被 deny——这正好实现「核心价值观不自动变更」的设计意图。

**b) Stage 10 不再设置 `_self_model_changed`**：入队 ≠ 变化。changed 标记改由
Stage 12 真正应用（allow_applied）时设置。否则被 deny 的提案也会让 Stage 13 触发
`save_state()` 空落盘。**注意：这使 4.1b 报告 G2 测试的语义失效**（该测试断言入队
即 changed），服务器侧补跑 pytest 时需同步更新该断言。

## 3. 验收结果（15/15 通过）

真实 `SelfModelEvolutionPolicy`（纯 stdlib 独立加载）+ 真实 `runtime_core.py` 裸实例，
stub 第三方依赖后验证。关键测试细节：FakeManager 的 `get_full()` 必须返回 ≥20 个
顶层字段，否则真实策略的 change_ratio > 0.20 会误伤成 needs_review（这是策略行为正确，
不是测试瑕疵）。

| 验收标准 | 测试 | 结果 |
|----------|------|------|
| allow proposal 正常应用 | H1 应用成功 / H2 应用后 Stage 13 落盘 | ✅ |
| deny proposal 永不落盘 | H3 未应用 / H4 已出队 / H5 无 save 调用 / H6 链标记 deny_rejected | ✅ |
| requires_approval 永远等待 | H7 留队 / H8 连跑两轮仍留队（不泄漏） | ✅ |
| ctx.self_model_chain 含 policy_decision | H6/H13-H15 逐条字典，含混合批次 | ✅ |
| fail-soft | H11 无 policy 不崩 / H12 全部 held_no_policy 挂起 | ✅ |
| 边界 | H9-H10 低置信度 needs_review 留队不应用 | ✅ |

临时验证脚本 `_verify_phase41c_policy.py` 已删除。

## 4. 架构影响

- **策略从「装配残留」变为「真实闸门」**：Stage 12 不再有任何绕过 PolicyEngine 的
  应用路径，4.1b 的宽松分支（policy=None 直批）被彻底移除。
- **决策可追踪**：`policy_decision` 逐条记录每个提案的 allow/deny/held 结果，
  与 4.1b 的 `ctx.self_model_chain` 链路自然衔接，成长可解释性增强。
- **保护字段生效**：`fundamental_values` 经映射成为 deny 目标，核心价值观获得了
  策略层的硬保护，而非仅靠 prompt 约束。
- **模块数量零增长**：全部改动在 runtime_core.py 内，复用既有 self_model_policy.py。

## 5. 遗留事项

1. **policy 注入位于 adapters 条件块内**：`adapters_enabled=false` 的部署下
   policy=None，所有提案 `held_no_policy` 挂起——方向是 fail-safe（不成长优于
   无审查成长），但运维侧需知晓此耦合。
2. **Stage 9 每轮 refresh 的派生态不落盘**：self_model_manager 每轮重建的派生字段
   只存在内存快照中，这是 4.1 以来的有意取舍（避免派生态污染持久态），保持不变。
3. **服务器侧补跑**：`pytest tests/runtime -x` 全量回归；4.1b G2 断言需按 2.3b
   节更新；端到端验证一次真实对话后 `ctx.self_model_chain` 完整链路。
4. **生产 `.env` 提醒（持续）**：`YUYI_ADMIN_TOKEN` 与 `YUYI_REMOTE_TOKEN`
   需配置强随机值（`openssl rand -hex 32`）。

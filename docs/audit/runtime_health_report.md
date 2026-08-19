# Runtime Health Report — Phase C.4.1

> **生成时间:** `2026-08-02T13:35:14Z`  
> **状态:** ⚠️ WITH_ALERTS  

## 0. 告警

| 级别 | 指标 | 实际 | 阈值 | 建议 |
| --- | --- | --- | --- | --- |
| WARNING | `proposals_pending` | 182 条 | < 100 条 | Pending proposal 累积过多,需人工 review (Phase C.4.3 提供 review 工具) |

## 1. 对话数量

| 指标 | 数值 |
| --- | --- |
| 估算对话轮次(基于 user role) | **286** |
| user turn 数 | 286 |
| 总 turn 数 | 286 |

## 2. Memory 写入与类型

| 指标 | 数值 |
| --- | --- |
| 总记忆数 | **286** |
| 活跃天数 | 1 |
| 活跃小时数 | 2 |
| 用户数 | 1 |

### 2.1 类型分布

| 类型 | 数量 | 占比 |
| --- | --- | --- |
| `user_shared` | 286 | 100.00% |

## 3. Growth Proposal 状态

| 指标 | 数值 |
| --- | --- |
| 总 proposal | **182** |
| pending(待审核) | 182 |
| applied/accepted(已应用) | 0 |
| rejected(已拒绝) | **0** |

### 3.2 按状态分布

| 状态 | 数量 |
| --- | --- |
| `pending` | 182 |

### 3.3 按类型分布

| 类型 | 数量 |
| --- | --- |
| `relationship` | 182 |


## 4. SelfModel 变化

| 指标 | 数值 |
| --- | --- |
| 数据状态 | empty |
| beliefs 记录数 | 0 |
| history 记录数 | 0 |
| reflection 记录数 | 0 |

## 5. CoreIdentity 变化尝试

| 指标 | 数值 |
| --- | --- |
| 禁止关键词 | 变得冷漠, 变得攻击性, 失去温柔, 完全改变人格 |
| 检测到尝试数 | **0** |

✅ 未检测到尝试改变 CoreIdentity 的 proposal。

## 6. LLM 错误统计

| 指标 | 数值 |
| --- | --- |
| 总错误数 | **9** |

### 6.1 错误类型分布

| 类型 | 数量 |
| --- | --- |
| `unknown` | 9 |


## 7. 总结

- ⚠️ 检测到 1 个告警项,需 review
  - WARNING: proposals_pending = 182 条 (阈值 < 100 条)

---

> 报告生成者: `scripts/runtime_health_monitor.py` (Phase C.4.1)
> 策略: 只读监控 + 不修改核心逻辑 + 仅告警

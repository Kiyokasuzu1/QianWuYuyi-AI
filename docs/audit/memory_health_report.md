# Memory Health Report — Phase C.3.2

> **生成时间:** `2026-08-02T13:26:21.175475Z`  
> **数据源:** `D:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI\data\memory.json`  
> **SHA-256:** `c5eb530ca9db76a1...`  
> **状态:** ⚠️ WITH_ALERTS  

## 0. 告警

| 级别 | 指标 | 实际 | 阈值 | 建议 |
| --- | --- | --- | --- | --- |
| WARNING | `user_1h[366648462]` | 286 条 | < 50 条 | 用户 366648462 1h 内写入过快,需频率限制检查 |

## 1. Memory 总量与质量

| 指标 | 数值 |
| --- | --- |
| 总记录数 | **286** |
| 文件大小 | 131.81 KB |
| normal_user | 286 |
| system_pollution | 0 |
| ai_internal_pollution | 0 |
| invalid | 0 |
| **污染率** | **0.0%** |
| **invalid 率** | **0.0%** |
| **schema 合法率** | **100.0%** |

## 2. Memory 类型分布

| 类型 | 数量 | 占比 |
| --- | --- | --- |
| `user_shared` | 286 | 100.00% |

## 3. 增长趋势

| 指标 | 数值 |
| --- | --- |
| 最近 24h 新增 | **286 条** |
| 最近 1h 按用户分布 | {'366648462': 286} |
| 按日分布(活跃天数) | 1 |
| 按小时分布(活跃小时数) | 2 |

## 4. Growth Proposal 统计

| 指标 | 数值 |
| --- | --- |
| 总 proposal 数 | **182** |
| applied(已应用) | 0 |
| pending(待审核) | 182 |
| rejected(已拒绝) | **0** |

### 4.1 按状态分布

| 状态 | 数量 |
| --- | --- |
| `pending` | 182 |

### 4.2 按类型分布

| 类型 | 数量 |
| --- | --- |
| `relationship` | 182 |


## 5. 阈值监控

| 指标 | 当前 | 阈值 | 状态 |
| --- | --- | --- | --- |
| pollution_rate | 0.0% | < 5.0% | ✅ |
| invalid_rate | 0.0% | < 5.0% | ✅ |
| 24h 增长 | 286 | < 500 | ✅ |

> **告警策略:** 仅告警,不自动删除。所有修复动作需要人工 review。

## 6. 总结

- ⚠️ 检测到 1 个告警项,需 review
  - WARNING: user_1h[366648462] = 286 条 (阈值 < 50 条)

---

> 报告生成者: `scripts/memory_health_monitor.py` (Phase C.3.2)
> 数据源: `data/memory.json` (只读) + `data/growth/proposals/proposals.json` (只读)
> 报警策略: 只读 + 只告警 + 不自动删除

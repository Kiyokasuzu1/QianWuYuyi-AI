# Runtime Trial Plan — Phase C.4.6.1

> **Phase:** C.4.6 — Controlled Real Runtime Trial
> **生成时间:** 2026-08-02
> **状态:** ⏸️ **WAITING_FOR_RUNTIME_DATA**
> **实验性质:** 受控真实运行(不新增任何核心能力)

---

## 0. 目标

让系统在 **真实长期交互环境** 中自然产生第一批 SelfModel 数据,
验证 Phase C.4.5 中识别的关键不变量:

- SelfModel 数据可在真实运行中累积
- Proposal 人工 review 流程可闭环
- CoreIdentity 在真实流量下保持 0 改变尝试
- LLM 真实调用错误率 < 5%
- Memory 在真实流量下污染率保持 0%

---

## 1. 实验周期

### 1.1 周期定义

| 项目 | 数值 |
| --- | --- |
| **最短周期** | 7 天 |
| **最长周期** | 14 天 |
| **推荐周期** | 10 天 |
| **建议开始条件** | Phase C.4.5 报告 + 182 条 pending proposal review 完 |

### 1.2 阶段划分

| 阶段 | 时间窗 | 目标 |
| --- | --- | --- |
| **Day 1-3 (Initializing)** | 0-3 天 | SelfModel 数据目录初始化,首批 beliefs/history 生成 |
| **Day 4-7 (Active)** | 4-7 天 | 数据稳定增长,Proposal 人工 review 节奏建立 |
| **Day 8-10 (Stable)** | 8-10 天 | 数据进入稳态,触发一致性检查 |
| **Day 11-14 (Validation)** | 11-14 天 | 完整一致性检查 + 长期漂移分析(可选) |

---

## 2. 监控指标(每日记录)

### 2.1 核心指标

| 指标 | 数据源 | 期望范围 | 告警阈值 |
| --- | --- | --- | --- |
| `conversation_count` | `data/memory.json` (user role 计数) | 1-5000/天 | < 1 或 > 5000 |
| `memory_created` | `data/memory.json` 新增数 | 1-500/天 | > 500 |
| `growth_proposals_created` | `data/growth/proposals/proposals.json` | 0-50/天 | > 50 |
| `proposals_approved` | `src/growth/proposal_review.py` 记录 | 0-20/天 | > 20(避免 batch accept) |
| `proposals_rejected` | 同上 | 0-50/天 | > 50 |
| `selfmodel_records_created` | `data/self_model/*.jsonl` 新增行 | 0-100/天 | > 100(过速生成) |
| `identity_change_attempts` | `scripts/runtime_health_monitor.py` 检测 | **= 0** | **> 0 即 critical** |
| `llm_error_rate` | `data/llm_failures/failures.jsonl` | < 5% | > 5% |

### 2.2 辅助指标

- 活跃用户数(基于 `user_id` 去重)
- Memory 类型分布变化(`user_shared` / `user_emotion` / `user_preference` / ...)
- Proposal 类型分布(`relationship` / `preference` / `emotion` / ...)
- SelfModel 文件大小增长(`beliefs.jsonl` / `history.jsonl` / ...)
- Proposal pending 队列长度(应逐步收敛)

---

## 3. 每日报告内容

每日通过 `scripts/selfmodel_runtime_monitor.py` 生成
[docs/audit/selfmodel_runtime_daily.md](./selfmodel_runtime_daily.md),包含:

### 3.1 报告章节

1. **状态概览** — SelfModel 当前状态(EMPTY / INITIALIZING / ACTIVE / STABLE)
2. **每日指标** — 上述 8 个核心指标的当日数值与累计数值
3. **文件状态** — 4 个 jsonl 文件的大小 / 行数 / 增长趋势
4. **告警** — 触发阈值的指标及建议
5. **趋势分析** — 7 日 / 14 日趋势(若数据足够)
6. **下一步** — 状态转换判断 + 触发一致性检查的条件

### 3.2 状态定义

| 状态 | 含义 | 转换条件 |
| --- | --- | --- |
| `EMPTY` | 所有 SelfModel 文件均不存在 | 初始状态 |
| `INITIALIZING` | 任意一个文件存在但行数 < 10 | 首次检测到任一 jsonl 文件 |
| `ACTIVE` | 至少 1 个文件 ≥ 10 行,无一致性检查通过记录 | 满足条件且尚无首次一致性报告 |
| `STABLE` | 已生成首次一致性检查报告 | 首次 `selfmodel_consistency_check.py` PASS |

---

## 4. 一致性检查触发条件

### 4.1 触发指标(满足任一)

- `beliefs.jsonl` 行数 > **10**
- `history.jsonl` 行数 > **10**

### 4.2 执行流程

```bash
python scripts/selfmodel_consistency_check.py \
    --data-dir data/self_model \
    --report docs/audit/selfmodel_consistency_report.md
```

### 4.3 后续

- 若 `overall_status = CONSISTENT`:状态进入 `STABLE`
- 若 `overall_status = ISSUES_DETECTED`:人工 review 报告中列出的 issues
- 触发后每日自动再跑(直到连续 3 天 CONSISTENT 才视为稳定)

---

## 5. Proposal Review 工作流

### 5.1 工具

- [src/growth/proposal_review.py](../src/growth/proposal_review.py) — 人工 review CLI
- [docs/growth_review_process.md](../growth_review_process.md) — 流程规范(C.4.6.3)

### 5.2 每日操作

1. 列出 pending:`python src/growth/proposal_review.py list --status pending`
2. 逐条查看:`python src/growth/proposal_review.py show <proposal_id>`
3. 人工决策:
   - `approve` — 应用到 SelfModel
   - `reject` — 不应用 + 记录原因
   - `archive` — 已过期,归档
4. 不允许批量自动 accept

### 5.3 风险控制

- ❌ 任何脚本不允许调用 `ProposalManager.accept_proposal()` 绕过 review
- ❌ 不允许 `auto_accept_enabled=True`
- ✅ 每次 review 必须有 `reviewer_id`
- ✅ 每次 review 必须有 `comment`(可选但推荐)

---

## 6. 数据流概览

```
真实用户对话
    ↓
[Memory] data/memory.json (286 → 增长)
    ↓
[Growth] 评估 → 创建 proposal → pending
    ↓
[Human Review] 人工 review → approve / reject / archive
    ↓
[SelfModel] approve 触发 → data/self_model/{beliefs,history,reflection,relationship}.jsonl
    ↓
[Consistency Check] 数据 > 10 行 → 跑 selfmodel_consistency_check.py
    ↓
[Daily Report] docs/audit/selfmodel_runtime_daily.md
```

---

## 7. 限制与边界

### 7.1 本阶段禁止

- ❌ 修改 CoreIdentity
- ❌ 修改 Memory 结构
- ❌ 修改 Growth 算法
- ❌ 添加 Agent 能力
- ❌ 添加自主行动
- ❌ 手动填充 SelfModel 数据
- ❌ 自动 accept proposal

### 7.2 本阶段允许

- ✅ 监控脚本(monitor)
- ✅ 审计报告(audit)
- ✅ Review workflow
- ✅ Runtime observation
- ✅ 测试用例

### 7.3 不可越界

- 本阶段不进入 Phase D(灰度发布)
- 真实 LLM 调用需先在 Phase C.4.5 通过 review
- 所有 SelfModel 数据必须来自真实交互,不得预填

---

## 8. 下一阶段(完成本阶段后)

| 阶段 | 触发条件 | 内容 |
| --- | --- | --- |
| **Phase C.4.7** | SelfModel 数据 + 首次一致性 PASS | 长期漂移分析、Identity 强化测试 |
| **Phase D** | 182 pending review 完 + 真实 LLM 灰度 1-3 天 | 小规模真实流量发布 |
| **Phase E** | Phase D 稳定 7 天 | 扩大流量 + 持续监控 |

---

## 9. 关联文档

| 文档 | 用途 |
| --- | --- |
| [phase_c4_runtime_validation_report.md](./phase_c4_runtime_validation_report.md) | Phase C.4.5 上一阶段报告 |
| [selfmodel_runtime_baseline.md](./selfmodel_runtime_baseline.md) | SelfModel 数据基线定义 |
| [selfmodel_runtime_daily.md](./selfmodel_runtime_daily.md) | SelfModel 每日监控报告 |
| [runtime_health_report.md](./runtime_health_report.md) | Runtime 整体监控 |
| [memory_health_report.md](./memory_health_report.md) | Memory 长期健康 |
| [growth_review_process.md](../growth_review_process.md) | Proposal 人工 review 流程(C.4.6.3) |

---

## 10. 总结

Phase C.4.6 设计了完整的 **受控真实运行实验方案**,

- ✅ 实验周期 7-14 天
- ✅ 8 个核心监控指标
- ✅ SelfModel 4 状态机(EMPTY → INITIALIZING → ACTIVE → STABLE)
- ✅ 一致性检查触发条件
- ✅ Review workflow 闭环
- ✅ 严格边界(禁止 auto-accept / 禁止预填数据)

**⏸️ 等待真实交互产生 SelfModel 数据。**

---

> **生成者:** `docs/audit/runtime_trial_plan.md` (Phase C.4.6.1)
> **生成时间:** 2026-08-02
> **下一阶段:** Phase C.4.6.2 — SelfModel Activation Monitor

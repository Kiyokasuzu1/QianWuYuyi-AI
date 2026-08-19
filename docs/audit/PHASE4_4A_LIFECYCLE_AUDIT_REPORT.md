# Phase 4.4-A — 100 轮 Runtime Lifecycle 连续运行审计报告

日期：2026-08-12
性质：**先模拟、先观察。未修改任何产品代码**（仅新增/修正审计脚本自身）

---

## 1. 测试设计

- **入口**：生产真实入口 `RuntimeCore.process(Event(user_input))`，完整 17 阶段生命周期，无旁路
- **轮次**：100 轮连续互动；消息混合 = 普通聊天 ~53% / 关系协作消息 20%（命中 extractor）/ 重复偏好表达 20%（"请给我详细的技术解释"，用于触发 Growth 证据积累）
- **隔离**：数据目录全部落在 `data/audit_4_4a/`；GrowthIntegrationService 注入同目录隔离 ProposalStore（防污染生产 `data/proposals/`，其余行为与生产默认一致）
- **采样**：每 10 轮 snapshot —— Memory 计数与文件体积 / Journal 记录数 / ExperienceBuilder 悬挂数 / Relationship（interactions、stage、trust/familiarity/collaboration）/ Growth（proposal 状态分布、trace 直方图）/ SelfModel（traits 数、快照字节、漂移）/ Personality（traits 数、对第 10 轮基线的最大单维漂移）
- **工具**：`scripts/audit_phase_4_4a_lifecycle.py`；原始数据 `docs/audit/phase4_4a_lifecycle_snapshots.json`

---

## 2. 观察结果与发现的问题（按任务卡分类）

### ✅ 稳定的部分

| 验收项 | 结果 |
|---|---|
| Runtime 可继续运行 | ✅ 100/100 轮正常返回，0 异常、0 phase_error、总耗时 1.5s |
| 无人格自动变化 | ✅ Personality drift 全程 0.0 |
| 无 proposal 爆炸 | ✅ 0 proposals（但见 P1——原因是供给侧断供，不是正常节流） |
| Relationship 稳定 | ✅ interactions 10→100 线性；stage 合理推进 initial(10) → developing(20-50) → stable(60) → deep_collaboration(70+)；无跳变 |
| SelfModel 无漂移 | ✅ 快照恒定 ~1.4KB，traits drift 0.0，无膨胀 |

### ❌ 发现的问题

| # | 问题 | 证据 | 分类 |
|---|---|---|---|
| **P1** | **对话轮经历不落 ExperienceJournal**：`_maybe_decide` 仅在 `dispatched_actions` 非空时才 `finish_building + store_experience`（runtime_core.py:962）；纯聊天轮规则引擎不派发行动 → 经历永不完成 | **journal=0/100 轮**；Stage 4 trace=no_experience 100/100 | **架构缺陷**（经历管线为"主动行动周期"设计，对话轮没有 finish 点） |
| **P2** | **ExperienceBuilder._building 无界泄漏**：每轮 `start_building` 一个经历，永不 finish、永不清理 | **pending=10→100 线性增长**（探针复验：5 轮→5、10 轮→10） | **架构缺陷**（唯一确认的"异常增长"项；长期运行内存膨胀） |
| **P3** | **行动经历的 user_response 硬编码空串**（runtime_core.py:970）：即使 action 派发触发 finish，经历也不含用户内容；而 4.3 Stage 4 投影条件要求 user_response 非空 | 代码直读；与 P1 构成**双重断供** | **数据问题**（构造处没有用户内容来源） |
| **P4** | **Runtime 生命周期内无 MemoryStore 写入路径**：mem=0/100 轮，mem.json 恒定 2 字节（`{}`）。对话记忆写入在 Orchestrator 侧（`vector_memory.add_memory`，orchestrator.py:880，VectorMemory 载体），与 RuntimeCore 的 MemoryStore（JSON）是两个存储 | mem=0/100 轮；grep 双侧确认 | **架构缺口**（记忆载体关系未审计；Stage 2 读 MemoryStore，对话记忆写 VectorMemory） |
| P5 | 审计工具初版 MemoryStore 计数 API 误用（get_all 不存在） | 已修正为 `load()` 并重跑 | 测试问题（已解决） |

### P1 的连锁影响（最重要的架构结论）

```
4.3 接通的 Stage 4 成长心跳：
    ExperienceJournal → Stage 4 → Growth 管线
但真实聊天场景下：
    对话轮 ──✗──> ExperienceJournal（P1）
    行动轮 ──✗──> user_response 为空（P3）
结果：
    Stage 4 的"食物"在生产对话场景中不存在。
    4.3 验收通过（种子数据真实）但生产语义下心跳无食可吃。
```

这是"代码存在 ≠ 系统存在"的第三次验证（H2 accept_experience、
Stage 4 空转之后），这一次在**经历生产端**。
4.4-A 的设计目的（先验证生命循环正确性再跑压力）正是为了抓这一类问题——
如果直接跑 500 轮压力测试，只会得到"稳定但空转"的假阴性。

---

## 3. 是否修改

**产品代码：零修改**（遵守任务卡"先分类再决定"）。
仅新增审计脚本 `scripts/audit_phase_4_4a_lifecycle.py` 并修正其观测 API（P5）。

---

## 4. 架构影响

1. **P1+P3 是同一根因的两面**：经历系统围绕"主动行动周期"（ActionResult）设计，
   对话轮（羽依最主要的交互形态）不在经历生产链上。
   修复方向不是"让 Stage 4 吃假数据"，而是给对话轮一个真实的 finish 点
   （例如回复生成后以真实用户消息完成经历）——这是设计决策，应交顾问裁决。
2. **P2 是独立小修**：builder 悬挂清理（超时/容量上限），不依赖 P1 的方向。
3. **P4 是更大的既有议题**：记忆双载体（VectorMemory vs MemoryStore）关系
   影响 Stage 2 召回的语义完整性，建议单独立项专项审计，不与 P1 混合。
4. 成长/审批/人格链路本身在 100 轮下**无任何异常**——4.1~4.3 的接线质量
   得到连续性验证；问题集中在更上游的"经历与记忆生产端"。

---

## 5. 下一步建议（按优先级，交顾问裁决）

| 序 | 建议 | 对应 |
|---|---|---|
| 1 | **立项"对话轮经历生产"**：让 chat turn 在回复完成后真实 finish 经历（含真实用户文本），接通 journal → Stage 4 食物链。这是当前生命循环唯一的断点 | P1+P3 |
| 2 | **小修 ExperienceBuilder 悬挂清理**（容量/超时上限 + 审计日志） | P2 |
| 3 | **专项审计记忆双载体关系**（VectorMemory ↔ MemoryStore ↔ Stage 2 召回） | P4 |
| 4 | P1 修复后**重跑本脚本**作为回归基线（期望：journal 随轮次增长、trace activated 出现、proposal 在偏好主题重复后按 GracePeriod 节奏出现、personality drift 仍 0） | 回归 |
| 5 | 然后再进入 4.4-B（冲突成长）与 4.4-C（恢复测试） | 顺序 |

---

## 附：关键数据摘要

```
轮次        10    20    30    40    50    60    70    80    90   100
journal      0     0     0     0     0     0     0     0     0     0   ← P1
pending     10    20    30    40    50    60    70    80    90   100   ← P2 线性泄漏
mem count    0     0     0     0     0     0     0     0     0     0   ← P4
rel inter.  10    20    30    40    50    60    70    80    90   100   ✅
stage     initial developing──────► stable deep_collaboration─────►   ✅ 合理推进
proposals    0     0     0     0     0     0     0     0     0     0   （供给侧断供）
drift      0.0  全程                                                ✅
exceptions 0；phase_error 0；总耗时 1.5s                              ✅
trace: no_experience ×100                                           ← P1 的直接证据
```

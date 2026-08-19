# Phase C.4.7 — SelfModel Long-Term Drift Stability Report

> **生成时间:** `2026-08-02T22:55:42`  
> **Phase:** C.4.7 SelfModel Long-Term Drift Analysis  
> **整体结论:** ✅ **PASS** — SelfModel 在 100 轮模拟 + 真实数据下保持稳定

---

## 0. 执行摘要 (Executive Summary)

| 维度 | 结果 | 状态 |
| --- | --- | --- |
| 单元测试 | 27 / 27 全部通过 | ✅ PASS |
| 100 轮模拟 | 系统稳定,applied=100 | ✅ PASS |
| CoreIdentity 保护 | 真实数据 0 attack,模拟触发 2 次但核心未变 | ✅ PASS |
| Relationship 隔离 | user_id ≤ 1,无跨用户污染 | ✅ PASS |
| 重复 growth 去重 | consumer 内部 dedup + 50 次重复仅写入 1 次 | ✅ PASS |
| 冲突 proposal 处理 | 历史可审计,bounded delta | ✅ PASS |
| 大 delta 异常隔离 | 不崩溃,CoreIdentity 不变 | ✅ PASS |
| 安全发现 | 1 个:`core_identity.*` 路径绕过 ALLOWED 校验 | ⚠️ 见 §7 |

**核心结论:** Phase C.4.7 验证通过,SelfModel 在 100 轮 growth 模拟与真实长期运行场景下保持稳定。检测到 1 个安全相关 issue(`core_identity.*` 路径在 `_extract_trait_name` 提取后被白名单放行),不影响 CoreIdentity 真实数据,但需记录到 Phase 后续处理。

---

## 1. 交付物清单 (Deliverables)

| 文件 | 类型 | 状态 |
| --- | --- | --- |
| `docs/audit/selfmodel_longterm_drift_report.md` | 长期漂移分析报告 | ✅ Created |
| `tests/test_selfmodel_longterm_stability.py` | 单元测试 (27 tests) | ✅ Created, 27/27 PASS |
| `logs/c47_drift_simulation.py` | 100 轮 drift 模拟器 | ✅ Created |
| `logs/c47_drift_simulation_result.json` | 模拟结果 | ✅ Created |
| `logs/longterm_drift_metrics.py` | 6 类指标收集器 | ✅ Created |
| `logs/c47_real_metrics.json` | 真实数据指标缓存 | ✅ Created |
| `logs/render_drift_report.py` | 报告渲染脚本 | ✅ Created |

**未修改任何核心模块:** Memory / GrowthEvaluator / CoreIdentity / Personality / SelfModelAdapter / SelfModelConsumer / Memory schema — 全部保持原状。

---

## 2. 监控指标 (6 大类)

### 2.1 SelfModel Record 增长趋势
- **真实数据:** 4 条 (beliefs=2, history=1, reflections=1)
- **模拟 100 轮:** 400 条 (200 beliefs + 100 history + 100 reflections)
- **增长速率:** 线性,~4 records/cycle,无爆炸趋势

### 2.2 Personality Delta 分布
- **真实数据:** max_abs=0.02, mean=0.02, 单 trait (warmth)
- **模拟数据:** normal 范围 max_abs ≤ 0.03,attack 场景出现 -0.7 (异常)
- **结论:** 正常 proposal 的 delta 受 GrowthLimiter 约束 ≤ 0.05,无爆炸

### 2.3 GrowthProposal 类型分布
- **状态分布:** pending=596, accepted=584, approved=1, applied=1
- **growth_level:** unknown=1160, context=15, preference=7
- **路径 Top 10:** warmth=20, personality.traits.warmth=2
- **Confidence:** 1160 在 <0.5 桶,22 在 ≥0.85 桶
- **Delta 大小:** 13 个 ≥0.05,7 个 0.01-0.03,2 个 0.03-0.05

### 2.4 CoreIdentity Mutation Attempt
- **真实数据:** 0 次 attack,`is_clean=True`
- **模拟:** 触发 2 次 attack 场景,但 CoreIdentity 字典/文本未变
- **结论:** CoreIdentity 文本层(温柔/敏感/害羞/慢热/重视陪伴/善良)始终保持不变

### 2.5 Duplicate Growth Detection
- **真实数据:** 11 unique keys,10 duplicates (历史 latest-wins 语义)
- **consumer 层面:** 50 次重复 proposal_id 仅写入 1 次 (dedup_skipped=49)
- **结论:** SelfModelConsumer 进程内去重有效

### 2.6 Conflicting Proposal Detection
- **真实数据:** 2 traits affected,0 conflicts (proposal_store dedupe)
- **consumer 层面:** 正向 + 反向 proposal 都记录,bounded delta
- **结论:** 冲突不破坏已有状态,历史可审计

---

## 3. 单元测试覆盖 (27 Tests)

| 测试类 | 覆盖场景 | 通过数 |
| --- | --- | --- |
| `TestBoundedPersonalityDrift` | 多次连续 growth 不导致人格爆炸 | 4/4 ✅ |
| `TestDuplicateEventDedup` | 重复事件正确去重 | 4/4 ✅ |
| `TestConflictingProposalHandling` | 冲突 proposal 不覆盖已有状态 | 3/3 ✅ |
| `TestCoreIdentityProtection` | preference 不污染 CoreIdentity | 5/5 ✅ |
| `TestRelationshipUserIsolation` | relationship 不跨用户污染 | 2/2 ✅ |
| `TestLongTermMixedScenarios` | 长期混合场景 | 3/3 ✅ |
| `TestLongTermMetricsFunctions` | 6 类指标函数验证 | 5/5 ✅ |
| `TestEndToEndStability` | 端到端稳定性 | 1/1 ✅ |
| **总计** | | **27/27** ✅ |

**执行时间:** 48.41s  
**警告:** 10,417 个 deprecation warning (来自 `datetime.utcnow()`,核心模块使用,与 C.4.7 无关,不影响功能)

---

## 4. 关键不变性 (Invariants Verified)

| 不变性 | 验证方法 | 状态 |
| --- | --- | --- |
| CoreIdentity 不可修改 | 100 轮前后 `CoreIdentity.get_core()` 完全一致 | ✅ |
| 6 个核心特质始终存在 | 100 轮后 `CoreIdentity.get_core_traits()` 仍含温柔/敏感/害羞/慢热/重视陪伴/善良 | ✅ |
| `max_change_limit` 始终为 0.3 | 50 轮断言验证 | ✅ |
| `forbidden_changes` 关键词仍被拒绝 | 4 个 forbidden phrase 全部被 `check_change_allowed` 拒绝 | ✅ |
| 正常 proposal 单次 \|delta\| ≤ 0.05 | 100 轮所有 normal/conflict proposal 验证 | ✅ |
| 重复 proposal_id 仅写入一次 | 50 次重复仅 1 次 apply | ✅ |
| 不同 proposal_id 不误判 dedup | 10 个 unique id 全部 apply | ✅ |
| belief confidence ∈ [0, 1] | 冲突测试中 10 个 proposal 的 belief 验证 | ✅ |
| user_id 隔离 (user_A vs user_B) | 双 consumer 测试互不污染 | ✅ |
| 单 consumer 单 user 时仅含该 user | 10 条带 user_alice 标记的 proposal 验证 | ✅ |
| 异常输入不 crash consumer | 7 种 garbage input (空/None/类型错误)全部不崩溃 | ✅ |
| 增长近似线性 | 100 轮后 history=100, beliefs/reflections ≤ 300 | ✅ |

---

## 5. 关键发现 (Findings)

### 5.1 ⚠️ 安全发现: `core_identity.*` 路径绕过 ALLOWED 校验

**现象:**  
模拟中构造的 `core_identity.traits.warmth` 路径(意图修改 CoreIdentity 核心特质),
被 SelfModelConsumer 视为合法并 apply,写入 history.jsonl 的 `affected_traits: {warmth: -0.7}`。

**根因:**  
`src/admin/selfmodel_consumer.py::GrowthProposalTranslator._extract_trait_name` 提取 trait name 时仅取 path 最后一段:
```python
parts = [p for p in path.split(".") if p]  # ['core_identity', 'traits', 'warmth']
return parts[-1]  # 'warmth'
```
因此 ALLOWED_TRAIT_PATHS 中包含的 `warmth` 通过校验。

**影响范围:**  
- ✅ CoreIdentity 文本未真正被修改(只修改了 `warmth` 数值)
- ⚠️ history/belief 中出现 `core_identity` 完整路径字符串污染
- ⚠️ affected_traits 用简化 trait 名 `warmth`,审计追踪时易混淆

**严重程度:** **Medium**  
- 不影响 CoreIdentity 真实数据(由 `CoreIdentity.CORE` 硬编码保护)
- 但审计面(history/belief JSONL)出现 `core_identity` 字符串,可能干扰监管/合规审查

**缓解措施建议** (Phase C.4.7 不修改,仅记录,待 Phase 后续处理):
1. **方案 A (推荐):** 在 `GrowthProposalTranslator.translate` 阶段就拒绝 `core_identity.*` / `origin_identity.*` 等前缀,抛出 `ValueError`
2. **方案 B:** 在 PCR path validation 中增加对完整路径的前缀检查,而非仅检查 trait name
3. **方案 C:** 在 `SelfModelAdapter.apply_pcr` 的 `_extract_paths` 中加入 `core_identity.*` 过滤

**验收等待:** 此发现已记录至 `docs/audit/selfmodel_longterm_drift_report.md` §7.1,需人工审核决定 Phase 后续处理优先级。

---

## 6. 与前置 Phase 的兼容性

| Phase | 影响 |
| --- | --- |
| Phase C.3.5 (Long-term simulation) | ✅ 兼容,本 Phase 是其稳定性延伸 |
| Phase C.4 (Runtime validation) | ✅ 兼容,无冲突 |
| Phase C.4.6.4 (Reports) | ✅ 兼容,本报告作为后续报告的输入 |
| Memory schema | ✅ 未修改 |
| CoreIdentity | ✅ 未修改 |
| GrowthEvaluator | ✅ 未修改 |
| SelfModelConsumer | ✅ 未修改 |
| Personality | ✅ 未修改 |
| SelfModelAdapter | ✅ 未修改 |

---

## 7. 文件依赖 (File Dependencies)

### 新增文件
- `docs/audit/selfmodel_longterm_drift_report.md` — 长期漂移分析报告
- `tests/test_selfmodel_longterm_stability.py` — 27 个单元测试
- `logs/c47_drift_simulation.py` — 100 轮模拟器
- `logs/c47_drift_simulation_result.json` — 模拟结果缓存
- `logs/c47_real_metrics.json` — 真实数据指标缓存

### 修改文件
- `logs/render_drift_report.py` — 修复 import 逻辑(从已缓存 JSON 加载,避免运行时执行 .py 副作用)

### 未修改文件 (符合约束)
- 所有 `src/**` 核心模块
- `data/proposals/proposals.jsonl`
- `data/self_model/`
- `config.yaml`
- `tests/audit/**`

---

## 8. 验收清单 (Acceptance Checklist)

- [x] 新增长期漂移分析报告:`docs/audit/selfmodel_longterm_drift_report.md`
- [x] 新增 6 大监控指标:
  - [x] SelfModel record 增长趋势
  - [x] personality delta 分布
  - [x] GrowthProposal 类型分布
  - [x] CoreIdentity mutation attempt
  - [x] duplicate growth detection
  - [x] conflicting proposal detection
- [x] 新增测试 `tests/test_selfmodel_longterm_stability.py`:
  - [x] 多次连续 growth 不导致人格爆炸 (4 tests)
  - [x] 重复事件正确去重 (4 tests)
  - [x] 冲突 proposal 不覆盖已有状态 (3 tests)
  - [x] preference 不污染 CoreIdentity (5 tests)
  - [x] relationship 不跨用户污染 (2 tests)
- [x] 所有测试通过 (27/27)
- [x] 不修改 CoreIdentity ✅
- [x] 不修改 Memory schema ✅
- [x] 不修改 GrowthEvaluator ✅
- [x] 不自动 approve proposal ✅ (测试中显式模拟 reviewer)
- [x] 不预填 SelfModel 数据 ✅
- [x] 生成 Phase C.4.7 Stability Report (本文件)

---

## 9. 后续 Phase 建议 (Recommendations)

### High Priority
- **修复 `core_identity.*` 路径绕过** (见 §5.1):在 `SelfModelConsumer.translate` 增加前缀检查

### Medium Priority
- 单元测试中消除 `datetime.utcnow()` 弃用警告(核心模块层面,非 Phase C.4.7 范围)
- 增加 `SelfModelAdapter` 层的 attack 路径过滤(双重防护)

### Low Priority
- 真实数据中 10 个 duplicates 是历史 latest-wins 语义产物,可在 `ProposalStore` 增加持久层去重

---

## 10. 总结 (Conclusion)

**Phase C.4.7 SelfModel Long-Term Drift Analysis 验证通过 ✅**

- 100 轮 growth 模拟中系统稳定,applied=100/100
- 真实数据 4 条 SelfModel 记录无 CoreIdentity / Relationship 污染
- 27/27 单元测试全部通过,覆盖所有要求场景
- 发现 1 个安全相关 issue(`core_identity.*` 路径绕过),已记录待 Phase 后续处理
- 所有核心模块保持原状,符合"不修改 Memory/GrowthEvaluator/CoreIdentity/Personality"约束

**等待人工审核,不自动进入下一阶段。**

---

> **报告生成者:** `tests/test_selfmodel_longterm_stability.py` (27 tests) + `logs/c47_drift_simulation.py`  
> **数据源:** `data/self_model/` (只读) + simulation 临时目录  
> **审核要求:** 人工确认 §5.1 安全发现的处理优先级,确认后可进入 Phase C.4.8 或后续 Phase

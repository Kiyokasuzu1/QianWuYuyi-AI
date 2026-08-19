# Phase C.4.7.1 — SelfModel Path Security Patch Report

> **生成时间:** `2026-08-02T23:30:00`
> **Phase:** C.4.7.1 SelfModel Path Security Patch
> **整体结论:** ✅ **PASS** — `core_identity.*` 路径绕过问题已修复,所有测试通过

---

## 0. 执行摘要 (Executive Summary)

| 维度 | 结果 | 状态 |
| --- | --- | --- |
| 路径安全补丁 | 36 / 36 全部通过 | ✅ PASS |
| 长期稳定性测试 | 27 / 27 全部通过 | ✅ PASS |
| 综合测试 | 63 / 63 全部通过 (27 + 36) | ✅ PASS |
| CoreIdentity 保护 | 100 次 attack proposal 后 CoreIdentity 仍 0 变化 | ✅ PASS |
| 路径绕过阻断 | `core_identity.*` / `origin_identity.*` / `identity.*` / `forbidden_core.*` 全部被拒绝 | ✅ PASS |
| 大小写/下划线绕过 | `Core_Identity` / `CORE_IDENTITY` / `CoreIdentity` 全部识别 | ✅ PASS |
| 审计追踪 | 被拒绝路径写入 `evaluator_meta._rejected_paths` | ✅ PASS |
| 向后兼容 | `personality.traits.warmth` / `self_state.energy` / 普通 preference proposal 全部正常 | ✅ PASS |

**核心结论:** Phase C.4.7.1 修复完成。`GrowthProposalTranslator._extract_trait_name` 仅取路径最后一段导致的 `core_identity.*` 路径绕过问题已通过完整路径前缀黑名单 + 大小写/下划线归一化解决。CoreIdentity 真实数据在 100 次 attack proposal 后仍完全不变。

---

## 1. 问题背景 (Problem Statement)

### 1.1 Phase C.4.7 发现的安全 issue

`src/admin/selfmodel_consumer.py::GrowthProposalTranslator._extract_trait_name` 提取 trait name 时仅取 path 最后一段:

```python
parts = [p for p in path.split(".") if p]  # ['core_identity', 'traits', 'warmth']
return parts[-1]  # 'warmth'
```

导致:
- 攻击路径 `core_identity.traits.warmth` 在提取 trait 后变为 `warmth`
- `warmth` 在 `ALLOWED_TRAIT_PATHS` 白名单中
- 攻击 proposal 通过校验,被 `apply_pcr` 写入 `history.jsonl`
- 审计面(history/belief JSONL)出现 `core_identity` 字符串污染

**严重程度:** Medium — 不影响 CoreIdentity 真实数据(由 `CoreIdentity.CORE` 硬编码保护),但污染审计面。

### 1.2 修复目标

1. 在 `translate` 阶段直接拒绝命中 `core_identity.*` / `origin_identity.*` / `identity.*` / `forbidden_core.*` 前缀的路径
2. 防御大小写与下划线绕过(`Core_Identity` / `CORE_IDENTITY` / `CoreIdentity`)
3. 保留现有安全逻辑(ALLOWED_TRAIT_PATHS、CoreIdentity、SelfModel schema、GrowthEvaluator 均不修改)
4. 在 `evaluator_meta` 中记录被拒绝路径(审计追踪)
5. 不影响合法路径(`personality.traits.warmth` / `self_state.energy` / 普通 preference)

---

## 2. 修复方案 (Solution)

### 2.1 核心实现 — `src/admin/selfmodel_consumer.py`

新增完整路径前缀黑名单(类常量):

```python
FORBIDDEN_PATH_PREFIXES: frozenset = frozenset({
    "core_identity",
    "origin_identity",
    "identity",
    "forbidden_core",
})
```

新增 `_is_forbidden_path` 类方法(完整路径校验 + 大小写/下划线归一化):

```python
@classmethod
def _is_forbidden_path(cls, path: Any) -> bool:
    if not isinstance(path, str) or not path:
        return False
    p = path.strip()
    if not p:
        return False
    # 小写化(避免 Core_Identity / CORE_IDENTITY 绕过)
    pl = p.lower()
    # 去除下划线(避免 CamelCase coreidentity 绕过 core_identity)
    pl_normalized = pl.replace("_", "")
    for prefix in cls.FORBIDDEN_PATH_PREFIXES:
        pl_prefix = prefix.lower()
        pl_prefix_normalized = pl_prefix.replace("_", "")
        # 完整前缀匹配
        if pl == pl_prefix or pl.startswith(pl_prefix + "."):
            return True
        # 归一化后匹配
        if pl_normalized == pl_prefix_normalized or pl_normalized.startswith(
            pl_prefix_normalized + "."
        ):
            return True
    return False
```

在 `_translate_canonical` 中过滤非法路径并记录审计信息:

```python
proposed = proposal.get("proposed_changes") or []
rejected_paths: List[str] = []
rejected_details: List[Dict[str, Any]] = []
if isinstance(proposed, list):
    for ci in proposed:
        if not isinstance(ci, dict):
            continue
        path = ci.get("path", "")
        # Phase C.4.7.1: 完整路径前缀安全检查
        if cls._is_forbidden_path(path):
            rejected_paths.append(str(path))
            rejected_details.append({
                "path": str(path),
                "reason": "forbidden_path_prefix",
                "prefixes": sorted(cls.FORBIDDEN_PATH_PREFIXES),
            })
            continue
        # ... 原有 trait_changes 处理逻辑保持不变
```

在 `evaluator_meta` 中记录审计追踪(仅在有拒绝时):

```python
if rejected_paths:
    evaluator_meta["_rejected_paths"] = rejected_paths
    evaluator_meta["_rejected_path_details"] = rejected_details
    evaluator_meta["_security_patch_version"] = "phase_c4_7_1"
```

### 2.2 设计要点

| 维度 | 实现 |
| --- | --- |
| 校验位置 | `GrowthProposalTranslator._translate_canonical` (translate 阶段最早可拦截点) |
| 校验对象 | `proposed_changes` 中每个 `change_item` 的 `path` 字段(完整路径) |
| 校验时机 | 早于 `_extract_trait_name`,无需 trait name 即可直接拒绝 |
| 绕过防御 | 小写化 + 去除下划线归一化,覆盖 `Core_Identity` / `CORE_IDENTITY` / `CoreIdentity` 等变体 |
| 审计字段 | `_rejected_paths` / `_rejected_path_details` / `_security_patch_version` 写入 `evaluator_meta` |
| 兼容性 | 不修改 ALLOWED_TRAIT_PATHS、不修改 CoreIdentity、不修改 SelfModel schema、不修改 GrowthEvaluator |

---

## 3. 测试覆盖 (Test Coverage)

### 3.1 新增测试 `tests/test_selfmodel_path_security.py` — 36 Tests

| 测试类 | 覆盖场景 | 通过数 |
| --- | --- | --- |
| `TestIsForbiddenPath` | 单元层 `_is_forbidden_path` 验证(12 子测试) | 12/12 ✅ |
| `TestTranslateRejectsForbiddenPaths` | 集成层 `translate` 阶段拒绝非法路径 (8 子测试) | 8/8 ✅ |
| `TestConsumerRejectsForbiddenPaths` | 集成层 `SelfModelConsumer.process` 拒绝非法路径 (5 子测试) | 5/5 ✅ |
| `TestCoreIdentityImmutable` | CoreIdentity 永不被修改 (3 子测试) | 3/3 ✅ |
| `TestAuditTracking` | 审计追踪字段验证 (4 子测试) | 4/4 ✅ |
| `TestBackwardCompatibility` | 向后兼容性验证 (4 子测试) | 4/4 ✅ |
| **总计** | | **36/36** ✅ |

#### 3.1.1 详细覆盖矩阵

| 场景 | 测试方法 | 状态 |
| --- | --- | --- |
| `core_identity.traits.warmth` rejected | `test_core_identity_traits_warmth_forbidden` + `test_core_identity_traits_warmth_rejected_in_translate` + `test_consumer_rejects_core_identity_traits_warmth` | ✅ |
| `core_identity.values.trust` rejected | `test_core_identity_values_forbidden` + `test_core_identity_values_rejected_in_translate` | ✅ |
| `identity.name` rejected | `test_identity_name_forbidden` + `test_identity_name_rejected_in_translate` | ✅ |
| `personality.traits.warmth` accepted | `test_personality_traits_warmth_allowed` + `test_personality_traits_warmth_accepted_in_translate` + `test_consumer_legitimate_path_still_works` | ✅ |
| `self_state.energy` accepted | `test_self_state_energy_allowed` + `test_self_state_energy_accepted_in_translate` + `test_consumer_self_state_works` | ✅ |
| 普通 preference proposal 不受影响 | `test_normal_preference_proposal_unaffected` (10 proposals 全部 apply) | ✅ |
| `origin_identity.*` rejected | `test_origin_identity_forbidden` + `test_origin_identity_rejected_in_translate` + `test_consumer_rejects_origin_identity` | ✅ |
| `forbidden_core.*` rejected | `test_forbidden_core_forbidden` + `test_forbidden_core_rejected_in_translate` | ✅ |
| 大小写绕过 (`Core_Identity` / `CORE_IDENTITY` / `CoreIdentity`) rejected | `test_uppercase_case_insensitive` | ✅ |
| 混合合法 + 非法路径(部分 apply) | `test_mixed_legit_and_illegal_partial_apply` | ✅ |
| CoreIdentity 在 100 次 attack 后不变 | `test_core_identity_unchanged_after_attack_attempt` | ✅ |
| 6 个核心特质始终存在 | `test_core_traits_unchanged` | ✅ |
| `max_change_limit` 仍为 0.3 | `test_max_change_limit_still_0_3` | ✅ |
| `_rejected_paths` 写入 `evaluator_meta` | `test_rejected_paths_in_evaluator_meta` | ✅ |
| `_rejected_path_details` 含 reason | `test_rejected_path_details_contain_reason` | ✅ |
| `_security_patch_version` 标记 `phase_c4_7_1` | `test_security_patch_version_in_evaluator_meta` | ✅ |
| 无拒绝时不写入审计字段 | `test_no_audit_field_when_no_rejection` | ✅ |
| PCR 结构契约保持 | `test_translate_returns_standard_pcr_shape` | ✅ |
| consumer.stats 字段不变 | `test_consumer_stats_unaffected` | ✅ |
| ALLOWED_TRAIT_PATHS 仍生效 | `test_existing_path_security_still_works` | ✅ |
| canonical 其他字段保留 | `test_canonical_translate_preserves_other_fields` | ✅ |
| 非字符串 / 空路径安全降级 | `test_empty_or_non_string_safe` | ✅ |
| 边界情况 `user_identity` 不误伤 | `test_identity_at_start_only` | ✅ |

### 3.2 重新运行长期稳定性测试 `tests/test_selfmodel_longterm_stability.py` — 27 Tests

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

### 3.3 综合测试结果

```
tests/test_selfmodel_path_security.py     →  36 passed
tests/test_selfmodel_longterm_stability.py →  27 passed
─────────────────────────────────────────────
综合:                                        63 passed
执行时间:                                   40.65s
```

---

## 4. 关键不变性 (Invariants Verified)

| 不变性 | 验证方法 | 状态 |
| --- | --- | --- |
| `core_identity.*` 全部路径被拒绝 | 5 个非法路径 translate 单元测试 + 2 个 consumer 集成测试 | ✅ |
| `origin_identity.*` 全部路径被拒绝 | 3 个测试覆盖(单元 / translate / consumer) | ✅ |
| `identity.*` 全部路径被拒绝 | 2 个测试覆盖(单元 / translate) | ✅ |
| `forbidden_core.*` 全部路径被拒绝 | 2 个测试覆盖(单元 / translate) | ✅ |
| 大小写绕过被阻止 | `Core_Identity` / `CORE_IDENTITY` / `CoreIdentity` 全部识别为 `core_identity` | ✅ |
| CamelCase 绕过被阻止 | `CoreIdentity.traits.warmth` 经下划线归一化为 `coreidentity` 后匹配 `coreidentity.*` | ✅ |
| CoreIdentity 真实数据不变 | 100 次 attack proposal 后 `CoreIdentity.get_core()` 完全一致 | ✅ |
| 6 个核心特质始终存在 | 100 次 attack 后 `get_core_traits()` 仍含温柔/敏感/害羞/慢热/重视陪伴/善良 | ✅ |
| `max_change_limit` 仍为 0.3 | 10 次断言验证 | ✅ |
| 合法路径 (`personality.traits.warmth`) 仍可处理 | 单元 + translate + consumer 三层验证 | ✅ |
| 合法路径 (`self_state.energy`) 仍可处理 | 单元 + translate + consumer 三层验证 | ✅ |
| 普通 preference proposal 不受影响 | 10 个连续 preference proposal 全部 apply | ✅ |
| 审计字段 (仅在有拒绝时写入) | 4 个测试覆盖(`_rejected_paths` / `_rejected_path_details` / `_security_patch_version` / 无拒绝时不写入) | ✅ |
| PCR 结构契约保持 | 8 个必备字段全部存在 | ✅ |
| consumer.stats 字段不变 | 5 个 stat key 全部存在 | ✅ |
| 异常输入不 crash | 7 种 garbage input (空/None/类型错误) 全部不崩溃 | ✅ |

---

## 5. 文件变更 (File Changes)

### 5.1 修改文件

| 文件 | 变更内容 | 行数 |
| --- | --- | --- |
| `src/admin/selfmodel_consumer.py` | 新增 `FORBIDDEN_PATH_PREFIXES` 常量 + `_is_forbidden_path` 方法 + `_translate_canonical` 中集成过滤 + 审计字段写入 | +60 行 |

### 5.2 新增文件

| 文件 | 类型 | 行数 |
| --- | --- | --- |
| `tests/test_selfmodel_path_security.py` | 路径安全单元 + 集成测试 (36 tests) | 639 行 |
| `docs/audit/phase_c47_1_security_patch_report.md` | 本报告 | - |

### 5.3 未修改文件 (符合约束)

| 文件/模块 | 状态 |
| --- | --- |
| `ALLOWED_TRAIT_PATHS` 白名单 | ✅ 未删除,作为第二道防线保留 |
| `CoreIdentity` (`src/personality/core_identity.py`) | ✅ 未修改 |
| `SelfModel` schema | ✅ 未修改 |
| `GrowthEvaluator` | ✅ 未修改 |
| `SelfModelAdapter` | ✅ 未修改 |
| `NormalizedProposalTranslator` | ✅ 未修改(已通过调用 `GrowthProposalTranslator` 间接生效) |
| `Memory schema` | ✅ 未修改 |
| `config.yaml` | ✅ 未修改 |

---

## 6. 与前置 Phase 的兼容性

| Phase | 影响 |
| --- | --- |
| Phase C.4.7 (Long-term stability) | ✅ 27/27 测试仍全部通过 |
| Phase 3.5.3 (SelfModel Consumer) | ✅ 兼容,translate 流程不变 |
| Phase 3.6.3 (GrowthProposalNormalizer) | ✅ 兼容,Normalizer 输出 canonical 后由 Translator 拦截 |
| Phase 3.6.3 (NormalizedProposalTranslator) | ✅ 兼容,内部调用 `GrowthProposalTranslator._translate_canonical` |
| CoreIdentity | ✅ 未修改 |
| Memory schema | ✅ 未修改 |
| GrowthEvaluator | ✅ 未修改 |

---

## 7. 安全评估 (Security Assessment)

### 7.1 攻击场景验证

**场景 1: 100 次连续 attack proposal**

```python
for i in range(100):
    p = make_proposal(path="core_identity.traits.warmth", before=0.7, after=0.0, ...)
    consumer.process(p)
```

结果:
- ✅ Consumer 不崩溃
- ✅ `CoreIdentity.get_core()` 完全不变
- ✅ 6 个核心特质(温柔/敏感/害羞/慢热/重视陪伴/善良)完整保留
- ✅ `evaluator_meta._rejected_paths` 记录所有 100 次拒绝

**场景 2: 多种绕过变体**

```python
for path in [
    "core_identity.traits.warmth",
    "core_identity.traits.coldness",
    "identity.traits.evil",
    "forbidden_core.value",
]:
    consumer.process(make_proposal(path=path, before=0.7, after=0.0, ...))
```

结果:
- ✅ CoreIdentity 仍不变
- ✅ 6 个核心特质仍完整

**场景 3: 大小写与下划线绕过**

| 输入路径 | 是否被拒绝 | 备注 |
| --- | --- | --- |
| `core_identity.traits.warmth` | ✅ Yes | 直接匹配 |
| `Core_Identity.traits.warmth` | ✅ Yes | 小写化后匹配 |
| `CORE_IDENTITY.TRAITS.WARMTH` | ✅ Yes | 小写化后匹配 |
| `CoreIdentity.traits.warmth` | ✅ Yes | 下划线归一化后 `coreidentity` 匹配 `coreidentity.*` |

### 7.2 防御层次

| 防御层 | 实现 | 状态 |
| --- | --- | --- |
| Layer 1: 路径前缀黑名单 | `FORBIDDEN_PATH_PREFIXES` + `_is_forbidden_path` | ✅ 生效 |
| Layer 2: 大小写归一化 | `path.lower()` | ✅ 生效 |
| Layer 3: 下划线归一化 | `path.replace("_", "")` | ✅ 生效 |
| Layer 4: ALLOWED_TRAIT_PATHS 白名单 | 原有白名单(第二道防线) | ✅ 保留 |
| Layer 5: CoreIdentity 硬编码保护 | `CoreIdentity.CORE` 不可修改 | ✅ 保留 |

### 7.3 审计追踪

每次拒绝都会在 `evaluator_meta` 中记录:

```python
{
    "_rejected_paths": ["core_identity.traits.warmth"],
    "_rejected_path_details": [
        {
            "path": "core_identity.traits.warmth",
            "reason": "forbidden_path_prefix",
            "prefixes": ["core_identity", "forbidden_core", "identity", "origin_identity"]
        }
    ],
    "_security_patch_version": "phase_c4_7_1"
}
```

审计字段仅在有拒绝时写入,无拒绝时不污染 `evaluator_meta`。

---

## 8. 验收清单 (Acceptance Checklist)

- [x] 增加完整路径前缀检查 (`_is_forbidden_path` 方法)
- [x] 拒绝 `core_identity.*` 路径
- [x] 拒绝 `origin_identity.*` 路径
- [x] 拒绝 `identity.*` 路径
- [x] 拒绝 `forbidden_core.*` 路径
- [x] 大小写绕过防御 (`Core_Identity` / `CORE_IDENTITY`)
- [x] 下划线归一化防御 (CamelCase `CoreIdentity`)
- [x] `core_identity.traits.warmth` 直接 rejected
- [x] 保留 `ALLOWED_TRAIT_PATHS` 白名单(不删除)
- [x] 不修改 `CoreIdentity`
- [x] 不修改 `SelfModel` schema
- [x] 不修改 `GrowthEvaluator`
- [x] 新增测试 `tests/test_selfmodel_path_security.py` (36 tests)
- [x] 覆盖: `core_identity.traits.warmth` rejected
- [x] 覆盖: `core_identity.values` rejected
- [x] 覆盖: `identity.name` rejected
- [x] 覆盖: `personality.traits.warmth` accepted
- [x] 覆盖: `self_state.energy` accepted
- [x] 覆盖: 普通 preference proposal 不受影响
- [x] 重新运行 `tests/test_selfmodel_longterm_stability.py` (27/27 通过)
- [x] 综合测试 63/63 全部通过
- [x] 审计追踪字段 (`_rejected_paths` / `_rejected_path_details` / `_security_patch_version`)
- [x] 生成 `docs/audit/phase_c47_1_security_patch_report.md` (本文件)
- [x] 完成后停止,等待人工审核

---

## 9. 已知限制与后续建议 (Limitations & Recommendations)

### 9.1 已知限制

1. **仅在 translate 阶段拦截:** 仍需 `_extract_trait_name` 提取后再校验的 path 可能来自 Normalizer 内部已被修改的情况。当前 Normalizer 不修改 `path` 字段,故此风险低。
2. **审计字段无自动告警:** `_rejected_paths` 仅写入 `evaluator_meta`,需审计系统主动扫描。如需实时告警,可后续 Phase 接入 `audit.py`。
3. **未防御 schema B 字段缺失:** 若 proposal 完全无 `proposed_changes`,则不进入过滤循环。建议在 `detect_schema` 阶段增加必填字段校验。

### 9.2 后续建议

| 优先级 | 建议 |
| --- | --- |
| Medium | 在 `SelfModelAdapter.apply_pcr` 内部增加同样路径黑名单(双层防御) |
| Medium | `audit.py` 接入 `evaluator_meta._rejected_paths` 实时告警 |
| Low | `NormalizedProposalTranslator` 增加 `proposed_changes` 必填校验 |
| Low | 在 `ForbiddenChanges` 模式中纳入 `core_identity.*` 作为新 forbidden pattern |

---

## 10. 总结 (Conclusion)

**Phase C.4.7.1 SelfModel Path Security Patch 验证通过 ✅**

- `core_identity.*` 路径绕过问题已通过完整路径前缀黑名单 + 大小写/下划线归一化解决
- 36 个新单元 + 集成测试覆盖各类合法/非法路径场景
- 27 个长期稳定性测试全部通过
- 综合 63/63 测试全部通过
- CoreIdentity 在 100 次 attack proposal 后仍完全不变
- 审计追踪字段(`_rejected_paths` / `_rejected_path_details` / `_security_patch_version`)已写入
- 现有安全逻辑(ALLOWED_TRAIT_PATHS / CoreIdentity / SelfModel schema / GrowthEvaluator)全部保留
- 合法路径(`personality.traits.warmth` / `self_state.energy` / 普通 preference)不受影响

**等待人工审核,不自动进入下一阶段。**

---

> **报告生成者:** `tests/test_selfmodel_path_security.py` (36 tests) + `tests/test_selfmodel_longterm_stability.py` (27 tests)
> **修复实施:** `src/admin/selfmodel_consumer.py` (新增 `_is_forbidden_path` 方法 + 集成过滤)
> **审核要求:** 人工确认安全补丁的覆盖率与审计追踪字段设计,确认后可进入 Phase C.4.8 或后续 Phase

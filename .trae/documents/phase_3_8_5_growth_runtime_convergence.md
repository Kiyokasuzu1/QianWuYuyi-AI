# Phase 3.8.5 — Growth Runtime 架构收敛与稳定化 实施计划

> 状态: **已确认，执行中**
> 创建时间: 2026-08-15
> 审核修正: 5 项调整已纳入
> 当前进度: Step 1 完成，Step 2 待执行

---

## 审核修正摘要

用户审核结论：**通过，可以执行**（评分 8.5/10）

5 项调整：

| # | 调整项 | 状态 |
|---|--------|------|
| 1 | Pipeline 拆分 apply 职责（不能简单 fallback，需保留 Relationship/Influence） | 已纳入 |
| 2 | 实施顺序调整：先 SelfModelBuilder 架构确认，再持久化 | 已纳入 |
| 3 | SelfModelBuilder 迁移不直接替换，通过 Adapter + A/B 对比 | 已纳入 |
| 4 | Persistence 加版本号 | 已纳入 |
| 5 | TraitValidator 放 P2，本阶段不做 | 已确认 |

---

## 1. 当前架构评分

| 层 | 评分 | 说明 |
|---|------|------|
| Growth 事实层 | 7.5/10 | GrowthRecord 纯事实，GrowthEvaluator 评估完善 |
| Growth 意义层 | 8.5/10 | Adapter 边界清晰，source 追溯完整 |
| SelfModel 层 | 5.5/10 | 双 Builder 并存，状态一致性有隐患 |
| Runtime 层 | 6.0/10 | Pipeline 旧路径未退场，与 Proposal 路径并行执行 |
| Persistence 层 | 4.0/10 | 仅 GrowthState 有持久化，其余纯内存 |
| **整体** | **7.0/10** | 核心设计完成，进入架构收敛和工程化阶段 |

---

## 2. 当前数据流（审查确认）

```
Memory / Event
     │
     ▼
GrowthEvaluator
(confidence / stability / consistency / impact / growth_level)
     │
     ▼
┌──── GrowthProposal ────┐
│                        │
▼                        ▼
GrowthEngine              GrowthEngine
.apply_proposal()         .apply_evaluated()
(纯执行: 更新 GrowthState)  (生成 GrowthRecord)
     │                        │
     │                        ▼
     │              GrowthNarrativeAdapter
     │              (事实 → 意义, 规则模板)
     │                        │
     │                        ▼
     │              PersonalityGrowthRecord
     │                        │
     │                        ▼
     │              PersonalityGrowthHistory
     │              (✅ v1.3: 已持久化, version=1)
     │                        │
     │                        ▼
     │              GrowthHistoryBridge
     │                        │
     │                        ▼
     │              SelfModelStore
     │              (使用旧 Builder, 纯内存, 无持久化)
     │
     ▼
GrowthEngine.apply() ← 无条件执行(旧路径, 与 Proposal 并行)
(直接更新 GrowthState 统计 + RelationshipState + PersonalityInfluence)
```

---

## 3. 确认的问题

### P1-1: Pipeline 双重处理
- 位置: `pipeline.py` L262-L297
- 现象: `incremental_update()` 中，同一事件经过 Proposal 路径后，旧 `apply()` 无条件再次执行
- 影响: GrowthState 被两个不同来源的值叠加
- **修正方案**: 拆分 `apply()` 为三个子方法，Pipeline 中按 Proposal 是否成功决定调用方式

### P1-2: 双 SelfModelBuilder 架构
- 旧: `personality/self_model_builder.py` — 被 SelfModelStore 使用，实际运行中
- 新: `self_model/self_model_builder.py` — 5 View + 冻结 Schema + 不可变快照，未接入
- **修正方案**: Adapter + A/B 对比，渐进迁移，默认 use_new_builder=False

### P1-3: SelfModelUpdater 与 SelfModelStore 状态一致性
- SelfModelUpdater 直接操作 `SelfModelStore._current_model`
- `update()` 调用旧 Builder 重建时可能覆盖增量更新
- **修正方案**: SelfModelStore 新增 `apply_change_proposal()` 公开方法

### P1-4: PersonalityGrowthHistory 持久化 ✅ 已完成
- 已添加 JSON 文件持久化，version=1
- 文件: `data/personality_growth_history.json`

### P1-5: SelfModelStore 持久化
- 纯内存，重启丢失
- **修正方案**: JSON 文件持久化，version=1

### P2: TraitValidator / UUID 扩展
- 本阶段不做

---

## 4. 实施顺序 (审核修正版)

```
Step 1: PersonalityGrowthHistory 持久化 (+版本号)  ✅ 已完成
        独立, 低风险, 为后续步骤提供持久化基础设施

Step 2: SelfModelUpdater → Store 公开接口修复  ← 当前
        独立, 解决状态一致性问题, 不依赖持久化

Step 3: SelfModelBuilder 架构确认 (Adapter + A/B 对比)
        创建 Adapter, 默认 use_new_builder=False, 不破坏现有行为

Step 4: SelfModelStore 持久化 (+版本号)
        依赖 Step 3 (Builder 架构确认后持久化格式稳定)

Step 5: Pipeline 拆分 apply 职责, 消除双处理
        独立, 不依赖持久化, 但需要 GrowthEngine 方法拆分

Step 6: 全量回归测试
```

### 核心原则
- 不删除旧模块
- 不一次性替换 Builder
- Pipeline 不允许简单删除 apply()
- 每一步完成后运行对应测试
- 如果发现测试依赖旧行为，先报告，不强改

---

## 5. Step 2 详细设计: SelfModelUpdater → Store 公开接口修复

### 问题
`SelfModelUpdater._apply_to_store()` (L259-L312) 直接操作 `self.store.get()` 返回的 dict，通过 `current["growth_narratives"] = narratives` 和 `current["self_understanding"] = understanding` 修改内部状态。

而 `SelfModelStore.update()` 调用 `self._builder.build()` 重新构建整个 `_current_model`，会覆盖 Updater 的增量更新。

### 修改

**文件 1: `src/personality/self_model_store.py`**

新增 `apply_change_proposal()` 公开方法：

```python
def apply_change_proposal(self, proposal: "SelfModelChangeProposal") -> None:
    """
    应用 SelfModelChangeProposal 到当前模型，保持状态一致性。

    与 update() 不同：不重建整个 SelfModel，只做增量追加。
    如果当前模型不存在，先通过 Builder 构建初始模型。
    """
    if self._current_model is None:
        # 尚无模型，先用 Builder 构建（需要 history + trait_states）
        # 但 Updater 可能没有这些参数 → 静默跳过
        return

    if proposal.change_type == "narrative_append":
        narratives = self._current_model.get("growth_narratives", [])
        if not isinstance(narratives, list):
            narratives = []
        narrative_entry = {
            "record_id": proposal.source.get("growth_id", ""),
            "dimension": proposal.change.get("dimension", ""),
            "event": proposal.change.get("event", ""),
            "narrative": proposal.change.get("narrative", ""),
            "meaning": proposal.change.get("meaning", ""),
            "timestamp": proposal.timestamp,
            "_source_growth_id": proposal.source.get("growth_id", ""),
            "_source_event_id": proposal.source.get("source_event_id", ""),
            "_evidence_ids": proposal.source.get("evidence_ids", []),
            "_confidence": proposal.source.get("confidence", 0.5),
        }
        narratives.append(narrative_entry)
        if len(narratives) > 20:
            narratives = narratives[-20:]
        self._current_model["growth_narratives"] = narratives

    elif proposal.change_type == "self_understanding_update":
        # 更新 self_understanding 指标
        understanding = self._current_model.get("self_understanding", {})
        if not isinstance(understanding, dict):
            understanding = {}
        confidence = proposal.source.get("confidence", 0.5)
        increment = min(0.05, confidence * 0.05)
        understanding["experience_awareness"] = min(
            1.0, understanding.get("experience_awareness", 0.3) + increment
        )
        understanding["trait_awareness"] = min(
            1.0, understanding.get("trait_awareness", 0.2) + increment * 0.8
        )
        growth_level = proposal.change.get("growth_level", "context")
        if growth_level in ("trait", "preference"):
            understanding["identity_continuity"] = min(
                1.0, understanding.get("identity_continuity", 0.4) + increment * 0.5
            )
        exp = understanding.get("experience_awareness", 0.3)
        trt = understanding.get("trait_awareness", 0.2)
        idn = understanding.get("identity_continuity", 0.4)
        understanding["overall"] = round((exp + trt + idn) / 3, 3)
        self._current_model["self_understanding"] = understanding

    self._current_model["last_updated"] = datetime.now().isoformat()
```

**文件 2: `src/personality/self_model_updater.py`**

修改 `_apply_to_store()` 方法，改为调用 `self.store.apply_change_proposal(proposal)` 而不是直接操作 dict。

```python
def _apply_to_store(self, proposal: SelfModelChangeProposal) -> None:
    if self.store is None:
        return
    try:
        # 通过 Store 公开接口操作，而非直接修改 _current_model
        if hasattr(self.store, "apply_change_proposal"):
            self.store.apply_change_proposal(proposal)
        else:
            # 向后兼容：旧 Store 无此方法时，回退到直接操作
            self._apply_to_store_legacy(proposal)
    except Exception as e:
        logger.warning(f"SelfModelUpdater: 应用到 Store 失败（已隔离）: {e}")

def _apply_to_store_legacy(self, proposal: SelfModelChangeProposal) -> None:
    """旧路径：直接操作 Store._current_model（向后兼容）"""
    # 保留原有逻辑不变
    ...
```

### 风险
低。公开接口 + 向后兼容 fallback。

### 测试
- `tests/test_phase383_self_model_growth.py`
- `tests/test_self_model_store.py`

---

## 6. Step 3 详细设计: SelfModelBuilder 架构确认

### 设计

```
SelfModelStore
       │
       ▼
SelfModelBuilderAdapter  ← config 控制 use_new_builder
       │
   ┌───┴───┐
   │       │
   ▼       ▼
Legacy    New
Builder   Builder
```

### 新增文件
`src/personality/self_model_builder_adapter.py`

```python
class SelfModelBuilderAdapter:
    """双 Builder 桥接层，支持 A/B 对比和渐进迁移"""

    def __init__(self, use_new_builder: bool = False):
        self._legacy = SelfModelBuilder()  # personality/self_model_builder.py
        self._new = None  # 延迟初始化 self_model/self_model_builder.py
        self._use_new = use_new_builder

    def build(self, history, trait_states, base_identity, capability_limitations):
        if self._use_new:
            return self._build_with_new(history, trait_states, base_identity, capability_limitations)
        else:
            return self._legacy.build(
                history=history, trait_states=trait_states,
                base_identity=base_identity, capability_limitations=capability_limitations,
            )
```

### 修改文件
- `src/personality/self_model_store.py` — `__init__` 中 `self._builder = SelfModelBuilderAdapter()`
- `src/personality/self_model_builder.py` — 添加 DEPRECATED 注释，不删除

### 风险
中。新 Builder 输入模型不同，需 Adapter 正确转换。通过 A/B 对比降低风险。

---

## 7. Step 4 详细设计: SelfModelStore 持久化

与 Step 1 一致的模式：JSON 文件 + version 字段。

**修改文件**: `src/personality/self_model_store.py`

```python
DEFAULT_STORAGE_PATH = "data/self_model.json"

def __init__(self, ..., storage_path=None):
    self._storage_path = Path(storage_path) if storage_path else None
    if self._storage_path:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_from_disk()

def _save_to_disk(self):
    if not self._storage_path or self._current_model is None:
        return
    data = {
        "version": 1,
        "saved_at": datetime.now().isoformat(),
        "model": self._current_model.copy(),
    }
    with open(self._storage_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_from_disk(self):
    if not self._storage_path or not self._storage_path.exists():
        return
    with open(self._storage_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    self._current_model = data.get("model", {})
```

在 `update()` 和 `apply_change_proposal()` 末尾自动调用 `_save_to_disk()`。

---

## 8. Step 5 详细设计: Pipeline 拆分 apply 职责

### 修正方案（用户审核要求）

不能简单 `if not proposal_applied: apply()`，因为 `apply()` 还负责：
- Relationship 更新
- PersonalityInfluence 生成

**需将 `apply()` 拆分为三个独立子方法**：

```python
# GrowthEngine 新增方法：

def _apply_growth_metrics(self, event, metrics, importance):
    """纯指标更新（从 apply() 中提取）"""
    before, delta = self._apply_metrics(metrics, importance)
    return before, delta

def _update_growth_history(self, event, mode, before, delta):
    """记录成长历史（从 apply() 中提取）"""
    self._record_history(event, mode, before, delta)

# apply() 保留作为 fallback，内部委托给子方法
```

**Pipeline 新流程**:

```python
for event in events:
    evaluated = self.evaluator.evaluate(...)
    proposal_applied = False

    if evaluated.get("growth_allowed", False):
        # 路径 A: Proposal 化
        proposal = self._build_proposal_from_evaluated(evaluated, event)
        if proposal is not None:
            result = self.growth_engine.apply_proposal(proposal)
            if result.get("status") == "applied":
                proposal_applied = True

        # GrowthRecord + Adapter（始终执行）
        record = self.growth_engine.apply_evaluated(evaluated)
        if record:
            personality_record = self.narrative_adapter.convert(record)
            self.growth_records.add(personality_record)

    # 旧路径: 仅在 Proposal 未成功时执行 GrowthState 更新
    # 但 Relationship 和 Influence 始终执行
    result = self.growth_engine.apply(event)
    if result.get("status") == "applied":
        if not proposal_applied:
            # GrowthState 未通过 Proposal 更新的，这里补
            applied.append({...})
        # 无论 Proposal 是否成功，都执行 Relationship 和 Influence
        if result.get("mode") == "first":
            self._update_relationship(event)
        self._generate_influences(event, result)
```

### 风险
低。`apply()` 内部逻辑不变，仅 Pipeline 调用时机调整。

---

## 9. 明确不要修改

| 项目 | 原因 |
|------|------|
| GrowthRecord 数据结构 | 纯事实层设计正确 |
| GrowthEvaluator 评估逻辑 | 五维评估体系成熟 |
| GrowthNarrativeAdapter 转换逻辑 | 边界清晰，职责正确 |
| GrowthEngine GROWTH_MAP | 作为 fallback 保留 |
| PersonalityGrowthRecord 数据结构 | source 追溯链完整 |
| GrowthHistoryBridge | 桥接逻辑干净 |
| GrowthState 持久化 | 已有 JSON 持久化 |
| 新 SelfModel Schema (self_model/) | 冻结设计，作为未来标准 |
| TraitValidator | P2，本阶段不做 |
| 所有测试文件 | 不修改，仅新增 |

---

## 10. 验证步骤

完成所有修改后：

```bash
# 1. Growth Pipeline 测试
python -m pytest tests/test_growth_pipeline.py -v
python -m pytest tests/test_growth_evaluator.py -v
python -m pytest tests/test_growth_narrative_adapter.py -v

# 2. SelfModel 测试
python -m pytest tests/test_phase383_self_model_growth.py -v
python -m pytest tests/test_self_model_store.py -v

# 3. Personality Growth 测试
python -m pytest tests/test_personality_growth_record.py -v

# 4. 回归测试
python -m pytest tests/test_growth_integration.py -v
python -m pytest tests/test_growth_proposal_runtime.py -v
```

---

**等待用户确认后继续执行 Step 2。**
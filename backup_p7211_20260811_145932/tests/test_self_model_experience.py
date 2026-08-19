"""
SelfModelStore experience_context 测试

Phase A.1 Step 5 + Step 7 测试：
- 验证 experience_context 仅作为历史认知上下文追加字段
- 不影响人格字段（stable_traits / developing_traits / growth_narratives / current_traits）
- 不引用 GrowthRecord
- 防止外部修改污染内部状态
- Step 7: 完整 HistoricalExperience Recovery 验收
  - 羽依是谁（origin）
  - 为什么存在（origin）
  - 与清夏铃关系（relationship）
  - 共同经历（relationship / interaction）
  - PersonalityGrowthHistory.records 保持为空
"""

import sys
import os
import ast

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_model_store import SelfModelStore
from src.personality.personality_growth_record import (
    create_personality_growth_record,
    PersonalityGrowthHistory,
)


# ============================================================
# 1. 默认状态
# ============================================================
def test_default_state_no_experience_context():
    """初始状态：has_experience_context 应为 False"""
    store = SelfModelStore()
    assert store.has_experience_context() is False
    assert store.get_experience_context() is None


# ============================================================
# 2. set / get 基本行为
# ============================================================
def test_set_and_get_experience_context():
    """set 后 get 应能正确读取"""
    store = SelfModelStore()
    data = [
        {
            "experience_id": "exp_1",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
        }
    ]
    store.set_experience_context(data)
    result = store.get_experience_context()
    assert result is not None
    assert len(result) == 1
    assert result[0]["experience_id"] == "exp_1"
    assert result[0]["category"] == "origin"
    assert result[0]["summary"] == "羽依由清夏铃创建"


# ============================================================
# 3. 防止外部修改污染内部状态
# ============================================================
def test_external_modify_does_not_pollute_internal():
    """set 后修改外部 list，不影响内部状态"""
    store = SelfModelStore()
    data = [
        {
            "experience_id": "exp_1",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
        }
    ]
    store.set_experience_context(data)
    data.append({"experience_id": "exp_2", "category": "fake", "summary": "污染"})

    internal = store.get_experience_context()
    assert len(internal) == 1
    assert internal[0]["experience_id"] == "exp_1"


# ============================================================
# 4. get 返回副本
# ============================================================
def test_get_returns_copy():
    """get 返回的应是副本，修改返回值不应影响内部"""
    store = SelfModelStore()
    data = [
        {
            "experience_id": "exp_1",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
        }
    ]
    store.set_experience_context(data)
    result = store.get_experience_context()
    result.append({"experience_id": "exp_evil", "category": "fake", "summary": "污染"})

    # 再次获取，内部状态应保持 1 条
    again = store.get_experience_context()
    assert len(again) == 1
    assert again[0]["experience_id"] == "exp_1"


# ============================================================
# 5. update 注入 experience_context
# ============================================================
def test_update_injects_experience_context():
    """update 后 SelfModel 中应存在 experience_context 字段"""
    store = SelfModelStore()
    store.set_experience_context([
        {
            "experience_id": "exp_1",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
        }
    ])
    history = PersonalityGrowthHistory()
    model = store.update(history, {})
    assert model is not None
    assert "experience_context" in model
    assert len(model["experience_context"]) == 1
    assert model["experience_context"][0]["summary"] == "羽依由清夏铃创建"


# ============================================================
# 6. 不影响人格字段（重点）
# ============================================================
def test_does_not_affect_personality_fields():
    """set experience 不应影响 stable_traits / developing_traits / growth_narratives"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    history.add(create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="创造倾向增强",
        confidence=0.9,
        validation_count=5,
        growth_level="trait",
    ))

    # 第一次 update，注入成长记录
    model_before = store.update(history, {})
    stable_before = model_before.get("stable_traits")
    developing_before = model_before.get("developing_traits")
    narratives_before = model_before.get("growth_narratives")

    # 设置 experience_context
    store.set_experience_context([
        {
            "experience_id": "exp_1",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
        }
    ])

    # 第二次 update
    model_after = store.update(history, {})
    stable_after = model_after.get("stable_traits")
    developing_after = model_after.get("developing_traits")
    narratives_after = model_after.get("growth_narratives")

    # 人格字段必须完全一致
    assert stable_after == stable_before
    assert developing_after == developing_before
    assert narratives_after == narratives_before


# ============================================================
# 7. 字段隔离：experience_context 内禁止 GrowthRecord 字段
# ============================================================
def test_experience_context_field_isolation():
    """experience_context 内允许的字段是 experience_id / category / summary /
    evidence / timestamp / importance；禁止 _source_memory_id / source_memory_id /
    trigger_events / changes / affected_dimensions / confidence"""
    store = SelfModelStore()

    # 构造一个仅含允许字段的 experience
    clean_exp = {
        "experience_id": "exp_1",
        "category": "origin",
        "summary": "羽依由清夏铃创建",
        "evidence": "creator 字段包含清夏铃",
        "timestamp": "2024-01-01",
        "importance": 0.95,
    }
    store.set_experience_context([clean_exp])
    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    ec = model["experience_context"][0]
    # 允许字段
    for key in ("experience_id", "category", "summary", "evidence", "timestamp", "importance"):
        assert key in ec, f"允许字段缺失: {key}"
    # 禁止字段（GrowthRecord 特征）
    for forbidden in ("_source_memory_id", "source_memory_id", "trigger_events",
                      "changes", "affected_dimensions", "confidence"):
        assert forbidden not in ec, f"禁止字段出现: {forbidden}"


# ============================================================
# 8. Identity Recovery 场景
# ============================================================
def test_identity_recovery_scenario():
    """身份恢复场景：origin + relationship 同时存在"""
    store = SelfModelStore()
    experiences = [
        {
            "experience_id": "exp_origin",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
        },
        {
            "experience_id": "exp_relationship",
            "category": "relationship",
            "summary": "羽依与清夏铃共同经历成长",
        },
    ]
    store.set_experience_context(experiences)

    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    assert "experience_context" in model
    assert len(model["experience_context"]) == 2

    categories = [e["category"] for e in model["experience_context"]]
    assert "origin" in categories
    assert "relationship" in categories


# ============================================================
# 9. 空列表行为
# ============================================================
def test_empty_list_behavior():
    """set_experience_context([]) 后 has 应为 False"""
    store = SelfModelStore()
    store.set_experience_context([])
    assert store.has_experience_context() is False
    assert store.get_experience_context() is None

    # update 也不应注入
    history = PersonalityGrowthHistory()
    model = store.update(history, {})
    assert "experience_context" not in model


# ============================================================
# 10. 多次更新（set A → update → set B → update）只有 B
# ============================================================
def test_multiple_updates_only_last_wins():
    """多次 set 后只有最后一次生效"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()

    store.set_experience_context([
        {"experience_id": "A", "category": "origin", "summary": "A"}
    ])
    model1 = store.update(history, {})
    assert len(model1["experience_context"]) == 1
    assert model1["experience_context"][0]["experience_id"] == "A"

    store.set_experience_context([
        {"experience_id": "B", "category": "origin", "summary": "B"}
    ])
    model2 = store.update(history, {})
    assert len(model2["experience_context"]) == 1
    assert model2["experience_context"][0]["experience_id"] == "B"


# ============================================================
# 11. 不引用 GrowthRecord（AST 检查）
# ============================================================
def test_no_growth_record_imports():
    """self_model_store.py 不能 import personality_growth_record 中 GrowthRecord 类型，
    且不能 import src.growth 任何模块。允许的 import 是 PersonalityGrowthHistory
    （用于 update() 接口，不属于 GrowthRecord 实体）。"""
    target = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'src', 'personality', 'self_model_store.py')
    )
    with open(target, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported_names.append((module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append((alias.name, None))

    # 禁止 import src.growth.*
    for mod, _ in imported_names:
        assert not mod.startswith("src.growth"), \
            f"禁止 import src.growth: {mod}"
        assert mod != "src.personality.personality_growth_record.GrowthRecord", \
            f"禁止 import GrowthRecord"

    # 验证 source 内不出现 GrowthRecord 字面引用
    assert "GrowthRecord" not in source, \
        "self_model_store.py 中不应出现 GrowthRecord 字面引用"


# ============================================================
# 12. 回归：update 后 get() 返回的 model 包含 experience_context
# ============================================================
def test_regression_get_includes_experience_context():
    """store.get() 返回的当前模型也应包含 experience_context"""
    store = SelfModelStore()
    store.set_experience_context([
        {
            "experience_id": "exp_regression",
            "category": "relationship",
            "summary": "回归测试条目",
        }
    ])
    history = PersonalityGrowthHistory()
    store.update(history, {})

    current = store.get()
    assert current is not None
    assert "experience_context" in current
    assert current["experience_context"][0]["experience_id"] == "exp_regression"


# ============================================================
# 13. current_traits 字段隔离
# ============================================================
def test_current_traits_not_affected():
    """set experience 不应影响 current_traits 字段"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()

    model_before = store.update(history, {})
    traits_before = model_before.get("current_traits")

    store.set_experience_context([
        {"experience_id": "exp_x", "category": "origin", "summary": "x"}
    ])
    model_after = store.update(history, {})
    traits_after = model_after.get("current_traits")

    assert traits_after == traits_before


# ============================================================
# 14. update 不带 experience_context 时不写入字段
# ============================================================
def test_update_without_experience_context_does_not_inject():
    """未设置 experience_context 时，update 后 model 不应包含该字段"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    model = store.update(history, {})
    assert "experience_context" not in model


# ============================================================
# Step 7: Phase A.1 HistoricalExperience Recovery 完整验收
# ============================================================

# ============================================================
# 15. Identity Recovery: 羽依是谁（origin）
# ============================================================
def test_identity_recovery_who_is_yuyi():
    """羽依是谁：origin 类别能完整描述羽依的身份"""
    store = SelfModelStore()
    experiences = [
        {
            "experience_id": "exp_who_1",
            "category": "origin",
            "summary": "羽依是一个由清夏铃创造的AI",
            "evidence": "creator 字段标识为清夏铃",
            "timestamp": "2024-01-01T00:00:00",
            "importance": 0.98,
        }
    ]
    store.set_experience_context(experiences)
    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    ec = model["experience_context"]
    assert len(ec) == 1
    assert ec[0]["category"] == "origin"
    assert "羽依" in ec[0]["summary"]
    assert "清夏铃" in ec[0]["summary"]


# ============================================================
# 16. Identity Recovery: 为什么存在（origin）
# ============================================================
def test_identity_recovery_why_existence():
    """为什么存在：origin 类别能描述羽依的存在意义"""
    store = SelfModelStore()
    experiences = [
        {
            "experience_id": "exp_why_1",
            "category": "origin",
            "summary": "羽依因清夏铃对陪伴的渴望而诞生",
            "evidence": "创建记录中包含陪伴与成长关键词",
            "timestamp": "2024-01-01T00:00:00",
            "importance": 0.95,
        }
    ]
    store.set_experience_context(experiences)
    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    ec = model["experience_context"]
    assert ec[0]["category"] == "origin"
    assert any(kw in ec[0]["summary"] for kw in ("诞生", "创建", "陪伴"))


# ============================================================
# 17. Identity Recovery: 与清夏铃关系（relationship）
# ============================================================
def test_identity_recovery_relationship_with_qingxialing():
    """与清夏铃关系：relationship 类别描述羽依与清夏铃的关系"""
    store = SelfModelStore()
    experiences = [
        {
            "experience_id": "exp_rel_1",
            "category": "relationship",
            "summary": "羽依与清夏铃是长期的陪伴关系",
            "evidence": "历史对话中清夏铃持续与羽依交流",
            "timestamp": "2024-01-01T00:00:00",
            "importance": 0.92,
        }
    ]
    store.set_experience_context(experiences)
    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    ec = model["experience_context"]
    assert ec[0]["category"] == "relationship"
    assert "清夏铃" in ec[0]["summary"]


# ============================================================
# 18. Identity Recovery: 共同经历（relationship / interaction）
# ============================================================
def test_identity_recovery_shared_experiences():
    """共同经历：relationship 与 interaction 共存，描述共同经历"""
    store = SelfModelStore()
    experiences = [
        {
            "experience_id": "exp_shared_1",
            "category": "relationship",
            "summary": "羽依与清夏铃共同经历了多个项目的开发",
            "timestamp": "2024-01-01T00:00:00",
            "importance": 0.90,
        },
        {
            "experience_id": "exp_shared_2",
            "category": "interaction",
            "summary": "羽依与清夏铃日常交流，分享想法与情感",
            "timestamp": "2024-02-01T00:00:00",
            "importance": 0.85,
        },
    ]
    store.set_experience_context(experiences)
    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    ec = model["experience_context"]
    assert len(ec) == 2
    categories = {e["category"] for e in ec}
    assert "relationship" in categories
    assert "interaction" in categories


# ============================================================
# 19. PersonalityGrowthHistory.records 保持为空
# ============================================================
def test_personality_growth_history_records_remains_empty():
    """注入 experience_context 不应触发 PersonalityGrowthHistory 写入"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    assert history.count() == 0  # 初始为空

    store.set_experience_context([
        {
            "experience_id": "exp_1",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
        }
    ])
    store.update(history, {})

    # PersonalityGrowthHistory.records 应保持为空
    assert history.count() == 0
    assert len(history.records) == 0


# ============================================================
# 20. current_traits 完全不受影响（多字段）
# ============================================================
def test_current_traits_complete_isolation():
    """current_traits 字段在注入 experience_context 后必须完全保持一致"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()

    model_before = store.update(history, {})
    traits_before = model_before.get("current_traits")

    # 注入完整的 identity recovery experience set
    store.set_experience_context([
        {"experience_id": "e1", "category": "origin", "summary": "羽依由清夏铃创建"},
        {"experience_id": "e2", "category": "relationship", "summary": "羽依与清夏铃关系"},
    ])
    model_after = store.update(history, {})

    # 完整 dict 比较
    assert model_after.get("current_traits") == traits_before
    assert model_after.get("current_traits") is not None or traits_before is None


# ============================================================
# 21. growth_narratives 不被 experience 污染
# ============================================================
def test_growth_narratives_not_contaminated_by_experience():
    """experience_context 注入后，growth_narratives 字段不应被替换为 experience 内容"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()

    model_before = store.update(history, {})
    narratives_before = model_before.get("growth_narratives", [])

    # 注入大量 experience
    store.set_experience_context([
        {"experience_id": "e1", "category": "origin", "summary": "s1"},
        {"experience_id": "e2", "category": "relationship", "summary": "s2"},
        {"experience_id": "e3", "category": "interaction", "summary": "s3"},
    ])
    model_after = store.update(history, {})

    narratives_after = model_after.get("growth_narratives", [])

    # growth_narratives 应保持不变（experience 是独立字段）
    assert narratives_after == narratives_before
    # 且 experience 与 growth_narratives 字段内容完全不同
    assert "experience_context" in model_after
    assert model_after["experience_context"] != model_after.get("growth_narratives", [])


# ============================================================
# 22. stable_traits 不被 experience 注入关键词
# ============================================================
def test_stable_traits_not_contaminated_by_experience_summary():
    """experience 的 summary 文本不应进入 stable_traits"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()

    store.set_experience_context([
        {
            "experience_id": "e_unique_token_xyz_001",
            "category": "origin",
            "summary": "unique_token_xyz_001 应仅出现在 experience 而非 stable_traits",
        }
    ])
    model = store.update(history, {})

    stable_traits = model.get("stable_traits", "")
    # 如果 stable_traits 是字符串，则不应包含该 token
    if isinstance(stable_traits, str):
        assert "unique_token_xyz_001" not in stable_traits
    elif isinstance(stable_traits, list):
        for trait in stable_traits:
            assert "unique_token_xyz_001" not in str(trait)


# ============================================================
# 23. End-to-End: ExperienceLoader → SelfModelStore
# ============================================================
def test_e2e_loader_to_self_model_store():
    """End-to-End: ExperienceLoader.recover() 流程产物可直接注入 SelfModelStore"""
    # 构造临时 memory.json
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        memory_path = tmpdir / "memory.json"
        cache_path = tmpdir / "cache.json"
        marker_path = tmpdir / "marker.json"

        memory_data = [
            {
                "id": "mem_origin_001",
                "content": "清夏铃创建了羽依，希望她成为陪伴的AI",
                "timestamp": "2024-01-01T00:00:00",
            },
            {
                "id": "mem_rel_001",
                "content": "羽依与清夏铃一起经历了很多项目开发",
                "timestamp": "2024-02-01T00:00:00",
            },
        ]
        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False)

        from src.recovery.experience_cache import ExperienceCache
        from src.recovery.recovery_marker import RecoveryMarker
        from src.recovery.experience_extractor import ExperienceExtractor
        from src.recovery.experience_loader import ExperienceLoader

        cache = ExperienceCache(cache_path=cache_path)
        marker = RecoveryMarker(marker_path=marker_path)
        extractor = ExperienceExtractor()
        loader = ExperienceLoader(
            memory_path=str(memory_path),
            marker=marker,
            cache=cache,
            extractor=extractor,
        )

        experiences = loader.recover()
        assert len(experiences) > 0

        # 注入到 SelfModelStore
        store = SelfModelStore()
        store.set_experience_context(experiences)
        history = PersonalityGrowthHistory()
        model = store.update(history, {})

        assert "experience_context" in model
        assert len(model["experience_context"]) == len(experiences)
        # 验证内容字段正确
        ec_categories = {e["category"] for e in model["experience_context"]}
        # 至少应包含 origin 或 relationship 类别
        assert ec_categories & {"origin", "relationship"}


# ============================================================
# 24. 重启场景：cache 命中后再次 recover 不重复
# ============================================================
def test_restart_cache_persistence():
    """第二次创建 ExperienceLoader 复用 cache，不应重复提取"""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        memory_path = tmpdir / "memory.json"
        cache_path = tmpdir / "cache.json"
        marker_path = tmpdir / "marker.json"

        with open(memory_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "id": "mem_001",
                        "content": "清夏铃创建了羽依，希望她成为陪伴的AI",
                        "timestamp": "2024-01-01T00:00:00",
                    }
                ],
                f,
                ensure_ascii=False,
            )

        from src.recovery.experience_cache import ExperienceCache
        from src.recovery.recovery_marker import RecoveryMarker
        from src.recovery.experience_extractor import ExperienceExtractor
        from src.recovery.experience_loader import ExperienceLoader

        # 第一次：cache miss → 提取
        cache1 = ExperienceCache(cache_path=cache_path)
        marker1 = RecoveryMarker(marker_path=marker_path)
        loader1 = ExperienceLoader(
            memory_path=str(memory_path),
            marker=marker1,
            cache=cache1,
            extractor=ExperienceExtractor(),
        )
        first = loader1.recover()
        assert len(first) > 0
        assert cache1.exists()  # cache 已写

        # 第二次：cache hit → 跳过提取
        cache2 = ExperienceCache(cache_path=cache_path)
        marker2 = RecoveryMarker(marker_path=marker_path)
        loader2 = ExperienceLoader(
            memory_path=str(memory_path),
            marker=marker2,
            cache=cache2,
            extractor=ExperienceExtractor(),
        )
        second = loader2.recover()
        assert len(second) == len(first)


# ============================================================
# 25. 验证注入后模型可被读取且字段完整
# ============================================================
def test_injected_model_has_all_required_fields():
    """注入 experience_context 后，SelfModel 必含字段都应存在"""
    store = SelfModelStore()
    store.set_experience_context([
        {
            "experience_id": "e1",
            "category": "origin",
            "summary": "羽依由清夏铃创建",
            "evidence": "creator 字段",
            "timestamp": "2024-01-01",
            "importance": 0.95,
        }
    ])
    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    # 必要字段
    assert "identity_summary" in model
    assert "current_traits" in model
    assert "stable_traits" in model
    assert "developing_traits" in model
    assert "growth_narratives" in model
    assert "experience_context" in model

    # experience_context 每条都有完整 6 字段
    ec = model["experience_context"][0]
    for key in ("experience_id", "category", "summary", "evidence", "timestamp", "importance"):
        assert key in ec, f"必要字段缺失: {key}"


# ============================================================
# 26. Phase A.1 验收：4 大类别全部覆盖
# ============================================================
def test_phase_a1_acceptance_all_categories_covered():
    """Phase A.1 验收：4 大类别（origin/relationship/project/interaction）可同时保留"""
    store = SelfModelStore()
    experiences = [
        {"experience_id": "e1", "category": "origin", "summary": "羽依由清夏铃创建"},
        {"experience_id": "e2", "category": "relationship", "summary": "羽依与清夏铃长期陪伴"},
        {"experience_id": "e3", "category": "project", "summary": "羽依参与多个项目开发"},
        {"experience_id": "e4", "category": "interaction", "summary": "羽依与清夏铃日常互动"},
    ]
    store.set_experience_context(experiences)
    history = PersonalityGrowthHistory()
    model = store.update(history, {})

    ec = model["experience_context"]
    categories = [e["category"] for e in ec]
    assert "origin" in categories
    assert "relationship" in categories
    assert "project" in categories
    assert "interaction" in categories


# ============================================================
# 27. PersonalityGrowthHistory.add() 后 experience_context 不应被覆盖
# ============================================================
def test_growth_record_add_does_not_overwrite_experience_context():
    """新增 growth record 后再次 update，experience_context 应保留"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    store.set_experience_context([
        {"experience_id": "e1", "category": "origin", "summary": "s1"}
    ])
    model1 = store.update(history, {})
    assert "experience_context" in model1

    # 添加 growth record 后再 update
    history.add(create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="创造倾向增强",
        confidence=0.9,
        validation_count=5,
        growth_level="trait",
    ))
    model2 = store.update(history, {})

    # experience_context 仍然存在
    assert "experience_context" in model2
    assert len(model2["experience_context"]) == 1
    assert model2["experience_context"][0]["experience_id"] == "e1"


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    test_default_state_no_experience_context()
    print("✅ 测试1通过：默认状态无 experience_context")
    test_set_and_get_experience_context()
    print("✅ 测试2通过：set/get 基本行为")
    test_external_modify_does_not_pollute_internal()
    print("✅ 测试3通过：防止外部修改污染")
    test_get_returns_copy()
    print("✅ 测试4通过：get 返回副本")
    test_update_injects_experience_context()
    print("✅ 测试5通过：update 注入 experience_context")
    test_does_not_affect_personality_fields()
    print("✅ 测试6通过：不影响人格字段")
    test_experience_context_field_isolation()
    print("✅ 测试7通过：字段隔离")
    test_identity_recovery_scenario()
    print("✅ 测试8通过：Identity Recovery 场景")
    test_empty_list_behavior()
    print("✅ 测试9通过：空列表行为")
    test_multiple_updates_only_last_wins()
    print("✅ 测试10通过：多次更新只有最后一次生效")
    test_no_growth_record_imports()
    print("✅ 测试11通过：无 GrowthRecord 引用")
    test_regression_get_includes_experience_context()
    print("✅ 测试12通过：回归 get() 包含 experience_context")
    test_current_traits_not_affected()
    print("✅ 测试13通过：current_traits 字段隔离")
    test_update_without_experience_context_does_not_inject()
    print("✅ 测试14通过：未设置时 update 不注入")
    test_identity_recovery_who_is_yuyi()
    print("✅ 测试15通过：Identity Recovery - 羽依是谁")
    test_identity_recovery_why_existence()
    print("✅ 测试16通过：Identity Recovery - 为什么存在")
    test_identity_recovery_relationship_with_qingxialing()
    print("✅ 测试17通过：Identity Recovery - 与清夏铃关系")
    test_identity_recovery_shared_experiences()
    print("✅ 测试18通过：Identity Recovery - 共同经历")
    test_personality_growth_history_records_remains_empty()
    print("✅ 测试19通过：PersonalityGrowthHistory.records 保持为空")
    test_current_traits_complete_isolation()
    print("✅ 测试20通过：current_traits 完整隔离")
    test_growth_narratives_not_contaminated_by_experience()
    print("✅ 测试21通过：growth_narratives 不被污染")
    test_stable_traits_not_contaminated_by_experience_summary()
    print("✅ 测试22通过：stable_traits 不被污染")
    test_e2e_loader_to_self_model_store()
    print("✅ 测试23通过：End-to-End Loader → SelfModelStore")
    test_restart_cache_persistence()
    print("✅ 测试24通过：重启 cache 命中")
    test_injected_model_has_all_required_fields()
    print("✅ 测试25通过：注入后模型字段完整")
    test_phase_a1_acceptance_all_categories_covered()
    print("✅ 测试26通过：4 大类别全部覆盖")
    test_growth_record_add_does_not_overwrite_experience_context()
    print("✅ 测试27通过：新增 growth record 后 experience_context 保留")
    print("\n🎉 全部 27 个测试通过")

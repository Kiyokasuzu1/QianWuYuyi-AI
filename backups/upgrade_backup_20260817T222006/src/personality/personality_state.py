"""
Phase 4.0 — R2.5.4: PersonalityState（运行时人格状态 唯一来源）

定位：
  Runtime 唯一的「羽依当前人格状态」。不是 persona.md（那是出生设定）。
  Persona Base + EvolutionRecord 历史 + Current PersonalityState = 当前羽依。

核心职责：
  1. 持有 trait 字典 {trait_name: float(0~1)}
  2. version 随每次 evolution 递增
  3. apply_evolution(record) 是修改 trait 的唯一合法入口
  4. Identity Anchor 二次保护：即使 Approval bug，identity.core.*/manifesto.* 也无法被 apply
  5. 完整审计 trail：evolution_record_ids 列表

红线（R2.5.4 冻结）：
  ❌ 不直接接受 dict 赋值修改 traits（必须走 apply_evolution）
  ❌ 不修改 persona.md / identity.md / 任何磁盘文件
  ❌ 不调用 LLM / 不生成回复
  ❌ apply_evolution 只接受 EvolutionRecord（必须有 proposal_id + approval_id）
  ✅ identity anchor 保护在 apply_evolution 内部强制（EP-4 二次保护）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from src.personality.evolution_record import EvolutionRecord, build_evolution_record

logger = logging.getLogger(__name__)


# ============================================================
# Identity Anchor 二次保护路径前缀（与 ApprovalPolicy 保持一致）
# 即使 Approval 有 bug 漏过了，这里也会硬拒
# ============================================================
IDENTITY_FORBIDDEN_PREFIXES: Tuple[str, ...] = (
    "identity.core.",
    "identity.origin.",
    "manifesto.",
)


@dataclass
class PersonalityState:
    """
    Runtime 人格状态。trait 字典 + version + 审计 trail。

    不继承 TypedDict / dict；这是有行为的值对象。
    """
    traits: Dict[str, float] = field(default_factory=lambda: {
        # 默认初始值（来自 persona.md 的大致映射；R2.5.4 不从文件加载，用默认值）
        "creativity": 0.6,
        "curiosity": 0.7,
        "empathy": 0.65,
        "independence": 0.6,
        "playfulness": 0.55,
    })
    version: int = 0
    evolution_record_ids: List[str] = field(default_factory=list)
    # 已应用的 proposal_id 集合（EP-2 幂等保护：不可重复 apply）
    _applied_proposal_ids: set = field(default_factory=set, repr=False)

    # ============================================================
    # 唯一合法修改入口
    # ============================================================
    def apply_evolution(self, record: EvolutionRecord) -> Dict[str, Any]:
        """
        应用一个 EvolutionRecord 到 traits。

        返回:
          {
            "applied": bool,
            "record_id": str,
            "affected_traits": Dict[str, {"before": float, "after": float}],
            "blocked_identity": List[str],  # 被 identity anchor 二次保护拦截的 trait
            "skipped_no_change": List[str], # before == after 的 trait
            "error": str | None,
          }

        红线：
          - record 必须有 proposal_id + approval_id（EP-3）
          - 同一 proposal_id 不可重复 apply（EP-2）
          - identity.core.*/manifesto.* 路径 → 硬拒（EP-4 二次保护）
        """
        result: Dict[str, Any] = {
            "applied": False,
            "record_id": record.get("record_id", ""),
            "affected_traits": {},
            "blocked_identity": [],
            "skipped_no_change": [],
            "error": None,
        }

        # EP-3: 必须有 proposal_id + approval_id
        proposal_id = str(record.get("proposal_id", "") or "")
        approval_id = str(record.get("approval_id", "") or "")
        if not proposal_id or not approval_id:
            result["error"] = "EP-3: EvolutionRecord 缺少 proposal_id 或 approval_id"
            logger.warning("[personality_state_apply_blocked] %s", result["error"])
            return result

        # EP-2: 幂等保护
        if proposal_id in self._applied_proposal_ids:
            result["error"] = f"EP-2: proposal_id={proposal_id} 已被应用过，不可重复执行"
            logger.warning("[personality_state_apply_blocked] %s", result["error"])
            return result

        # 取 before / after
        before_snap = record.get("before") or {}
        after_snap = record.get("after") or {}

        # 如果 before/after 为空，尝试从 trait_changes 提取（兼容旧格式）
        if not after_snap and record.get("trait_changes"):
            for trait_name, change in record["trait_changes"].items():
                try:
                    after_snap[trait_name] = float(change.get("after", change.get("current_value", 0.0)))
                    before_snap[trait_name] = float(change.get("before", self.traits.get(trait_name, 0.5)))
                except Exception:  # noqa: BLE001
                    pass

        if not after_snap:
            result["error"] = "EvolutionRecord after 为空，无 trait 变化"
            return result

        # 应用每个 trait 变化
        any_applied = False
        for trait_name, new_value in after_snap.items():
            trait_key = str(trait_name)

            # EP-4: Identity Anchor 二次保护（检查完整 path）
            is_identity_blocked = False
            for prefix in IDENTITY_FORBIDDEN_PREFIXES:
                if trait_key.startswith(prefix):
                    result["blocked_identity"].append(trait_key)
                    logger.warning(
                        "[personality_state_identity_blocked] trait=%s prefix=%s (EP-4 二次保护)",
                        trait_key, prefix,
                    )
                    is_identity_blocked = True
                    break
            if is_identity_blocked:
                continue

            # 对正常 trait，剥离 path 前缀以匹配 traits 字典
            # e.g. "trait.creativity" → "creativity"; "interests.AI_art" → "AI_art"
            short_name = trait_key
            for strip_prefix in ("trait.", "interests.", "interest."):
                if short_name.startswith(strip_prefix):
                    short_name = short_name[len(strip_prefix):]
                    break

            old_value = self.traits.get(short_name, 0.5)
            try:
                new_v = round(min(max(float(new_value), 0.0), 1.0), 6)
            except Exception:  # noqa: BLE001
                result["error"] = f"trait {trait_key} value {new_value!r} 不是数值"
                continue

            if abs(new_v - old_value) < 1e-9:
                result["skipped_no_change"].append(trait_key)
            else:
                self.traits[short_name] = new_v
                result["affected_traits"][trait_key] = {
                    "before": round(old_value, 6),
                    "after": new_v,
                }
                any_applied = True

        # 如果有被 identity 拦截的 trait，记录但不阻止其他合法 trait 的应用
        if result["blocked_identity"]:
            logger.warning(
                "[personality_state_apply] identity_blocked=%s applied_others=%s",
                result["blocked_identity"], list(result["affected_traits"].keys()),
            )

        # 只有至少一个 trait 被成功修改才算 applied
        if any_applied:
            self.version += 1
            record_id = record.get("record_id", "")
            if record_id:
                self.evolution_record_ids.append(record_id)
            self._applied_proposal_ids.add(proposal_id)
            result["applied"] = True
            logger.info(
                "[personality_state_evolved] version=%d proposal=%s affected=%s",
                self.version, proposal_id, list(result["affected_traits"].keys()),
            )
        else:
            result["error"] = result["error"] or "no trait was actually changed"

        return result

    # ============================================================
    # 只读查询
    # ============================================================
    def get_trait(self, name: str, default: float = 0.5) -> float:
        return self.traits.get(name, default)

    def snapshot(self) -> Dict[str, Any]:
        """返回当前状态的只读快照（深拷贝）。"""
        return {
            "traits": dict(self.traits),
            "version": self.version,
            "evolution_record_ids": list(self.evolution_record_ids),
            "applied_proposal_count": len(self._applied_proposal_ids),
        }

    def has_proposal_been_applied(self, proposal_id: str) -> bool:
        return proposal_id in self._applied_proposal_ids

    # ============================================================
    # R2.7.4：快照序列化 / 反序列化（跨进程存活）
    # ============================================================
    # 约束：
    #   - to_dict() 必须包含 _applied_proposal_ids（不然恢复后重复 apply → EP-2 报错）
    #   - from_dict() 必须是安全恢复：trait 必须在 [0,1]；version 不可回退（版本单调）
    #   - from_dict() 不接受 identity.* / manifesto.* 的 trait key（恢复也受 EP-4 保护）
    # ============================================================
    def to_dict(self) -> Dict[str, Any]:
        """序列化完整 PersonalityState（持久化用）。"""
        return {
            "schema_version": 1,
            "traits": {k: round(float(v), 6) for k, v in self.traits.items()},
            "version": int(self.version),
            "evolution_record_ids": list(self.evolution_record_ids),
            "applied_proposal_ids": list(self._applied_proposal_ids),
            "serialized_at_ms": int(__import__("time").time() * 1000),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, current_version_ceiling: Optional[int] = None) -> "PersonalityState":
        """安全反序列化 → PersonalityState。

        参数：
            current_version_ceiling: 可选。若传入，则加载的 data.version 必须 >= ceiling。
                否则视为"版本回退攻击"，返回 ceiling 时的 state（拒绝回退）。
                （用于 RPG-3 稳定性保护：load 时不能回退到更旧的版本。）
        """
        if not isinstance(data, dict):
            raise ValueError("PersonalityState.from_dict: data 必须是 dict")

        traits_raw = data.get("traits") or {}
        if not isinstance(traits_raw, dict):
            raise ValueError("PersonalityState.from_dict: traits 必须是 dict")

        version = int(data.get("version", 0) or 0)
        if version < 0:
            version = 0

        # 版本单调保护：如果传入 ceiling（当前运行时的 version），不能比它低
        if current_version_ceiling is not None and version < int(current_version_ceiling):
            # 拒绝回退：返回一个基于空 version 的 PersonalityState（调用者通常会放弃本次 load，保留现有 state）
            # 这里不抛异常，而是返回 None 更合适？ 为了类型稳定，抛 ValueError("version rollback detected") 由上层处理
            raise ValueError(
                f"PersonalityState.from_dict: version rollback detected "
                f"(data.version={version} < ceiling={current_version_ceiling})"
            )

        # 清洗 traits：
        #   - key 不允许 identity / manifesto 前缀（EP-4 恢复阶段也保护）
        #   - value 必须 clamp 到 [0, 1]
        cleaned_traits: Dict[str, float] = {}
        for k, v in traits_raw.items():
            key = str(k)
            # EP-4：identity.* / manifesto.* 即使在快照里存了，也不恢复
            skip = False
            for prefix in IDENTITY_FORBIDDEN_PREFIXES:
                if key.startswith(prefix):
                    logger.warning("[personality_state_from_dict] skip EP-4 forbidden key: %s", key)
                    skip = True
                    break
            if skip:
                continue
            # strip trait. / interest. 前缀（与 apply_evolution 一致）
            short = key
            for strip_prefix in ("trait.", "interests.", "interest."):
                if short.startswith(strip_prefix):
                    short = short[len(strip_prefix):]
                    break
            try:
                val = round(min(max(float(v), 0.0), 1.0), 6)
            except Exception:  # noqa: BLE001
                continue
            cleaned_traits[short] = val

        # 如果 traits 空（损坏文件），用默认基线（避免 Personality 完全空）
        if not cleaned_traits:
            default_ps = cls()
            cleaned_traits = dict(default_ps.traits)

        evolution_ids = [str(x) for x in list(data.get("evolution_record_ids") or []) if x]
        proposal_ids = {str(x) for x in list(data.get("applied_proposal_ids") or []) if x}

        instance = cls(
            traits=cleaned_traits,
            version=version,
            evolution_record_ids=evolution_ids,
            _applied_proposal_ids=proposal_ids,
        )
        return instance


# ============================================================
# 全局单例（Runtime 唯一来源）
# ============================================================
_global_state: Optional[PersonalityState] = None


def get_personality_state() -> PersonalityState:
    global _global_state
    if _global_state is None:
        _global_state = PersonalityState()
    return _global_state


def reset_personality_state() -> PersonalityState:
    """测试用：重置全局状态。"""
    global _global_state
    _global_state = PersonalityState()
    return _global_state

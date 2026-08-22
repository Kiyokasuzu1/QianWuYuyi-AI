# -*- coding: utf-8 -*-
"""
src/admin/selfmodel_consumer.py

Phase 3.5.3 Step 1: SelfModel Consumer（旁路最小接入）
Phase 3.6.3: 接入 GrowthProposalNormalizer（仅 Translator 层，SelfModelConsumer 下游只处理 canonical）

职责：
- GrowthProposal(dict) → PCR dict → SelfModelAdapter.apply_pcr() → 持久化
- 不接 RuntimeCore / RuntimeBridge / Orchestrator / GrowthPipeline
- 不修改 Personality / Growth 任何代码
- 兼容两套 GrowthProposal schema：
    A) src/contracts/growth_schema.py::GrowthProposal
       字段：id / proposed_changes(List[ChangeItem]) / evidence_ids / confidence / evaluator_meta
    B) src/growth/proposal/proposal.py::GrowthProposal
       字段：proposal_id / affected_dimensions / evidence / confidence / metadata

Phase 3.6.3 变化（仅 GrowthProposalTranslator 内部）：
- B schema 输入时先经过 src.contracts.proposal_normalizer.normalize_to_canonical() 归一化
- Translator 下游只看到 canonical dict，不再 if proposal_id / elif id 双判断
- canonical 输入直通
- 行为兼容：output PCR 与原 Translator 字段一致（_source_schema 保留 "proposal" / "growth_schema"）
- GrowthProposalTranslator 已被标记为 deprecated；新业务请使用 NormalizedProposalTranslator

设计原则：
- 纯 dict 接口（不强制 import 任何 proposal class）
- 容错优先：字段缺失 / 类型错误均隔离
- 单步可独立运行（self-bootstrap adapter + persistence + bootstrap）
- 默认 data_dir 指向临时目录；不污染正式 data/self_model
- 进程内 proposal_id 去重（同一 proposal 不重复写入）
- Normalizer 失败时回退旧双分支逻辑，保证系统不因归一化失败而停摆
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================================
# 路径常量
# ============================================================

DEFAULT_DATA_DIR = "data/self_model"

BELIEFS_FILENAME = "beliefs.jsonl"
HISTORY_FILENAME = "history.jsonl"
REFLECTION_FILENAME = "reflection.jsonl"


# ============================================================
# GrowthProposalTranslator
# ============================================================

class GrowthProposalTranslator:
    """
    GrowthProposal dict → PCR dict 转换器。

    .. deprecated::
        Phase 3.6.3 起已标记为 deprecated。
        新业务模块请使用 ``NormalizedProposalTranslator``（位于
        ``src.admin.normalized_proposal_translator``）作为统一入口，
        其内部已完成 A/B schema → canonical → PCR 的归一化流程。

        本类保留的原因：
        - 旧调用方仍可继续使用
        - 提供回滚路径
        - 后续 Phase 3.6.4 可彻底删除

    兼容 Schema A（growth_schema.GrowthProposal）和 Schema B（proposal.GrowthProposal）。
    通过字段存在性自动判定 schema 形态。

    Phase 3.6.3 行为变化：
    - B schema 输入时先经过 ``src.contracts.proposal_normalizer.normalize_to_canonical()``
    - 下游只处理 canonical dict，无 ``if "proposal_id" / elif "id"`` 双重判断
    - Normalizer 失败时回退到内嵌的双分支逻辑，保证兼容
    - 对外 PCR 字段保持兼容（_source_schema 仍记录原始 schema）
    """

    # Phase 3.6.3: deprecation 标记
    __deprecated__ = True
    __deprecated_since__ = "3.6.3"
    __deprecated_replacement__ = "src.admin.normalized_proposal_translator.NormalizedProposalTranslator"

    # Schema 标识
    SCHEMA_UNKNOWN = "unknown"
    SCHEMA_A = "growth_schema"   # id / proposed_changes / evidence_ids / evaluator_meta
    SCHEMA_B = "proposal"        # proposal_id / affected_dimensions / evidence / metadata

    # Phase C.4.7.1: 完整路径前缀黑名单(防止 core_identity.* 等非法路径
    # 通过 _extract_trait_name 取最后一段后绕过 ALLOWED 校验)
    # 任何 proposed_changes 中 path 命中以下前缀的 change_item 都会被直接拒绝
    FORBIDDEN_PATH_PREFIXES: frozenset = frozenset({
        "core_identity",
        "origin_identity",
        "identity",
        "forbidden_core",
    })

    @classmethod
    def _is_forbidden_path(cls, path: Any) -> bool:
        """
        Phase C.4.7.1: 检查完整 path 是否命中禁止前缀。

        返回 True 即视为非法 CoreIdentity / Identity 路径,
        对应 change_item 在 _translate_canonical 中会被直接拒绝。

        与 _extract_trait_name 配合使用:
        - _extract_trait_name 仅取最后一段(用于 trait 名)
        - _is_forbidden_path 检查完整路径(用于安全校验)

        大小写与下划线绕过防御:
        - 路径会被小写化,前缀列表也会小写化
        - 同时移除下划线以防 CamelCase 绕过(例如 CoreIdentity → coreidentity)
        """
        if not isinstance(path, str) or not path:
            return False
        # 去除前导空白
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
            # 完整前缀匹配: 'core_identity.*' 或 'core_identity' 本身
            # 同时支持下划线归一化后的匹配
            if pl == pl_prefix or pl.startswith(pl_prefix + "."):
                return True
            if pl_normalized == pl_prefix_normalized or pl_normalized.startswith(
                pl_prefix_normalized + "."
            ):
                return True
        return False

    @classmethod
    def detect_schema(cls, proposal: Any) -> str:
        """
        探测 proposal 所属 schema 形态。

        判定规则：
        - 非 dict → unknown
        - 含 "proposal_id" 字段 → Schema B
        - 含 "id" 字段（且为 str） → Schema A
        - 其他 → unknown
        """
        if not isinstance(proposal, dict):
            return cls.SCHEMA_UNKNOWN
        if "proposal_id" in proposal:
            return cls.SCHEMA_B
        if "id" in proposal and isinstance(proposal.get("id"), str):
            return cls.SCHEMA_A
        return cls.SCHEMA_UNKNOWN

    @classmethod
    def _extract_trait_name(cls, path: Any) -> str:
        """从 path 中提取 trait 名称（最后一段）。"""
        if not isinstance(path, str) or not path:
            return "unknown"
        parts = [p for p in path.split(".") if p]
        if not parts:
            return "unknown"
        return parts[-1]

    @classmethod
    def _safe_float(cls, v: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if v is None:
                return default
            return float(v)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _build_growth_records(
        cls,
        *,
        proposal_id: str,
        source_event_id: str,
        confidence: float,
        trait_changes: Dict[str, Dict[str, float]],
        evaluator_meta: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        构造 PCR.growth_records（List[dict]）。

        字段契约（apply_pcr 需要）：
        - growth_signal: str
        - record_id: str
        - confidence: float
        - reason: str
        - affected_dimensions: dict
        """
        records: List[Dict[str, Any]] = []
        for trait, ch in (trait_changes or {}).items():
            try:
                signal = f"proposal:{proposal_id} trait:{trait}"
                rec: Dict[str, Any] = {
                    "record_id": f"gr_{uuid.uuid4().hex[:8]}",
                    "source_event_id": source_event_id or proposal_id,
                    "growth_signal": signal,
                    "source_type": "preference",
                    "growth_level": "trait" if abs(float(ch.get("delta", 0.0) or 0.0)) > 0.01 else "context",
                    "affected_dimensions": {trait: float(ch.get("delta", 0.0) or 0.0)},
                    "confidence": float(confidence or 0.5),
                    "reason": str(evaluator_meta.get("reason_summary") or f"from_proposal:{proposal_id}"),
                    "created_at": cls._now_iso(),
                }
                records.append(rec)
            except Exception:
                continue
        return records

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"

    @classmethod
    def translate(
        cls,
        proposal: Any,
        source_insight_id: Optional[str] = None,
        extra_evaluator_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        将 GrowthProposal dict 转换为 SelfModelAdapter.apply_pcr() 所需 PCR dict。

        Phase 3.6.3 行为：
        - 检测 proposal 所属 schema（A / B / unknown）
        - 如果是 B schema：先调用 ``src.contracts.proposal_normalizer.normalize_to_canonical``
          归一化为 canonical dict
        - 后续处理只针对 canonical dict（_translate_canonical），不再有双分支判断
        - Normalizer 异常时回退到内嵌双分支逻辑（系统不停摆）
        - _source_schema 始终记录原始 schema 标签（"growth_schema" / "proposal"），
          保证审计和测试可追溯

        Returns:
            dict，结构如下：
            {
                "request_id": str,
                "source_proposal_id": str,
                "source_insight_id": str|None,
                "evolution_record": {
                    "record_id": str,
                    "trait_changes": {trait: {"delta": float, "before": float}},
                    ...
                },
                "growth_records": List[dict],
                "confidence": float,
                "evidence_count": int,
                "evaluator_meta": dict,
                "reason": str,
            }

        Raises:
            ValueError: proposal 不是 dict 或 schema 无法识别或 proposal_id 缺失
        """
        # 1) 基础校验
        if not isinstance(proposal, dict):
            raise ValueError(f"proposal must be dict, got {type(proposal).__name__}")

        schema = cls.detect_schema(proposal)
        if schema == cls.SCHEMA_UNKNOWN:
            raise ValueError(
                "proposal schema 无法识别：缺少 id (Schema A) 或 proposal_id (Schema B)"
            )

        # 2) Phase 3.6.3: B schema 走 Normalizer 归一化
        #    - 归一化失败时回退到旧双分支逻辑（隔离异常）
        #    - 归一化后 proposal 形态 = canonical（7 字段 + extra）
        normalized = False
        if schema == cls.SCHEMA_B:
            try:
                from src.contracts.proposal_normalizer import normalize_to_canonical
                canonical = normalize_to_canonical(proposal)
                # 把原始 schema 标签写回 evaluator_meta（保持 _source_schema 兼容）
                if isinstance(canonical, dict):
                    em = canonical.get("evaluator_meta")
                    if not isinstance(em, dict):
                        em = {}
                        canonical["evaluator_meta"] = em
                    em["_source_schema"] = cls.SCHEMA_B
                proposal = canonical
                schema = cls.SCHEMA_A  # 后续统一走 A 路径
                normalized = True
                logger.debug(
                    "GrowthProposalTranslator: B schema 已通过 Normalizer 归一化"
                )
            except Exception as e:
                logger.warning(
                    f"GrowthProposalTranslator: Normalizer 归一化失败（已回退旧路径）: {e}"
                )
                # 保持原始 proposal + schema 不变，走旧双分支
                pass

        # 3) 统一交给 _translate_canonical 处理（不再有双分支判断扩散到下游）
        return cls._translate_canonical(
            proposal=proposal,
            schema=schema,
            source_insight_id=source_insight_id,
            extra_evaluator_meta=extra_evaluator_meta,
            _normalized=normalized,
        )

    @classmethod
    def _translate_canonical(
        cls,
        proposal: Dict[str, Any],
        schema: str,
        source_insight_id: Optional[str] = None,
        extra_evaluator_meta: Optional[Dict[str, Any]] = None,
        _normalized: bool = False,
    ) -> Dict[str, Any]:
        """
        处理 canonical dict（仅 A 形态 / 7 字段）→ PCR。

        Phase 3.6.3 接入 Normalizer 后，下游仅看 canonical dict。
        不再有 ``if "proposal_id" / elif "id"`` 双重判断。

        Args:
            proposal: canonical dict（含 id / proposed_changes / evidence_ids /
                confidence / evaluator_meta / status / timestamp）
            schema: 原始 schema 标签（"growth_schema" / "proposal" / "unknown"），
                用于设置 _source_schema
            _normalized: 内部标记，标识是否经过 Normalizer 归一化（用于未来扩展）

        Returns:
            PCR dict
        """
        # 1) 提取 proposal_id / source_event_id
        proposal_id = str(proposal.get("id", "") or "")
        source_event_id = str(proposal.get("source_event_id", "") or proposal_id)
        confidence = float(proposal.get("confidence", 0.5) or 0.5)
        evidence_ids = proposal.get("evidence_ids") or []
        evidence_count = len(evidence_ids) if isinstance(evidence_ids, list) else 0
        evaluator_meta = dict(proposal.get("evaluator_meta") or {})
        raw_reason = (
            proposal.get("reason")
            or evaluator_meta.get("reason_summary")
            or "growth_proposal_change_request"
        )
        source_insight_id = (
            source_insight_id
            or evaluator_meta.get("source_insight_id")
            or proposal.get("source_insight_id")
        )

        # 2) 提取 trait_changes（来自 proposed_changes）
        trait_changes: Dict[str, Dict[str, float]] = {}
        proposed = proposal.get("proposed_changes") or []
        # Phase C.4.7.1: 记录被拒绝的非法路径(用于审计)
        rejected_paths: List[str] = []
        rejected_details: List[Dict[str, Any]] = []
        if isinstance(proposed, list):
            for ci in proposed:
                if not isinstance(ci, dict):
                    continue
                path = ci.get("path", "")
                # Phase C.4.7.1: 完整路径前缀安全检查
                # 任何命中 FORBIDDEN_PATH_PREFIXES 的路径直接拒绝
                if cls._is_forbidden_path(path):
                    rejected_paths.append(str(path))
                    rejected_details.append({
                        "path": str(path),
                        "reason": "forbidden_path_prefix",
                        "prefixes": sorted(cls.FORBIDDEN_PATH_PREFIXES),
                    })
                    continue
                trait = cls._extract_trait_name(path)
                if trait == "unknown":
                    continue
                before = cls._safe_float(ci.get("before"))
                after = cls._safe_float(ci.get("after"))
                if after is not None and before is not None:
                    delta = round(after - before, 5)
                elif after is not None:
                    delta = round(after, 5)
                else:
                    delta = 0.0
                trait_changes[trait] = {
                    "delta": delta,
                    "before": before if before is not None else 0.5,
                }

        # 3) proposal_id 兜底
        if not proposal_id:
            proposal_id = f"prop_unknown_{uuid.uuid4().hex[:8]}"

        # 4) 合并额外 evaluator_meta
        if extra_evaluator_meta:
            evaluator_meta.update(dict(extra_evaluator_meta))
        # 记录 schema 来源，便于溯源
        # 优先保留 Normalizer / 业务方已写入的 _source_schema（如有），否则用 schema
        if "_source_schema" not in evaluator_meta:
            evaluator_meta["_source_schema"] = schema
        evaluator_meta.setdefault("_translator", "GrowthProposalTranslator@phase_3_5_3")
        # Phase 3.6.3: 标记是否经过 Normalizer（便于调试与回滚排查）
        if _normalized:
            evaluator_meta["_normalized"] = True
            evaluator_meta.setdefault(
                "_normalizer_version", "GrowthProposalNormalizer@phase_3_6_2"
            )
        # Phase C.4.7.1: 记录被拒绝的非法路径(用于审计)
        if rejected_paths:
            evaluator_meta["_rejected_paths"] = rejected_paths
            evaluator_meta["_rejected_path_details"] = rejected_details
            evaluator_meta["_security_patch_version"] = "phase_c4_7_1"

        # 5) 构造 evolution_record（按 PersonalityAdapter.map_proposal_to_evolution_record 风格）
        evolution_record: Dict[str, Any] = {
            "record_id": f"rec_{uuid.uuid4().hex[:8]}",
            "timestamp": cls._now_iso(),
            "trigger_candidates": list(trait_changes.keys()),
            "source_growth_records": [],
            "trait_changes": trait_changes,
            "approved": False,
            "confidence": confidence,
            "decision_reason": f"translated_from_{schema}",
            "rejection_reasons": {},
            "rejected_dimensions": [],
            "evolution_level": "proposal",
            "requires_validation": False,
        }

        # 6) 构造 growth_records
        growth_records = cls._build_growth_records(
            proposal_id=proposal_id,
            source_event_id=source_event_id,
            confidence=confidence,
            trait_changes=trait_changes,
            evaluator_meta=evaluator_meta,
        )

        return {
            "request_id": f"pcr_{uuid.uuid4().hex[:10]}",
            "source_proposal_id": proposal_id,
            "source_insight_id": source_insight_id,
            "evolution_record": evolution_record,
            "growth_records": growth_records,
            "confidence": confidence,
            "evidence_count": int(evidence_count),
            "evaluator_meta": evaluator_meta,
            "reason": str(raw_reason),
        }


# ============================================================
# SelfModelConsumer
# ============================================================

class SelfModelConsumer:
    """
    旁路 SelfModel 消费器。

    流程：
        GrowthProposal dict
            ↓ GrowthProposalTranslator.translate
        PCR dict
            ↓ SelfModelAdapter.apply_pcr
        内存 SelfBelief / SelfHistory / SelfReflection
            ↓ adapter.save_state
        JSONL 文件

    用法：
        consumer = SelfModelConsumer(data_dir="/tmp/some_dir")
        result = consumer.process(proposal_dict)
        # 关闭：consumer.close()

    约束：
    - 不依赖 RuntimeCore / RuntimeBridge / Orchestrator
    - 不依赖 GrowthPipeline
    - 自管 SelfModelAdapter + SelfModelBootstrap + SelfModelPersistence
    - 默认不持久化 GrowthLimiter / SelfModelUpdater / SelfModelManager
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        *,
        actor: str = "selfmodel_consumer@phase_3_5_3",
        enable_dedup: bool = True,
        auto_save: bool = True,
        bootstrap: bool = True,
    ) -> None:
        """
        Args:
            data_dir: SelfModel 持久化目录；None 时使用临时目录（推荐测试用）
            actor: PCR 调用方标识
            enable_dedup: 是否启用 proposal_id 去重（默认 True）
            auto_save: apply_pcr 后是否自动 save_state
            bootstrap: 是否在初始化时 attach persistence + load_state
        """
        # 1) data_dir 处理
        if data_dir is None:
            # 默认用临时目录，避免污染正式 data
            self._temp_dir: Optional[str] = None
            self._data_dir = Path(tempfile.mkdtemp(prefix="phase_3_5_3_selfmodel_"))
            self._temp_dir = str(self._data_dir)
            self._owns_data_dir = True
        else:
            self._data_dir = Path(data_dir)
            self._temp_dir = None
            self._owns_data_dir = False
        # ensure exists
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"SelfModelConsumer: cannot create data dir {self._data_dir}: {e}")

        # 2) Adapter + Bootstrap
        self._adapter: Any = None
        self._bootstrap: Any = None
        self._persistence: Any = None

        from src.personality.self_model_adapter import SelfModelAdapter
        self._adapter = SelfModelAdapter(actor=actor)
        self._adapter_cls_ref = SelfModelAdapter

        if bootstrap:
            try:
                from src.runtime.self_model_bootstrap import SelfModelBootstrap
                self._bootstrap = SelfModelBootstrap(data_dir=str(self._data_dir))
                env = self._bootstrap.bootstrap(self._adapter)
                self._persistence = getattr(self._bootstrap, "_persistence", None)
            except Exception as e:
                # 失败隔离：consumer 仍可继续，但 persistence 不可用
                logger.warning(f"SelfModelConsumer: bootstrap failed (已隔离): {e}")
                self._bootstrap = None
                self._persistence = None

        # 3) 状态
        self._actor = actor
        self._enable_dedup = bool(enable_dedup)
        self._auto_save = bool(auto_save)
        self._processed_ids: Set[str] = set()
        self._stats: Dict[str, int] = {
            "proposals_received": 0,
            "proposals_translated": 0,
            "pcrs_applied": 0,
            "dedup_skipped": 0,
            "save_calls": 0,
        }

    # ============================================================
    # 属性
    # ============================================================

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def adapter(self) -> Any:
        return self._adapter

    @property
    def persistence(self) -> Any:
        return self._persistence

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    @property
    def processed_ids(self) -> Set[str]:
        return set(self._processed_ids)

    # ============================================================
    # 公开 API
    # ============================================================

    def process(self, proposal: Any) -> Dict[str, Any]:
        """
        处理一个 GrowthProposal dict（单步闭环）。

        Phase 3.6.3 变化：
        - 使用 NormalizedProposalTranslator.translate 作为标准入口
        - 所有 proposal（A/B）都先归一化为 canonical，再走 PCR 翻译
        - 下游不再有 schema 双分支判断
        - 旧 GrowthProposalTranslator 入口仍兼容（deprecated 标记）

        Returns:
            {
                "proposal_id": str,
                "pcr_generated": bool,
                "selfmodel_updated": bool,
                "files_written": {
                    "beliefs": str|None,
                    "history": str|None,
                    "reflection": str|None,
                },
                "dedup_skipped": bool,
                "error": str|None,
                "envelope": dict|None,    # apply_pcr 返回的 envelope
            }
        """
        self._stats["proposals_received"] += 1

        result: Dict[str, Any] = {
            "proposal_id": "",
            "pcr_generated": False,
            "selfmodel_updated": False,
            "files_written": {
                "beliefs": None,
                "history": None,
                "reflection": None,
            },
            "dedup_skipped": False,
            "error": None,
            "envelope": None,
        }

        # 1) 翻译（Phase 3.6.3: 走 NormalizedProposalTranslator 统一入口）
        try:
            from src.admin.normalized_proposal_translator import (
                NormalizedProposalTranslator,
            )
            pcr = NormalizedProposalTranslator.translate(proposal)
        except ValueError as e:
            result["error"] = f"translate_failed: {e}"
            return result
        except Exception as e:
            result["error"] = f"translate_exception: {e}"
            return result

        proposal_id = pcr.get("source_proposal_id", "")
        result["proposal_id"] = proposal_id
        result["pcr_generated"] = True
        self._stats["proposals_translated"] += 1

        # 2) 去重
        if self._enable_dedup and proposal_id in self._processed_ids:
            result["dedup_skipped"] = True
            result["error"] = "duplicate_proposal_id"
            self._stats["dedup_skipped"] += 1
            return result

        # G-1.2: 治理模式 —— apply_pcr 改写为治理提案（不直接修改文件）
        try:
            from src.personality.self_model_governance import (
                is_self_model_governance_enabled,
            )

            if is_self_model_governance_enabled():
                return self._route_pcr_to_governance(result, pcr, proposal_id)
        except Exception as _gov_check_exc:  # noqa: BLE001
            logger.warning(
                "SelfModelConsumer: governance check failed (已隔离): %s",
                _gov_check_exc,
            )

        # 3) apply_pcr
        if self._adapter is None:
            result["error"] = "adapter_unavailable"
            return result
        try:
            from src.governance.write_path_registry import warn_deprecated_once

            warn_deprecated_once(
                "selfmodel_consumer.admin_bypass",
                "[G-0 deprecated] SelfModelConsumer 旁路 apply_pcr+save_state 写 self_model 文件, "
                "无审批证明。迁移计划: G-1 引导至 governance review 审批流。",
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            envelope = self._adapter.apply_pcr(pcr)
        except Exception as e:
            result["error"] = f"apply_pcr_exception: {e}"
            return result

        result["envelope"] = envelope
        applied = bool(envelope.get("applied"))
        if applied:
            self._stats["pcrs_applied"] += 1

        # 4) 记录 proposal_id（即便 apply 失败也记录，避免失败重试导致重复副作用）
        if proposal_id:
            self._processed_ids.add(proposal_id)

        # 5) save_state
        if self._auto_save and self._persistence is not None:
            try:
                save_result = self._adapter.save_state(note=f"consumer:{proposal_id}")
                self._stats["save_calls"] += 1
                # 校验文件存在
                if save_result.get("beliefs"):
                    result["files_written"]["beliefs"] = str(
                        self._data_dir / BELIEFS_FILENAME
                    )
                if save_result.get("history"):
                    result["files_written"]["history"] = str(
                        self._data_dir / HISTORY_FILENAME
                    )
                if save_result.get("reflections"):
                    result["files_written"]["reflection"] = str(
                        self._data_dir / REFLECTION_FILENAME
                    )
            except Exception as e:
                result["error"] = f"save_state_exception: {e}"

        result["selfmodel_updated"] = applied
        return result

    def _route_pcr_to_governance(
        self,
        result: Dict[str, Any],
        pcr: Dict[str, Any],
        proposal_id: str,
    ) -> Dict[str, Any]:
        """G-1.2: PCR → SelfModelChangeProposal → 治理存储 pending（零文件写入）。

        不调用 apply_pcr / save_state；identity 级按政策 DENY 跳过。
        """
        try:
            from src.personality.self_model_governance import SelfModelGovernancePolicy
            from src.personality.self_model_updater import SelfModelUpdater
            from src.growth.proposal.storage import (
                build_self_model_governance_proposal,
                get_proposal_storage,
            )

            policy = SelfModelGovernancePolicy()
            updater = SelfModelUpdater()
            storage = get_proposal_storage()
            persisted = 0
            for growth_record in pcr.get("growth_records") or []:
                decision = policy.evaluate(growth_record)
                if decision.action.value == "deny":
                    continue
                sm_proposal = updater.create_proposal_from_growth(growth_record)
                if sm_proposal is None:
                    continue
                governance_proposal = build_self_model_governance_proposal(
                    source_event_id=str(proposal_id or ""),
                    confidence=float(growth_record.get("confidence", 0.5) or 0.5),
                    reason=str(growth_record.get("reason", "") or "consumer_governance"),
                    self_model_payload=sm_proposal.to_dict(),
                    decision_meta={
                        "action": decision.action.value,
                        "growth_level": decision.growth_level,
                        "confidence": decision.confidence,
                        "reason": decision.reason,
                    },
                )
                if governance_proposal is None:
                    continue
                storage.save(governance_proposal)
                persisted += 1
            result["governed"] = True
            result["governance_proposals"] = persisted
            result["selfmodel_updated"] = False
            result["error"] = None if persisted else "governance_no_proposals"
            return result
        except Exception as _gov_exc:  # noqa: BLE001
            result["governed"] = False
            result["error"] = f"governance_route_failed: {_gov_exc}"
            return result

    def process_batch(self, proposals: List[Any]) -> List[Dict[str, Any]]:
        """批量处理 proposals；逐个 process，单点失败不阻断后续。"""
        out: List[Dict[str, Any]] = []
        for p in proposals:
            try:
                out.append(self.process(p))
            except Exception as e:
                out.append({
                    "proposal_id": "",
                    "pcr_generated": False,
                    "selfmodel_updated": False,
                    "files_written": {"beliefs": None, "history": None, "reflection": None},
                    "dedup_skipped": False,
                    "error": f"process_exception: {e}",
                    "envelope": None,
                })
        return out

    def close(self) -> None:
        """关闭 consumer；如 data_dir 由 consumer 创建，则清理临时目录。"""
        if self._owns_data_dir and self._temp_dir:
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass

    # ============================================================
    # 上下文管理
    # ============================================================

    def __enter__(self) -> "SelfModelConsumer":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ============================================================
# 模块级便捷函数
# ============================================================

def consume_one(
    proposal: Any,
    data_dir: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    一行消费接口：创建临时 consumer → process → 关闭。

    适合测试 / 一次性调用；不要在长生命周期里使用（会反复创建/销毁 consumer）。
    """
    consumer = SelfModelConsumer(data_dir=data_dir, **kwargs)
    try:
        return consumer.process(proposal)
    finally:
        consumer.close()


__all__ = [
    "GrowthProposalTranslator",
    "SelfModelConsumer",
    "consume_one",
    "BELIEFS_FILENAME",
    "HISTORY_FILENAME",
    "REFLECTION_FILENAME",
    "DEFAULT_DATA_DIR",
]

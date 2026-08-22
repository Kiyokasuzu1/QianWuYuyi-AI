"""
情绪管理器 (EmotionManager) — Phase 9.7 v2 最终版
集成情绪轨迹持久化、记忆桥接、分析计数器持久化。

P2.3-B.7：情绪 mutation 治理迁移（flag 默认 False = 旧路径完全一致；
开启后 process_event 直写改经 EmotionMutationAdapter → MutationGateway）。
"""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_decay import EmotionDecay
from src.emotion.emotion_context_provider import EmotionContextProvider
from src.emotion.emotion_context import EmotionContext
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_delta import EmotionDelta
from src.emotion.emotion_engine import EmotionEngine
from src.emotion.emotion_repository import EmotionRepository
from src.emotion.emotional_trace import EmotionalTrace
from src.emotion.emotion_trace_repository import EmotionTraceRepository
from src.emotion.emotion_memory_bridge import EmotionMemoryBridge
from src.emotion.mutation_adapter import is_emotion_mutation_gateway_enabled
from src.governance.write_path_registry import warn_deprecated_once

logger = logging.getLogger(__name__)

# ============================================================
# R-1.3.b: Emotion 治理提案链本地开关（默认 False = legacy 直写不变；
# 与 B.7 mutation gateway flag 完全独立, 不翻转任何全局治理 flag）
# ============================================================
_EMOTION_GOVERNANCE_ENABLED = False


def is_emotion_governance_enabled() -> bool:
    """R-1.3.b: Emotion 治理提案模式是否开启（默认 False = legacy）。"""
    return _EMOTION_GOVERNANCE_ENABLED


def set_emotion_governance_enabled(enabled: bool) -> None:
    """R-1.3.b: 显式开启/关闭（测试/装配方调用; 生产默认关闭）。"""
    global _EMOTION_GOVERNANCE_ENABLED
    _EMOTION_GOVERNANCE_ENABLED = bool(enabled)

logger = logging.getLogger(__name__)


class EmotionManager:
    def __init__(
        self,
        repository: EmotionRepository = None,
        trace_repository: EmotionTraceRepository = None,
        counter_file: str = "data/emotion_analysis_counter.json",
        mutation_adapter=None,
    ):
        self.repository = repository or EmotionRepository()
        self.trace_repository = trace_repository or EmotionTraceRepository()
        self.state = self.repository.load()
        self.decay = EmotionDecay()
        self.engine = EmotionEngine()
        self.provider = EmotionContextProvider()
        self.bridge = EmotionMemoryBridge()

        # 分析计数器持久化
        self._counter_file = Path(counter_file)
        self._analysis_counter: int = self._load_counter()

        # P2.3-B.7：治理迁移适配器（按需构造，flag 关闭时完全不参与）
        self._mutation_adapter = mutation_adapter

        # R-1.4: 进程内短窗口重复事件防护（防 5B 双引擎对同一消息双处理;
        # 不改 EmotionEvent 结构; 不同事件不受影响）
        self._last_event_sig: Optional[str] = None
        self._last_event_ts: float = 0.0

    # ----------------- 情绪事件 -----------------
    def _record_event_signature(self, event: EmotionEvent) -> bool:
        """R-1.4: 记录事件签名并判定短窗口（2s）重复（每次调用都更新记录）。

        签名 = sha1(event_type|description|intensity) 前 12 位;
        所有 process_event 调用都会更新签名记录, 跳过仅当调用方
        经 skip_if_recent_duplicate=True 显式要求时生效——
        普通调用行为完全不变。
        """
        try:
            import hashlib
            import time

            sig = hashlib.sha1(
                (
                    f"{getattr(event, 'event_type', '')}|"
                    f"{getattr(event, 'description', '')}|"
                    f"{float(getattr(event, 'intensity', 0.0) or 0.0):.3f}"
                ).encode("utf-8")
            ).hexdigest()[:12]
        except Exception:  # noqa: BLE001
            return False
        now = time.time()
        is_dup = sig == self._last_event_sig and (now - self._last_event_ts) < 2.0
        self._last_event_sig = sig
        self._last_event_ts = now
        return is_dup

    def process_event(
        self,
        event: EmotionEvent,
        memory_id: Optional[str] = None,
        skip_if_recent_duplicate: bool = False,
    ):
        """处理情绪事件，更新内部状态并持久化轨迹。

        P2.3-B.7 治理分支（emotion_mutation_gateway_enabled，默认 False）：
        - False（默认）→ 旧路径，与迁移前逐行一致
        - True → EmotionEvent → EmotionMutationAdapter → MutationGateway：
            ACCEPT     → 调用原执行件（apply_delta + repository.save）
            REJECT     → 不修改状态（审计留痕）
            NEED_REVIEW→ 生成 pending proposal（不修改状态）
            DEFER      → 进入延迟路径（不修改状态）
        轨迹 append 与 cognitive trace hook 两种模式一致保留。
        """
        before = self.state.to_dict()

        # R-1.4: 每次调用都记录事件签名; 仅在 skip_if_recent_duplicate=True
        # 且命中短窗口重复时跳过（用于 5B 双引擎回退场景, 默认行为不变）
        _is_dup = self._record_event_signature(event)
        if skip_if_recent_duplicate and _is_dup:
            logger.info(
                "[emotion_manager] R-1.4 短窗口重复事件已跳过: %s",
                getattr(event, "event_type", ""),
            )
            return {
                "delta": {},
                "state_before": before,
                "state_after": before,
                "trace": None,
                "dedup_skipped": True,
            }

        delta = self.engine.process(event)

        # R-1.3.b: Emotion 治理提案链（本地开关, 默认 False; 优先于 B.7 分支）
        if is_emotion_governance_enabled():
            return self._process_event_governance_proposals(
                event, delta, before, memory_id=memory_id,
            )

        if not is_emotion_mutation_gateway_enabled():
            # ── 旧路径（默认开启路径，与迁移前逐行一致）──
            warn_deprecated_once(
                "emotion_manager.process_event_legacy",
                "[G-0 deprecated] process_event 旧路径直写情绪状态(apply_delta+save), "
                "无 EmotionChangeProposal/审批/审计。迁移计划: G-2 接入 EmotionEvaluator→EmotionUpdater 链。",
            )
            self.state = self.state.apply_delta(delta)
            self.repository.save(self.state)

            trace = self.bridge.bind(event, memory_id=memory_id)
            self.trace_repository.append(trace)
            self._emit_cognitive_trace_hook()

            return {
                "delta": delta.to_dict() if hasattr(delta, "to_dict") else {},
                "state_before": before,
                "state_after": self.state.to_dict(),
                "trace": trace,
            }

        # ── 治理路径（flag=True）──
        return self._process_event_governed(event, delta, before, memory_id=memory_id)

    def _process_event_governance_proposals(
        self,
        event: EmotionEvent,
        delta: EmotionDelta,
        before: dict,
        memory_id: Optional[str] = None,
    ) -> dict:
        """R-1.3.b: delta → EmotionChangeProposal → B-store pending（零状态写入）。

        - 不直接修改 EmotionState、不落盘状态；
        - 每个非零维度生成一条合法提案（11 维全量, evidence/confidence/before/after 完整）;
        - 轨迹 append 与 cognitive trace hook 与 legacy 路径一致;
        - 事件（EmotionChangedEvent）发布由调用方（orchestrator / runtime stage 3）负责, 本方法不变。
        """
        try:
            from src.emotion.emotion_evaluator import (
                DEFAULT_RULE_CONFIDENCE,
                UNKNOWN_EVENT_CONFIDENCE,
                EVENT_RULES,
            )
            from src.emotion.emotion_change_proposal import EmotionChangeProposal
            from src.growth.proposal.proposal import GrowthProposal
            from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS
            from src.growth.proposal.storage import get_proposal_storage

            confidence = (
                DEFAULT_RULE_CONFIDENCE
                if getattr(event, "event_type", "") in EVENT_RULES
                else UNKNOWN_EVENT_CONFIDENCE
            )
            persisted = 0
            for dim in (
                "valence", "arousal", "curiosity", "anxiety", "confidence",
                "energy", "stability", "happiness", "sadness", "trust",
                "attachment",
            ):
                value = getattr(delta, dim, 0.0) or 0.0
                if abs(value) < 1e-9:
                    continue
                ecp = EmotionChangeProposal(
                    emotion_dimension=dim,
                    delta=round(float(value), 4),
                    confidence=confidence,
                    reason=(
                        f"事件类型 {getattr(event, 'event_type', '')} 触发情绪评估规则"
                        "（治理模式, 待审批）"
                    ),
                    evidence_ids=[memory_id] if memory_id else [],
                    source_event_id=str(getattr(event, "id", "") or ""),
                )
                current_value = float(getattr(self.state, dim, 0.0) or 0.0)
                governance_proposal = GrowthProposal(
                    proposal_type=PROPOSAL_TYPE["EMOTION"],
                    status=PROPOSAL_STATUS["PENDING"],
                    source="emotion_manager",
                    source_event_id=str(getattr(event, "id", "") or ""),
                    before_state={dim: round(current_value, 4)},
                    after_state={dim: round(current_value + value, 4)},
                    affected_dimensions={dim: round(float(value), 4)},
                    confidence=confidence,
                    reason=ecp.reason,
                    evidence=[memory_id] if memory_id else [],
                    metadata={
                        "emotion_proposal": ecp.to_dict(),
                        "source": "emotion_governance",
                    },
                )
                get_proposal_storage().save(governance_proposal)
                persisted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("[emotion_manager] 治理提案生成失败（已隔离）: %s", exc)
            persisted = -1

        trace = self.bridge.bind(event, memory_id=memory_id)
        self.trace_repository.append(trace)
        self._emit_cognitive_trace_hook()

        return {
            "delta": delta.to_dict() if hasattr(delta, "to_dict") else {},
            "state_before": before,
            "state_after": before,  # 治理模式零状态变化（审批后经 drain 应用）
            "trace": trace,
            "governance": {"mode": "proposal_pending", "persisted": max(persisted, 0)},
        }

    def _process_event_governed(
        self,
        event: EmotionEvent,
        delta: EmotionDelta,
        before: dict,
        memory_id: Optional[str] = None,
    ) -> dict:
        """B.7 治理路径：按维度逐项走 Gateway，裁决后才进入执行件。

        每维一个 MutationRequest（target_path=emotion.state.<dim>）；
        执行件 _apply_accepted_emotion 只被 ACCEPT 调用；
        REJECT / NEED_REVIEW / DEFER 均不修改状态（留痕在 adapter 槽位）。
        """
        adapter = self._ensure_mutation_adapter()
        dims = {
            "valence": delta.valence,
            "arousal": delta.arousal,
            "curiosity": delta.curiosity,
            "anxiety": delta.anxiety,
            "confidence": delta.confidence,
            "energy": delta.energy,
        }
        changed = [d for d, v in dims.items() if abs(v) > 1e-9]

        outcomes = []
        if changed:
            for dim in changed:
                d_value = dims[dim]
                request = adapter.from_emotion_event(
                    event,
                    dimension=dim,
                    delta=d_value,
                    before=float(getattr(self.state, dim)),
                    memory_id=memory_id,
                )
                if request is None:
                    continue
                envelope = adapter.route(
                    request,
                    apply_route=lambda req, dec, _dim=dim, _d=d_value:
                        self._apply_accepted_emotion(_dim, _d),
                )
                outcomes.append(envelope)

        trace = self.bridge.bind(event, memory_id=memory_id)
        self.trace_repository.append(trace)

        if any(o.get("applied") for o in outcomes):
            self._emit_cognitive_trace_hook()

        return {
            "delta": delta.to_dict() if hasattr(delta, "to_dict") else {},
            "state_before": before,
            "state_after": self.state.to_dict(),
            "trace": trace,
            "mutation": {
                "mode": "governed",
                "decisions": [o.get("decision") for o in outcomes],
                "applied": any(o.get("applied") for o in outcomes),
                "outcomes": outcomes,
            },
        }

    def _apply_accepted_emotion(self, dimension: str, delta_value: float) -> bool:
        """ACCEPT 裁决执行件：单维 apply_delta + repository.save（原执行件保留）。

        Gateway / Adapter 均不负责执行；本方法是被 ACCEPT 调用的执行入口。
        """
        try:
            single = EmotionDelta(**{dimension: delta_value})
            self.state = self.state.apply_delta(single)
            self.repository.save(self.state)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[emotion_manager] governed apply 失败（已隔离）dim=%s: %s",
                dimension, exc,
            )
            return False

    def _ensure_mutation_adapter(self):
        """治理路径按需构造 EmotionMutationAdapter（与注入实例共用）。"""
        if self._mutation_adapter is None:
            from src.emotion.mutation_adapter import EmotionMutationAdapter

            self._mutation_adapter = EmotionMutationAdapter()
        return self._mutation_adapter

    def get_mutation_adapter(self):
        """P2.3-B.7 治理可观测：返回 adapter（flag 关闭时可能为 None）。"""
        return self._mutation_adapter

    def _emit_cognitive_trace_hook(self):
        """Phase 7.2: Cognitive Trace hook（只读，不改 state/delta）。"""
        try:
            from src.runtime.observer.cognitive_hooks import emit_emotion_updated

            state_dict = self.state.to_dict() if hasattr(self.state, "to_dict") else {}
            emit_emotion_updated(
                state=str(state_dict.get("emotion", state_dict.get("state", ""))),
                state_cn=str(state_dict.get("emotion_cn", state_dict.get("state_cn", ""))),
                intensity=float(state_dict.get("intensity", 0.0)),
                valence=float(state_dict.get("valence", 0.0)),
                trend=str(state_dict.get("trend", "stable")),
                decay_applied=False,
                engine_version="v1",
            )
        except Exception:  # noqa: BLE001
            pass

    def update(self):
        """应用时间衰减"""
        if self.state.updated_at:
            try:
                last = datetime.fromisoformat(self.state.updated_at)
                now = datetime.now()
                seconds = (now - last).total_seconds()
                if seconds > 0:
                    warn_deprecated_once(
                        "emotion_manager.update_decay_legacy",
                        "[G-0 deprecated] decay 直写情绪状态并落盘, 无提案/审计。"
                        "迁移计划: G-2 经 EmotionUpdater decay 引擎 + 审计。",
                    )
                    self.state = self.decay.apply(self.state, seconds)
                    self.repository.save(self.state)
            except (ValueError, TypeError):
                pass

    def get_context(self, influence: float = 0.3) -> EmotionContext:
        """获取当前情绪上下文，influence 控制情绪表达强度"""
        return self.provider.build(self.state, influence=influence)

    def get_recent_traces(self, limit: int = 5) -> List[EmotionalTrace]:
        return self.trace_repository.get_recent(limit)

    # ----------------- 分析计数器 -----------------
    @property
    def analysis_counter(self) -> int:
        return self._analysis_counter

    def increment_analysis_counter(self):
        self._analysis_counter += 1
        self._save_counter()

    def reset_analysis_counter(self):
        self._analysis_counter = 0
        self._save_counter()

    def _load_counter(self) -> int:
        if self._counter_file.exists():
            with open(self._counter_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("count", 0)
        return 0

    def _save_counter(self):
        self._counter_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._counter_file, "w", encoding="utf-8") as f:
            json.dump({"count": self._analysis_counter}, f)

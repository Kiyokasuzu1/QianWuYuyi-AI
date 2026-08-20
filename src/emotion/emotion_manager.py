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

    # ----------------- 情绪事件 -----------------
    def process_event(self, event: EmotionEvent, memory_id: Optional[str] = None):
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
        delta = self.engine.process(event)

        if not is_emotion_mutation_gateway_enabled():
            # ── 旧路径（默认开启路径，与迁移前逐行一致）──
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

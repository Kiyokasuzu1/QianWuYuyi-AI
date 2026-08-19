"""
SelfModelContextProvider v8.4.2 + Phase 6.2 extension

职责：
- v8.4.2 (legacy): 从 SelfModelStore 获取当前自我模型，生成安全的 Prompt 参考片段
- Phase 6.2 (新): 聚合 Phase 6.1 的 SelfBelief / SelfHistory / SelfReflection
  通过 SelfModelRuntimeContext 输出新的 prompt 文本

约束：
- 不修改 get_context() 行为，保持向后兼容
- 新方法 get_runtime_self_context() / get_runtime_self_prompt() 默认行为降级
  为返回 None / 空字符串（不破坏旧调用方）
- 所有错误隔离为降级返回
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from src.personality.self_model_store import SelfModelStore
from src.personality.self_model_v3 import SelfModelV3

logger = logging.getLogger(__name__)


class SelfModelContextProvider:
    def __init__(self, store: SelfModelStore):
        self.store = store
        # Phase 6.2 optional: 延迟注入
        self._phase_6_2_attached: bool = False
        self._phase_6_2_runtime: Optional[Any] = None  # SelfModelRuntimeContext
        self._phase_6_2_beliefs: Any = None
        self._phase_6_2_history: Any = None
        self._phase_6_2_reflections: Any = None
        self._phase_6_2_identity_provider: Optional[Callable[[], Dict[str, Any]]] = None

    # ============================================================
    # v8.4.2 legacy interface (保持不变)
    # ============================================================

    def get_context(self) -> str:
        """
        返回用于注入 Prompt 的自我认知上下文。
        若当前无模型，返回空字符串。
        """
        model: Optional[SelfModelV3] = self.store.get_active_self_model()
        if model is None:
            return ""

        lines = []

        # 标题强调"参考"，而非强制性设定
        lines.append("【自我认知参考】")
        lines.append("以下是基于你过去经历形成的自我理解，它会影响你的表达方式，但不是绝对事实。")

        # 身份
        lines.append(f"身份：{model.identity}")

        # 性格倾向（仅列出维度名称，不暴露数值）
        if model.traits:
            trait_names = list(model.traits.keys())
            lines.append(f"当前活跃的性格维度：{', '.join(trait_names)}")

        # 信念
        if model.beliefs:
            belief_text = "；".join(model.beliefs)
            lines.append(f"你目前形成的信念：{belief_text}")

        # 成长叙事（最多展示2条，每条截断到80字，防止 Prompt 过长）
        if model.narrative_items:
            lines.append("近期重要的自我成长认知：")
            for item in model.narrative_items[-2:]:
                text = item.text
                if len(text) > 80:
                    text = text[:80] + "…"
                lines.append(f"- {text}")

        return "\n".join(lines)

    # ============================================================
    # Phase 6.2: attachment / late binding
    # ============================================================

    def attach_phase_6_2(
        self,
        beliefs: Any = None,
        history: Any = None,
        reflections: Any = None,
        identity_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        """
        Phase 6.2: 注入 Phase 6.1 的 store 与 identity 提取函数。
        可在构造 Provider 后随时调用（late binding），保持向后兼容。
        """
        self._phase_6_2_beliefs = beliefs
        self._phase_6_2_history = history
        self._phase_6_2_reflections = reflections
        self._phase_6_2_identity_provider = identity_provider
        self._phase_6_2_attached = True

    def detach_phase_6_2(self) -> None:
        self._phase_6_2_attached = False
        self._phase_6_2_beliefs = None
        self._phase_6_2_history = None
        self._phase_6_2_reflections = None
        self._phase_6_2_identity_provider = None
        self._phase_6_2_runtime = None

    def is_phase_6_2_attached(self) -> bool:
        return self._phase_6_2_attached

    # ============================================================
    # Phase 6.2: new read APIs
    # ============================================================

    def get_runtime_self_context(
        self,
        max_beliefs: int = 5,
        max_history: int = 3,
        max_reflections: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """
        Phase 6.2: 返回结构化 SelfModel runtime context dict。

        Returns:
            None 若未 attach 或 runtime context 不可用
            dict 若成功生成（即使为空内容也返回 dict）
        """
        if not self._phase_6_2_attached:
            return None
        try:
            runtime = self._ensure_runtime()
            if runtime is None:
                return None
            return runtime.build_context(
                max_beliefs=max_beliefs,
                max_history=max_history,
                max_reflections=max_reflections,
            )
        except Exception as e:
            logger.warning(f"get_runtime_self_context failed: {e}")
            return None

    def get_runtime_self_prompt(
        self,
        max_beliefs: int = 5,
        max_history: int = 3,
        max_reflections: int = 3,
    ) -> str:
        """
        Phase 6.2: 返回可直接注入 Prompt 的运行时文本片段。

        未 attach 或无内容时返回空字符串。
        """
        if not self._phase_6_2_attached:
            return ""
        try:
            runtime = self._ensure_runtime()
            if runtime is None:
                return ""
            return runtime.build_prompt_text(
                max_beliefs=max_beliefs,
                max_history=max_history,
                max_reflections=max_reflections,
            )
        except Exception as e:
            logger.warning(f"get_runtime_self_prompt failed: {e}")
            return ""

    def get_combined_context(
        self,
        max_beliefs: int = 5,
        max_history: int = 3,
        max_reflections: int = 3,
    ) -> str:
        """
        Phase 6.2: 合并 v8.4.2 legacy context + Phase 6.2 runtime prompt。

        顺序：
            [Legacy 自我认知参考]
            ---
            [Phase 6.2 Runtime 自我认知]

        未 attach Phase 6.2 时退化为仅 legacy 文本。
        """
        legacy = self.get_context() or ""
        runtime = ""
        try:
            if self._phase_6_2_attached:
                runtime = self.get_runtime_self_prompt(
                    max_beliefs=max_beliefs,
                    max_history=max_history,
                    max_reflections=max_reflections,
                )
        except Exception:
            runtime = ""

        if not runtime:
            return legacy
        if not legacy:
            return runtime
        return legacy + "\n\n---\n\n" + runtime

    # ============================================================
    # 内部
    # ============================================================

    def _ensure_runtime(self) -> Optional[Any]:
        if self._phase_6_2_runtime is not None:
            return self._phase_6_2_runtime
        try:
            from src.personality.self_model_runtime_context import SelfModelRuntimeContext
        except Exception as e:
            logger.warning(f"import SelfModelRuntimeContext failed: {e}")
            return None
        try:
            runtime = SelfModelRuntimeContext(
                beliefs=self._phase_6_2_beliefs,
                history=self._phase_6_2_history,
                reflections=self._phase_6_2_reflections,
                identity_provider=self._phase_6_2_identity_provider,
            )
            self._phase_6_2_runtime = runtime
            return runtime
        except Exception as e:
            logger.warning(f"create SelfModelRuntimeContext failed: {e}")
            return None

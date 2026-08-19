# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/providers/mock_provider.py

Phase 4.2.0: MockVisionProvider —— 架构验证用 mock provider

职责:
- 提供一个不依赖任何真实 Vision SDK 的 Provider
- 验证 VisionAdapter → Provider → VisionResult → FactBuilder 链路
- 在 OpenAI/Qwen/LLaVA 接入前用于回归测试

行为:
- 总是返回:
  description: "screen contains unknown desktop content"
  confidence: 0.5
  source_model: "mock_vision"
- 任何失败都返回 None
- 不抛异常
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from src.runtime.perception.vision.providers.base import VisionProvider
from src.runtime.perception.vision.vision_result import VisionResult
from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
)


MOCK_VISION_PROVIDER_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class MockVisionProvider(VisionProvider):
    """Mock Vision Provider(Phase 4.2.0 v1.0)。

    用于在没有真实 Vision 模型时验证架构。
    真实实现(OpenAIVisionProvider / QwenVLProvider / LLaVAProvider)
    在后续 Phase 接入;本类不依赖任何外部 SDK。
    """

    provider_name: str = "mock_vision"
    schema_version: str = MOCK_VISION_PROVIDER_SCHEMA_VERSION

    DEFAULT_DESCRIPTION = "screen contains unknown desktop content"
    DEFAULT_CONFIDENCE = 0.5

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._config: Dict[str, Any] = dict(config or {})
        self._call_count: int = 0
        self._last_result_id: Optional[str] = None

    def attach(self) -> None:
        """接入。Phase 4.2.0: mock 不需要加载任何东西。"""
        self._attached = True

    def detach(self) -> None:
        """解除接入。"""
        self._attached = False

    def analyze(
        self,
        observation: Observation,
    ) -> Optional[VisionResult]:
        """对一次 observation 返回固定 mock 结果。

        规则:
        - observation 为 None → 返回 None
        - observation.available=False → 返回 None
        - observation.kind 不在视觉类(SCREEN/CAMERA)→ 返回 None
        - 其它 → 返回固定描述
        """
        if observation is None:
            return None
        if not self._attached:
            return None
        if not observation.available:
            return None
        if not observation.kind.is_visual:
            return None

        try:
            self._call_count += 1
            desc = self._config.get(
                "default_description", self.DEFAULT_DESCRIPTION,
            )
            conf = float(
                self._config.get("default_confidence", self.DEFAULT_CONFIDENCE)
            )
            # 限制 confidence 在 [0, 1]
            conf = max(0.0, min(1.0, conf))

            result = VisionResult(
                description=desc,
                confidence=conf,
                source_model=self.provider_name,
                evidence_ids=[observation.observation_id],
                observation_id=observation.observation_id,
                metadata={
                    "phase": "4.2.0",
                    "kind": observation.kind.value,
                    "backend": "mock",
                    "call_count": self._call_count,
                },
                timestamp=_now_iso(),
            )
            self._last_result_id = result.result_id
            return result
        except Exception:  # noqa: BLE001
            return None

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self._attached,
            "provider_name": self.provider_name,
            "schema_version": self.schema_version,
            "details": {
                "phase": "4.2.0",
                "backend": "mock",
                "call_count": self._call_count,
                "last_result_id": self._last_result_id,
            },
        }


__all__ = [
    "MockVisionProvider",
    "MOCK_VISION_PROVIDER_SCHEMA_VERSION",
]

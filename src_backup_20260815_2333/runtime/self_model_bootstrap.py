"""
Phase 6.3: SelfModel Runtime Bootstrap

职责：
- 在 RuntimeCore 启动阶段自动恢复 SelfModel 状态
- 失败隔离（任何 I/O 异常均不阻塞 Runtime）
- 复用 Phase 6.2 SelfModelAdapter + SelfModelPersistence

行为契约：
- bootstrap(adapter, data_dir) → counts（已恢复的条目数）
  - 若 adapter 已有 persistence，则复用
  - 否则创建新 persistence 并 attach
  - 然后 load_state()，失败返回错误但不抛
- save(adapter) → bool
  - 调用 adapter.save_state()；失败隔离
- get_status() → dict
  - 返回当前 self_model_adapter / persistence / 状态摘要

约束：
- 不修改 SelfModelAdapter / SelfModelPersistence 任何 API
- 不重写 RuntimeCore（RuntimeCore 在适当时机调用本模块）
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


DEFAULT_DATA_DIR = "data/self_model"


class SelfModelBootstrap:
    """
    SelfModel Runtime 启动协调器。

    用法：
        bootstrap = SelfModelBootstrap(data_dir="data/self_model")
        counts = bootstrap.bootstrap(adapter)
        # ... Runtime 生命周期 ...
        ok = bootstrap.save(adapter)
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._data_dir = data_dir or DEFAULT_DATA_DIR
        self._last_load_counts: Dict[str, int] = {}
        self._last_save_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._persistence_attached: bool = False
        self._persistence: Any = None

    @property
    def data_dir(self) -> str:
        return self._data_dir

    @property
    def last_load_counts(self) -> Dict[str, int]:
        return dict(self._last_load_counts)

    @property
    def last_save_result(self) -> Optional[Dict[str, Any]]:
        return self._last_save_result

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # ============================================================
    # 核心：bootstrap
    # ============================================================

    def bootstrap(
        self,
        adapter: Any,
        *,
        force_persistence: bool = False,
    ) -> Dict[str, Any]:
        """
        启动阶段：自动 attach persistence + load_state。

        Returns:
            {
                "persistence_attached": bool,
                "load_counts": {"beliefs": int, "history": int, "reflections": int},
                "errors": List[str],
            }
        """
        envelope: Dict[str, Any] = {
            "persistence_attached": False,
            "load_counts": {"beliefs": 0, "history": 0, "reflections": 0},
            "errors": [],
        }
        if adapter is None:
            envelope["errors"].append("adapter is None")
            self._last_error = "adapter is None"
            return envelope
        # 1) 注入 persistence（如尚未）
        if not getattr(adapter, "has_persistence", lambda: False)() or force_persistence:
            try:
                from src.personality.self_model_persistence import SelfModelPersistence
                self._persistence = SelfModelPersistence(self._data_dir)
                if hasattr(adapter, "attach_persistence"):
                    adapter.attach_persistence(self._persistence)
                self._persistence_attached = True
                envelope["persistence_attached"] = True
            except Exception as e:
                envelope["errors"].append(f"persistence_attach_failed: {e}")
                self._last_error = f"persistence_attach_failed: {e}"
                logger.warning(f"SelfModelBootstrap: persistence attach failed: {e}")
                return envelope
        else:
            self._persistence_attached = True
            envelope["persistence_attached"] = True
            self._persistence = getattr(adapter, "_persistence", None)
        # 2) load_state
        try:
            counts = adapter.load_state() if hasattr(adapter, "load_state") else {}
            self._last_load_counts = dict(counts or {})
            envelope["load_counts"] = self._last_load_counts
        except Exception as e:
            envelope["errors"].append(f"load_state_failed: {e}")
            self._last_error = f"load_state_failed: {e}"
            logger.warning(f"SelfModelBootstrap: load_state failed: {e}")
        return envelope

    # ============================================================
    # save（可选）
    # ============================================================

    def save(self, adapter: Any) -> bool:
        if adapter is None:
            self._last_save_result = {"ok": False, "errors": ["adapter is None"]}
            return False
        try:
            if not hasattr(adapter, "save_state"):
                return False
            result = adapter.save_state()
            self._last_save_result = dict(result or {})
            ok = bool(result.get("beliefs")) or bool(result.get("history")) or bool(result.get("reflections"))
            return ok
        except Exception as e:
            self._last_save_result = {"ok": False, "errors": [str(e)]}
            self._last_error = f"save_failed: {e}"
            logger.warning(f"SelfModelBootstrap.save failed: {e}")
            return False

    # ============================================================
    # 状态
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        return {
            "data_dir": self._data_dir,
            "persistence_attached": self._persistence_attached,
            "last_load_counts": self._last_load_counts,
            "last_save_result": self._last_save_result,
            "last_error": self._last_error,
        }


# ============================================================
# 便捷函数
# ============================================================

def auto_bootstrap(adapter: Any, data_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    一次性启动入口：创建 SelfModelBootstrap → bootstrap → 返回 envelope。
    失败隔离：任何异常被记录到 envelope["errors"]。
    """
    try:
        boot = SelfModelBootstrap(data_dir=data_dir)
        return boot.bootstrap(adapter)
    except Exception as e:
        return {
            "persistence_attached": False,
            "load_counts": {"beliefs": 0, "history": 0, "reflections": 0},
            "errors": [f"auto_bootstrap_exception: {e}"],
        }

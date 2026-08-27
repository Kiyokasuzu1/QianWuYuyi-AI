# -*- coding: utf-8 -*-
"""注入台账（Injection Ledger）v0 — v1.5-T3。

职责：prompt 组装完成后，按 section 记录各来源文本的字符数，落 jsonl，
供 L5-token 占比等测量使用。

fail-soft 铁律：本模块任何异常只记 warning，绝不抛给主链。

数据格式（jsonl 每行）：
{
  "ts": "ISO8601",
  "request_id": "str|''",
  "user_id": "str|''",
  "sections": {
    "yui_core": 123, "identity": 0, "user_meta": 0,
    "agreement": 0, "personality": 0, "behavior": 0,
    "self_model": 0, "goal": 0, "experience": 0,
    "relationship": 0, "emotion": 0, "temporal": 0,
    "context_blocks": 0, "chat_memories": 456
  },
  "total": 579
}
键固定为 14 个（缺失=0）；值为该 section 文本字符数（中文 1 字 = 1）。
"""
import json
import os
import threading
from typing import Dict

# 固定 section 键（缺失一律记 0）
SECTION_KEYS = (
    "yui_core",
    "identity",
    "user_meta",
    "agreement",
    "personality",
    "behavior",
    "self_model",
    "goal",
    "experience",
    "relationship",
    "emotion",
    "temporal",
    "context_blocks",
    "chat_memories",
)

_LEDGER_PATH = os.environ.get(
    "YUYI_INJECTION_LEDGER_PATH",
    os.path.join("data", "injection_ledger.jsonl"),
)
_LEDGER_LOCK = threading.Lock()
_MAX_BYTES = 20 * 1024 * 1024  # 超过轮转为 .1


def _char_len(value) -> int:
    """非字符串一律按 0 处理。"""
    return len(value) if isinstance(value, str) else 0


def is_ledger_enabled() -> bool:
    """读配置 injection_ledger.enabled；异常/缺失默认 True（fail-soft）。"""
    try:
        from src.config import get as _cfg_get
        return bool(_cfg_get("injection_ledger.enabled", True))
    except Exception:  # noqa: BLE001
        return True


def record_injection(
    sections: Dict[str, int],
    request_id: str = "",
    user_id: str = "",
) -> None:
    """fail-soft 追加一条台账。任何异常仅 warning，绝不抛。"""
    try:
        if not is_ledger_enabled():
            return
        # 归一：只保留已知键，缺失/非法/负值一律钳 0
        norm: Dict[str, int] = {}
        total = 0
        for k in SECTION_KEYS:
            try:
                v = int(sections.get(k, 0) or 0)
            except Exception:  # noqa: BLE001
                v = 0
            if v < 0:
                v = 0
            norm[k] = v
            total += v
        row = {
            "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "request_id": request_id or "",
            "user_id": user_id or "",
            "sections": norm,
            "total": total,
        }
        with _LEDGER_LOCK:
            _path = _LEDGER_PATH
            parent = os.path.dirname(os.path.abspath(_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            try:
                if os.path.exists(_path) and os.path.getsize(_path) > _MAX_BYTES:
                    _rotated = _path + ".1"
                    if os.path.exists(_rotated):
                        os.remove(_rotated)
                    os.rename(_path, _rotated)
            except Exception:  # noqa: BLE001
                pass  # 轮转失败不影响写入
            with open(_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as _exc:  # noqa: BLE001
        try:
            import logging
            logging.getLogger("injection_ledger").warning(
                "injection ledger 写入失败（已隔离）: %s", _exc,
            )
        except Exception:  # noqa: BLE001
            pass


def get_ledger_path() -> str:
    """测试/工具用：返回当前台账路径。"""
    return _LEDGER_PATH

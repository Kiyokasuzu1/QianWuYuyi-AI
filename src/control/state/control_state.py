# -*- coding: utf-8 -*-
"""
src/control/state/control_state.py

Phase C.10.5.1 — Yuyi Control Plane: Control State Layer

职责:
- 定义 ControlState: 羽依各模块运行开关 + 系统模式
- 提供状态持久化(原子写入、append-only audit)
- 提供状态查询与变更接口
- 不直接修改任何业务模块(纯状态层)

核心原则:
- Runtime / Memory / Growth 等业务模块不被直接修改
- 仅提供状态,Runtime 在读取时判断是否启用

模块字段:
    runtime_enabled
    memory_enabled
    emotion_enabled
    growth_enabled
    initiative_enabled
    dream_enabled
    live2d_enabled

系统模式字段:
    maintenance_mode
    safe_mode
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ============================================================
# 默认值
# ============================================================
DEFAULT_MODULE_STATES: Dict[str, bool] = {
    "runtime_enabled": True,
    "memory_enabled": True,
    "emotion_enabled": True,
    "growth_enabled": True,
    "initiative_enabled": True,
    "dream_enabled": True,
    "live2d_enabled": True,
}

DEFAULT_SYSTEM_STATES: Dict[str, bool] = {
    "maintenance_mode": False,
    "safe_mode": False,
}

ALL_MODULE_FIELDS: List[str] = list(DEFAULT_MODULE_STATES.keys())
ALL_SYSTEM_FIELDS: List[str] = list(DEFAULT_SYSTEM_STATES.keys())
ALL_FIELDS: List[str] = ALL_MODULE_FIELDS + ALL_SYSTEM_FIELDS


# ============================================================
# 错误
# ============================================================
class ControlStateError(Exception):
    """ControlState 错误。"""


# ============================================================
# 状态对象
# ============================================================
@dataclass
class ControlState:
    """
    羽依控制状态。

    包含:
    - 模块开关(runtime/memory/emotion/growth/initiative/dream/live2d)
    - 系统模式(maintenance/safe)

    所有字段均为 bool。
    """

    runtime_enabled: bool = True
    memory_enabled: bool = True
    emotion_enabled: bool = True
    growth_enabled: bool = True
    initiative_enabled: bool = True
    dream_enabled: bool = True
    live2d_enabled: bool = True

    maintenance_mode: bool = False
    safe_mode: bool = False

    schema_version: str = "1.0"
    updated_at: str = ""
    updated_by: str = ""

    @classmethod
    def default(cls) -> "ControlState":
        """构造默认状态(全开、normal 模式)。"""
        return cls(
            runtime_enabled=True,
            memory_enabled=True,
            emotion_enabled=True,
            growth_enabled=True,
            initiative_enabled=True,
            dream_enabled=True,
            live2d_enabled=True,
            maintenance_mode=False,
            safe_mode=False,
            schema_version="1.0",
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by="system",
        )

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ControlState":
        if not isinstance(data, dict):
            return cls.default()
        kwargs: Dict[str, Any] = {}
        for f in ALL_FIELDS:
            if f in data:
                kwargs[f] = bool(data[f])
        if "schema_version" in data:
            kwargs["schema_version"] = str(data["schema_version"])
        if "updated_at" in data:
            kwargs["updated_at"] = str(data["updated_at"])
        if "updated_by" in data:
            kwargs["updated_by"] = str(data["updated_by"])
        return cls(**kwargs)

    # --------------------------------------------------------
    # 字段查询
    # --------------------------------------------------------
    def get_field(self, name: str) -> Optional[bool]:
        """获取字段值(仅 bool 字段)。"""
        if name in ALL_FIELDS:
            return bool(getattr(self, name))
        return None

    def set_field(self, name: str, value: bool, operator: str = "system") -> bool:
        """
        设置字段值(仅 bool 字段)。

        Returns:
            True 表示实际修改,False 表示值未变。
        """
        if name not in ALL_FIELDS:
            raise ControlStateError(f"unknown_field: {name}")
        new_val = bool(value)
        old_val = bool(getattr(self, name))
        if old_val == new_val:
            return False
        setattr(self, name, new_val)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.updated_by = str(operator or "system")
        return True

    def get_modules(self) -> Dict[str, bool]:
        """获取所有模块开关状态(7 个)。"""
        return {f: bool(getattr(self, f)) for f in ALL_MODULE_FIELDS}

    def get_system(self) -> Dict[str, bool]:
        """获取所有系统模式状态(2 个)。"""
        return {f: bool(getattr(self, f)) for f in ALL_SYSTEM_FIELDS}


# ============================================================
# 状态变更记录(append-only audit)
# ============================================================
@dataclass
class ControlStateChange:
    """
    单次状态变更记录(append-only)。
    """

    change_id: str
    timestamp: str
    field: str
    old_value: bool
    new_value: bool
    operator: str
    reason: str = ""
    source: str = "control_plane"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ControlStateChange":
        if not isinstance(data, dict):
            raise ControlStateError("change must be dict")
        return cls(
            change_id=str(data.get("change_id", "")),
            timestamp=str(data.get("timestamp", "")),
            field=str(data.get("field", "")),
            old_value=bool(data.get("old_value", False)),
            new_value=bool(data.get("new_value", False)),
            operator=str(data.get("operator", "system")),
            reason=str(data.get("reason", "")),
            source=str(data.get("source", "control_plane")),
        )


# ============================================================
# 持久化(状态 + 审计)
# ============================================================
class ControlStatePersistence:
    """
    状态持久化 + append-only audit 存储。

    文件:
        {data_dir}/control_state.json    # 当前状态(单文件,原子写入)
        {data_dir}/control_state_audit.jsonl  # 追加审计

    线程安全(RLock)。
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        if data_dir is None:
            data_dir = os.environ.get("YUYI_CONTROL_DIR", "data/control")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.data_dir / "control_state.json"
        self.audit_file = self.data_dir / "control_state_audit.jsonl"
        self._lock = threading.RLock()
        self._state: ControlState = ControlState.default()
        self._load_state()

    # --------------------------------------------------------
    # 内部:加载/保存
    # --------------------------------------------------------
    def _load_state(self) -> None:
        if not self.state_file.exists():
            self._state = ControlState.default()
            self._save_state()
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._state = ControlState.from_dict(data if isinstance(data, dict) else {})
        except Exception:
            # 损坏则使用默认状态
            self._state = ControlState.default()

    def _save_state(self) -> None:
        """原子写入(临时文件 + rename)。"""
        tmp = self.state_file.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    self._state.to_dict(),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp, self.state_file)
        except Exception as exc:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            raise ControlStateError(f"save_state_failed: {exc}")

    def _append_audit(self, change: ControlStateChange) -> None:
        """追加一条审计记录(append-only)。"""
        try:
            line = json.dumps(change.to_dict(), ensure_ascii=False)
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            raise ControlStateError(f"append_audit_failed: {exc}")

    # --------------------------------------------------------
    # 公开 API:状态查询
    # --------------------------------------------------------
    def get_state(self) -> ControlState:
        with self._lock:
            # 返回浅拷贝,避免外部修改
            return ControlState.from_dict(self._state.to_dict())

    def get_field(self, name: str) -> Optional[bool]:
        with self._lock:
            return self._state.get_field(name)

    def get_modules(self) -> Dict[str, bool]:
        with self._lock:
            return self._state.get_modules()

    def get_system(self) -> Dict[str, bool]:
        with self._lock:
            return self._state.get_system()

    def is_module_enabled(self, name: str) -> bool:
        """判断某个模块是否启用。name 是 ControlState 字段名(如 'growth_enabled')。"""
        with self._lock:
            v = self._state.get_field(name)
            return bool(v) if v is not None else False

    # --------------------------------------------------------
    # 公开 API:状态变更(单字段)
    # --------------------------------------------------------
    def set_field(
        self,
        name: str,
        value: bool,
        operator: str = "system",
        reason: str = "",
    ) -> ControlStateChange:
        """
        设置字段并追加审计。

        Returns:
            产生的 ControlStateChange(若值未变,返回的 change.old_value == new_value)。
        """
        with self._lock:
            if name not in ALL_FIELDS:
                raise ControlStateError(f"unknown_field: {name}")
            old_value = bool(getattr(self._state, name))
            new_value = bool(value)
            change = ControlStateChange(
                change_id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                field=name,
                old_value=old_value,
                new_value=new_value,
                operator=str(operator or "system"),
                reason=str(reason or ""),
                source="control_plane",
            )
            if old_value == new_value:
                # 值未变:仍然记录 audit,以便追踪
                self._append_audit(change)
                return change
            setattr(self._state, name, new_value)
            self._state.updated_at = change.timestamp
            self._state.updated_by = change.operator
            self._save_state()
            self._append_audit(change)
            return change

    # --------------------------------------------------------
    # 公开 API:审计查询
    # --------------------------------------------------------
    def list_audit(
        self,
        limit: int = 100,
        field: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        读取审计记录(最近的在前)。
        """
        with self._lock:
            if not self.audit_file.exists():
                return []
            try:
                n = max(1, min(1000, int(limit)))
            except (TypeError, ValueError):
                n = 100
            items: List[Dict[str, Any]] = []
            try:
                with open(self.audit_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception:
                return []
            # 倒序
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if field and rec.get("field") != field:
                    continue
                items.append(rec)
                if len(items) >= n:
                    break
            return items

    def audit_count(self) -> int:
        with self._lock:
            if not self.audit_file.exists():
                return 0
            try:
                with open(self.audit_file, "r", encoding="utf-8") as f:
                    return sum(1 for line in f if line.strip())
            except Exception:
                return 0

    # --------------------------------------------------------
    # 公开 API:重置(测试用)
    # --------------------------------------------------------
    def reset(self) -> None:
        """重置为默认状态(并清空审计文件)。"""
        with self._lock:
            self._state = ControlState.default()
            self._save_state()
            try:
                if self.audit_file.exists():
                    self.audit_file.unlink()
            except Exception:
                pass


# ============================================================
# 模块级单例
# ============================================================
_persistence_instance: Optional[ControlStatePersistence] = None
_persistence_lock = threading.Lock()


def get_control_state_persistence() -> ControlStatePersistence:
    """获取 ControlStatePersistence 单例。"""
    global _persistence_instance
    if _persistence_instance is None:
        with _persistence_lock:
            if _persistence_instance is None:
                _persistence_instance = ControlStatePersistence()
    return _persistence_instance


def reset_control_state_persistence_for_testing(
    data_dir: Optional[str] = None,
) -> ControlStatePersistence:
    """测试用:重置并返回新实例。"""
    global _persistence_instance
    with _persistence_lock:
        if data_dir is None:
            _persistence_instance = ControlStatePersistence()
        else:
            _persistence_instance = ControlStatePersistence(data_dir=data_dir)
    return _persistence_instance


__all__ = [
    "ControlState",
    "ControlStateChange",
    "ControlStateError",
    "ControlStatePersistence",
    "DEFAULT_MODULE_STATES",
    "DEFAULT_SYSTEM_STATES",
    "ALL_MODULE_FIELDS",
    "ALL_SYSTEM_FIELDS",
    "ALL_FIELDS",
    "get_control_state_persistence",
    "reset_control_state_persistence_for_testing",
]

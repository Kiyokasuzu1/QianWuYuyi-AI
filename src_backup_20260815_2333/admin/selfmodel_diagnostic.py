# -*- coding: utf-8 -*-
"""
src/admin/selfmodel_diagnostic.py

Phase 3.5.2 Step 2: SelfModel 磁盘诊断模块（只读）

职责：
- 读取 data/self_model/ 目录下的 JSONL 文件
- 统计 beliefs / history / reflection 数量
- 报告 persistence 是否就位（仅看文件存在与否，不连接 Runtime）
- 不依赖 RuntimeCore / RuntimeBridge / Orchestrator
- 不依赖 Growth / Personality 写链路
- 不修改任何业务代码

诊断返回：
    {
        "initialized": bool,                # 数据目录是否存在
        "persistence_available": bool,      # 三个 JSONL 文件是否至少存在一个
        "data_directory": str,              # 实际诊断的数据目录（绝对路径）
        "files": {
            "beliefs_count": int,
            "history_count": int,
            "reflection_count": int
        },
        "runtime_adapter_connected": False  # 本模块不接 Runtime，永远为 False
    }

设计原则：
- 容错优先：文件损坏、空行、缺字段、目录不存在等所有异常被隔离
- 只读：只对 JSONL 文件做读操作，不创建、不修改
- 无副作用：可被任意线程 / 任意时刻调用
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 路径常量
# ============================================================

DEFAULT_DATA_DIR = "data/self_model"

BELIEFS_FILENAME = "beliefs.jsonl"
HISTORY_FILENAME = "history.jsonl"
REFLECTION_FILENAME = "reflection.jsonl"
META_FILENAME = "meta.json"

# 已知可被 _count_records 识别的"主键"字段，用于判断一行 JSON 是否合法记录
KNOWN_RECORD_KEYS = {
    "beliefs.jsonl": "belief_id",
    "history.jsonl": "event_id",
    "reflection.jsonl": "note_id",
}


# ============================================================
# 工具
# ============================================================

def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


# ============================================================
# SelfModelDiagnostic
# ============================================================

class SelfModelDiagnostic:
    """
    SelfModel 磁盘诊断器（只读）。

    用法：
        diag = SelfModelDiagnostic(data_dir="data/self_model")
        report = diag.run()
        # 或：直接调用模块级函数 diagnose(data_dir=...)

    约束：
    - 不 import runtime_core / runtime_bridge / orchestrator
    - 不 import growth 写链路
    - 不 import personality 写链路
    - 只 import 标准库 + 日志
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        # 允许传入 None 或空字符串；统一回退到默认
        self._data_dir = Path(data_dir) if data_dir else Path(DEFAULT_DATA_DIR)

    # ============================================================
    # 属性
    # ============================================================

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    # ============================================================
    # 内部：单文件计数
    # ============================================================

    def _count_records(self, filename: str) -> int:
        """
        统计单个 JSONL 文件中合法记录条数。

        容错策略：
        - 文件不存在 → 返回 0
        - 空文件 / 全空行 → 0
        - 单行 JSON 解析失败 → 跳过该行
        - 缺少已知主键字段（belief_id/event_id/note_id） → 仍计数（视为合法记录）
          —— 诊断阶段不强制 schema 严格性，避免漏数
        """
        path = self._data_dir / filename
        if not path.exists():
            return 0
        try:
            count = 0
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        line = line.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            count += 1
                        # 非 dict（如 list / str）一律不计
                    except (json.JSONDecodeError, ValueError):
                        # 单行 JSON 损坏 → 跳过
                        continue
            return count
        except Exception as e:
            logger.warning(f"SelfModelDiagnostic: count {filename} failed: {e}")
            return 0

    def _file_exists(self, filename: str) -> bool:
        try:
            return (self._data_dir / filename).exists()
        except Exception:
            return False

    # ============================================================
    # 公开：run
    # ============================================================

    def run(self) -> Dict[str, Any]:
        """
        执行一次诊断，返回结构化报告。

        Returns:
            {
                "initialized": True,           # 诊断模块本身已加载就绪 → 总是 True
                "persistence_available": bool, # 目录存在且至少一个 JSONL 存在
                "data_directory": str,         # 实际诊断的数据目录（绝对路径）
                "files": {
                    "beliefs_count": int,
                    "history_count": int,
                    "reflection_count": int
                },
                "runtime_adapter_connected": False  # 本模块不接 Runtime
            }

        语义说明：
        - initialized：表示「诊断模块本身已就绪」；不依赖数据目录是否存在。
          一旦模块被加载并能返回结果，initialized=True。
          即便 data_dir 不存在（情况1），诊断仍可执行安全 fallback。
        - persistence_available：表示「数据目录可用」。
          仅当目录存在且至少一个 JSONL 存在时为 True。
        """
        try:
            data_dir_str = str(self._data_dir)
            # 1) initialized：诊断模块本身已就绪 → 总是 True
            #    走 try 分支说明模块能正常执行，无 catastrophic 错误
            initialized = True

            # 2) 各文件计数（不存在的文件返回 0，不抛异常）
            beliefs_count = self._count_records(BELIEFS_FILENAME)
            history_count = self._count_records(HISTORY_FILENAME)
            reflection_count = self._count_records(REFLECTION_FILENAME)

            # 3) persistence_available：目录存在 + 至少一个 JSONL 存在
            dir_exists = False
            try:
                dir_exists = self._data_dir.exists() and self._data_dir.is_dir()
            except Exception:
                dir_exists = False
            any_file_exists = (
                self._file_exists(BELIEFS_FILENAME)
                or self._file_exists(HISTORY_FILENAME)
                or self._file_exists(REFLECTION_FILENAME)
            )
            persistence_available = bool(dir_exists and any_file_exists)

            return {
                "initialized": bool(initialized),
                "persistence_available": bool(persistence_available),
                "data_directory": data_dir_str,
                "files": {
                    "beliefs_count": int(beliefs_count),
                    "history_count": int(history_count),
                    "reflection_count": int(reflection_count),
                },
                "runtime_adapter_connected": False,
            }
        except Exception as e:
            # 任何意外都不能让诊断器本身崩；返回安全 fallback
            # 异常情况下 initialized 仍为 True（模块已加载，只是当前次失败）
            logger.warning(f"SelfModelDiagnostic.run failed: {e}")
            return {
                "initialized": True,
                "persistence_available": False,
                "data_directory": str(self._data_dir),
                "files": {
                    "beliefs_count": 0,
                    "history_count": 0,
                    "reflection_count": 0,
                },
                "runtime_adapter_connected": False,
                "error": str(e),
            }

    # ============================================================
    # 公开：run_extended（带元信息，便于排查）
    # ============================================================

    def run_extended(self) -> Dict[str, Any]:
        """
        扩展诊断：除基础字段外，附带文件大小、最近修改时间、meta 摘要等。

        用途：人工排查问题时比 run() 更有用；不影响 run() 的契约。
        """
        base = self.run()
        try:
            files_meta: Dict[str, Dict[str, Any]] = {}
            for fname in (BELIEFS_FILENAME, HISTORY_FILENAME, REFLECTION_FILENAME, META_FILENAME):
                p = self._data_dir / fname
                if p.exists():
                    try:
                        st = p.stat()
                        files_meta[fname] = {
                            "exists": True,
                            "size_bytes": int(st.st_size),
                            "mtime": int(st.st_mtime),
                        }
                    except Exception as e:
                        files_meta[fname] = {"exists": True, "error": str(e)}
                else:
                    files_meta[fname] = {"exists": False}

            base["files_meta"] = files_meta

            # meta.json 摘要（如有）
            meta_path = self._data_dir / META_FILENAME
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    base["meta_summary"] = {
                        "version": meta.get("version"),
                        "last_updated": meta.get("last_updated"),
                        "sections": {
                            k: {"count": v.get("count"), "last_saved": v.get("last_saved")}
                            for k, v in (meta.items() or {})
                            if isinstance(v, dict)
                        },
                    }
                except Exception as e:
                    base["meta_summary"] = {"error": str(e)}
        except Exception as e:
            base["extended_error"] = str(e)
        return base


# ============================================================
# 模块级便捷函数
# ============================================================

def diagnose(data_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    一行诊断入口。

    Args:
        data_dir: 数据目录路径；None 时使用默认 data/self_model

    Returns:
        见 SelfModelDiagnostic.run()
    """
    return SelfModelDiagnostic(data_dir=data_dir).run()


def diagnose_extended(data_dir: Optional[str] = None) -> Dict[str, Any]:
    """扩展诊断（含文件元信息）一行入口。"""
    return SelfModelDiagnostic(data_dir=data_dir).run_extended()


__all__ = [
    "SelfModelDiagnostic",
    "diagnose",
    "diagnose_extended",
    "BELIEFS_FILENAME",
    "HISTORY_FILENAME",
    "REFLECTION_FILENAME",
    "META_FILENAME",
    "DEFAULT_DATA_DIR",
]

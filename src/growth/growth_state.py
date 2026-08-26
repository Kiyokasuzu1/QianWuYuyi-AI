"""
羽依成长状态管理（GrowthState）
职责：管理 growth_state.json 的读写、状态更新、每日衰减

Phase 2.4 更新：
- 写入接入 atomic_write_json（原子替换，失败时旧文件完好）
- 每路径 RLock 串行化多实例写入
- 加载失败 → 损坏文件备份 .corrupt.timestamp，不立即覆盖旧数据
"""

import json
import logging
import os
import shutil
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Dict

from src.memory.atomic_write import atomic_write_json

logger = logging.getLogger(__name__)

# ============================================================
# Phase 2.4: per-path RLock + 损坏备份（与 memory/audit 同模式）
# ============================================================
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path) -> threading.RLock:
    """按绝对路径取 RLock（可重入，同线程嵌套写安全）。"""
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _backup_corrupt(path) -> None:
    """把损坏文件复制为 .corrupt.timestamp 备份（copy 而非 move，
    不干扰并发写者；备份失败不阻断主流程）。"""
    try:
        src = Path(path)
        if not src.exists():
            return
        backup = Path(
            f"{path}.corrupt.{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
        )
        shutil.copy2(src, backup)
        logger.warning("GrowthState 损坏文件已备份: %s", backup)
    except Exception as e:  # noqa: BLE001
        logger.warning("GrowthState 损坏文件备份失败: %s", e)


class GrowthState:
    def __init__(self, state_path: str = "data/growth_state.json"):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = None
        self._load()

    def _load(self):
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("growth_state.json 顶层不是 dict")
                self._state = loaded
            except Exception as e:
                # Phase 2.4: 损坏 → 备份后以默认状态继续服务；
                # 不立即覆盖旧数据（旧文件保留，由下一次合法 save 原子替换）。
                logger.warning("GrowthState 加载失败（已备份损坏文件）: %s", e)
                try:
                    _backup_corrupt(self.state_path)
                except Exception:  # noqa: BLE001
                    pass
                self._state = self._get_default_state()
        else:
            self._state = self._get_default_state()
            self._save()
        self._migrate()
        self._apply_daily_decay()
        # V1.0-OPT: 记录最近观测的磁盘 mtime（stale-write guard 基线）
        try:
            self._last_disk_mtime = (
                os.path.getmtime(self.state_path) if self.state_path.exists() else None
            )
        except OSError:
            self._last_disk_mtime = None

    def _migrate(self):
        """确保所有字段存在，兼容旧版本"""
        metrics = self._state.setdefault("metrics", {})
        defaults = {
            "trust": 0.30,
            "closeness": 0.20,
            "safety": 0.30,
            "self_awareness": 0.20,
            "self_confidence": 0.10,
        }
        for k, v in defaults.items():
            metrics.setdefault(k, v)

        self._state.setdefault("daily_growth_count", {})
        if "last_growth_date" not in self._state:
            self._state["last_growth_date"] = date.today().isoformat()
        if "last_decay_date" not in self._state:
            self._state["last_decay_date"] = date.today().isoformat()

        self._state.setdefault("processed_growth_events", [])
        self._state.setdefault("processed_events", [])
        self._state.setdefault("milestones", [])
        self._state.setdefault("identities", [])
        self._state.setdefault("behaviors", {
            "active_care": False,
            "use_nickname": False,
            "initiate_topic": False
        })

    def _save(self) -> bool:
        # Phase 2.4: per-path RLock + 原子写（失败时旧文件完好，异常上抛由调用方处理）
        # V1.0-OPT: stale-write guard —— 若磁盘文件在本实例最近观测之后被
        # 其他实例更新过，则跳过保存并告警，避免陈旧 fallback 实例整体覆盖
        # 权威实例的较新数据（last-write-wins 防陈旧）。
        _p = str(self.state_path)
        _disk_mtime = getattr(self, "_last_disk_mtime", None)
        if _disk_mtime is not None and os.path.exists(_p):
            try:
                _current = os.path.getmtime(_p)
                if _current > _disk_mtime:
                    logger.warning(
                        "GrowthState: 检测到磁盘状态已被其他实例更新（磁盘 mtime %s > 本实例观测 %s），"
                        "跳过保存以避免陈旧覆盖（stale-write guard）",
                        _current,
                        _disk_mtime,
                    )
                    return False
            except OSError:
                pass
        with _path_lock(self.state_path):
            atomic_write_json(_p, self._state)
        try:
            self._last_disk_mtime = os.path.getmtime(_p)
        except OSError:
            self._last_disk_mtime = None
        return True

    def _get_default_state(self) -> Dict:
        return {
            "version": "0.2",
            "last_updated": datetime.now().isoformat(),
            "metrics": {
                "trust": 0.30,
                "closeness": 0.20,
                "safety": 0.30,
                "self_awareness": 0.20,
                "self_confidence": 0.10,
            },
            "milestones": [],
            "identities": [],
            "behaviors": {
                "active_care": False,
                "use_nickname": False,
                "initiate_topic": False
            },
            "processed_events": [],
            "processed_growth_events": [],
            "daily_growth_count": {},
            "last_growth_date": date.today().isoformat(),
            "last_decay_date": date.today().isoformat()
        }

    # ==========================================
    # 成长上限
    # ==========================================
    MAX_GROWTH = {
        "trust": 0.95,
        "closeness": 0.92,
        "safety": 0.90,
        "self_awareness": 0.85,
        "self_confidence": 0.90,
    }

    # ==========================================
    # 每日衰减率
    # ==========================================
    DECAY_RATE = {
        "trust": 0.995,
        "closeness": 0.997,
        "safety": 0.995,
        "self_awareness": 0.999,
        "self_confidence": 0.996,
    }

    def _apply_daily_decay(self):
        """每日衰减：关系需要持续维护"""
        today = date.today().isoformat()
        last_decay = self._state.get("last_decay_date", today)

        if last_decay == today:
            return

        try:
            d1 = datetime.strptime(last_decay, "%Y-%m-%d").date()
            d2 = datetime.strptime(today, "%Y-%m-%d").date()
            days = (d2 - d1).days
        except:
            days = 1

        if days <= 0:
            return

        metrics = self._state["metrics"]
        for key, rate in self.DECAY_RATE.items():
            if key in metrics:
                for _ in range(min(days, 30)):
                    metrics[key] = round(metrics[key] * rate, 3)
                    initial = {
                        "trust": 0.30,
                        "closeness": 0.20,
                        "safety": 0.30,
                        "self_awareness": 0.20,
                        "self_confidence": 0.10,
                    }.get(key, 0.1)
                    if metrics[key] < initial * 0.8:
                        metrics[key] = initial * 0.8

        self._state["last_decay_date"] = today
        self._save()

    def get(self) -> Dict:
        return self._state

    def get_metric(self, key: str) -> float:
        return self._state["metrics"].get(key, 0.0)

    def _is_personality_trait_key(self, key: str) -> bool:
        """P0-1: 是否为合法 personality trait 目标（personality_state.traits 键）。

        growth_state 是 legacy 行为统计轨（metrics 白名单 5 键）；personality trait
        是 0-1 状态与 metrics 语义兼容。合法 trait 键不再被静默丢弃（此前 proposal
        目标维度 creativity/self_expression/initiative 全被丢弃，产生幻影 delta）；
        未知键仍拒绝（防污染）。
        """
        try:
            from src.personality.personality_state import get_personality_state
            return key in (get_personality_state().traits or {})
        except Exception:  # noqa: BLE001
            return False

    def update_metrics(self, deltas: Dict[str, float]):
        """更新 metrics，包含同日递减"""
        today = date.today().isoformat()
        daily_count = self._state["daily_growth_count"]
        last_date = self._state.get("last_growth_date", today)

        if last_date != today:
            self._state["daily_growth_count"] = {}
            self._state["last_growth_date"] = today
            daily_count = {}

        for key, delta in deltas.items():
            if key not in self._state["metrics"]:
                if not self._is_personality_trait_key(key):
                    continue
                # P0-1: 合法 personality target 初始化后纳入（上限默认 1.0）
                self._state["metrics"][key] = 0.0

            count = daily_count.get(key, 0)
            decay_factor = max(0.2, 1.0 - count * 0.2)

            old_val = self._state["metrics"][key]
            max_val = self.MAX_GROWTH.get(key, 1.0)
            new_val = min(max_val, old_val + delta * decay_factor)
            self._state["metrics"][key] = round(new_val, 3)

            daily_count[key] = count + 1

        self._state["daily_growth_count"] = daily_count

    def add_milestone(self, event_id: str, topic: str):
        self._state["milestones"].append({
            "event_id": event_id,
            "topic": topic,
            "applied_at": datetime.now().isoformat()
        })

    def add_identity(self, identity: str):
        if identity not in self._state["identities"]:
            self._state["identities"].append(identity)

    def set_behavior(self, behavior: str, value: bool):
        if behavior in self._state["behaviors"]:
            self._state["behaviors"][behavior] = value

    def mark_event_processed(self, event_id: str):
        if event_id not in self._state["processed_events"]:
            self._state["processed_events"].append(event_id)

    def is_event_processed(self, event_id: str) -> bool:
        return event_id in self._state["processed_events"]

    def mark_growth_applied(self, event_id: str):
        if event_id not in self._state["processed_growth_events"]:
            self._state["processed_growth_events"].append(event_id)

    def is_growth_applied(self, event_id: str) -> bool:
        return event_id in self._state["processed_growth_events"]

    def save(self) -> bool:
        self._state["last_updated"] = datetime.now().isoformat()
        return self._save()

    def reset(self):
        self._state = self._get_default_state()
        self._save()

# =====================================================================
# V1.0-1C: GrowthState Authority fallback 收口
# =====================================================================
def resolve_authority_growth_state() -> "GrowthState":
    """bridge-first 获取 GrowthState 权威实例。

    生产权威实例由 RuntimeCore.get_growth_state() 持有（RuntimeBridge 透传）。
    本 helper 供各模块裸构造 fallback 使用：优先复用运行时权威单例，
    仅当 Bridge 不可用/未初始化时自建（旧行为保留，fail-soft）。
    惰性导入 RuntimeBridge 避免模块级循环依赖。
    """
    try:
        from src.runtime.runtime_bridge import get_runtime_bridge
        _gs = get_runtime_bridge().get_growth_state()
        if _gs is not None:
            return _gs
    except Exception:
        pass
    return GrowthState()

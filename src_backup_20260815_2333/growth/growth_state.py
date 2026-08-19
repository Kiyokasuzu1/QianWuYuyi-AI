"""
羽依成长状态管理（GrowthState）
职责：管理 growth_state.json 的读写、状态更新、每日衰减
"""

import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, date


class GrowthState:
    def __init__(self, state_path: str = "data/growth_state.json"):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = None
        self._load()

    def _load(self):
        if self.state_path.exists():
            with open(self.state_path, "r", encoding="utf-8") as f:
                self._state = json.load(f)
        else:
            self._state = self._get_default_state()
            self._save()
        self._migrate()
        self._apply_daily_decay()

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

    def _save(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

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
                continue

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

    def save(self):
        self._state["last_updated"] = datetime.now().isoformat()
        self._save()

    def reset(self):
        self._state = self._get_default_state()
        self._save()
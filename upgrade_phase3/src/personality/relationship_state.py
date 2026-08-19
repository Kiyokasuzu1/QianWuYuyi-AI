"""
关系状态（RelationshipState） v0.6

浅雾羽依成长系统

职责：
独立管理羽依与清清之间的长期关系状态。

v0.6 更新:
- 增加 _enforce_maturity_constraints，防止信任/羁绊与熟悉度矛盾
- trust 增长受 familiarity 制约
- bond 增长受 familiarity + shared_history 共同制约
- update_familiarity 会同步小幅提升信任与羁绊
- 提供测试用重置方法 recalibrate_for_testing
"""

import json
from pathlib import Path
from typing import Dict, List
from datetime import datetime, date


class RelationshipState:

    def __init__(self, state_path="data/relationship_state.json"):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()
        self._apply_daily_decay()
        # 启动时校准一次，若数据被修改则保存
        if self._enforce_maturity_constraints():
            self._save()

    # =========================
    # 存储
    # =========================
    def _load(self):
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return self._upgrade(data)
            except Exception:
                pass
        return self._default()

    def _save(self):
        self._state["last_updated"] = datetime.now().isoformat()
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _default(self):
        return {
            "version": "0.6",
            "last_updated": datetime.now().isoformat(),
            "last_decay": date.today().isoformat(),
            # 永久关系
            "bond_strength": 0.10,
            "trust": 0.30,
            "familiarity": 0.20,
            "promise_level": 0.0,
            "shared_history": 0.0,
            # 近期状态
            "activity_level": 0.20,
            # 关系事件
            "milestones": [],
            "important_events": []
        }

    def _upgrade(self, data):
        default = self._default()
        for k, v in default.items():
            if k not in data:
                data[k] = v
        data["version"] = "0.6"
        return data

    # =========================
    # 时间衰减
    # =========================
    def _apply_daily_decay(self):
        today = date.today().isoformat()
        last = self._state.get("last_decay", today)
        if today == last:
            return
        try:
            days = (date.fromisoformat(today) - date.fromisoformat(last)).days
        except Exception:
            days = 1
        if days <= 0:
            return
        for _ in range(min(days, 30)):
            self._state["activity_level"] = max(
                0.05,
                self._state["activity_level"] * 0.96
            )
        self._state["last_decay"] = today
        self._save()

    # =========================
    # 成熟度约束（新增）
    # =========================
    def _enforce_maturity_constraints(self):
        """
        确保关系维度不会出现矛盾组合。
        - 信任不能远超出熟悉度
        - 羁绊需要熟悉度 + 共同经历支撑
        返回是否发生了修改
        """
        familiarity = self._state.get("familiarity", 0.0)
        history = self._state.get("shared_history", 0.0)

        old_trust = self._state["trust"]
        old_bond = self._state["bond_strength"]

        # 信任上限：基础0.2 + 熟悉度*0.8
        max_trust = min(1.0, 0.2 + familiarity * 0.8)
        self._state["trust"] = min(self._state["trust"], max_trust)

        # 羁绊上限：基础0.3 + 熟悉度*0.4 + 共同经历*0.3
        max_bond = min(1.0, 0.3 + familiarity * 0.4 + history * 0.3)
        self._state["bond_strength"] = min(self._state["bond_strength"], max_bond)

        return self._state["trust"] != old_trust or self._state["bond_strength"] != old_bond

    # =========================
    # 获取
    # =========================
    def get(self):
        return self._state

    def get_bond_strength(self):
        return self._state["bond_strength"]

    def get_trust(self):
        return self._state["trust"]

    def get_familiarity(self):
        return self._state["familiarity"]

    def get_promise_level(self):
        return self._state["promise_level"]

    def get_shared_history(self):
        return self._state["shared_history"]

    def get_activity(self):
        return self._state["activity_level"]

    # =========================
    # 更新关系（每个方法末尾调用约束）
    # =========================
    def update_bond(self, delta):
        self._state["bond_strength"] = min(
            1.0,
            self._state["bond_strength"] + max(0, delta)
        )
        self._enforce_maturity_constraints()
        self._save()

    def update_trust(self, delta):
        self._state["trust"] = min(
            1.0,
            max(0, self._state["trust"] + delta)
        )
        self._enforce_maturity_constraints()
        self._save()

    def update_familiarity(self, delta):
        """熟悉度增长时，信任和羁绊也会自然小幅提升"""
        self._state["familiarity"] = min(
            1.0,
            self._state["familiarity"] + max(0, delta)
        )
        # 熟悉度带动信任和羁绊自然增长
        self._state["trust"] = min(1.0, self._state["trust"] + delta * 0.3)
        self._state["bond_strength"] = min(1.0, self._state["bond_strength"] + delta * 0.2)
        self._enforce_maturity_constraints()
        self._save()

    def update_promise(self, delta):
        self._state["promise_level"] = min(
            1.0,
            self._state["promise_level"] + max(0, delta)
        )
        self._save()

    def update_history(self, delta):
        self._state["shared_history"] = min(
            1.0,
            self._state["shared_history"] + max(0, delta)
        )
        self._enforce_maturity_constraints()
        self._save()

    def update_activity(self, delta):
        self._state["activity_level"] = min(
            1.0,
            max(0.05, self._state["activity_level"] + delta)
        )
        self._save()

    # =========================
    # 关系事件
    # =========================
    def add_milestone(self, event_id, topic):
        self._state["milestones"].append({
            "event_id": event_id,
            "topic": topic,
            "time": datetime.now().isoformat()
        })
        self._save()

    def add_important_event(self, event):
        self._state["important_events"].append(event)
        self._save()

    def get_milestones(self):
        return self._state.get("milestones", [])

    # =========================
    # Prompt描述
    # =========================
    def to_prompt_text(self):
        parts = []
        bond = self.get_bond_strength()
        trust = self.get_trust()
        promise = self.get_promise_level()
        familiarity = self.get_familiarity()
        history = self.get_shared_history()

        # 羁绊描述结合熟悉度和共同经历
        if bond > 0.7 and history > 0.5 and familiarity > 0.6:
            parts.append("拥有深厚且长期积累的羁绊")
        elif bond > 0.4:
            parts.append("正在形成稳定联系")
        else:
            parts.append("正在互相了解")

        if trust > 0.7:
            parts.append("高度信任清清")
        elif trust > 0.4:
            parts.append("逐渐信任清清")
        else:
            parts.append("仍在认识阶段")

        if promise > 0.5:
            parts.append("记得彼此的长期约定")

        return "与清清的关系：" + "，".join(parts)

    # =========================
    # 测试重置
    # =========================
    def reset(self):
        """彻底重置为出厂默认（包括事件）"""
        self._state = self._default()
        self._enforce_maturity_constraints()
        self._save()

    def recalibrate_for_testing(self):
        """
        测试用校准：将关系数值恢复为初识状态，并清空历史事件。
        确保测试从干净初始状态开始。
        """
        self._state["bond_strength"] = 0.10
        self._state["trust"] = 0.30
        self._state["familiarity"] = 0.20
        self._state["activity_level"] = 0.20
        self._state["shared_history"] = 0.0
        self._state["milestones"] = []
        self._state["important_events"] = []
        self._enforce_maturity_constraints()
        self._save()
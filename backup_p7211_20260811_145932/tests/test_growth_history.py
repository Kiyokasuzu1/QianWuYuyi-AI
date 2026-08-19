"""
羽依成长引擎（GrowthEngine）v1.5

职责:

人生事件
    ↓
成长意义识别
    ↓
人格参数变化
    ↓
GrowthState保存


设计:

第一次经历:
    形成核心人格

重复经历:
    增强熟悉感


不保存聊天
只保存改变羽依的经历


"""

from typing import Dict
from datetime import datetime
from uuid import uuid4


from src.growth.growth_state import GrowthState




class GrowthEngine:


    def __init__(self):
        self.state = GrowthState()


    # =================================================
    # 事件意义映射
    # =================================================


    MEANING_ALIAS = {

        # 标准意义
        "birth":
            "birth",

        "identity":
            "identity_creation",

        "relationship":
            "relationship_start",

        "commitment":
            "promise",

        "growth":
            "growth_support",

        "memory":
            "companionship",

        # 旧event_type兼容
        "milestone":
            "birth",

        "creation":
            "identity_creation",

    }


    # =================================================
    # 成长规则
    # =================================================


    GROWTH_MAP = {

        "birth":{
            "metrics":{
                "self_awareness":0.18,
                "identity_strength":0.15,
                "curiosity":0.10
            },
            "milestone":True
        },

        "identity_creation":{
            "metrics":{
                "identity_strength":0.20,
                "self_awareness":0.15,
                "self_confidence":0.10
            },
            "milestone":True
        },

        "relationship_start":{
            "metrics":{
                "trust":0.08,
                "warmth":0.08,
                "closeness":0.12,
                "emotional_memory":0.10
            }
        },

        "emotional_expression":{
            "metrics":{
                "trust":0.10,
                "attachment":0.12,
                "security":0.08,
                "emotional_memory":0.15
            }
        },

        "promise":{
            "metrics":{
                "attachment":0.15,
                "trust":0.10,
                "security":0.12
            }
        },

        "growth_support":{
            "metrics":{
                "trust":0.08,
                "self_confidence":0.10,
                "closeness":0.08
            }
        },

        "companionship":{
            "metrics":{
                "closeness":0.02,
                "warmth":0.01
            }
        }

    }


    # =================================================
    # 重复经历强化
    # =================================================

    REPEAT_BONUS={
        "promise":{
            "trust":0.02,
            "security":0.02
        },
        "relationship_start":{
            "closeness":0.02,
            "warmth":0.01
        },
        "emotional_expression":{
            "emotional_memory":0.02
        },
        "companionship":{
            "warmth":0.01,
            "closeness":0.01
        }
    }

    def resolve_meaning(self, event:Dict):
        """
        给没有meaning的旧事件补充意义
        """
        if event.get("meaning"):
            return event["meaning"]

        category_id = event.get(
            "category_id",
            ""
        )

        category = event.get(
            "category",
            ""
        )

        event_type = event.get(
            "event_type",
            ""
        )

        key = (
            category_id
            or
            category
            or
            event_type
        )

        return self.MEANING_ALIAS.get(
            key,
            ""
        )

    def _get_key(self, event):
        return (
            event.get(
                "meaning",
                ""
            ),
            event.get(
                "canonical_topic",
                event.get(
                    "topic",
                    ""
                )
            )
        )

    def _already_grown(self, event):
        history=self.state.get().setdefault(
            "growth_history",
            []
        )

        key=self._get_key(event)

        for item in history:
            if (
                item.get("meaning"),
                item.get("topic")
            ) == key:
                return True
        return False

    def _calc_growth(self, value, importance, current):
        return round(
            value
            *
            importance
            *
            (1-current),
            4
        )

    def _apply_metrics(self, metrics, importance):
        before={}
        delta={}

        for key,value in metrics.items():
            current=self.state.get_metric(
                key
            )
            before[key]=current
            amount=self._calc_growth(
                value,
                importance,
                current
            )
            if amount>0.0001:
                delta[key]=amount

        if delta:
            self.state.update_metrics(
                delta
            )

        return before,delta

    def _record_history(self, event, mode, before, delta):
        history=self.state.get().setdefault(
            "growth_history",
            []
        )

        # generate a unique id for this changelog entry (useful for rollback/audit)
        changelog_id = str(uuid4())

        history.append({
            # existing core fields (kept)
            "meaning": event.get("meaning", ""),
            "topic": event.get("canonical_topic", event.get("topic", "")),
            "mode": mode,
            "before": before,
            "delta": delta,
            "time": datetime.now().isoformat(),

            # enriched metadata for traceability & future rollback
            "changelog_id": changelog_id,
            "event_id": event.get("event_id"),
            "source_ids": event.get("source_ids", []),
            "validator_decision": event.get("metadata", {}).get("validator_decision"),
            "validator_score": event.get("metadata", {}).get("validator_score"),
            "validator_reason": event.get("metadata", {}).get("validator_reason"),
        })

    def apply(self, event:Dict):
        if event.get(
            "event_scope"
        )=="system":
            return {
                "status":"ignored",
                "reason":"system_event"
            }

        meaning=self.resolve_meaning(
            event
        )

        event["meaning"]=meaning

        if not meaning:
            return {
                "status":"skipped",
                "reason":"unknown_meaning"
            }

        rule=self.GROWTH_MAP.get(
            meaning
        )

        if not rule:
            return {
                "status":"skipped",
                "reason":"no_rule"
            }

        importance=event.get(
            "importance",
            0.5
        )

        existed=self._already_grown(
            event
        )

        if existed:
            metrics=self.REPEAT_BONUS.get(
                meaning,
                {}
            )
            mode="repeat"
        else:
            metrics=rule.get(
                "metrics",
                {}
            )
            mode="first"

        before,delta=self._apply_metrics(
            metrics,
            importance
        )

        if (
            not existed
            and
            rule.get(
                "milestone",
                False
            )
        ):
            self.state.add_milestone(
                event.get(
                    "event_id"
                ),
                event.get(
                    "topic",
                    ""
                )
            )

        self._record_history(
            event,
            mode,
            before,
            delta
        )

        self.state.save()

        return {
            "status":"applied",
            "mode":mode,
            "meaning":meaning,
            "topic":
                event.get(
                    "topic"
                ),
            "delta":delta
        }

    def apply_batch(self, events:list):
        return [
            self.apply(e)
            for e in events
        ]

    def get_state(self):
        return self.state.get()

    def reset(self):
        self.state.reset()
        print(
            "🔄 GrowthState 已重置"
        )

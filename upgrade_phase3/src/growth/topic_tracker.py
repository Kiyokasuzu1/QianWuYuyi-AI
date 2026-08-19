import hashlib
from typing import List, Dict, Optional
from datetime import datetime

from src.config import get
from src.memory.store import MemoryStore
from src.growth.consolidation import EventExtractor


class EventHistoryMatcher:
    """
    事件历史匹配器 v3.4
    - 使用带权重的锚点匹配
    - 只有用户完整句子作为高权重锚点
    - 特殊行为 token 不再作为锚点（改用 event_id 追踪）
    - 匹配需要达到 30% 权重阈值
    """

    def __init__(self):
        self.store = MemoryStore()
        self.target_user_id = get("memory.target_user_id", "366648462")

    def _get_all_target_memories(self) -> List[Dict]:
        return self.store.get_by_user(self.target_user_id)

    def _extract_date(self, timestamp: str) -> str:
        if not timestamp:
            return ""
        try:
            return timestamp[:10]
        except:
            return ""

    def _get_session_id(self, mem: Dict) -> str:
        metadata = mem.get("metadata", {})
        session_id = metadata.get("session_id")
        if session_id:
            return session_id
        date = self._extract_date(mem.get("timestamp", ""))
        if date:
            return f"session_{date}"
        return "session_unknown"

    def _generate_event_id(self, event: Dict) -> str:
        """为事件生成稳定的 ID"""
        topic = event.get("topic", "unknown")
        event_desc = event.get("event", "")[:50]
        source_str = "_".join(event.get("source_ids", []))
        hash_input = f"{topic}|{event_desc}|{source_str}"
        return f"evt_{hashlib.md5(hash_input.encode()).hexdigest()[:12]}"

    def _extract_anchors(self, evidence: List) -> List[Dict]:
        """
        从 evidence 中提取匹配锚点（带权重）
        只使用用户证据，且长度为 8 以上（完整句子）
        特殊 token 不再作为锚点
        """
        anchors = []

        for ev in evidence:
            if not isinstance(ev, dict):
                continue

            text = ev.get("text", "")
            role = ev.get("role", "")

            if role != "user":
                continue

            # 特殊 token 跳过（不作为锚点）
            if text in ["[用户发送图片]", "[用户发送文件]", "[用户发送语音]", "[用户发送视频]", "[用户发送表情]"]:
                continue

            # 用户完整句子作为高权重锚点
            if len(text) >= 8:
                anchors.append({"text": text, "weight": 10})

        return anchors

    def _matches_memory(self, content: str, anchors: List[Dict]) -> bool:
        """加权匹配，需要达到 30% 权重阈值"""
        if not content or not anchors:
            return False

        content_lower = content.lower()
        total_weight = 0
        matched_weight = 0

        for anchor in anchors:
            anchor_text = anchor.get("text", "")
            anchor_weight = anchor.get("weight", 5)

            total_weight += anchor_weight
            if anchor_text.lower() in content_lower:
                matched_weight += anchor_weight

        # 至少匹配总权重的 30%
        return total_weight > 0 and (matched_weight / total_weight) >= 0.3

    def track(self, events: List[Dict]) -> List[Dict]:
        if not events:
            return events

        all_memories = self._get_all_target_memories()

        for event in events:
            # 为事件生成稳定的 ID
            if "event_id" not in event:
                event["event_id"] = self._generate_event_id(event)

            evidence = event.get("evidence", [])
            anchors = self._extract_anchors(evidence)

            if not anchors:
                event["history"] = {
                    "mention_count": 0,
                    "first_seen": "",
                    "last_seen": "",
                    "date_span": 0,
                    "cross_session": False,
                    "related_ids": []
                }
                continue

            matched = []
            sessions = set()
            dates = []
            related_ids = set()

            for mem in all_memories:
                if mem.get("role") != "user":
                    continue

                content = mem.get("content", "")
                if not self._matches_memory(content, anchors):
                    continue

                matched.append(mem)
                mem_id = mem.get("id") or f"mem_{id(mem)}"
                related_ids.add(mem_id)

                session_id = self._get_session_id(mem)
                if session_id:
                    sessions.add(session_id)

                date = self._extract_date(mem.get("timestamp", ""))
                if date:
                    dates.append(date)

            mention_count = len(matched)

            first_seen = min(dates) if dates else ""
            last_seen = max(dates) if dates else ""
            date_span = 0
            if first_seen and last_seen and first_seen != last_seen:
                try:
                    d1 = datetime.strptime(first_seen, "%Y-%m-%d")
                    d2 = datetime.strptime(last_seen, "%Y-%m-%d")
                    date_span = (d2 - d1).days
                except:
                    date_span = 0

            event["history"] = {
                "mention_count": mention_count,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "date_span": date_span,
                "cross_session": len(sessions) > 1,
                "related_ids": list(related_ids)
            }

            print(f"\n📊 事件 ID: {event.get('event_id', 'unknown')}")
            print(f"   topic: {event.get('topic', 'unknown')}")
            print(f"   锚点数: {len(anchors)}")
            print(f"   匹配用户消息: {mention_count}")
            print(f"   日期范围: {first_seen} ~ {last_seen} ({date_span} 天)")
            print(f"   跨会话: {len(sessions) > 1}")
            print(f"   相关 IDs: {list(related_ids)[:5]}{'...' if len(related_ids) > 5 else ''}")

        return events


class Consolidator:
    def __init__(self):
        self.extractor = EventExtractor()
        self.matcher = EventHistoryMatcher()

    def run_event_extraction(self, limit: Optional[int] = None) -> List[Dict]:
        return self.extractor.extract(limit)

    def run_full_consolidation(self, limit: Optional[int] = None) -> List[Dict]:
        events = self.extractor.extract(limit)
        if not events:
            return []
        print("\n📊 EventHistoryMatcher 统计结果：")
        return self.matcher.track(events)
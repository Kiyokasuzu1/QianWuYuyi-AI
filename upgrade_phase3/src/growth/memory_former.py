import json
import uuid
from typing import List, Dict, Optional
from datetime import datetime

from src.config import get
from src.response.llm import LLMClient


class MemoryFormer:
    """
    记忆形成器 v1.1 最终版
    输入：事件（Event）
    输出：记忆（Memory）
    职责：将"发生过什么"转化为"这件事意味着什么"
    """

    def __init__(self):
        self.llm = LLMClient()

    def _generate_memory_id(self) -> str:
        """生成唯一记忆 ID"""
        return f"memory_{uuid.uuid4().hex[:12]}"

    def _get_event_id(self, event: Dict) -> str:
        """获取或生成事件 ID（带 fallback）"""
        event_id = event.get("event_id")
        if event_id:
            return event_id

        topic = event.get("topic", "unknown")
        # 用 topic 生成一个稳定的 fallback ID
        return f"event_{abs(hash(topic))}"

    def _calculate_factual_confidence(self, event: Dict) -> float:
        """
        基于证据计算事实置信度（程序计算，不由 LLM 决定）
        """
        evidence = event.get("evidence", [])
        source_ids = event.get("source_ids", [])
        history = event.get("history", {})
        mention_count = history.get("mention_count", 0)

        base = 0.6
        evidence_bonus = min(len(evidence) * 0.05, 0.2)
        source_bonus = min(len(source_ids) * 0.03, 0.1)
        mention_bonus = min(mention_count * 0.01, 0.05)

        confidence = base + evidence_bonus + source_bonus + mention_bonus
        return round(min(confidence, 0.95), 2)

    def _normalize_tags(self, tags) -> Dict[str, List[str]]:
        """防御性处理 tags"""
        if not tags:
            return {"event": [], "relationship": [], "search": []}

        if not isinstance(tags, dict):
            return {"event": [], "relationship": [], "search": []}

        return {
            "event": tags.get("event", []) if isinstance(tags.get("event"), list) else [],
            "relationship": tags.get("relationship", []) if isinstance(tags.get("relationship"), list) else [],
            "search": tags.get("search", []) if isinstance(tags.get("search"), list) else []
        }

    def _build_prompt(self, event: Dict) -> str:
        topic = event.get("topic", "未知主题")
        event_desc = event.get("event", "")
        evidence = event.get("evidence", [])
        history = event.get("history", {})
        mention_count = history.get("mention_count", 0)
        date_span = history.get("date_span", 0)
        cross_session = history.get("cross_session", False)

        evidence_text = "\n".join([f"  - {e}" for e in evidence[:5]])

        prompt = f"""你是一个记忆形成系统。

你的任务：将一段经历转化为长期记忆。

主题：{topic}
事件描述：{event_desc}
证据片段：
{evidence_text}
历史提及次数：{mention_count} 次
时间跨度：{date_span} 天
跨会话：{'是' if cross_session else '否'}

【输出要求】
输出 JSON 格式，包含以下字段：

1. summary: 这段经历的核心事实概括
   - 基于真实发生的事，可引用证据中的内容
   - 不添加新信息

2. meaning: 这件事在用户与羽依互动历史中的意义
   - 只允许基于重复行为、明确表达、长期趋势推断
   - 禁止推断不存在的情感状态
   - 禁止描述 AI 的主观感受（禁止使用：'羽依感受到'、'羽依很幸福'、'羽依永远记得' 等）
   - 只能描述：用户行为、互动历史、明确表达
   - 如果不确定，保持简洁，降低 confidence

3. meaning_confidence: 意义推断的置信度 (0-1)
   - 0.9+: 有明确证据支撑（多次提及、明确表达）
   - 0.7-0.9: 有合理推断空间
   - <0.7: 不确定，仅基于单次事件

4. tags: 三层标签，每层 2-4 个
   - event: 事件层面标签（发生了什么）：["诞生", "配置", "启动"]
   - relationship: 关系层面标签（对关系的意义）：["第一次相遇", "陪伴"]
   - search: 检索层面标签（便于搜索）：["2026-07-16", "羽依第一天"]

输出 JSON：
{{
  "summary": "核心事实概括",
  "meaning": "关系意义推断",
  "meaning_confidence": 0.75,
  "tags": {{
    "event": ["标签1", "标签2"],
    "relationship": ["标签1", "标签2"],
    "search": ["标签1", "标签2"]
  }}
}}"""

        return prompt

    def _cap_meaning_confidence(self, confidence: float, event: Dict) -> float:
        """根据事件历史限制 meaning_confidence 上限"""
        history = event.get("history", {})
        mention_count = history.get("mention_count", 0)
        cross_session = history.get("cross_session", False)

        cap = 0.7  # 默认上限（单次事件）

        if mention_count >= 5:
            cap = 0.95
        elif mention_count >= 3:
            cap = 0.85

        if cross_session and mention_count >= 3:
            cap = min(cap + 0.05, 0.95)

        if cross_session and mention_count == 1:
            cap = 0.75

        return min(confidence, cap)

    def _parse_result(self, result: str, event: Dict) -> Dict:
        """解析 LLM 返回结果"""
        try:
            start = result.find('{')
            end = result.rfind('}') + 1
            if start == -1 or end == 0:
                return self._get_default_memory(event)

            json_str = result[start:end]
            data = json.loads(json_str)

            factual_confidence = self._calculate_factual_confidence(event)
            raw_meaning_confidence = float(data.get("meaning_confidence", 0.5))
            capped_meaning_confidence = self._cap_meaning_confidence(raw_meaning_confidence, event)

            return {
                "memory_id": self._generate_memory_id(),
                "topic": event.get("topic", "未知主题"),
                "source_event_id": self._get_event_id(event),
                "summary": {
                    "text": data.get("summary", event.get("event", "")),
                    "confidence": factual_confidence
                },
                "meaning": {
                    "text": data.get("meaning", ""),
                    "confidence": round(capped_meaning_confidence, 2)
                },
                "tags": self._normalize_tags(data.get("tags", {})),
                "memory_type": "episodic",
                "created_at": datetime.now().isoformat()
            }
        except Exception as e:
            print(f"⚠️ MemoryFormer 解析失败: {e}")
            return self._get_default_memory(event)

    def _get_default_memory(self, event: Dict) -> Dict:
        """返回默认记忆结构"""
        factual_confidence = self._calculate_factual_confidence(event)
        return {
            "memory_id": self._generate_memory_id(),
            "topic": event.get("topic", "未知主题"),
            "source_event_id": self._get_event_id(event),
            "summary": {
                "text": event.get("event", ""),
                "confidence": factual_confidence
            },
            "meaning": {
                "text": "",
                "confidence": 0.0
            },
            "tags": {"event": [], "relationship": [], "search": []},
            "memory_type": "episodic",
            "created_at": datetime.now().isoformat()
        }

    def form(self, event: Dict) -> Dict:
        """
        将事件转化为长期记忆
        输入：事件（含 topic、event、evidence、source_ids、history）
        输出：记忆（含 memory_id、topic、source_event_id、summary、meaning、tags）
        """
        if not event.get("topic") and not event.get("event"):
            return self._get_default_memory(event)

        prompt = self._build_prompt(event)

        try:
            response = self.llm.generate_raw(prompt)
        except Exception as e:
            print(f"❌ MemoryFormer 调用失败: {e}")
            return self._get_default_memory(event)

        memory = self._parse_result(response, event)

        print(f"\n🧠 MemoryFormer 完成:")
        print(f"   memory_id: {memory.get('memory_id', '')}")
        print(f"   topic: {memory.get('topic', '')}")
        print(f"   source_event_id: {memory.get('source_event_id', '')}")
        print(f"   summary: {memory.get('summary', {}).get('text', '')[:60]}...")
        print(f"   summary.confidence: {memory.get('summary', {}).get('confidence', 0)}")
        print(f"   meaning: {memory.get('meaning', {}).get('text', '')[:60]}...")
        print(f"   meaning.confidence: {memory.get('meaning', {}).get('confidence', 0)}")

        return memory
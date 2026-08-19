"""
反思评估器 v2.2
增加认知对比检测，避免仅靠关键词导致漏判
"""
from src.reflection.reflection_record import ReflectionRecord, ReflectionLevel


class ReflectionEvaluator:
    def evaluate(self, record: ReflectionRecord) -> ReflectionLevel:
        if not record.previous_self_view and not record.current_understanding:
            return ReflectionLevel.OBSERVATION

        if record.new_beliefs:
            for belief in record.new_beliefs:
                if any(kw in belief for kw in ["我是", "身份", "存在", "角色"]):
                    return ReflectionLevel.IDENTITY_CHANGE
            return ReflectionLevel.BELIEF_CHANGE

        if record.current_understanding and not record.previous_self_view:
            return ReflectionLevel.INSIGHT

        if record.previous_self_view and record.current_understanding:
            if self._has_belief_shift(record):
                return ReflectionLevel.BELIEF_CHANGE
            return ReflectionLevel.INSIGHT

        return ReflectionLevel.OBSERVATION

    def _has_belief_shift(self, record: ReflectionRecord) -> bool:
        new_text = record.current_understanding
        shift_keywords = [
            "但是", "原来", "发现", "不再", "改变了看法",
            "以前认为", "现在理解", "以前觉得", "现在觉得"
        ]
        if any(kw in new_text for kw in shift_keywords):
            return True
        if self._has_contrast(record):
            return True
        return False

    def _has_contrast(self, record: ReflectionRecord) -> bool:
        old = record.previous_self_view
        new = record.current_understanding
        contrast_words = ["以前", "过去", "曾经", "现在", "逐渐", "开始", "已经"]
        return (
            any(word in old for word in contrast_words) or
            any(word in new for word in contrast_words)
        )
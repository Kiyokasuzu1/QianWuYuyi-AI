"""
自检模块。

负责：
- 真实性检查（回复内容是否有记忆依据）
- 清晰度检查（是否表达清楚）
- 一致性检查（是否与历史矛盾）
"""

from datetime import datetime
from typing import Dict, List, Optional

from src.memory.memory_provider import MemoryProvider


class SelfChecker:
    """
    自检模块。

    在生成回复后、发送前进行自检。
    不修改现有模块，只读取数据进行验证。
    """

    def __init__(self):
        self._memory_store = None
        self._auditor = None
        self._check_history: List[Dict] = []

    def _get_memory_store(self):
        # Phase 4.4.3 Secondary Memory Authority 收口：优先通过 RuntimeBridge 获取共享实例
        if self._memory_store is None:
            try:
                from src.runtime.runtime_bridge import get_runtime_bridge
                _bridge = get_runtime_bridge()
                _shared_store = _bridge.get_memory_store()
                if _shared_store is not None:
                    self._memory_store = _shared_store
            except Exception:
                pass

        # Fallback：RuntimeBridge 不可用时 → MemoryProvider 共享单例
        if self._memory_store is None:
            try:
                # Phase 4.0-R2.3.2: 不再 MemoryStore() 自建，改为 Authority Provider 单例
                self._memory_store = MemoryProvider.get_store()
            except Exception:
                pass
        return self._memory_store

    def _get_auditor(self):
        if self._auditor is None:
            try:
                from src.safety.relational_expression_auditor import RelationalExpressionAuditor
                self._auditor = RelationalExpressionAuditor()
            except Exception:
                pass
        return self._auditor

    # ==========================
    # 真实性检查
    # ==========================

    def check_truthfulness(self, text: str, context: Dict = None) -> Dict:
        """
        检查真实性（是否有记忆依据）。

        Args:
            text: 待检查的回复文本
            context: 上下文信息

        Returns:
            {"passed": bool, "score": float, "issues": [...]}
        """
        issues = []

        # 1. 安全审核器检查
        auditor = self._get_auditor()
        if auditor is not None:
            try:
                audit_result = auditor.audit(text)
                if not audit_result.get("safe", True):
                    issues.append({
                        "type": "safety_violation",
                        "detail": audit_result.get("violations", []),
                    })
            except Exception:
                pass

        # 2. 事实声明检查（简单启发式）
        # 检查是否包含"你说过"等事实声明，如果有，验证是否有依据
        factual_keywords = ["你说过", "你之前说", "你曾经", "上次你说", "你答应过"]
        has_factual_claim = any(kw in text for kw in factual_keywords)

        if has_factual_claim:
            memory_store = self._get_memory_store()
            if memory_store is not None:
                memories = memory_store.load()
                # 简单检查：是否有相关记忆
                if len(memories) == 0:
                    issues.append({
                        "type": "unsupported_claim",
                        "detail": "回复中包含事实声明，但记忆库为空",
                    })

        score = max(0.0, 1.0 - len(issues) * 0.3)
        passed = len(issues) == 0

        return {
            "passed": passed,
            "score": round(score, 2),
            "issues": issues,
        }

    # ==========================
    # 清晰度检查
    # ==========================

    def check_clarity(self, text: str) -> Dict:
        """
        检查清晰度（是否表达清楚）。

        Args:
            text: 待检查的回复文本

        Returns:
            {"passed": bool, "score": float, "issues": [...]}
        """
        issues = []

        # 1. 长度检查
        text_stripped = text.strip()
        if len(text_stripped) < 2:
            issues.append({
                "type": "too_short",
                "detail": "回复过短",
            })

        if len(text_stripped) > 2000:
            issues.append({
                "type": "too_long",
                "detail": "回复过长，可能不够清晰",
            })

        # 2. 重复检查
        words = text_stripped.split()
        if len(words) > 5:
            # 检查是否有连续重复
            for i in range(len(words) - 1):
                if words[i] == words[i + 1] and len(words[i]) > 2:
                    issues.append({
                        "type": "repetition",
                        "detail": f"连续重复：{words[i]}",
                    })
                    break

        # 3. 模糊表达检查
        vague_expressions = ["可能吧", "也许", "不太清楚", "随便"]
        vague_count = sum(1 for expr in vague_expressions if expr in text)
        if vague_count >= 2:
            issues.append({
                "type": "vague",
                "detail": "回复中包含过多模糊表达",
            })

        score = max(0.0, 1.0 - len(issues) * 0.2)
        passed = len(issues) == 0

        return {
            "passed": passed,
            "score": round(score, 2),
            "issues": issues,
        }

    # ==========================
    # 一致性检查
    # ==========================

    def check_consistency(self, text: str, history: List[Dict] = None) -> Dict:
        """
        检查一致性（是否与历史矛盾）。

        Args:
            text: 待检查的回复文本
            history: 对话历史

        Returns:
            {"passed": bool, "score": float, "issues": [...]}
        """
        issues = []

        if not history or len(history) == 0:
            return {"passed": True, "score": 1.0, "issues": []}

        # 提取最近回复
        recent_replies = [
            h for h in history
            if h.get("role") == "assistant"
        ][-5:]

        if not recent_replies:
            return {"passed": True, "score": 1.0, "issues": []}

        # 简单矛盾检测：检查是否与最近回复有直接矛盾
        # 否定词 + 相同关键词
        negation_words = ["不是", "没有", "不对", "并非", "错"]
        for prev_reply in recent_replies:
            prev_content = prev_reply.get("content", "")
            if not prev_content:
                continue

            # 找共同关键词
            prev_words = set(prev_content.split())
            curr_words = set(text.split())
            common = prev_words & curr_words

            for word in common:
                if len(word) < 3:
                    continue
                # 检查当前回复中是否否定这个词
                for neg in negation_words:
                    if neg + word in text or word + neg in text:
                        issues.append({
                            "type": "contradiction",
                            "detail": f"可能矛盾的表述：{neg}{word}",
                        })
                        break

        score = max(0.0, 1.0 - len(issues) * 0.25)
        passed = len(issues) == 0

        return {
            "passed": passed,
            "score": round(score, 2),
            "issues": issues,
        }

    # ==========================
    # 完整自检
    # ==========================

    def full_check(
        self,
        text: str,
        context: Dict = None,
        history: List[Dict] = None
    ) -> Dict:
        """
        完整自检（真实性 + 清晰度 + 一致性）。

        Args:
            text: 待检查的回复文本
            context: 上下文信息
            history: 对话历史

        Returns:
            {
                "passed": bool,
                "overall_score": float,
                "truthfulness": {...},
                "clarity": {...},
                "consistency": {...},
                "timestamp": str,
            }
        """
        truthfulness = self.check_truthfulness(text, context)
        clarity = self.check_clarity(text)
        consistency = self.check_consistency(text, history)

        overall_score = (
            truthfulness["score"] * 0.5
            + clarity["score"] * 0.3
            + consistency["score"] * 0.2
        )

        passed = truthfulness["passed"] and clarity["passed"]

        result = {
            "passed": passed,
            "overall_score": round(overall_score, 2),
            "truthfulness": truthfulness,
            "clarity": clarity,
            "consistency": consistency,
            "timestamp": datetime.now().isoformat(),
        }

        # 记录检查历史
        self._check_history.append(result)
        if len(self._check_history) > 50:
            self._check_history = self._check_history[-50:]

        return result

    def get_recent_stats(self) -> Dict:
        """获取最近的检查统计"""
        if not self._check_history:
            return {"total": 0, "pass_rate": 0.0, "avg_score": 0.0}

        total = len(self._check_history)
        passed = sum(1 for r in self._check_history if r["passed"])
        avg_score = sum(r["overall_score"] for r in self._check_history) / total

        return {
            "total": total,
            "pass_rate": round(passed / total, 2),
            "avg_score": round(avg_score, 2),
        }


# 全局实例
_global_checker: Optional[SelfChecker] = None


def get_self_checker() -> SelfChecker:
    """获取全局自检器实例"""
    global _global_checker
    if _global_checker is None:
        _global_checker = SelfChecker()
    return _global_checker
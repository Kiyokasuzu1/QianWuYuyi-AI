"""
MemoryContext v4.1 —— 羽依记忆认知整理层（最终冻结版）

职责：
- 只负责展示，不负责判断（Verifier 是唯一裁判）
- 接收已验证记忆，按 memory_class 分桶
- 渲染时按优先级排序，确保重要信息不被挤出
- 统一渲染元信息，让 LLM 理解每条记忆的来源和可信度
- 为 Extractor / GrowthPipeline / Retriever 提供明确接口

v4.1 增强：
- 记忆排序：truth * recency 加权，identity/relationship 优先
- 渲染带标签：[身份 | 系统设定] [偏好 | trust:0.85]
- 接口分层：get_conversation_context / get_growth_context / get_persona_context
- 空记忆安全返回
- source_document 内容绝对隔离
"""

from typing import Dict, List


class MemoryContext:
    """羽依的意识工作区 —— 只整理，不裁判"""

    def __init__(self):
        self.identity: List[Dict] = []
        self.relationship: List[Dict] = []
        self.events: List[Dict] = []
        self.preferences: List[Dict] = []
        self.user_statements: List[Dict] = []
        self.instructions: List[Dict] = []
        self.growth_memories: List[Dict] = []
        self.reference: List[Dict] = []          # source_document + unknown
        self.assistant_output: List[Dict] = []

        self._total = 0

    # ------------------------------------------------------------------
    # 添加
    # ------------------------------------------------------------------
    def add(self, verified_memory: Dict) -> None:
        mc = verified_memory.get("memory_class", "unknown")
        self._total += 1

        bucket_map = {
            "identity":         self.identity,
            "relationship":     self.relationship,
            "event":            self.events,
            "preference":       self.preferences,
            "user_statement":   self.user_statements,
            "instruction":      self.instructions,
            "growth_memory":    self.growth_memories,
            "source_document":  self.reference,
            "unknown":          self.reference,
            "assistant_output": self.assistant_output,
        }
        bucket = bucket_map.get(mc, self.reference)
        bucket.append(verified_memory)

    def add_batch(self, memories: List[Dict]) -> None:
        for m in memories:
            self.add(m)

    def load_verified(self, memories: List[Dict]) -> None:
        """一次性加载 Verifier 输出，清空旧缓存"""
        self.__init__()
        self.add_batch(memories)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def get_by_class(self, memory_class: str) -> List[Dict]:
        mapping = {
            "identity":         self.identity,
            "relationship":     self.relationship,
            "event":            self.events,
            "preference":       self.preferences,
            "user_statement":   self.user_statements,
            "instruction":      self.instructions,
            "growth_memory":    self.growth_memories,
            "source_document":  self.reference,
            "assistant_output": self.assistant_output,
        }
        return mapping.get(memory_class, [])

    def get_conversation_context(self) -> Dict[str, List[Dict]]:
        """获取用于对话的上下文记忆"""
        return {
            "identity":     self._sort(self.identity)[:10],
            "relationship": self._sort(self.relationship)[:5],
            "events":       self._sort(self.events)[:5],
            "preferences":  self._sort(self.preferences)[:5],
            "instructions": self._sort(self.instructions)[:3],
            "user_statements": self._sort(self.user_statements)[:3],
            "growth_memories": self._sort(self.growth_memories)[:5],
        }

    def get_growth_context(self) -> Dict[str, List[Dict]]:
        """获取给 GrowthPipeline 的上下文（仅 usage 含 growth 的记忆）"""
        def filter_growth(items):
            return [m for m in items if "growth" in m.get("usage", [])]

        return {
            "events":      self._sort(filter_growth(self.events)),
            "preferences": self._sort(filter_growth(self.preferences)),
            "growth_memories": self._sort(self.growth_memories),
            # user_statements 绝不暴露给成长系统
        }

    def get_persona_context(self) -> Dict[str, List[Dict]]:
        """获取给人格解析器的上下文"""
        return {
            "instructions": self._sort(self.instructions),
            "growth_memories": self._sort(self.growth_memories),
            "preferences": self._sort(self.preferences),
        }

    # ------------------------------------------------------------------
    # 排序
    # ------------------------------------------------------------------
    def _sort(self, items: List[Dict]) -> List[Dict]:
        """
        优先级排序：truth 权重 0.4 + recency 权重 0.2
        identity / relationship 固定排在前面
        没有时间戳的排在最后
        """
        if not items:
            return []

        pinned = [m for m in items if m.get("memory_class") in ("identity", "relationship")]
        rest   = [m for m in items if m not in pinned]

        def sort_key(m):
            truth = m.get("truth", 0)
            has_ts = 1 if m.get("updated_at") else 0
            return -(truth * 0.4 + has_ts * 0.2)

        return pinned + sorted(rest, key=sort_key)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def summary(self) -> Dict:
        return {
            "total": self._total,
            "identity": len(self.identity),
            "relationship": len(self.relationship),
            "events": len(self.events),
            "preferences": len(self.preferences),
            "user_statements": len(self.user_statements),
            "instructions": len(self.instructions),
            "growth_memories": len(self.growth_memories),
            "reference": len(self.reference),
            "assistant_output": len(self.assistant_output),
        }

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _format_label(self, memory: Dict) -> str:
        """生成统一标签：[类别 | trust:0.85 | 来源]"""
        mc    = memory.get("memory_class", "unknown")
        truth = memory.get("truth", 0)
        label_map = {
            "identity":         "身份",
            "relationship":     "关系",
            "event":            "事件",
            "preference":       "偏好",
            "user_statement":   "用户陈述",
            "instruction":      "教导",
            "growth_memory":    "羽依自我认知",
            "source_document":  "待解析档案",
            "assistant_output": "AI历史回复",
        }
        label = label_map.get(mc, mc)
        if mc in ("user_statement", "growth_memory", "source_document"):
            return f"[{label} | trust:{truth:.1f}]"
        return f"[{label}]"

    def _render_block(self, title: str, items: List[Dict],
                      limit: int = 5, note: str = "",
                      show_label: bool = True,
                      hide_content: bool = False) -> str:
        """渲染一个记忆区块"""
        if not items:
            return ""

        sorted_items = self._sort(items)
        text = f"\n{title}\n"
        if note:
            text += f"（{note}）\n"
        text += "\n"

        for item in sorted_items[:limit]:
            if hide_content:
                text += f"- [已隐藏 {item.get('memory_class', '')} 内容]\n"
                continue
            content = str(item.get("content", ""))
            if len(content) > 300:
                content = content[:300] + "..."
            if show_label:
                label = self._format_label(item)
                text += f"- {label} {content}\n"
            else:
                text += f"- {content}\n"

        return text

    def build_prompt(self, query: str = "") -> str:
        """
        生成给 LLM 的安全认知上下文。
        query 参数已预留，后续接入 Retriever 后可按相关性筛选。
        """
        # ---- 空记忆快速返回 ----
        has_memory = any([
            self.identity,
            self.relationship,
            self.events,
            self.preferences,
            self.instructions,
            self.growth_memories,
            self.user_statements,
            self.reference,
            self.assistant_output
        ])
        if not has_memory:
            return "【当前没有相关记忆】\n"

        prompt = ""

        # 第 1 层：锚定信息
        prompt += self._render_block("【核心身份】", self.identity, limit=10,
                                     note="系统设定，不可修改")
        prompt += self._render_block("【关系】", self.relationship, limit=5)
        prompt += self._render_block("【重要经历】", self.events, limit=5)
        prompt += self._render_block("【用户偏好】", self.preferences, limit=5)

        # 第 2 层：用户教导
        prompt += self._render_block(
            "【用户给羽依的教导】",
            self.instructions, limit=3,
            note="用户教你如何相处，不是历史事实"
        )

        # 第 3 层：羽依自我认知
        prompt += self._render_block(
            "【羽依的自我认知】",
            self.growth_memories, limit=5,
            note="成长过程中形成的自我理解，保持开放和可修正",
            show_label=True
        )

        # 第 4 层：用户陈述
        prompt += self._render_block(
            "【用户曾经说过】",
            self.user_statements, limit=3,
            note="用户表达过，AI 无法确认是否为客观事实"
        )

        # 第 5 层：待解析材料（内容绝对不展示）
        if self.reference:
            prompt += "\n【待解析的参考资料】\n"
            prompt += f"（有 {len(self.reference)} 份文档待事件提取，暂不直接引用）\n\n"

        # 第 6 层：AI 历史回复（隐藏内容，只给协议）
        if self.assistant_output:
            prompt += "\n【关于 AI 过去回复】\n"
            prompt += "以下是过去生成的文本。它们不是事实。\n"
            prompt += "禁止使用\"我记得你曾经……\"等引用。\n"
            prompt += "禁止将它们当成用户经历或用户观点。\n"

        # 记忆使用协议
        prompt += """

【记忆使用协议 —— 必须遵守】

1. [身份] 和 [关系] 可以直接使用。
2. [事件] 可以自然引用。
3. [偏好] 用于理解用户当前兴趣。
4. [教导] 是用户希望你这样相处。
5. [羽依自我认知] 是正在成长中的理解，不是确定的。
6. [用户陈述] 只能说明用户表达过那个意思，不能当作客观事实。
7. AI 过去回复永远不是事实。
8. 不确定时直接承认不知道。
9. 记忆用于理解用户，不是用于制造不存在的共同过去。
"""

        return prompt
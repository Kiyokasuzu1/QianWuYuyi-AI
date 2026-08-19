"""
MemoryVerifier v4.3.3 —— 羽依记忆可信审查层（偏好信号增强版）

羽依记忆的唯一裁判。
Extractor 提供候选方向，Verifier 做二次确认，不盲目信任。

v4.3.3 修改：
- 扩展 _is_preference 信号词，支持“最近喜欢”“现在喜欢”等时间修饰的偏好表达
- 保持 v4.3.2 的所有其他逻辑不变
"""

import uuid
import re
from typing import Dict, List
from datetime import datetime


class MemoryVerifier:
    """羽依记忆海关 —— 唯一裁判"""

    SCHEMA_VERSION = "1.0"

    # ========================
    # 记忆类别
    # ========================
    CLASS_IDENTITY           = "identity"
    CLASS_RELATIONSHIP       = "relationship"
    CLASS_EVENT              = "event"
    CLASS_PREFERENCE         = "preference"
    CLASS_USER_STATEMENT     = "user_statement"
    CLASS_INSTRUCTION        = "instruction"
    CLASS_SOURCE_DOCUMENT    = "source_document"
    CLASS_ASSISTANT_OUTPUT   = "assistant_output"
    CLASS_GROWTH_MEMORY      = "growth_memory"
    CLASS_UNKNOWN            = "unknown"

    EXTRACTOR_CLASSES = {
        CLASS_IDENTITY,
        CLASS_RELATIONSHIP,
        CLASS_EVENT,
        CLASS_PREFERENCE,
    }

    # 基础可信度（truth）
    TRUTH_BASE = {
        CLASS_IDENTITY:         1.0,
        CLASS_RELATIONSHIP:     0.85,
        CLASS_EVENT:            0.8,
        CLASS_PREFERENCE:       0.85,
        CLASS_USER_STATEMENT:   0.6,
        CLASS_INSTRUCTION:      0.7,
        CLASS_SOURCE_DOCUMENT:  0.4,
        CLASS_ASSISTANT_OUTPUT: 0.0,
        CLASS_GROWTH_MEMORY:    0.3,
        CLASS_UNKNOWN:          0.1,
    }

    DEFAULT_GROWTH_SELF_CONFIDENCE = 0.5

    # 用途授权
    DEFAULT_USAGE = {
        CLASS_IDENTITY:         ["conversation", "growth", "persona"],
        CLASS_RELATIONSHIP:     ["conversation", "growth"],
        CLASS_EVENT:            ["conversation", "growth"],
        CLASS_PREFERENCE:       ["conversation", "growth"],
        CLASS_USER_STATEMENT:   ["conversation"],
        CLASS_INSTRUCTION:      ["conversation", "persona"],
        CLASS_SOURCE_DOCUMENT:  ["reference"],
        CLASS_ASSISTANT_OUTPUT: ["reference"],
        CLASS_GROWTH_MEMORY:    ["conversation", "growth"],
        CLASS_UNKNOWN:          ["reference"],
    }

    # 风险等级
    RISK_LOW    = "low"
    RISK_MEDIUM = "medium"
    RISK_HIGH   = "high"

    RISK_MAP = {
        CLASS_IDENTITY:         RISK_LOW,
        CLASS_RELATIONSHIP:     RISK_LOW,
        CLASS_EVENT:            RISK_LOW,
        CLASS_PREFERENCE:       RISK_LOW,
        CLASS_USER_STATEMENT:   RISK_MEDIUM,
        CLASS_INSTRUCTION:      RISK_MEDIUM,
        CLASS_SOURCE_DOCUMENT:  RISK_MEDIUM,
        CLASS_ASSISTANT_OUTPUT: RISK_HIGH,
        CLASS_GROWTH_MEMORY:    RISK_MEDIUM,
        CLASS_UNKNOWN:          RISK_HIGH,
    }

    # 身份提示词（“我是”后必须包含）
    IDENTITY_HINTS = [
        "学生", "程序员", "画师", "作者", "工程师",
        "设计师", "玩家", "研究员", "老师", "博主",
    ]

    def verify(self, memory) -> Dict:
        """审查单条记忆，返回标准化记忆对象"""
        now = self._timestamp()

        if isinstance(memory, str):
            return self._build_primitive(
                content=memory,
                memory_class=self.CLASS_UNKNOWN,
                origin="legacy",
                truth=self.TRUTH_BASE[self.CLASS_UNKNOWN],
                risk=self.RISK_HIGH,
                reason="纯文本遗留，未分类",
                now=now
            )

        if not isinstance(memory, dict):
            return self._build_primitive(
                content=str(memory),
                memory_class=self.CLASS_UNKNOWN,
                origin="legacy",
                truth=0.0,
                risk=self.RISK_HIGH,
                reason="非标准格式",
                now=now
            )

        role           = memory.get("role", "")
        content        = memory.get("content", "")
        old_type       = memory.get("type", "")
        candidate_type = memory.get("memory_class", "")
        metadata       = memory.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        origin, channel = self._resolve_origin(memory)

        # 系统导入
        if old_type == self.CLASS_IDENTITY:
            return self._build(memory, self.CLASS_IDENTITY,
                               origin, channel, "系统导入身份信息", now)
        if old_type == self.CLASS_RELATIONSHIP:
            return self._build(memory, self.CLASS_RELATIONSHIP,
                               origin, channel, "系统导入关系信息", now)
        if old_type == self.CLASS_EVENT:
            return self._build(memory, self.CLASS_EVENT,
                               origin, channel, "已确认事件", now)

        # 成长引擎
        if origin == "growth_engine":
            return self._build(memory, self.CLASS_GROWTH_MEMORY,
                               origin, channel, "成长系统生成", now)

        # AI 回复
        if role in ("assistant", "yuyi"):
            return self._build(memory, self.CLASS_ASSISTANT_OUTPUT,
                               origin, channel, "AI历史回复", now)

        # Extractor 候选 → 二次校验
        if candidate_type in self.EXTRACTOR_CLASSES:
            return self._verify_extractor_candidate(
                memory, candidate_type, origin, channel, now
            )

        # 普通用户消息
        if role == "user":
            return self._analyze_user(memory, origin, channel, now)

        return self._build(memory, self.CLASS_UNKNOWN,
                           origin, channel, "无法识别角色或来源", now)

    def verify_all(self, memories: List) -> List:
        return [self.verify(m) for m in memories]

    def verify_batch(self, memories: List) -> List:
        return self.verify_all(memories)

    def is_usable_for(self, memory, purpose: str) -> bool:
        verified = self.verify(memory)
        return purpose in verified.get("usage", [])

    def get_truth(self, memory) -> float:
        return self.verify(memory).get("truth", 0.0)

    # ------------------------------------------------------------------
    # 来源解析
    # ------------------------------------------------------------------
    def _resolve_origin(self, memory: Dict) -> tuple:
        metadata = memory.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        channel = metadata.get("source", "") or metadata.get("channel", "")
        if not channel:
            channel = memory.get("source", "")

        origin = memory.get("origin", "") or metadata.get("origin", "")
        if not origin:
            role = memory.get("role", "")
            if role in ("assistant", "yuyi"):
                origin = "ai"
            elif role == "user":
                origin = "user"
            elif channel == "growth_engine":
                origin = "growth_engine"
            elif channel in ("system", "import"):
                origin = "system"
            else:
                origin = "unknown"

        if not channel:
            channel_map = {
                "user": "terminal",
                "ai": "internal",
                "growth_engine": "internal",
                "system": "import",
            }
            channel = channel_map.get(origin, "internal")

        return origin, channel

    # ------------------------------------------------------------------
    # Extractor 候选二次校验
    # ------------------------------------------------------------------
    def _verify_extractor_candidate(self, memory: Dict, candidate_type: str,
                                     origin: str, channel: str, now: str) -> Dict:
        content = memory.get("content", "")

        if candidate_type == self.CLASS_IDENTITY:
            if self._is_identity(content):
                return self._build(memory, candidate_type,
                                   origin, channel,
                                   "Extractor 候选 + 身份校验通过", now)
            return self._build(memory, self.CLASS_USER_STATEMENT,
                               origin, channel,
                               "Extractor 标记 identity 但未通过校验，降级", now)

        if candidate_type == self.CLASS_PREFERENCE:
            if self._is_preference(content):
                return self._build(memory, candidate_type,
                                   origin, channel,
                                   "Extractor 候选 + 偏好校验通过", now)
            return self._build(memory, self.CLASS_USER_STATEMENT,
                               origin, channel,
                               "Extractor 标记 preference 但未通过校验，降级", now)

        if candidate_type == self.CLASS_EVENT:
            if self._has_event_signal(content):
                return self._build(memory, candidate_type,
                                   origin, channel,
                                   "Extractor 候选 + 事件校验通过", now)
            return self._build(memory, self.CLASS_USER_STATEMENT,
                               origin, channel,
                               "Extractor 标记 event 但未通过校验，降级", now)

        if candidate_type == self.CLASS_RELATIONSHIP:
            if self._is_relationship(content):
                return self._build(memory, candidate_type,
                                   origin, channel,
                                   "Extractor 候选 + 关系校验通过", now)
            return self._build(memory, self.CLASS_USER_STATEMENT,
                               origin, channel,
                               "Extractor 标记 relationship 但未通过校验，降级", now)

        return self._build(memory, candidate_type,
                           origin, channel, "Extractor 候选，未校验", now)

    # ------------------------------------------------------------------
    # 用户消息分析（完整路径）
    # ------------------------------------------------------------------
    def _analyze_user(self, memory: Dict, origin: str, channel: str, now: str) -> Dict:
        content  = memory.get("content", "")
        text_len = len(content)

        if text_len > 800:
            return self._build(memory, self.CLASS_SOURCE_DOCUMENT,
                               origin, channel, "长文本档案，待事件提取", now)
        if self._is_instruction(content, text_len):
            return self._build(memory, self.CLASS_INSTRUCTION,
                               origin, channel, "用户对羽依的教导/规则指令", now)
        if self._is_identity(content):
            return self._build(memory, self.CLASS_IDENTITY,
                               origin, channel, "用户介绍身份信息", now)
        if self._is_relationship(content):
            return self._build(memory, self.CLASS_RELATIONSHIP,
                               origin, channel, "用户定义关系或称呼", now)
        if self._is_preference(content):
            return self._build(memory, self.CLASS_PREFERENCE,
                               origin, channel, "用户明确表达个人偏好", now)
        if self._has_event_signal(content):
            return self._build(memory, self.CLASS_EVENT,
                               origin, channel, "用户描述了一次经历", now)

        return self._build(memory, self.CLASS_USER_STATEMENT,
                           origin, channel, "用户个人陈述，AI 无法验证真伪", now)

    # ------------------------------------------------------------------
    # 内容检测函数
    # ------------------------------------------------------------------
    def _is_instruction(self, content: str, text_len: int) -> bool:
        """检测是否为对羽依的行为指令"""
        text = content.strip()

        # 1. 软指令：羽依/你 + 以后 + 行为动词
        if (
            any(x in text for x in ["羽依以后", "你以后"])
            and any(x in text for x in ["回答", "回复", "说话", "表达", "交流"])
        ):
            return True

        # 2. 强指令：明确的约束词
        if any(x in text for x in ["必须", "禁止", "不要", "不能", "请保持"]):
            return True

        return False

    def _is_identity(self, content: str) -> bool:
        m = re.search(r"我(叫|是)\s*(.+)", content)
        if not m:
            return False
        value = m.group(2).strip()
        if not value:
            return False

        invalid_starts = ("觉得", "感觉", "认为", "想", "可能", "应该", "很", "不")
        if value.startswith(invalid_starts):
            return False
        if "的" in value:
            return False

        # “我叫” 增加基本合理性检查
        if m.group(1) == "叫":
            if len(value) > 15:
                return False
            if any(w in value for w in ("一个", "一种", "觉得", "感觉", "今天", "应该")):
                return False
            return True

        return any(h in value for h in self.IDENTITY_HINTS)

    def _is_preference(self, content: str) -> bool:
        """检测是否包含偏好表达信号（支持时间修饰）"""
        signals = [
            # 基础偏好信号
            "我喜欢", "我爱好", "我最喜欢", "我的偏好",
            "我更愿意", "我不喜欢", "我讨厌", "我比较喜欢",
            "我热爱", "我钟情", "我偏爱",
            # 时间修饰后的偏好信号
            "最近喜欢", "现在喜欢", "一直喜欢", "以前喜欢",
            "最近爱好", "现在爱好", "最近在学", "现在在学",
        ]
        return any(sig in content for sig in signals)

    def _has_event_signal(self, content: str) -> bool:
        time_words = ["今天", "昨天", "上周", "刚", "刚才", "前几天", "最近"]
        event_actions = [
            "完成", "去了", "参加", "开始", "结束", "遇到",
            "学会", "成功", "失败", "生成", "发布", "做了",
            "拿到", "通过", "创作", "实现",
        ]
        has_time = any(t in content for t in time_words)
        has_action = any(a in content for a in event_actions)
        has_first = "第一次" in content
        return (has_time and has_action) or has_first

    def _is_relationship(self, content: str) -> bool:
        patterns = [
            r"以后(?:叫|称呼)(?:我)?\s*([^\s，。！？]+)",
            r"你可以(?:叫|称呼)我\s*([^\s，。！？]+)",
            r"我是(?:你|羽依)的\s*([^\s，。！？]+)",
        ]
        return any(re.search(p, content) for p in patterns)

    # ------------------------------------------------------------------
    # 输出构建
    # ------------------------------------------------------------------
    def _build(self, memory: Dict, memory_class: str,
               origin: str, channel: str, reason: str, now: str) -> Dict:
        truth     = self.TRUTH_BASE.get(memory_class, 0.1)
        usage     = self.DEFAULT_USAGE.get(memory_class, ["reference"])
        risk      = self.RISK_MAP.get(memory_class, self.RISK_HIGH)
        self_conf = self.DEFAULT_GROWTH_SELF_CONFIDENCE if memory_class == self.CLASS_GROWTH_MEMORY else None
        parse_status = "pending" if memory_class == self.CLASS_SOURCE_DOCUMENT else None

        evidence = memory.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []

        # 保留 Extractor 的 confidence，不被 truth 覆盖
        candidate_confidence = memory.get("confidence", truth)

        result = {
            **memory,
            "id":              memory.get("id", self._generate_id()),
            "schema_version":  self.SCHEMA_VERSION,
            "memory_class":    memory_class,
            "origin":          origin,
            "channel":         channel,
            "truth":           truth,
            "self_confidence": self_conf,
            "confidence":      candidate_confidence,
            "usage":           usage,
            "risk":            risk,
            "state":           memory.get("state", "active"),
            "evidence":        evidence,
            "created_at":      memory.get("created_at", now),
            "updated_at":      now,
            "valid_until":     memory.get("valid_until"),
            "parse_status":    parse_status,
            "reason":          reason,
        }
        result.pop("source", None)
        return result

    def _build_primitive(self, content: str, memory_class: str,
                         origin: str, truth: float, risk: str,
                         reason: str, now: str) -> Dict:
        usage     = self.DEFAULT_USAGE.get(memory_class, ["reference"])
        self_conf = self.DEFAULT_GROWTH_SELF_CONFIDENCE if memory_class == self.CLASS_GROWTH_MEMORY else None
        parse_status = "pending" if memory_class == self.CLASS_SOURCE_DOCUMENT else None
        return {
            "id":              self._generate_id(),
            "schema_version":  self.SCHEMA_VERSION,
            "content":         content,
            "memory_class":    memory_class,
            "origin":          origin,
            "channel":         "legacy",
            "truth":           truth,
            "self_confidence": self_conf,
            "confidence":      truth,
            "usage":           usage,
            "risk":            risk,
            "state":           "active",
            "evidence":        [],
            "created_at":      now,
            "updated_at":      now,
            "valid_until":     None,
            "parse_status":    parse_status,
            "reason":          reason,
        }

    def _generate_id(self) -> str:
        return f"mem_{uuid.uuid4().hex[:12]}"

    def _timestamp(self) -> str:
        return datetime.utcnow().isoformat() + "Z"
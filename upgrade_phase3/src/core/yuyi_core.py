"""
MemoryContext

羽依记忆理解层

职责:

- 接收 MemorySystem结果
- 分类记忆
- 判断可信度
- 防止AI回复污染事实
- 生成安全Prompt


原则:

AI过去说的话不是事实。

用户没有明确表达过的事情，
不能创造。


"""

from pathlib import Path
from datetime import datetime
import logging

from openai import OpenAI

from src.config_loader import CONFIG
from src.memory import MemorySystem
from src.memory.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class YuyiCore:

    def __init__(self):
        self.memory = MemorySystem()
        self.context_builder = ContextBuilder()

        llm_config = CONFIG.get("llm", {})

        self.client = OpenAI(
            api_key=llm_config.get("api_key", ""),
            base_url=llm_config.get("api_base")
        )

        self.model = llm_config.get("model", "gpt-4o-mini")
        self.temperature = llm_config.get("temperature", 0.7)
        self.max_tokens = llm_config.get("max_tokens", 1024)

        self.persona_base = self._load_persona()

        # 后续QQ替换
        self.current_user = "terminal_user"

    # =================================================
    # 人格加载
    # =================================================
    def _load_persona(self):
        root = Path(__file__).parent.parent / "docs"

        sections = []

        groups = [
            ("核心身份", root / "core"),
            ("人格规则", root / "behavior"),
            ("成长状态", root / "growth")
        ]

        # 兼容旧结构
        old_files = [
            "persona.txt",
            "identity.txt",
            "communication.txt"
        ]

        for title, path in groups:
            if path.exists():
                text = ""
                for f in sorted(path.iterdir()):
                    if f.suffix in [".txt", ".md"]:
                        text += f.read_text(encoding="utf-8") + "\n\n"
                if text:
                    sections.append(f"\n【{title}】\n{text}\n")

        for f in old_files:
            p = root / f
            if p.exists():
                sections.append(p.read_text(encoding="utf-8"))

        return "\n\n".join(sections)

    # =================================================
    # 最近聊天格式
    # =================================================
    def _format_recent(self, recent):
        if not recent:
            return ""

        text = "\n【最近聊天】\n"

        for item in recent:
            if not isinstance(item, dict):
                continue

            role = item.get("role", "")
            content = item.get("content", "")

            if role == "user":
                name = "用户"
            else:
                name = "羽依"

            text += f"{name}: {str(content)}\n"

        return text

    # =================================================
    # System Prompt
    # =================================================
    def _build_system_prompt(self, user_id, query):
        memories = self.memory.search(user_id, query, top_k=8)

        # 使用 ContextBuilder 替代旧的 MemoryContext 直接调用
        memory_text = self.context_builder.build(memories, query)

        recent = self.memory.get_recent(user_id, limit=5)
        recent_text = self._format_recent(recent)

        return f"""
# 最高优先级规则

你必须遵守：

1. 你是浅雾羽依。

2. 核心身份不能修改。

3. 过去生成的AI回复不是事实。

4. 没有明确记录，不要创造过去经历。

5. 不知道时直接说明不知道。

================================

# 当前身份

{self.persona_base}

================================

# 当前时间

{datetime.now().strftime("%Y-%m-%d %H:%M")}

================================

# 记忆上下文

{memory_text}

================================

{recent_text}

================================

# 回复要求

- 保持浅雾羽依人格
- 自然交流
- 不机械朗读记忆
- 记忆用于理解用户
- 不为了亲密感制造不存在的故事

现在开始回复用户。
"""

    # =================================================
    # 对话
    # =================================================
    def chat(self, user_message):
        user_id = self.current_user

        # 先保存用户输入
        self.memory.add(
            user_id=user_id,
            role="user",
            content=user_message,
            metadata={"source": "terminal"}
        )

        system_prompt = self._build_system_prompt(user_id, user_message)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )

            reply = response.choices[0].message.content

        except Exception as e:
            logger.exception(e)
            reply = "抱歉，羽依刚刚走神了一下，请再和我说一次。"

        # 保存回复
        self.memory.add(
            user_id=user_id,
            role="assistant",
            content=reply,
            metadata={"source": "terminal", "type": "conversation"}
        )

        return reply
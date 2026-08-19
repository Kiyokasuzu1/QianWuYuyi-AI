import json
import hashlib
from pathlib import Path
from datetime import datetime

from src.config import get
from src.memory.memory_store import MemoryStore
from src.response.llm import LLMClient
from src.utils.text import clean_content


class EventExtractor:
    """
    羽依成长系统
    事件提取器 v5.0

    聊天记录
        ↓
    原始事件

    只负责发现：
    发生了什么
    """

    def __init__(self):

        self.store = MemoryStore()

        self.llm = LLMClient()

        self.batch_size = get(
            "growth.consolidation_batch_size",
            50
        )

        self.target_user_id = get(
            "memory.target_user_id",
            "366648462"
        )

        # failures dir for LLM parsing issues
        self._fail_dir = Path("data/llm_failures")
        self._fail_dir.mkdir(parents=True, exist_ok=True)


    def get_unprocessed_memories(
        self,
        limit=None
    ):

        memories = self.store.get_by_user(
            self.target_user_id
        )

        limit = limit or self.batch_size

        result=[]

        for mem in memories:

            if not mem.get("summary"):

                result.append(mem)

                if len(result)>=limit:
                    break

        return result


    def _format_memories(
        self,
        memories
    ):

        lines=[]

        for idx,mem in enumerate(memories):

            role = (
                "用户"
                if mem.get("role")=="user"
                else
                "羽依"
            )

            content = clean_content(
                mem.get(
                    "content",
                    ""
                )
            )

            if len(content)>800:

                content = (
                    content[:400]
                    +
                    "\n...[省略]...\n"
                    +
                    content[-400:]
                )


            lines.append(
                f"[{idx}] {role}: {content}"
            )


        return "\n".join(lines)


    def build_prompt(
        self,
        memories
    ):

        chat = self._format_memories(
            memories
        )

        return f"""

你是浅雾羽依成长系统的事件提取器。

任务：

从聊天记录中提取真实发生的成长事件。


不要总结。
不要评价。
不要编造。


只有以下事件可以提取：

- 羽依第一次启动/唤醒
- 配置完成
- 用户表达重要情感
- 长期陪伴约定
- 用户创造相关内容
- 明确偏好形成


普通聊天不要提取。


event_type只能：

relationship
milestone
creation
identity
preference
conversation



必须严格输出：

{{
"events":[
{{
"event":"事件名称",
"topic":"事件主题",
"event_type":"类型",
"evidence":[
{{
"text":"原文",
"role":"user 或 assistant",
"source_index":数字
}}
]
}}
]
}}



聊天：

{chat}


只输出JSON。

"""


    def _safe_load_json(self, text: str):
        """
        更稳健的 JSON 提取：优先尝试全文解析，若失败尝试提取最外层大括号，
        若仍失败返回 None（上层负责写入 failure queue）。
        """
        try:
            return json.loads(text)
        except Exception:
            # 退回到查找第一个 '{' 与最后一个 '}'（保守）
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                return None
            try:
                return json.loads(text[start:end+1])
            except Exception:
                return None

    def _write_failure(self, prompt: str, result: str):
        fname = self._fail_dir / "failures.jsonl"
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "prompt": prompt[:200],
            "response": result[:500]
        }
        with open(fname, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


    def _extract_json(
        self,
        text
    ):

        # kept for backward compatibility but replaced by _safe_load_json usage
        start=text.find("{")
        end=text.rfind("}")

        if start==-1 or end==-1:
            return None

        return text[start:end+1]


    def parse_result(
        self,
        result,
        memories,
        prompt: str = None
    ):

        try:
            # Attempt robust JSON parsing
            parsed = self._safe_load_json(result)
            if parsed is None:
                # write failure for manual review
                try:
                    self._write_failure(prompt or "", result)
                except Exception:
                    pass
                print("⚠️JSON 解析失败: 写入待审队列")
                return []

            data = parsed

        except Exception as e:

            print(
                "⚠️JSON解析失败:",
                e
            )

            try:
                self._write_failure(prompt or "", result)
            except Exception:
                pass

            return []

        output=[]

        for event in data.get(
            "events",
            []
        ):

            # 兼容旧模型输出
            if not event.get(
                "event"
            ):

                event["event"] = event.get(
                    "description",
                    "未知事件"
                )

            if not event.get(
                "topic"
            ):

                event["topic"] = event.get(
                    "description",
                    event["event"]
                )

            source_ids=[]
            evidence=[]

            for ev in event.get(
                "evidence",
                []
            ):

                idx=ev.get(
                    "source_index"
                )

                if idx is None:
                    continue

                if idx>=len(memories):
                    # skip out-of-range evidence but keep evidence text
                    evidence.append({
                        "text": ev.get("text", ""),
                        "role": ev.get("role", "assistant"),
                        "source_index": idx,
                        "memory_id": None
                    })
                    continue

                mem=memories[idx]

                mem_id=mem.get(
                    "id"
                )

                if not mem_id:
                    raw=(
                        mem.get(
                            "content",
                            ""
                        )
                    )

                    mem_id=(
                        "mem_"
                        +
                        hashlib.md5(
                            raw.encode(
                                "utf-8"
                            )
                        )
                        .hexdigest()[:12]
                    )

                evidence.append(
                    {
                        "text":
                            mem.get(
                                "content",
                                ""
                            ),

                        "role":
                            mem.get(
                                "role",
                                "assistant"
                            ),

                        "source_index":
                            idx,

                        "memory_id":
                            mem_id
                    }
                )

                source_ids.append(
                    mem_id
                )

            # If no evidence found in-range, but event provided evidence text, we still keep the event
            if not source_ids and not evidence:
                # keep events that have textual evidence in the payload even if not matched to memory
                raw_evidence = event.get("evidence", [])
                for ev in raw_evidence:
                    if ev.get("text"):
                        evidence.append({
                            "text": ev.get("text"),
                            "role": ev.get("role", "assistant"),
                            "source_index": ev.get("source_index")
                        })

            if not evidence:
                # skip events that have no evidence at all
                continue

            event["evidence"]=evidence
            event["source_ids"]=list(
                dict.fromkeys(source_ids)
            )

            output.append(
                event
            )

        return output


    def extract(
        self,
        limit=None
    ):

        memories=self.get_unprocessed_memories(
            limit
        )

        if not memories:

            return []

        print(
            f"📂正在处理 {len(memories)} 条未整理记忆..."
        )

        prompt=self.build_prompt(
            memories
        )

        print(
            f"📝 Prompt长度:{len(prompt)}"
        )

        result=self.llm.generate_raw(
            prompt
        )

        print(
            "\n📝 LLM原始响应:\n",
            result
        )

        events=self.parse_result(
            result,
            memories,
            prompt=prompt
        )

        print(
            f"✅提取到 {len(events)} 个事件"
        )

        for e in events:

            print(
                f" - [{e.get('event_type')}] {e.get('topic')}"
            )

        return events

    def extract_from_text(self, text: str):
        """
        从当前文本中提取事件（不依赖历史记忆库）
        用于实时成长
        """
        # 构建单条消息的模拟记忆列表
        memories = [
            {
                "role": "user",
                "content": text,
                "source_index": 0
            }
        ]

        prompt = self.build_prompt(memories)
        result = self.llm.generate_raw(prompt)
        events = self.parse_result(result, memories, prompt=prompt)

        print(f"✅ 从当前消息提取到 {len(events)} 个事件")
        for e in events:
            print(f" - [{e.get('event_type')}] {e.get('topic')}")

        return events
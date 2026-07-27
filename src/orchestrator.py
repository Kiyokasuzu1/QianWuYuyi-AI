import os
import random
from pathlib import Path
from datetime import datetime
import uuid

from src.engine import ResponseEngine
from src.personality.personality_resolver import PersonalityResolver
from src.personality.self_model_context_provider import SelfModelContextProvider
from src.personality.self_model_store import SelfModelStore
from src.memory.memory_store import MemoryStore
from src.memory.vector import VectorMemory
from src.identity.user_context import UserContext
from src.identity.user_resolver import UserResolver
from src.runtime.runtime_context import RuntimeContext
from src.relationship.relationship_event import RelationshipEvent
from src.relationship.relationship_evaluator import RelationshipEvaluator


class RelationshipState:
    """
    Orchestrator 内部使用的关系状态包装。

    历史上这是一个仅返回硬编码占位数据的临时类，仅用于满足早期测试。
    现已替换为对 src.personality.relationship_state.RelationshipState（v0.6，
    持久化到 data/relationship_state.json）的薄包装，保持原有 .get() /
    recalibrate_for_testing() 接口不变，从而不破坏调用方代码。
    """

    def __init__(self, state_path: str = "data/relationship_state.json"):
        # 延迟导入，避免在模块加载阶段就锁定到特定状态文件路径
        from src.personality.relationship_state import RelationshipState as _RealRelationshipState
        self._impl = _RealRelationshipState(state_path=state_path)

    def recalibrate_for_testing(self):
        """测试用校准入口：透传到真实实现。"""
        self._impl.recalibrate_for_testing()

    def get(self):
        """
        读取当前关系快照。

        返回的字典与之前占位实现的 key 兼容（trust / familiarity / events），
        同时附带真实实现提供的完整字段，供后续模块按需消费。
        """
        state = self._impl.get()
        return {
            # 向后兼容字段
            "trust": state.get("trust", 0.0),
            "familiarity": state.get("familiarity", 0.0),
            "events": state.get("important_events", []),
            # 真实状态完整字段
            "bond_strength": state.get("bond_strength", 0.0),
            "promise_level": state.get("promise_level", 0.0),
            "shared_history": state.get("shared_history", 0.0),
            "activity_level": state.get("activity_level", 0.0),
            "milestones": state.get("milestones", []),
            "important_events": state.get("important_events", []),
            "last_updated": state.get("last_updated", ""),
        }


class Orchestrator:
    """核心调度器 —— 处理单次对话的完整生命周期"""

    def __init__(self, config=None):
        """初始化 Orchestrator 及各子系统"""
        self.config = config or {}
        self.target_user_id = None
        self.history = []
        self.current_personality = None

        # 初始化各子系统
        self.memory_store = MemoryStore()
        self.vector_memory = VectorMemory()
        self.personality_resolver = PersonalityResolver()
        self.self_model_store = SelfModelStore()
        self.self_model_context_provider = SelfModelContextProvider(store=self.self_model_store)
        self.user_resolver = UserResolver()
        self.engine = ResponseEngine()

        # RuntimeContext（用于 agreements-first 上下文组装）
        self.runtime_context = RuntimeContext()

        # 关系数据缓存
        self.relationship_profile = None
        # 关系状态（测试用）
        self.relationship_state = RelationshipState()

        # 初始化记忆索引
        self._init_memory_index()

    def _init_memory_index(self):
        """初始化向量记忆索引"""
        try:
            pass
        except Exception as e:
            print(f"[Orchestrator] 记忆索引初始化失败: {e}")

    def process(self, user_message: str) -> str:
        """
        处理用户消息，返回回复
        """
        # Step 1: 解析用户身份
        user_context = self.user_resolver.resolve(self.target_user_id)
        if user_context:
            self.target_user_id = user_context.user_id if hasattr(user_context, "user_id") else None

        # Step 2: 检索记忆
        chat_memories = []
        try:
            if self.target_user_id:
                chat_memories = self.memory_store.load()
                if self.vector_memory:
                    results = self.vector_memory.search(user_message, top_k=5)
                    for res in results:
                        if res not in chat_memories:
                            chat_memories.append(res)
        except Exception as e:
            print(f"[Orchestrator] 记忆检索失败: {e}")

        # Step 3: 组装优先级上下文
        try:
            assembled_context = self.runtime_context.assemble_context(
                user_id=self.target_user_id or "default",
                conversation={"recent_turns": self.history[-10:] if self.history else []},
                self_model_snapshot=self.self_model_context_provider.get_context(),
                memory_summary={"recent_memories": chat_memories},
                options={"relationship_summary": self.relationship_profile},
            )
        except Exception as e:
            print(f"[Orchestrator] 上下文组装失败: {e}")
            assembled_context = None

        # Step 4: 获取当前人格
        personality = self.personality_resolver.resolve()
        self.current_personality = personality
        personality_context = self._get_personality_context(personality)

        # Step 5: 获取情绪上下文（如果有）
        emotion_ctx = {}
        if assembled_context and "emotion_manager" in assembled_context:
            em_manager = assembled_context.get("emotion_manager")
            if em_manager and hasattr(em_manager, "state"):
                state = em_manager.state
                emotion_ctx = {
                    "dominant": getattr(state, "dominant", None) or getattr(state, "primary_emotion", None),
                    "intensity": getattr(state, "intensity", None) or 0.0,
                }

        # Step 6: 获取关系上下文（如果有）
        relationship_ctx = {}
        if assembled_context and "relationship_profile" in assembled_context:
            rel_profile = assembled_context.get("relationship_profile")
            if rel_profile:
                relationship_ctx = {
                    "trust": rel_profile.get("trust", 0.5) if isinstance(rel_profile, dict) else 0.5,
                    "familiarity": rel_profile.get("familiarity", 0.0) if isinstance(rel_profile, dict) else 0.0,
                }

        # Step 7: 执行情绪预处理器
        if assembled_context:
            assembled_context = self._process_emotion_pre(assembled_context, user_message, self.target_user_id)

        # Step 8: 生成回复
        prompt_blocks = assembled_context.get("prompt_blocks", []) if assembled_context else []
        try:
            reply = self.engine.generate(
                user_message=user_message,
                history=self.history,
                chat_memories=chat_memories,
                life_events=[],
                personality_context=personality_context,
                resolved_behavior=None,
                self_model_context=self.self_model_context_provider.get_context(),
                emotion_context=emotion_ctx,
                relationship_context=relationship_ctx,
                context_prompt_blocks=prompt_blocks,
            )
        except Exception as e:
            print(f"[Orchestrator] 生成回复失败: {e}")
            reply = "抱歉，我遇到了一些问题，请稍后再试。"

        # Step 9: 记录本次对话到历史
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": reply})

        # Step 10: 保存记忆（异步/非阻塞）
        try:
            if self.target_user_id:
                memory_record = {
                    "id": f"mem_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}",
                    "content": user_message,
                    "timestamp": datetime.now().isoformat(),
                    "user_id": self.target_user_id,
                    "importance": 0.5,
                    "source_event_id": "",
                    "emotion_tag": emotion_ctx.get("dominant", ""),
                    "relationship_id": self.target_user_id,
                }
                self.memory_store.add(memory_record)
        except Exception as e:
            print(f"[Orchestrator] 保存记忆失败: {e}")

        # Step 11: 执行情绪后处理器
        if assembled_context:
            assembled_context = self._process_emotion_post(assembled_context, reply, self.target_user_id)

        # Step 12: 执行关系后处理器
        if assembled_context:
            assembled_context = self._process_relationship_post(
                assembled_context, user_message, reply, chat_memories, self.target_user_id
            )

        return reply

    def _get_personality_context(self, personality):
        if not personality:
            return ""
        return f"当前人格：{personality.get('name', '未知')}。{personality.get('description', '')}"

    def _process_emotion_pre(self, assembled_context, user_message: str, user_id: str):
        try:
            em_manager = assembled_context.get("emotion_manager") if assembled_context else None
            if not em_manager:
                return assembled_context
            from src.emotion.emotion_event import EmotionEvent
            ev = EmotionEvent(
                source="user_message",
                content=user_message,
                timestamp=datetime.now().isoformat()
            )
            em_manager.process_event(ev)
            if assembled_context is not None:
                assembled_context["trace"].append(
                    f"emotion_bridge_event: processed user_message at {datetime.now().isoformat()}"
                )
            return assembled_context
        except Exception as e:
            print(f"[Orchestrator] emotion pre-processing failed: {e}")
            return assembled_context

    def _process_emotion_post(self, assembled_context, reply: str, user_id: str):
        try:
            em_manager = assembled_context.get("emotion_manager") if assembled_context else None
            if not em_manager:
                return assembled_context
            from src.emotion.emotion_event import EmotionEvent
            ev = EmotionEvent(
                source="assistant_reply",
                content=reply,
                timestamp=datetime.now().isoformat()
            )
            em_manager.process_event(ev)

            user_emotion_path = Path("data/emotions")
            user_emotion_path.mkdir(parents=True, exist_ok=True)
            per_user_file = user_emotion_path / f"{user_id}.json"
            try:
                repo = getattr(em_manager, "repository", None)
                if repo is not None and hasattr(repo, "save"):
                    try:
                        repo.filepath = per_user_file
                        repo.save(em_manager.state)
                    except Exception:
                        with open(per_user_file, "w", encoding="utf-8") as f:
                            json.dump(
                                em_manager.state.to_dict() if hasattr(em_manager.state, "to_dict") else {},
                                f,
                                ensure_ascii=False,
                                indent=2
                            )
                else:
                    with open(per_user_file, "w", encoding="utf-8") as f:
                        json.dump(
                            em_manager.state.to_dict() if hasattr(em_manager.state, "to_dict") else {},
                            f,
                            ensure_ascii=False,
                            indent=2
                        )
            except Exception as e:
                print(f"[Orchestrator] persist emotion state failed: {e}")

            if assembled_context is not None:
                assembled_context["trace"].append(
                    f"emotion_bridge_event: processed assistant_reply and persisted to {per_user_file}"
                )

            try:
                if assembled_context and assembled_context.get("on_emotion_change"):
                    dominant = (
                        getattr(em_manager.state, "dominant", None) or
                        getattr(em_manager.state, "primary_emotion", None)
                    )
                    intensity = getattr(em_manager.state, "intensity", None) or 0.0
                    cb = assembled_context.get("on_emotion_change")
                    if cb:
                        cb(dominant, intensity)
            except Exception as e:
                print(f"[Orchestrator] emotion change callback failed: {e}")

            return assembled_context
        except Exception as e:
            print(f"[Orchestrator] emotion post-processing failed: {e}")
            return assembled_context

    def _process_relationship_post(self, assembled_context, user_message: str, reply: str, chat_memories: list, user_id: str):
        try:
            rel_repo = assembled_context.get("relationship_repo") if assembled_context else None
            rel_profile = assembled_context.get("relationship_profile") if assembled_context else None
            if not rel_repo:
                return assembled_context

            event = RelationshipEvent(
                event_id=f"rel_{uuid.uuid4().hex[:8]}",
                event_type="interaction",
                evidence_ids=[m.get("id") for m in chat_memories if m.get("id")],
                signal_strength=0.6,
                potential_dimensions=set(["trust_building"]),
                description=f"Interaction length {len(user_message)}",
                timestamp=datetime.now().isoformat()
            )

            evaluator = RelationshipEvaluator()
            res = evaluator.evaluate(event)

            try:
                if isinstance(rel_profile, dict):
                    old_trust = rel_profile.get("trust", 0.5)
                    new_trust = max(0.0, min(1.0, old_trust + (0.05 if res.passed else -0.02)))
                    rel_profile["trust"] = new_trust
                    rel_profile["familiarity"] = rel_profile.get("familiarity", 0.0) + 0.02
                    rel_profile.setdefault("events", []).append(event.to_dict())
                    rel_repo.save(rel_profile)
                    assembled_context["trace"].append(
                        f"relationship_event: {event.event_id} evaluated passed={res.passed} trust {old_trust}->{new_trust}"
                    )
            except Exception as e:
                print(f"[Orchestrator] relationship persist failed: {e}")

            return assembled_context
        except Exception as e:
            print(f"[Orchestrator] relationship post-processing failed: {e}")
            return assembled_context

    def generate_initiative(self, user_id: str) -> str:
        """
        生成主动消息，不写入历史或记忆
        会参考最近 15 条对话历史，避免与刚刚聊过的内容冲突
        """
        try:
            # 1. 获取最近 15 条对话历史（用于上下文连贯）
            recent_history = self.history[-15:] if self.history else []
            history_text = "\n".join([
                f"{'用户' if h.get('role') == 'user' else '羽依'}: {h.get('content', '')}"
                for h in recent_history
            ]) if recent_history else "无最近对话"

            # 2. 只读获取最近记忆
            from src.memory.memory_store import MemoryStore
            memory_store = MemoryStore()
            memories = memory_store.load() if hasattr(memory_store, 'load') else []
            recent_memories = memories[-5:] if memories else []

            # 3. 构建 prompt
            from datetime import datetime
            prompt = f"""你是浅雾羽依。根据以下当前状态，判断你是否有什么话想主动对用户说。

当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

最近 15 条对话历史：
{history_text}

最近记忆：
{recent_memories}

用户 ID：{user_id}

注意：
1. 如果最近对话中用户刚明确说过某件事（例如"还没做某事"），不要主动询问"是否已经做了"。
2. 如果你有想说的话，请直接输出你想说的那句话（一句自然的话，不要带任何格式）。
3. 如果你觉得现在不是时候，或者没有什么特别想说的，请只输出一个空字符串。
4. 你的回答只会用于判断是否发送消息，不会写入任何历史记录。"""

            # 4. 调用 LLM
            reply = self.engine.generate(
                user_message=prompt,
                history=[],
                chat_memories=[],
                life_events=[],
                personality_context="你是浅雾羽依，温柔、细腻、有直觉力，在亲近的人面前会逐渐敢于表达自己。",
                resolved_behavior=None,
                self_model_context={},
                emotion_context={},
                relationship_context={},
                context_prompt_blocks=[]
            )

            if reply and len(reply.strip()) > 5:
                return reply.strip()
            return ""
        except Exception as e:
            print(f"[Orchestrator] generate_initiative 失败: {e}")
            return ""

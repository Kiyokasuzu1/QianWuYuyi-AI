"""
Phase 4.0 — R2.7.6 RuntimeController（实验 Runtime → 真实生产 Runtime）

核心职责：
    handle_message(user_id, message) =
        load_state
        → build_context
        → cognitive_loop (PromptRenderer + LLMAdapter + ResponseEngine)
        → update_memory
        → maybe_grow（自动 proposal + EvolutionRecord，受 RP-3 稳定性保护）
        → save_state
        → return reply

设计原则（R2.7.6）：
    · 每个 user_id 独立 PersistenceManager（data/users/<user_id>/ 三态目录）
    · acquire_lock 组合锁（threading.Lock + 文件锁）保证同用户并发串行（无重复成长/覆盖）
    · feature flag phase4_enabled：从 runtime.phase4_enabled 配置读取
        false → RuntimeController.handle_message() 直接抛 NotImplementedError（调用方降级 Orchestrator）
    · 成长 authority 集中化：只在 controller 内部可能触发 trait 修改；
      Orchestrator 旧的 _create_growth_proposal 在 phase4=true 时应该被禁用（只读写 audit 不保存）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.personality.evolution_record import build_evolution_record
from src.personality.personality_state import PersonalityState, reset_personality_state
from src.response_phase4.mock_response_engine import (
    MockResponseEngine,
    _extract_memory_accumulation_hits,
)
from src.response_phase4.persistence_manager import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_USERS_ROOT_DIR,
    LoadResult,
    Phase4PersistenceManager,
)
from src.runtime.health_companion import HealthCompanion
from tests.support.continuous_loop_runner import ContinuousLoopRunner


logger = logging.getLogger(__name__)


@dataclass
class HandleResult:
    reply: str
    debug: Dict[str, Any] = field(default_factory=dict)  # 可在 /v1/chat/completions 中通过 debug=true 返回


class RuntimeController:
    """R2.7.6：统一真实消息生命周期。

    用法：
        ctrl = RuntimeController(config=app_config_dict)
        if ctrl.phase4_enabled:
            result = ctrl.handle_message(user_id="12345", message="你好呀")
            return result.reply
        else:
            return orchestrator.process(message)  # legacy fallback
    """

    # 进程级全局单例锁（防止 RuntimeController 被重复创建）
    _instance_lock = threading.Lock()
    _instance: Optional["RuntimeController"] = None

    @classmethod
    def get_singleton(cls, config: Optional[Dict[str, Any]] = None) -> "RuntimeController":
        """典型 API server：进程启动时 init_admin 里调用一次，后续 get。"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(config=config or {})
            return cls._instance

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        cfg = dict(config or {})
        runtime_cfg = cfg.get("runtime", {}) if isinstance(cfg, dict) else {}

        # Feature flag（R2.7.6 灰度开关）
        #   config.yaml:
        #       runtime:
        #         phase4_enabled: true   # 默认 false，保持旧链路可用
        self.phase4_enabled: bool = bool(runtime_cfg.get("phase4_enabled", False))

        # LLM 选择（R2.7.6-D）：
        #   runtime:
        #     llm_engine: "mock" | "deepseek"
        #     deepseek:
        #       enabled: false
        #       max_retries: 2
        #       token_budget_per_turn: 4096
        self.llm_engine: str = str(runtime_cfg.get("llm_engine", "mock")).lower()
        self.deepseek_cfg = runtime_cfg.get("deepseek", {}) if isinstance(runtime_cfg, dict) else {}
        self.deepseek_max_retries: int = int(self.deepseek_cfg.get("max_retries", 2) or 2)
        self.deepseek_token_budget: int = int(self.deepseek_cfg.get("token_budget_per_turn", 4096) or 4096)

        # Persistence 目录
        self.users_root_dir: str = str(
            runtime_cfg.get("users_root_dir", DEFAULT_USERS_ROOT_DIR)
            if isinstance(runtime_cfg, dict)
            else DEFAULT_USERS_ROOT_DIR
        )

        # Growth 开关（默认 true，关闭后所有消息不会改 trait；只读表达用）
        self.growth_enabled: bool = bool(
            runtime_cfg.get("growth_enabled", True) if isinstance(runtime_cfg, dict) else True
        )

        # Legacy data/memory.json 自动导入开关（仅在新用户目录还没 memory 时，自动尝试一次）
        self.legacy_memory_auto_import: bool = bool(
            runtime_cfg.get("legacy_memory_auto_import", False)
            if isinstance(runtime_cfg, dict)
            else False
        )
        self.legacy_memory_path: str = str(runtime_cfg.get("legacy_memory_path", "data/memory.json") if isinstance(runtime_cfg, dict) else "data/memory.json")

        # R2.7.6-P1: Memory 上下文限制（只限制传入 prompt 的 memory 数量，不影响存储）
        self.max_context_memories: int = int(runtime_cfg.get("max_context_memories", 50) or 50)

        # Per-user PM 缓存（避免每次 for_user 构造 Path 检查）
        self._pm_cache_lock = threading.Lock()
        self._pm_cache: Dict[str, Phase4PersistenceManager] = {}

        # R2.7.6-P1: Per-user ContinuousLoopRunner 缓存（避免跨用户状态泄漏）
        self._runner_cache_lock = threading.Lock()  # R2.7.6-AUDIT: 修复并发创建 runner 的竞态
        self._runner_cache: Dict[str, ContinuousLoopRunner] = {}

        # R2.7.6-YUYI: 健康陪伴检测器（per-user 状态隔离）
        self._health_companion = HealthCompanion()

        # R2.7.6-P0: DeepSeek adapter（所有用户共享，因为不含用户态）
        self._deepseek_adapter = None
        if self.llm_engine == "deepseek" or bool(self.deepseek_cfg.get("enabled", False)):
            try:
                from src.response_phase4.deepseek_adapter import DeepSeekAdapter
                self._deepseek_adapter = DeepSeekAdapter(
                    timeout=float(self.deepseek_cfg.get("timeout_seconds", 30.0) or 30.0),
                    max_tokens=int(self.deepseek_cfg.get("max_tokens", 2048) or 2048),
                    max_retries=int(self.deepseek_cfg.get("max_retries", 3) or 3),
                )
                logger.info("[R2.7.6-P0] DeepSeekAdapter 已实例化（llm_engine=%s，失败将自动降级 Mock）", self.llm_engine)
            except Exception as e:  # noqa: BLE001
                logger.warning("[R2.7.6-P0] DeepSeekAdapter 初始化失败，将只用 Mock：%s", e)
                self._deepseek_adapter = None

        # 启动日志：打印实际加载的 R2.7.6 配置
        logger.info(
            "[R2.7.6] RuntimeController 配置: phase4_enabled=%s | llm_engine=%s | deepseek=%s | "
            "users_root=%s | growth=%s | max_context_memories=%d | legacy_import=%s",
            self.phase4_enabled, self.llm_engine,
            "ready" if self._deepseek_adapter else "off/fallback",
            self.users_root_dir, self.growth_enabled,
            self.max_context_memories, self.legacy_memory_auto_import,
        )

    # ================================================================
    # Public API（api_server.py 会调用这一个函数）
    # ================================================================
    def handle_message(
        self,
        user_id: str,
        message: str,
        *,
        session_id: Optional[str] = None,
        user_name: Optional[str] = None,
    ) -> HandleResult:
        """处理一条真实消息（从收到 → 保存完毕的全生命周期）。"""
        if not self.phase4_enabled:
            raise NotImplementedError("RuntimeController.phase4_enabled=false；调用方应该降级 Orchestrator。")

        uid = str(user_id or "default")
        pm = self._get_pm_for_user(uid)
        ts_ms = int(time.time() * 1000)
        turn_uuid = uuid.uuid4().hex[:10]
        sid = session_id or f"s_{uid}_{turn_uuid}"

        # ===== R2.7.6：单用户串行 =====
        # 用 acquire_lock（thread_lock + filelock）保证：同用户两条并发消息不会
        # 出现 load v10 → save v11 vs load v10 → save v11 的互相覆盖。
        with pm.acquire_lock():
            # 1. 恢复三态（含 RPG-3 drift gate / version 单调拒绝 / 损坏降级）
            load_res = self._load(pm)

            ps = load_res.personality
            memories: List[Dict[str, Any]] = list(load_res.memories or [])
            relationship = dict(load_res.relationship)

            # 2. Legacy 只读导入（一次性：如果新目录完全没 memory 才尝试）
            # R2.7.6-AUDIT FIX:
            #   - 修复 import_legacy_memory 返回 int，但实际是修改另一个内部列表引用的问题
            #   - 修复缺少 target_user_id 过滤导致跨用户数据泄漏的问题
            if self.legacy_memory_auto_import and len(memories) == 0:
                try:
                    imported_records = pm.import_legacy_memory(
                        self.legacy_memory_path,
                        max_import=100,
                        target_user_id=uid,  # R2.7.6-AUDIT: 按 user_id 过滤，防跨用户泄漏
                    )
                    if imported_records:
                        # import_legacy_memory 修改的是内部列表，这里把返回的记录合并到 memories
                        if isinstance(imported_records, list):
                            # 新版本：返回 List[Dict]，直接 extend（防重复）
                            existing_ids = {r.get("id") for r in memories if isinstance(r, dict)}
                            for rec in imported_records:
                                if isinstance(rec, dict) and rec.get("id") not in existing_ids:
                                    memories.append(rec)
                        else:
                            # 兼容旧版本：int，重新从 PM 读取最新内存状态
                            reload2 = pm.load_all()
                            memories = list(reload2.memories)
                        logger.info(
                            "[R2.7.6-AUDIT] Legacy memory 导入成功 user=%s, count=%d",
                            uid, len(memories),
                        )
                except TypeError:
                    # 旧签名：import_legacy_memory(legacy_path, max_import=N) -> int
                    # 尝试无 target_user_id 版本调用（警告：可能跨用户泄漏）
                    logger.warning(
                        "[R2.7.6-AUDIT] PersistenceManager 版本较旧无 target_user_id 参数，"
                        "可能造成跨用户泄漏，建议升级 persistence_manager.py"
                    )
                    count = pm.import_legacy_memory(self.legacy_memory_path, max_import=100)
                    if count:
                        reload2 = pm.load_all()
                        memories = list(reload2.memories)

            # 3. Transform memory 格式 + R2.7.6-P1: 上下文筛选
            #    完整 memory 存储在 PersistenceManager（不裁剪），但只取 top-N 高 importance
            #    传入 prompt context，避免 token 爆炸
            #
            # R2.7.6-YUYI: 跨用户记忆保护——过滤掉 user_id 水印不匹配的记忆
            #   防止目录复制后 B 用户读到 A 用户的记忆
            memories = [
                r for r in memories
                if isinstance(r, dict) and (
                    r.get("user_id") is None  # 旧记忆没有 user_id 字段，允许通过（兼容）
                    or str(r.get("user_id")) == uid
                )
            ]
            context_mems = self._select_context_memories(memories)
            cum_mems: Dict[str, Tuple[str, str, str]] = {}
            for rec in context_mems:
                mid = str(rec.get("id") or f"m_{uuid.uuid4().hex[:6]}")
                topic = str(rec.get("topic") or "general")
                text = str(rec.get("text") or rec.get("content") or "")
                cum_mems[mid] = (mid, topic, text)

            # 4. 构造 turn_cfg（与 30 天 LifeSimulation 统一）
            interaction_count = int(relationship.get("interaction_count") or 0) + 1

            # R2.7.6-YUYI: 健康陪伴检测——注入到 prompt context
            health_signal = self._health_companion.check(uid, message)

            turn_cfg: Dict[str, Any] = {
                "turn_idx": interaction_count,
                "day_label": f"Turn {interaction_count}",
                "user_input": message,
                "memories": [v for v in cum_mems.values()],  # (id, topic, text) list
                "proposal_delta": {},  # 先空，后续 step maybe_grow 再填
                "sr_cause": "real_message_chat",
                "ts_ms": ts_ms,
                "user_name": user_name or "",
                "user_id": uid,
                # R2.7.6-YUYI: 健康提醒文本注入
                "health_hint": health_signal.health_hint,
                "companion_silent": health_signal.companion_silent,
            }

            # 5. Cognitive loop：per-user ContinuousLoopRunner 跑一轮
            #    R2.7.6-P0: 传入 DeepSeek adapter（如果配置了），失败自动降级 Mock
            runner = self._get_runner_for_user(uid)
            before_version = max(ps.version, 1)
            turn = runner._run_one_turn(
                cur_traits=dict(ps.traits),
                current_personality_version=before_version,
                cumulative_memories=cum_mems,
                turn_cfg=turn_cfg,
                session_id=sid,
                llm_adapter=self._deepseek_adapter,
            )

            reply: str = str(turn.get("reply_text") or "")

            # R2.7.6-AUDIT FIX: reply 为空时不 save_all，允许 api_server 降级
            # 原因：如果已经 save_all，api_server 降级走 Pipeline/Orchestrator 会再写一次 →
            #   memory 重复 / interaction_count 重复 +1 / personality 重复成长（严重数据一致性 bug）
            if not reply.strip():
                logger.warning(
                    "[R2.7.6-AUDIT] RuntimeController reply 为空，跳过 save_all 以允许降级；"
                    "user_id=%s turn_uuid=%s",
                    uid, turn_uuid,
                )
                # 返回 HandleResult(reply="")，api_server 会检测到空 reply 降级
                return HandleResult(
                    reply="",
                    debug={
                        "turn_uuid": turn_uuid,
                        "session_id": sid,
                        "user_id": uid,
                        "new_memory_count": 0,
                        "delta_applied": {},
                        "personality_version": ps.version,
                        "interaction_count": interaction_count - 1,  # 回退计数
                        "recovery_details": list(getattr(load_res, "recovery_details", [])),
                        "fallback_required": True,  # R2.7.6-AUDIT: 显式标记需要降级
                    },
                )

            # 6. Update memories：从 turn 提取新增 proposal 生成的新 memory，
            #    另外再加一条"本 turn 对话本身"的轻量记忆（只在重要/长消息加）
            new_records_from_turn: List[Dict[str, Any]] = []
            if len(message.strip()) >= 10:
                mem_id = f"m_r{interaction_count:05d}_{turn_uuid}"
                topic = _classify_message_topic(message)
                new_records_from_turn.append({
                    "id": mem_id,
                    "text": f"（用户{user_name or uid}）{message.strip()}",
                    "topic": topic,
                    "timestamp_ms": ts_ms,
                    "importance": _estimate_memory_importance(message, topic),
                    "source_turn_uuid": turn_uuid,
                    "user_id": uid,  # R2.7.6-YUYI: 用户水印，防跨用户复制
                })
            memories.extend(new_records_from_turn)

            # R2.7.6-AUDIT: 限制单用户 memory 总数（防长期运行 memory.json 无限增大）
            # 保留最近 + 重要性高的 5000 条；超过部分按 (importance, timestamp) 删除
            MAX_MEMORIES_PER_USER = 5000
            if len(memories) > MAX_MEMORIES_PER_USER:
                # 排序：先按 importance 升序，再按 timestamp_ms 升序（优先删老的不重要的）
                memories_sorted = sorted(
                    memories,
                    key=lambda r: (
                        float(r.get("importance", 0.5) if isinstance(r, dict) else 0.5),
                        int(r.get("timestamp_ms", 0) if isinstance(r, dict) else 0),
                    ),
                )
                drop_count = len(memories) - MAX_MEMORIES_PER_USER
                dropped_ids = {
                    r.get("id") for r in memories_sorted[:drop_count] if isinstance(r, dict)
                }
                memories = [r for r in memories if (isinstance(r, dict) and r.get("id") not in dropped_ids)]
                logger.warning(
                    "[R2.7.6-AUDIT] Memory 超限，裁剪 %d → %d 条 (user_id=%s)",
                    len(memories) + drop_count, len(memories), uid,
                )

            # 7. Maybe grow（只有在 message 强度够、且 growth_enabled=true 时才产生 proposal_delta）
            delta_applied: Dict[str, float] = {}
            if self.growth_enabled:
                proposed_delta = _propose_tiny_delta_from_message(
                    user_message=message,
                    cum_memories_count=len(cum_mems),
                    current_traits=dict(ps.traits),
                )
                if proposed_delta:
                    self._apply_delta_safely(ps, proposed_delta, turn_uuid=turn_uuid)
                    delta_applied = proposed_delta

            # 8. Update relationship
            relationship["interaction_count"] = interaction_count
            relationship["last_interaction_ts_ms"] = ts_ms
            relationship.setdefault("history", [])
            relationship["history"].append({
                "ts_ms": ts_ms,
                "turn_uuid": turn_uuid,
                "message_preview": message[:60],
                "delta_applied": delta_applied,
            })
            if len(relationship["history"]) > 2000:
                relationship["history"] = relationship["history"][-2000:]

            # R2.7.6-AUDIT FIX: relationship 值关联消息情感，不再无条件增长
            #   负面消息（辱骂/生气）不增长甚至轻微下降；中性/正面消息才增长
            closeness_delta, trust_delta = _analyze_relationship_impact(message)
            relationship["closeness"] = round(
                min(1.0, max(0.0, float(relationship.get("closeness") or 0.0) + closeness_delta)), 6
            )
            relationship["trust_level"] = round(
                min(1.0, max(0.0, float(relationship.get("trust_level") or 0.0) + trust_delta)), 6
            )
            if "shared_memory_tags" not in relationship:
                relationship["shared_memory_tags"] = []

            # 9. Save 三态（只有在 reply 非空时才 save，防止降级路径重复写）
            pm.save_all(personality=ps, memories=memories, relationship=relationship)

        # ===== end lock =====

        return HandleResult(
            reply=reply,
            debug={
                "turn_uuid": turn_uuid,
                "session_id": sid,
                "user_id": uid,
                "new_memory_count": len(new_records_from_turn),
                "delta_applied": delta_applied,
                "personality_version": ps.version,
                "interaction_count": interaction_count,
                "recovery_details": list(getattr(load_res, "recovery_details", [])),
                "closeness_delta": closeness_delta,
                "trust_delta": trust_delta,
                # R2.7.6-YUYI: 健康陪伴信号
                "health_hint": health_signal.health_hint,
                "companion_silent": health_signal.companion_silent,
            },
        )

    # ================================================================
    # 内部 helper
    # ================================================================
    def _get_pm_for_user(self, user_id: str) -> Phase4PersistenceManager:
        with self._pm_cache_lock:
            pm = self._pm_cache.get(user_id)
            if pm is None:
                pm = Phase4PersistenceManager.for_user(
                    user_id=user_id,
                    users_root=self.users_root_dir,
                )
                self._pm_cache[user_id] = pm
            return pm

    def _get_runner_for_user(self, user_id: str) -> ContinuousLoopRunner:
        """R2.7.6-P1: Per-user ContinuousLoopRunner（隔离 IdentityAnchorManager 等状态）。

        R2.7.6-AUDIT: 加锁防止 Flask threaded=True 下同用户并发请求时重复创建 runner。
        """
        # double-checked locking
        runner = self._runner_cache.get(user_id)
        if runner is not None:
            return runner
        with self._runner_cache_lock:
            runner = self._runner_cache.get(user_id)
            if runner is None:
                runner = ContinuousLoopRunner(llm_adapter=self._deepseek_adapter)
                self._runner_cache[user_id] = runner
                logger.info("[R2.7.6] 为用户 %s 创建独立 RuntimeContext", user_id)
            return runner

    def _select_context_memories(self, memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """R2.7.6-P1: Memory 上下文保护——完整 memory 保留在磁盘，但只取 top-N 传入 prompt。

        策略：
          1. 按 importance 降序排
          2. 取前 max_context_memories 条
          3. 再按 timestamp_ms 升序排（让旧记忆排在前面，符合对话时间线）
        """
        if len(memories) <= self.max_context_memories:
            return list(memories)
        sorted_by_imp = sorted(
            memories,
            key=lambda r: float(r.get("importance", 0.5)),
            reverse=True,
        )
        top_n = sorted_by_imp[:self.max_context_memories]
        # 按时间排序，让对话脉络自然
        top_n.sort(key=lambda r: int(r.get("timestamp_ms", 0) or 0))
        logger.debug(
            "[R2.7.6] Memory 上下文筛选: total=%d → context=%d (max=%d)",
            len(memories), len(top_n), self.max_context_memories,
        )
        return top_n

    def _load(self, pm: Phase4PersistenceManager) -> LoadResult:
        load_res = pm.load_all()
        return load_res

    def _apply_delta_safely(
        self,
        ps: PersonalityState,
        delta: Dict[str, float],
        turn_uuid: str,
    ) -> None:
        """严格的 RP-3 / RPG-3：每次 apply 单 trait |Δ| ≤ 0.10（比 RPG-3 更保守，
        因为消息频率更高）。"""
        for k, v in list(delta.items()):
            if abs(float(v)) > 0.10:
                delta[k] = 0.10 if v > 0 else -0.10
        before = {f"trait.{k}": float(ps.traits.get(k, 0.5)) for k in delta}
        after = {
            f"trait.{k}": min(1.0, max(0.0, float(ps.traits.get(k, 0.5)) + float(v)))
            for k, v in delta.items()
        }
        rec = build_evolution_record(
            proposal_id=f"p_rt_{turn_uuid}",
            approval_id=f"a_rt_{turn_uuid}",
            change_type="trait_delta",
            before=before,
            after=after,
            reasons=[f"RuntimeController R2.7.6 real chat delta"],
            confidence=0.78,
        )
        ps.apply_evolution(rec)


# ================================================================
# R2.7.6：消息 → 超小 proposal delta（避免"每次聊天人格大改"）
# ================================================================
def _classify_message_topic(msg: str) -> str:
    m = msg.lower()
    if any(w in msg for w in ("角色", "猫娘", "设计", "形象", "character")):
        return "character_design"
    if any(w in msg for w in ("画画", "绘画", "画图", "AI绘画", "AI 绘画", "stable", "midjourney")):
        return "AI_art"
    if any(w in msg for w in ("困难", "累", "压力", "加班", "难过", "沮丧")):
        return "relationship"
    if any(w in msg for w in ("创造", "创作", "原创", "new")):
        return "creative"
    if any(w in msg for w in ("独立", "自由", "辞职", "选择", "方向")):
        return "value_reflection"
    return "general"


def _estimate_memory_importance(message: str, topic: str) -> float:
    base = 0.5
    if topic in ("character_design", "AI_art", "value_reflection"):
        base = 0.75
    if len(message) >= 80:
        base += 0.08
    return round(min(0.98, base), 4)


def _propose_tiny_delta_from_message(
    user_message: str,
    cum_memories_count: int,
    current_traits: Dict[str, float],
) -> Dict[str, float]:
    """超保守：每次聊天单 trait |Δ| ≤ 0.007，月上限（~100 轮 ~30天）自然 ≤ 0.28，稳。"""
    out: Dict[str, float] = {}
    if len(user_message.strip()) < 6:
        return out
    topic = _classify_message_topic(user_message)

    # trait → 基础 delta
    tiny = 0.007  # 每 turn 基础量（100 轮 × 50 次命中 × headroom≈0.8 ≈ 0.28 ≤ 0.30）

    if topic == "character_design":
        out["creativity"] = +tiny
        out["character_interest"] = +tiny * 1.2
    elif topic == "AI_art":
        out["creativity"] = +tiny
        out["aesthetic"] = +tiny * 0.5
    elif topic == "relationship":
        out["empathy"] = +tiny
        out["warmth"] = +tiny * 0.8
        out["curiosity"] = -tiny * 0.2
    elif topic == "creative":
        out["creativity"] = +tiny * 1.2
    elif topic == "value_reflection":
        out["independence"] = +tiny * 1.1
        out["patience"] = +tiny * 0.7
    else:
        # general 闲聊：playfulness 和 warmth 微小 +
        out["playfulness"] = +tiny * 0.5
        out["warmth"] = +tiny * 0.3

    # 饱和保护：越接近 1.0 越难继续升（避免无限成长硬顶到 1.0）
    for k in list(out.keys()):
        cur = float(current_traits.get(k, 0.5))
        dv = out[k]
        headroom = max(0.0, 1.01 - cur) if dv > 0 else max(0.0, cur - 0.0)
        if headroom <= 0:
            out.pop(k)
            continue
        out[k] = round(dv * min(1.0, headroom / 0.3), 6)

    # 30 轮都不产生 delta 的冷启动保护：保证前 10 条至少 +warmth*0.002
    if not out and cum_memories_count < 10:
        out["warmth"] = 0.002
    return out


# ================================================================
# R2.7.6-AUDIT: 消息 → relationship 变化（基于极简情感关键词分析）
# ================================================================
# 负面关键词分两桶：
#   · ABUSIVE（辱骂桶）：哪怕命中 1 个 → 直接扣减关系值
#   · COMPLAINT（抱怨桶）：命中 2+ 个才扣减，1 个只是冻结
# 所有 token ≥ 2 字，避免单字误伤（"气" 误伤 "天气"、"烦" 误伤 "麻烦" 等）。
_NEGATIVE_ABUSIVE_TOKENS = frozenset((
    "傻逼", "煞笔", "sb", "SB", "sb吧", "垃圾", "废物", "脑残", "智障",
    "去死", "滚蛋", "滚开", "gun", "白痴", "骗子",
))
_NEGATIVE_COMPLAINT_TOKENS = frozenset((
    "菜鸡", "笨蛋", "讨厌你", "烦死了", "烦人", "恶心", "有病",
    "撒谎", "骗我", "冷漠", "无聊", "没用", "不会吧", "听不懂人话",
    "生气", "气死我了", "火大", "烦死", "离谱", "离谱啊", "无语",
    "恨你", "别烦我", "走开啊", "爬啊",
))

# 强正面关键词（出现 → closeness/trust 额外微增）
_POSITIVE_STRONG_TOKENS = frozenset((
    "喜欢", "爱你", "谢谢你", "感谢", "最好的", "最爱", "超棒",
    "好棒", "优秀", "聪明", "厉害", "mua", "亲亲", "抱抱", "想你",
    "晚安", "早安", "嘿嘿", "哈哈哈哈", "开心", "高兴", "幸福",
))

# 弱正面/中性（保留定义，后续可能接入；当前未直接用于计数）
_POSITIVE_WEAK_TOKENS = frozenset((
    "嗯", "好的", "好呀", "可以", "行", "对的", "是的", "呢", "啦", "呀",
    "哦哦", "原来", "知道了", "明白", "嗯呢", "哈哈", "嘿嘿", "嘻嘻",
))

# 注意：单字 "爱"、"棒"、"可爱" 等强正面保留在 POSITIVE_STRONG 里；
# 如果发现误匹配（如 "可爱" 命中 "不可爱"）再细化。


def _analyze_relationship_impact(message: str) -> Tuple[float, float]:
    """R2.7.6-AUDIT: 极简基于关键词的情感影响分析。

    返回 (closeness_delta, trust_delta)：
      · 辱骂（ABUSIVE ≥ 1）: closeness −0.004, trust −0.003（立即下降）
      · 重度负面（COMPLAINT ≥ 2）: closeness −0.003, trust −0.002（轻微下降）
      · 轻度负面（COMPLAINT == 1 且无 ABUSIVE）: (0, 0) 冻结不增长
      · 强正面（POSITIVE_STRONG ≥ 1）且无任何负面：closeness +0.010, trust +0.007
      · 混合（有负面但也有正面）：给弱增量 +0.003/0.002（避免"先骂后道歉"被直接判零）
      · 普通闲聊：closeness +0.006, trust +0.004（略低于老版 0.008/0.005）
      · 空消息：(0, 0)

    超保守设计：单轮 |closeness_delta| ≤ 0.01，月 100 轮正常聊天上限 +0.6，
    辱骂 10 轮 −0.04，留足修复空间，不出现一天刷满或一天清零的情况。
    """
    text = str(message or "").strip()
    if not text:
        return (0.0, 0.0)

    abusive_hits: List[str] = [t for t in _NEGATIVE_ABUSIVE_TOKENS if t in text]
    complaint_hits: List[str] = [t for t in _NEGATIVE_COMPLAINT_TOKENS if t in text]
    pos_strong_hits: List[str] = [t for t in _POSITIVE_STRONG_TOKENS if t in text]

    has_any_negative = bool(abusive_hits or complaint_hits)
    has_strong_positive = bool(pos_strong_hits)

    # 优先级 1：有辱骂 → 直接扣（不管其他正面词）
    if abusive_hits:
        return (-0.004, -0.003)
    # 优先级 2：抱怨 ≥ 2 个 → 轻微扣
    if len(complaint_hits) >= 2:
        return (-0.003, -0.002)
    # 优先级 3：抱怨 == 1 个 → 冻结（抱怨可能只是抱怨事情不一定骂羽依）
    if len(complaint_hits) == 1 and not has_strong_positive:
        return (0.0, 0.0)
    # 优先级 4：负面 + 强正面混合（"讨厌你但是谢谢你"）→ 弱增量
    if has_any_negative and has_strong_positive:
        return (0.003, 0.002)
    # 优先级 5：纯强正面 → 高增量
    if has_strong_positive:
        return (0.010, 0.007)
    # 默认：中性闲聊（略低于之前的 0.008/0.005）
    return (0.006, 0.004)

# -*- coding: utf-8 -*-
"""
P2.3-B.10 Phase 6 — GovernanceConfig（治理灰度开关设计层）

定位：
    统一三域迁移开关 + 全局 governance_mode 的灰度配置（设计层）。
    本模块**不接线生产路径**：默认配置保持 legacy（所有域 flag 关闭），
    RuntimeCore / 各域 adapter 的现有模块级开关继续作为唯一事实开关，
    本配置仅作为未来灰度启用机制的单一入口设计（B.10 债务 #4）。

governance_mode 语义（B.10 任务书冻结）：
    - legacy ：执行旧路径，Gateway 完全不参与（零行为变化，默认）
    - shadow ：执行旧路径，同时把同一 mutation 送入 Gateway 记录 verdict
                （只记录、不拦截；用于灰度期对比旧行为与治理裁决）
    - enforce：Gateway verdict 控制实际 mutation（对应各域 adapter
                迁移开关开启后的行为）

硬边界：
    - 默认 legacy + 全 flag False，任何导入本模块的代码不得改变默认值
    - 本模块不写入 config.yaml、不创建文件、不修改任何域状态
    - 三域 adapter 的模块级开关（is/set_*_mutation_gateway_enabled）
      保持唯一权威；本配置只是它们的汇总视图，接线方需自行同步
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

GOVERNANCE_MODES: tuple = ("legacy", "shadow", "enforce")

# 域 → 迁移开关 flag 名（汇总视图，与三域 adapter 模块级开关对应）
DOMAIN_FLAG_FIELDS: tuple = (
    "growth_mutation_gateway_enabled",
    "emotion_mutation_gateway_enabled",
    "relationship_mutation_gateway_enabled",
    "relationship_self_model_gateway_enabled",
)


@dataclass(frozen=True)
class GovernanceConfig:
    """治理灰度配置（frozen；修改一律整体替换，禁止就地改字段）。"""

    governance_mode: str = "legacy"
    growth_mutation_gateway_enabled: bool = False
    emotion_mutation_gateway_enabled: bool = False
    relationship_mutation_gateway_enabled: bool = False
    # B.9 追加的跨域开关（relationship → self_model），一并纳入统一视图
    relationship_self_model_gateway_enabled: bool = False

    def __post_init__(self) -> None:
        if self.governance_mode not in GOVERNANCE_MODES:
            raise ValueError(
                f"governance_mode 必须是 {GOVERNANCE_MODES} 之一，"
                f"得到 {self.governance_mode!r}"
            )
        for name in DOMAIN_FLAG_FIELDS:
            object.__setattr__(self, name, bool(getattr(self, name)))

    # ============================================================
    # 模式判定辅助（未来接线方使用；本阶段不接线）
    # ============================================================
    def is_legacy(self) -> bool:
        return self.governance_mode == "legacy"

    def is_shadow(self) -> bool:
        return self.governance_mode == "shadow"

    def is_enforce(self) -> bool:
        return self.governance_mode == "enforce"

    def to_dict(self) -> dict:
        return {
            "governance_mode": self.governance_mode,
            **{name: getattr(self, name) for name in DOMAIN_FLAG_FIELDS},
        }


# ============================================================
# 进程级配置单例（默认 legacy；测试用 set 替换，结束后 reset）
# ============================================================
_default_config: GovernanceConfig = GovernanceConfig()
_config_lock = threading.Lock()


def get_governance_config() -> GovernanceConfig:
    """当前进程级治理配置（默认 GovernanceConfig() = legacy 全关）。"""
    with _config_lock:
        return _default_config


def set_governance_config(config: GovernanceConfig) -> None:
    """替换进程级配置（测试 / 未来灰度接线使用）。"""
    global _default_config
    if not isinstance(config, GovernanceConfig):
        raise TypeError(
            f"set_governance_config 需要 GovernanceConfig，"
            f"得到 {type(config).__name__}"
        )
    with _config_lock:
        _default_config = config


def reset_governance_config() -> None:
    """恢复默认（legacy 全关）。"""
    set_governance_config(GovernanceConfig())


__all__ = [
    "GovernanceConfig",
    "GOVERNANCE_MODES",
    "DOMAIN_FLAG_FIELDS",
    "get_governance_config",
    "set_governance_config",
    "reset_governance_config",
]

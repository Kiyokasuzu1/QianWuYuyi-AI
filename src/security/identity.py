# -*- coding: utf-8 -*-
"""用户身份解析基础层（P2.1.1 Identity Resolver Foundation）。

职责（单一）：
    输入请求携带的原始 user 标识，输出结构化 Identity。
    只做判定，不做修正、不做猜测、不做转换。

设计约束：
    - 纯函数模块：不依赖 Runtime / Flask / Memory / LLM / 配置文件
    - 永不抛异常：任何非法输入一律落入 _unknown_sender 沙盒
    - fail-closed：无法确认身份 = 无权限

字段语义：
    id          最终内部身份 ID（沙盒统一为 "_unknown_sender"）
    source      身份来源：qq（真实 QQ 号）/ api（API 会话，预留）/
                system（系统内部调用，预留）/ unknown（无法确认）
    verified    是否通过格式校验（QQ 为 5~12 位纯数字）
    permission  权限等级：user（正常用户）/ sandbox（隔离沙盒）/
                admin（管理通道，预留）

对应契约：tests/test_p6sec_user_id_safety.py（p6sec 安全红线）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, FrozenSet, Optional, Tuple

__all__ = [
    "Identity",
    "IdentityResolver",
    "SANDBOX_ID",
    "DEFAULT_RESOLVER",
]

#: 沙盒身份 ID：所有无法确认身份的请求统一落入此 ID（隔离存储由接入层路由）
SANDBOX_ID = "_unknown_sender"

#: QQ 号格式：5~12 位纯数字（不允许前导/尾随空白以外的任何修饰）
_QQ_PATTERN = re.compile(r"^[0-9]{5,12}$")

#: 占位符黑名单（大小写不敏感）：显式列出便于审计，非列表内的非法格式同样落沙盒
_PLACEHOLDER_VALUES: FrozenSet[str] = frozenset(
    {"default", "anonymous", "guest", "unknown", "none", "null"}
)

#: 合法来源白名单（本阶段仅 qq 生效；api / system 为 P2.1.2+ 预留）
_ALLOWED_SOURCES: FrozenSet[str] = frozenset({"qq", "api", "system"})


@dataclass(frozen=True)
class Identity:
    """解析后的用户身份（不可变）。"""

    id: str
    source: str
    verified: bool
    permission: str

    @property
    def is_sandbox(self) -> bool:
        """是否为沙盒身份（未验证，需隔离存储）。"""
        return self.permission == "sandbox"


class IdentityResolver:
    """原始 user 标识 → Identity。

    规则（P2.1.1 契约，P2.1.2 修订）：
        1. 合法 QQ（5~12 位纯数字字符串）
           → {id: 原样, source: "qq", verified: True, permission: "user"}
        1b. JSON 数字型 user（int，非 bool，字符串形式恰为 5~12 位数字）
           → 按 QQ 原样使用（API 兼容：JSON number 无格式歧义，
              int→str 属规范化而非猜测/修正；True/False 排除）
        2. 非法情况（None / 空字符串 / 占位符 / 其他非字符串 / 异常格式）
           → {id: "_unknown_sender", source: "unknown", verified: False,
              permission: "sandbox"}

    禁止行为：
        - 不自动修正（如去前缀、补零）
        - 不猜测（如把 "366648462abc" 拆出数字）
        - 不转换（float/dict/bool 等非字符串一律落沙盒；仅 int 数字例外，见 1b）

    扩展接口（本阶段仅存储，不启用行为）：
        allowed_sources: 未来放行的来源白名单（当前仅 qq 实际生效）
        single_user_mode: 单用户显式兜底开关（预留；True 不改变本层行为，
                          由接入层在 P2.1.2 决定如何使用）
    """

    def __init__(
        self,
        *,
        allowed_sources: Tuple[str, ...] = ("qq",),
        single_user_mode: bool = False,
    ) -> None:
        sources = frozenset(allowed_sources) & _ALLOWED_SOURCES
        self._allowed_sources: FrozenSet[str] = sources or frozenset({"qq"})
        self._single_user_mode: bool = bool(single_user_mode)

    @property
    def single_user_mode(self) -> bool:
        """单用户模式开关（预留，本阶段不产生行为差异）。"""
        return self._single_user_mode

    @property
    def allowed_sources(self) -> FrozenSet[str]:
        """放行来源白名单（预留扩展点）。"""
        return self._allowed_sources

    # ------------------------------------------------------------------
    # 内部构造器
    # ------------------------------------------------------------------

    @staticmethod
    def _sandbox() -> Identity:
        """非法/无法确认身份 → 统一沙盒 Identity。"""
        return Identity(
            id=SANDBOX_ID,
            source="unknown",
            verified=False,
            permission="sandbox",
        )

    @staticmethod
    def _qq_identity(raw: str) -> Identity:
        """合法 QQ → user Identity（原样使用，不做任何变换）。"""
        return Identity(
            id=raw,
            source="qq",
            verified=True,
            permission="user",
        )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def resolve(self, raw_user: Any) -> Identity:
        """解析原始 user 标识。

        Args:
            raw_user: 请求携带的原始 user 值（任意类型）。

        Returns:
            Identity：合法 QQ → user；其余一切情况 → 沙盒。
            本方法永不抛异常。
        """
        try:
            if isinstance(raw_user, bool):
                # bool 是 int 子类，必须先排除（True 不能变成 "1"）
                return self._sandbox()

            if isinstance(raw_user, int):
                # JSON number 型 user：字符串形式恰为 5~12 位数字 → 按 QQ 使用
                as_str = str(raw_user)
                if _QQ_PATTERN.match(as_str):
                    return self._qq_identity(as_str)
                return self._sandbox()

            if not isinstance(raw_user, str):
                # 其余非字符串（None / float / dict / bytes …）一律落沙盒
                return self._sandbox()

            stripped = raw_user.strip()
            if not stripped:
                return self._sandbox()

            lowered = stripped.lower()
            if lowered in _PLACEHOLDER_VALUES:
                return self._sandbox()

            if _QQ_PATTERN.match(stripped):
                # 已通过 5~12 位纯数字校验；使用 strip 后的规范形式
                #（strip 仅去除首尾空白，不属于"修正"语义）
                return self._qq_identity(stripped)

            # 数字位数越界（如 4 位 / 13 位）或含非法字符 → 沙盒
            return self._sandbox()
        except Exception:  # noqa: BLE001
            # fail-closed：解析器自身异常也不允许外溢
            return self._sandbox()


#: 模块级默认解析器（纯函数语义，进程内可复用）
DEFAULT_RESOLVER = IdentityResolver()


def resolve_identity(raw_user: Any) -> Identity:
    """便捷函数：使用默认解析器解析身份。"""
    return DEFAULT_RESOLVER.resolve(raw_user)

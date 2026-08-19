# -*- coding: utf-8 -*-
"""
yuyi_desktop/__init__.py

Phase C.10.1 — Yuyi Desktop 控制平台顶层包

Yuyi Desktop 是浅雾羽依 AI 的桌面控制中心,作为 Runtime 的"只读控制层":

- 通过 Provider 间接观察 Runtime / Memory / Personality / Growth / Initiative 状态
- 不修改任何核心模块
- 不调用任何写接口
- 不影响 Runtime 生命周期
- 不改变 QQ / AstrBot 主动消息链路

安全边界:
- yuyi_desktop.services.* 仅可调用已有 Provider 的 get_* / list_* / read_* 接口
- 禁止: 修改 Personality / SelfModel / Memory / Growth / Relationship
- 禁止: 调用 apply / resolve / 任何写入流程
"""
from __future__ import annotations

__version__ = "0.1.0"
__phase__ = "C.10.1"

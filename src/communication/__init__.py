# -*- coding: utf-8 -*-
"""
Phase 4.1: Communication 模块

从 Relationship 状态推导 CommunicationStyle，
统一羽依的称呼、语气、互动行为。

当前状态：
- P4.1.0: CommunicationStyle 契约（src/contracts/communication_style.py）
- P4.1.1: CommunicationStyleResolver（src/communication/style_resolver.py）
- P4.1.2-B: CommunicationRenderer + 双 Runtime 接入（src/communication/renderer.py）
- P4.1.3: 移除 calling_rule 硬编码（待实现）
"""

from src.communication.style_resolver import CommunicationStyleResolver
from src.communication.renderer import CommunicationRenderer, merge_with_legacy_meta

__all__ = ["CommunicationStyleResolver", "CommunicationRenderer", "merge_with_legacy_meta"]
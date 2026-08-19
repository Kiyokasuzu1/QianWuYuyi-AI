# -*- coding: utf-8 -*-
"""
src/experience/

Phase 4.0-R2.5.1: 经历分诊台（Runtime 层 Experience 概念，非 Growth 专属）。

模块：
    - route_decision.py   ExperienceRouteDecision 结构化审计对象 + AuditEvent 桥接
    - experience_bridge.py ExperienceBridge 分诊入口（三通道路由，不执行业务管道）

设计原则:
    - Experience 是 Runtime 概念, 不是 Growth 专属。
    - 未来可挂 Self / Emotion / WorldKnowledge / Curiosity 通道。
    - 所有 Decision 必须可审计 (AuditEvent 体系)。
"""

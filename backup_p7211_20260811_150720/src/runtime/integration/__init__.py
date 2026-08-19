# -*- coding: utf-8 -*-
"""src/runtime/integration package.

Phase 5.0-D2: Runtime Integration 骨架层。

职责：
- 提供 IntegrationEvent 数据契约
- 提供 EventBridge（业务事件 ↔ 集成事件）
- 提供 RuntimeIntegrationHost（顶层宿主，持有 LifecycleManager）
- 提供 Adapter 基类 + 业务模块薄包装
- 提供 LifecycleTask 适配器接入点（Skeleton）

约束：
- 不依赖任何业务模块（Memory/Growth/Personality/Emotion/Relationship/Identity）
- 不修改 Lifecycle Core（src/runtime/lifecycle/**）
- 不修改业务模块源码
- 不调用 LLM / 数据库 / 第三方服务
- 仅依赖 Lifecycle Core + Python 标准库
"""

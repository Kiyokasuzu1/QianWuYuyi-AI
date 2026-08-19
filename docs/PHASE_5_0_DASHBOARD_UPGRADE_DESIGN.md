# Phase 5.0 Dashboard Upgrade 设计文档

文档版本：v1.0
适用范围：Phase 5.0 Dashboard 升级阶段
本阶段不写代码，仅输出架构设计等待确认。

---

## 1. 当前面板分析

### 1.1 当前技术栈

| 层级       | 技术                       |
|------------|----------------------------|
| 前端       | 原生 HTML + JavaScript（无框架）|
| 样式       | CSS（design-tokens + components）|
| 后端       | Flask Blueprint（admin_bp）|
| 状态读取   | RuntimeProvider / GovernanceProvider / SelfModelProvider |
| 数据持久化 | JSON 文件 + audit log       |
| 通信方式   | HTTP REST                   |
| 图标资源   | 自定义 SVG                  |
| 主题       | themes/yuyi-default.json   |
| Live2D     | pixi-live2d-display        |

### 1.2 当前前端资源

```
static/admin/
├── css/
│   ├── design-tokens.css
│   ├── style.css
│   ├── components/
│   │   ├── button.css
│   │   ├── card.css
│   │   ├── status.css
│   │   └── toast.css
│   ├── governance_dashboard.css
│   ├── runtime_dashboard.css
│   ├── selfmodel_dashboard.css
│   ├── selfmodel_health.css
│   └── selfmodel_timeline.css
├── icons/  (core / emotion / memory / module / system)
├── images/
├── js/
│   ├── app.js
│   ├── dashboard.js
│   ├── theme-manager.js
│   ├── toast.js
│   ├── governance_dashboard.js
│   ├── runtime_dashboard.js
│   ├── selfmodel_dashboard.js
│   ├── selfmodel_health.js
│   ├── selfmodel_timeline.js
│   ├── yuyi-state-manager.js
│   ├── yuyi-avatar.js
│   ├── yuyi-character.js
│   ├── yuyi-cognitive.js
│   ├── yuyi-memory-graph.js
│   ├── yuyi-radar.js
│   ├── yuyi-reasoning.js
│   ├── yuyi-relationship-timeline.js
│   ├── yuyi-timeline.js
│   ├── yuyi-autonomous.js
│   ├── yuyi-remote.js
│   ├── yuyi-agents.js
│   ├── yuyi-replies.js
│   ├── yuyi-growth-preview.js
│   ├── yuyi-audio.js
│   └── yuyi-background.js
├── themes/
│   └── yuyi-default.json
├── libs/  (Live2D SDK)
└── index.html
```

### 1.3 当前已有页面 / Tab

根据 index.html + JS 模块推断，当前面板包括：

- 首页 Dashboard（Uptime / Modules / 角色 / 系统健康）
- 角色面板（Character 状态 + Live2D 表情）
- 认知心智（Emotion trend / Attention / Thinking state）
- 认知记忆（Identity / Events / Memories / Stats）
- 认知成长（Stage / Records / Proposals / Stats）
- 认知关系（Trust / Familiarity / Milestones / Stats）
- Memory Graph（Canvas 力导向图）
- Cognitive Radar（五维能力）
- Reasoning Explanation
- Relationship Timeline
- Growth Proposal Preview
- Autonomous Goals / Reflections / Proactive / Dream
- Runtime State（SelfState / WorldState / Recent Actions）
- Authority Status（Memory / Vector / Emotion / Personality / SelfModel / Growth）
- Governance Personality / Memory / Growth
- SelfModel Identity / Beliefs / History / Reflections / Evolution Timeline / Health / Retention
- Modules 管理（启停 / 重载）
- Logs 实时查看
- Config 编辑 / 回滚
- Audit 日志
- Live2D Preview

### 1.4 当前已有功能

- 实时仪表盘聚合（uptime / health / modules / yuyi）
- Live2D 角色显示与动作
- 认知数据展示（情绪 / 记忆 / 成长 / 关系）
- 记忆图谱可视化
- 成长提案预览 / 批准 / 拒绝
- 治理操作（只读 + 受控 Proposal 提交）
- SelfModel 视图（Identity / Beliefs / History / Reflections / Timeline / Health）
- 模块启停 / 重载
- 配置查看 / 修改 / 回滚
- 审计日志查询
- 远程屏幕查看（screen capture）
- 远程控制（click / type / key）— 注意：当前已存在但需要被 Phase 5.0-E 权限层管理
- 日志文件查看

### 1.5 当前数据来源

```
Frontend (HTML/JS)
   ↓
Admin API (/api/...) —— Flask Blueprint admin_bp
   ↓
Provider 层
   ├── RuntimeProvider → RuntimeBridge → RuntimeCore（只读）
   ├── GovernanceProvider → RuntimeProvider + ProposalStorage
   └── SelfModelProvider → RuntimeProvider + audit
   ↓
业务模块（MemoryStore / GrowthState / PersonalityResolver / EmotionManager / SelfModelStore）
```

### 1.6 当前架构

```
Frontend
   ↓ HTTP REST
Admin API Blueprint
   ↓
Provider 层（RuntimeProvider / GovernanceProvider / SelfModelProvider）
   ↓
RuntimeBridge（单例）
   ↓
RuntimeCore Authority（Memory / Emotion / Personality / Growth / SelfModel）
```

### 1.7 当前缺陷

#### 数据层缺陷

- 缺少 Phase 5.0-D Body System 数据展示（Observation / Action / Permission / Executor）
- 缺少 SelfModel 演化深度视图（Belief → Proposal → PCR 链路的可视追踪不完整）
- 缺少事件流时序图（IntegrationEvent 真实时序）
- 缺少 Lifecycle Task 列表与状态（Phase 5.0-A 数据无 UI）
- 缺少 Goal / Initiative 专门页面（仅有 mock）
- 缺少 Reflection 详细页面（仅有 mock / 与 SelfModel 部分耦合）
- 缺少 Creator Engine / Curiosity Engine 可视化

#### 实时层缺陷

- 全部使用 HTTP REST + 手动 refresh，没有 WebSocket 推送
- 数据更新依赖前端轮询，延迟高
- 事件流只能通过日志文件查看，不是结构化展示

#### 可视化缺陷

- 状态展示以卡片列表为主，缺乏时序趋势图
- 缺少事件流时间线
- 缺少 Life Timeline（羽依一生的关键事件）
- 缺少 Memory 网络图（仅有 mock）

#### 一致性缺陷

- 部分接口直接通过 `_orchestrator.xxx` 访问（如 _get_emotion_snapshot, _get_character_state），未统一走 Provider
- 部分接口使用 mock 数据，但 mock 与真实数据混在返回结构中
- 部分接口容错时返回 _mock_xxx() 但 ok 字段仍为 True，前端难以区分

#### 体验缺陷

- 没有 WebSocket 实时通知
- 没有按时间倒序的事件流
- 没有按状态分组的提案列表
- 没有按主题 / 域分类的记忆浏览

#### 安全缺陷

- 远程控制接口（/api/control/click、/api/control/type、/api/control/key）当前直接执行，没有经过 Phase 5.0-E Permission 层
- 模块启停 / 配置修改 / 远程控制全部没有危险操作二次确认 UI
- 没有操作日志的实时展示

---

## 2. 结合羽依当前架构分析

### 2.1 已有系统

Phase 5.0 已经完成：

- Phase 5.0-A：Lifecycle Core
- Phase 5.0-B：Runtime Integration
- Phase 5.0-C：Memory / Emotion / Growth / Personality / Relationship / SelfModel
- Phase 5.0-D：Reflection / Initiative / Goal

### 2.2 接入方式

新增 Dashboard 接入只能通过以下四种方式：

1. API（HTTP REST + WebSocket）
2. Adapter（Provider 层）
3. Event（IntegrationEvent 订阅）
4. Snapshot（运行时快照）

禁止：

- Dashboard 直接 import 业务模块
- Dashboard 直接访问 RuntimeCore
- Dashboard 绕过 Provider 层
- Dashboard 修改任何 Authority 实例

### 2.3 数据流设计

```
Dashboard UI
   ↓
API Adapter（新增 /api/dashboard/v2/*）
   ↓
Dashboard Provider（新增 src/admin/dashboard_provider.py）
   ↓
   ├── RuntimeProvider（已有）
   ├── GovernanceProvider（已有）
   ├── SelfModelProvider（已有）
   ├── BodyProvider（新增，Phase 5.0-E）
   ├── LifecycleProvider（新增，Phase 5.0-A）
   ├── GoalProvider（新增，Phase 5.0-D）
   ├── InitiativeProvider（新增，Phase 5.0-D）
   └── ReflectionProvider（新增，Phase 5.0-C）
   ↓
Runtime Snapshot（统一快照契约）
```

### 2.4 不修改业务模块

- 不修改 src/memory/**
- 不修改 src/emotion/**
- 不修改 src/growth/**
- 不修改 src/personality/**
- 不修改 src/relationship/**
- 不修改 src/runtime/lifecycle/**
- 不修改 src/runtime/reflection/**
- 不修改 src/runtime/initiative/**
- 不修改 src/runtime/goal/**
- 不修改 src/runtime/self_model/**

只新增：

- src/admin/dashboard_provider.py
- src/admin/dashboard_router.py
- src/admin/dashboard_snapshot.py
- src/admin/dashboard_event_hub.py
- src/admin/dashboard_ws.py
- src/admin/permissions/dashboard_auth.py

---

## 3. 新版 Dashboard 架构

### 3.1 设计目标

将 Dashboard 升级为羽依的"生命状态控制中心"。

核心定位：

- 生命感：展示羽依当前状态像"看一个生命体"
- 透明度：所有数据可追溯、可解释
- 安全性：所有操作经过权限、审计、确认
- 实时性：关键状态通过 WebSocket 推送

### 3.2 整体布局

```
┌─────────────────────────────────────────────────────┐
│  顶部状态栏：在线状态 / 当前情绪 / 当前目标 / 通知   │
├──────┬──────────────────────────────┬───────────────┤
│      │                              │               │
│ 导  │     主内容区                  │  实时事件流   │
│ 航  │     （根据页面切换）          │  （常驻）     │
│ 栏  │                              │               │
│      │                              │               │
│      │                              │               │
└──────┴──────────────────────────────┴───────────────┘
```

### 3.3 导航结构

```
Overview
   ├── 生命状态总览
   ├── 当前 Goal
   ├── 最近事件
   └── 紧急通知

SelfModel
   ├── Identity
   ├── Traits
   ├── Capabilities
   ├── Interests
   └── ChangeHistory

Memory
   ├── 最近记忆
   ├── 重要事件
   ├── 影响关系
   └── 时间线

Reflection
   ├── 每日反思
   ├── 洞察
   └── 证据链

Initiative
   ├── 兴趣信号
   ├── PossibleAction
   ├── 主动倾向
   └── 过滤原因

Goal
   ├── 当前目标
   ├── 目标历史
   └── 目标变化

Runtime
   ├── Lifecycle 状态
   ├── Task 列表
   ├── Tick 记录
   └── Event 流

Body
   ├── Observation
   ├── Action 队列
   ├── Permission 状态
   └── Executor 日志

Debug
   ├── IntegrationEvent
   ├── EventLog
   ├── EventStore
   └── 错误记录
```

### 3.4 页面设计

#### 3.4.1 Overview（首页）

布局：

- 顶部 4 个核心卡片
  - 在线状态
  - 当前情绪
  - 当前 Goal
  - 最近事件数
- 中部 2 个大卡片
  - 当前目标（最近 1 个 GoalState）
  - 当前兴趣（最近 1 个 InterestSignal）
- 右侧事件流
- 底部 1 个时间线
  - 最近 24 小时事件

数据来源：

- RuntimeProvider.get_status
- RuntimeProvider.get_emotion_summary
- GoalProvider.get_active_goal
- InitiativeProvider.get_recent_interest
- IntegrationEventStore.get_recent_events

#### 3.4.2 SelfModel

布局：

- 顶部 Identity 卡片
- 左 Traits（5 大特质条形图）
- 中 Capabilities（5 维能力雷达）
- 右 Interests（兴趣列表）
- 下 ChangeHistory（演化时间线）

数据来源：

- SelfModelProvider.get_identity
- SelfModelProvider.list_beliefs
- SelfModelProvider.get_evolution_timeline
- SelfModelProvider.get_health_report

#### 3.4.3 Memory

布局：

- 顶部统计卡片（总数 / 重要数 / 近期数）
- 左侧时间线（按天聚合）
- 右侧详情面板（点击时间线条目后展开）
- 顶部筛选：memory_type / importance / date range

数据来源：

- RuntimeProvider.get_memory_summary
- 新增 MemoryProvider.get_memory_timeline
- 新增 MemoryProvider.get_important_memories

#### 3.4.4 Reflection

布局：

- 顶部 tabs：每日 / 事件 / 成长
- 左侧反思列表
- 右侧选中项的洞察 + 证据链
- 底部 action 按钮（不影响数据，仅查看）

数据来源：

- 新增 ReflectionProvider.list_reflections
- 新增 ReflectionProvider.get_insights
- 新增 ReflectionProvider.get_evidence_chain

#### 3.4.5 Initiative

布局：

- 顶部统计（interest_count / possible_action_count / filter_rate）
- 左侧 InterestSignal 列表
- 中间 PossibleAction 列表
- 右侧 ActionFilter 原因（被过滤掉的动作）

数据来源：

- 新增 InitiativeProvider.list_interest_signals
- 新增 InitiativeProvider.list_possible_actions
- 新增 InitiativeProvider.list_filtered_actions

#### 3.4.6 Goal

布局：

- 顶部 GoalState 当前值
- 左侧目标列表（按 status 分组）
- 右侧目标历史
- 底部目标变化趋势图

数据来源：

- 新增 GoalProvider.list_goals
- 新增 GoalProvider.get_goal_history
- 新增 GoalProvider.get_goal_changes

#### 3.4.7 Runtime

布局：

- 顶部 Runtime 状态（is_running / initialized）
- 左侧 LifecycleTask 列表（每行：name / status / last_tick_ago / error_count）
- 中间 tick 时间线
- 右侧 Event 流（最近 100 条）

数据来源：

- RuntimeProvider.get_status
- 新增 LifecycleProvider.list_tasks
- 新增 LifecycleProvider.get_tick_history
- IntegrationEventStore.get_recent_events

#### 3.4.8 Body（Phase 5.0-E）

布局：

- 顶部 4 个核心数据卡
  - Observation 计数
  - Action 队列长度
  - 当前 PermissionLevel
  - Executor 最近结果
- 左侧 ObservationEvent 列表
- 中间 ActionProposal 列表
- 右侧 Permission 状态
- 底部 Executor 日志

数据来源：

- 新增 BodyProvider.list_observations
- 新增 BodyProvider.list_action_proposals
- 新增 BodyProvider.get_permission_status
- 新增 BodyProvider.list_executor_results

#### 3.4.9 Debug

布局：

- 顶部 tab：IntegrationEvent / EventLog / EventStore / Errors
- 左侧筛选器：source / event_type / time range
- 中间事件流
- 右侧选中事件的 payload 详情

数据来源：

- IntegrationEventStore.query
- IntegrationEventLog.query
- AuditLog.query

### 3.5 UI 设计原则

符合"AI 生命控制中心"风格：

- 主色调：白底 / 浅蓝 / 浅紫 / 粉（轻量化、温柔）
- 卡片：glassmorphism、圆角、柔阴影
- 动画：微动效、状态过渡
- 字体：圆润、易读
- 图标：现有 SVG 体系
- Live2D 区域：保留作为核心生命感表达

### 3.6 实时性

- WebSocket 推送：IntegrationEvent 实时推送
- 前端订阅：按页面订阅不同事件类型
- 通知系统：紧急事件（如 Proposal Pending / Permission Denied / Error）显示 toast

---

## 4. 数据接口规划

### 4.1 API 层次

```
Dashboard
   ↓
Dashboard Router（src/admin/dashboard_router.py）
   ↓
Dashboard Provider（src/admin/dashboard_provider.py）
   ↓
多个 Provider（RuntimeProvider / GovernanceProvider / SelfModelProvider / BodyProvider / ...）
   ↓
Runtime / RuntimeBridge（只读）
```

### 4.2 GET 接口（v2）

#### Overview

- `GET /api/dashboard/v2/overview`
  - 返回：online / emotion / current_goal / current_interest / recent_event_count / health
- `GET /api/dashboard/v2/overview/timeline?range=24h`
  - 返回：最近 24 小时事件时间线

#### SelfModel

- `GET /api/dashboard/v2/selfmodel/identity`
- `GET /api/dashboard/v2/selfmodel/traits`
- `GET /api/dashboard/v2/selfmodel/capabilities`
- `GET /api/dashboard/v2/selfmodel/interests`
- `GET /api/dashboard/v2/selfmodel/change-history?limit=50`

#### Memory

- `GET /api/dashboard/v2/memory/recent?limit=20`
- `GET /api/dashboard/v2/memory/important?limit=20`
- `GET /api/dashboard/v2/memory/timeline?range=24h`
- `GET /api/dashboard/v2/memory/related?memory_id=xxx`

#### Reflection

- `GET /api/dashboard/v2/reflection/list?type=daily&limit=20`
- `GET /api/dashboard/v2/reflection/<reflection_id>/insights`
- `GET /api/dashboard/v2/reflection/<reflection_id>/evidence`

#### Initiative

- `GET /api/dashboard/v2/initiative/interests?limit=20`
- `GET /api/dashboard/v2/initiative/possible-actions?limit=20`
- `GET /api/dashboard/v2/initiative/filtered?limit=20`

#### Goal

- `GET /api/dashboard/v2/goal/list?status=active`
- `GET /api/dashboard/v2/goal/<goal_id>`
- `GET /api/dashboard/v2/goal/history?limit=50`
- `GET /api/dashboard/v2/goal/changes?range=7d`

#### Runtime

- `GET /api/dashboard/v2/runtime/status`
- `GET /api/dashboard/v2/runtime/tasks`
- `GET /api/dashboard/v2/runtime/ticks?limit=100`
- `GET /api/dashboard/v2/runtime/events?limit=50&source=...`

#### Body

- `GET /api/dashboard/v2/body/observations?limit=20`
- `GET /api/dashboard/v2/body/action-proposals?status=pending`
- `GET /api/dashboard/v2/body/permission`
- `GET /api/dashboard/v2/body/executor-results?limit=20`

#### Debug

- `GET /api/dashboard/v2/debug/events?source=...&type=...&since=...&limit=100`
- `GET /api/dashboard/v2/debug/event-log?limit=100`
- `GET /api/dashboard/v2/debug/event-store?type=...&limit=100`
- `GET /api/dashboard/v2/debug/errors?since=...&limit=50`

### 4.3 POST 接口（仅管理操作）

- `POST /api/dashboard/v2/governance/proposal/review`
  - Body：proposal_id, action（approve/reject/modify）, reason
  - 走 GovernanceProvider

- `POST /api/dashboard/v2/memory/propose-action`
  - Body：memory_id, action, reason
  - 走 GovernanceProvider

- `POST /api/dashboard/v2/permission/set-level`
  - Body：level
  - 仅在 PermissionLevel=LEVEL_2+ 时可设置
  - 二次确认

- `POST /api/dashboard/v2/runtime/inject-event`（调试用）
  - Body：event_type, event_data
  - 走 RuntimeProvider

### 4.4 WebSocket 接口

`WS /api/dashboard/v2/ws`

#### 客户端订阅格式

```json
{
    "action": "subscribe",
    "channels": ["integration_events", "permission_changes", "errors"]
}
```

#### 推送消息格式

```json
{
    "channel": "integration_events",
    "event": {
        "event_id": "evt_xxx",
        "event_type": "INTEGRATION_ACTION_PROPOSED",
        "source": "agent_body.action",
        "timestamp": "2026-08-01T10:00:00Z",
        "payload": {...}
    }
}
```

#### 推送通道

- integration_events
- permission_changes
- errors
- lifecycle_status
- body_status
- yuyi_state_changes

### 4.5 响应统一格式

成功：

```json
{
    "ok": true,
    "data": {...},
    "timestamp": "2026-08-01T10:00:00Z"
}
```

失败：

```json
{
    "ok": false,
    "error": {
        "code": "PERMISSION_DENIED",
        "message": "..."
    },
    "timestamp": "2026-08-01T10:00:00Z"
}
```

### 4.6 限流

- 默认 60 次 / 分钟
- WebSocket 推送限流：100 条 / 秒
- 错误超过 10 次 / 分钟返回 429

---

## 5. UI 设计规划

### 5.1 整体布局

```
┌─────────────────────────────────────────────────────┐
│  顶栏：Logo / 状态 / 通知 / 用户                     │
├──────┬──────────────────────────┬──────────────────┤
│      │                          │                  │
│ 导航 │  主内容区                 │  实时事件流      │
│      │                          │  （常驻）        │
│      │                          │                  │
│      │                          │                  │
└──────┴──────────────────────────┴──────────────────┘
```

### 5.2 顶栏

- 左侧：Logo + "羽依 Yuyi"
- 中部：当前状态指示（在线 / 离线 / 维护中）
- 右侧：通知 / 主题切换 / 用户菜单

### 5.3 左侧导航

宽度：240px

- Overview
- SelfModel
- Memory
- Reflection
- Initiative
- Goal
- Runtime
- Body
- Debug

每个图标使用现有 SVG 体系。

### 5.4 主内容区

- 自适应宽度
- 页面切换使用 SPA 路由（前端 hash router）
- 状态保持：切回页面时恢复上次滚动位置和筛选条件

### 5.5 实时事件流（右栏）

宽度：320px

- 默认显示最近 20 条 IntegrationEvent
- 按时间倒序
- 颜色按 event_type 区分
- 点击展开 payload
- 可订阅多个通道

### 5.6 视觉规范

- 主色：浅紫 / 浅蓝 / 粉
- 强调色：粉（关键状态）
- 背景：白 / 浅色玻璃
- 字体：圆润（PingFang / Noto Sans CJK）
- 圆角：12px
- 阴影：soft shadow
- 动画：transition 0.2s ease

### 5.7 Live2D 集成

- 保留现有 Live2D 区域
- 在 Overview 页面右上角显示
- 表情 / 动作 / 消息与情绪状态实时联动
- 不与新事件流重叠

---

## 6. 安全设计

### 6.1 权限分层

```
Dashboard User
   ↓
Dashboard Auth（仅本地访问，无外部用户）
   ↓
API Adapter
   ↓
Provider（只读 / 受控）
   ↓
Runtime / RuntimeBridge
```

### 6.2 危险操作分类

| 操作                     | 风险等级 | 二次确认 |
|--------------------------|----------|----------|
| 查看任何数据              | LOW      | 否       |
| 提交 Proposal Review     | MEDIUM   | 是       |
| 修改配置                  | HIGH     | 是       |
| 设置 PermissionLevel    | HIGH     | 是       |
| 远程控制（Phase 5.0-E 后）| CRITICAL | 是 + Phase 5.0-E Permission |

### 6.3 远程控制安全

Phase 5.0-E 后：

- 远程控制按钮必须先经过 PermissionManager.check
- 必须经过 ConfirmationModal
- 必须记录到 audit log
- 默认 PermissionLevel=LEVEL_1，远程控制不可用
- 提升到 LEVEL_4 后才可使用 COMMUNICATE 类动作

### 6.4 禁止路径

- Dashboard 按钮 → 直接执行电脑操作 ❌
- Dashboard 按钮 → 绕过 PermissionManager ❌
- Dashboard 按钮 → 直接修改 Authority 实例 ❌
- Dashboard WebSocket 推送 → 写入业务模块 ❌
- Dashboard → 调用 LLM / 网络 / OS 控制 ❌

### 6.5 审计

- 所有 POST 接口写入 audit log
- 所有危险操作记录 reason + operator
- audit log 可在 Debug 页面查询

### 6.6 Dashboard 访问控制

- 仅本机访问（127.0.0.1）
- 不暴露到公网
- 未来如需远程访问，必须加 token + IP 白名单

---

## 7. 实施路线

### Step 1：基础框架

新增：
- `src/admin/dashboard_provider.py`
- `src/admin/dashboard_router.py`
- `src/admin/dashboard_snapshot.py`
- `src/admin/dashboard_event_hub.py`
- `src/admin/dashboard_ws.py`
- `static/admin/dashboard_v2/index.html`
- `static/admin/dashboard_v2/css/dashboard_v2.css`
- `static/admin/dashboard_v2/js/router.js`
- `static/admin/dashboard_v2/js/api.js`
- `static/admin/dashboard_v2/js/ws.js`

修改：
- `src/admin/api/routes.py`（注册新 Blueprint）
- `static/admin/index.html`（添加 v2 入口链接）

风险：
- 不影响现有 /api/ 路径
- 现有 routes 完全保留

测试：
- 单元测试：DashboardProvider / DashboardRouter / DashboardEventHub
- 集成测试：现有 API 不受影响
- UI 测试：v2 入口可访问

### Step 2：Runtime 状态接入

新增：
- `src/admin/runtime_dashboard_provider.py`
- `static/admin/dashboard_v2/js/pages/runtime.js`
- `static/admin/dashboard_v2/css/pages/runtime.css`

GET：
- `/api/dashboard/v2/runtime/status`
- `/api/dashboard/v2/runtime/tasks`
- `/api/dashboard/v2/runtime/ticks`
- `/api/dashboard/v2/runtime/events`

风险：
- RuntimeProvider 已就绪，无新依赖

测试：
- Runtime 数据完整性
- Tick 历史正确性
- Event 流正确性

### Step 3：SelfModel / Reflection 接入

新增：
- `src/admin/selfmodel_dashboard_provider.py`
- `src/admin/reflection_dashboard_provider.py`
- `static/admin/dashboard_v2/js/pages/selfmodel.js`
- `static/admin/dashboard_v2/js/pages/reflection.js`
- `static/admin/dashboard_v2/css/pages/selfmodel.css`
- `static/admin/dashboard_v2/css/pages/reflection.css`

GET：
- `/api/dashboard/v2/selfmodel/*`
- `/api/dashboard/v2/reflection/*`

风险：
- SelfModelProvider 已就绪
- ReflectionProvider 新增，复用 ReflectionEngine

测试：
- Identity / Beliefs / History 正确性
- Reflection 洞察 + 证据链完整性

### Step 4：Goal / Initiative / Memory 接入

新增：
- `src/admin/goal_dashboard_provider.py`
- `src/admin/initiative_dashboard_provider.py`
- `src/admin/memory_dashboard_provider.py`
- `static/admin/dashboard_v2/js/pages/goal.js`
- `static/admin/dashboard_v2/js/pages/initiative.js`
- `static/admin/dashboard_v2/js/pages/memory.js`
- `static/admin/dashboard_v2/css/pages/{goal,initiative,memory}.css`

GET：
- `/api/dashboard/v2/goal/*`
- `/api/dashboard/v2/initiative/*`
- `/api/dashboard/v2/memory/*`

风险：
- GoalProvider / InitiativeProvider / MemoryProvider 新增
- 必须仅做只读访问

测试：
- 数据隔离（Dashboard Provider 不修改业务模块）
- 真实数据 vs fallback 区分

### Step 5：实时事件流

新增：
- WebSocket handler
- 前端 EventStream 组件
- 订阅 / 取消订阅

修改：
- `src/admin/dashboard_event_hub.py`（订阅 IntegrationEvent）
- `static/admin/dashboard_v2/js/event_stream.js`

风险：
- WebSocket 性能
- 大量事件时前端压力

测试：
- WebSocket 订阅正常
- 取消订阅正常
- 大量事件不卡顿

### Step 6：视觉优化

新增：
- 完整设计 token
- 所有页面 CSS
- 微动效
- 主题切换

修改：
- `static/admin/dashboard_v2/css/design-tokens.css`

风险：
- 视觉一致性
- 性能（动画）

测试：
- 跨页面视觉一致
- 动画流畅

### Step 7：安全与权限

新增：
- Dashboard Auth（仅本地）
- 危险操作二次确认 Modal
- 审计日志集成

修改：
- 所有 POST 接口

风险：
- 现有功能不受影响

测试：
- 危险操作必须确认
- 远程控制受 Phase 5.0-E Permission 限制
- 审计日志记录完整

### Step 8：验收与文档

新增：
- `docs/PHASE_5_0_DASHBOARD_UPGRADE_REPORT.md`
- `docs/dashboard.md`（使用说明）
- 单元测试 + 集成测试 + E2E 测试

---

## 8. 验收标准

### 8.1 数据完整性

- 所有 GET 接口在数据可用时返回真实数据
- 所有 GET 接口在数据不可用时返回 fallback（不报错）
- fallback 字段必须明确标识（如 `"fallback": true`）
- 不允许返回 mock 数据时 `"ok": true`

### 8.2 实时性

- WebSocket 推送延迟 < 500ms
- HTTP 轮询默认 5s（可配置）
- 重要事件立即推送

### 8.3 安全性

- 所有危险操作二次确认
- 所有 POST 操作记录 audit log
- 远程控制必须经过 Phase 5.0-E Permission
- Dashboard 不直接 import 业务模块
- Dashboard 不修改任何 Authority 实例

### 8.4 性能

- 页面首屏加载 < 2s
- 数据接口响应 < 500ms
- WebSocket 推送 < 200ms
- 长时间运行不内存泄漏

### 8.5 测试覆盖

- 单元测试 >= 200 个
- 集成测试覆盖所有 GET / POST
- WebSocket 测试覆盖订阅 / 推送 / 取消
- E2E 测试覆盖主要页面

### 8.6 兼容性

- 现有 /api/ 路径完全保留
- 现有 /admin/ 页面完全保留
- 现有 Live2D 完全保留
- v2 入口与 v1 入口可独立访问

### 8.7 验收检查清单

- [ ] 所有 Provider 仅做只读访问
- [ ] 所有 Provider 通过 RuntimeProvider / GovernanceProvider / SelfModelProvider 访问
- [ ] 所有 POST 接口记录 audit log
- [ ] 远程控制必须经过 Phase 5.0-E Permission
- [ ] WebSocket 推送稳定
- [ ] 所有页面有加载状态 / 错误状态 / 空状态
- [ ] 视觉符合"AI 生命控制中心"风格
- [ ] config.yaml 未被修改
- [ ] 现有 /api/ 路径完全保留

---

## 9. 设计边界

本设计：

- 只新增 Dashboard v2 入口与 API
- 完全不修改业务模块
- 严格通过 Provider 层访问
- 不绕过权限层
- 不连接 LLM / 网络 / OS 控制
- 与 Phase 5.0-E Body System 协同（Dashboard 不直接调用 Executor）

---

等待确认。

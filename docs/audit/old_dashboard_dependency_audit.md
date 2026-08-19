# Old Dashboard Dependency Audit

> Phase C.10.0 — 旧 Web Dashboard 详细依赖审计
> 仅审计,无任何代码修改
> 生成时间:2026-08-04

---

## 1. 概述

项目早期制作了两个网页控制面板:

1. **Dashboard v1** — 老式 AI 控制中心(`static/admin/index.html`)
2. **Dashboard v2** — 生命状态控制中心(`static/admin/dashboard_v2/index.html`)

两者均通过 Flask Blueprint 提供后端,均部署在 `admin_bp` 路径下。

---

## 2. Dashboard v1 详细清单

### 2.1 前端文件(45 个)

#### HTML(1 个)
- `static/admin/index.html` — 入口

#### CSS(12 个)
- `static/admin/css/design-tokens.css`
- `static/admin/css/style.css`
- `static/admin/css/runtime_dashboard.css`
- `static/admin/css/governance_dashboard.css`
- `static/admin/css/selfmodel_dashboard.css`
- `static/admin/css/selfmodel_timeline.css`
- `static/admin/css/selfmodel_health.css`
- `static/admin/css/components/card.css`
- `static/admin/css/components/button.css`
- `static/admin/css/components/status.css`
- `static/admin/css/components/toast.css`

#### JS 核心(4 个)
- `static/admin/js/app.js`
- `static/admin/js/dashboard.js`
- `static/admin/js/theme-manager.js`
- `static/admin/js/toast.js`

#### JS 角色/UI(4 个)
- `static/admin/js/yuyi-background.js`
- `static/admin/js/yuyi-character.js`
- `static/admin/js/yuyi-avatar.js`
- `static/admin/js/yuyi-audio.js`

#### JS 视图(11 个)
- `static/admin/js/yuyi-radar.js`
- `static/admin/js/yuyi-cognitive.js`
- `static/admin/js/yuyi-reasoning.js`
- `static/admin/js/yuyi-timeline.js`
- `static/admin/js/yuyi-replies.js`
- `static/admin/js/yuyi-state-manager.js`
- `static/admin/js/yuyi-relationship-timeline.js`
- `static/admin/js/yuyi-memory-graph.js`
- `static/admin/js/yuyi-growth-preview.js`
- `static/admin/js/yuyi-autonomous.js`
- `static/admin/js/yuyi-remote.js`
- `static/admin/js/yuyi-agents.js`

#### JS 仪表板(5 个)
- `static/admin/js/runtime_dashboard.js`
- `static/admin/js/governance_dashboard.js`
- `static/admin/js/selfmodel_dashboard.js`
- `static/admin/js/selfmodel_health.js`
- `static/admin/js/selfmodel_timeline.js`

#### 资源
- `static/admin/images/yuyi-character.jpg`
- `static/admin/images/yuyi-character-ref.txt`
- `static/admin/themes/yuyi-default.json`
- `static/admin/icons/{core,emotion,memory,module,system}/*.svg`(25+)

### 2.2 后端依赖

| 文件 | 角色 | 关键引用 |
|---|---|---|
| `src/admin/api/routes.py::admin_index` | 入口 | `send_from_directory("static/admin", "index.html")` |
| `src/admin/dashboard/router.py` | 共享 v1+v2 | 间接通过 v2 Blueprint |
| `src/admin/dashboard/governance.py` | v1 残留 | 仅 v1 使用 |
| `src/admin/dashboard/runtime_router.py` | v1+v2 共用 | 两者都用 |
| `src/admin/dashboard/selfmodel_router.py` | v1+v2 共用 | 两者都用 |
| `src/admin/runtime_dashboard_provider.py` | 共享 | 提供 runtime 快照 |
| `src/admin/selfmodel_dashboard_provider.py` | 共享 | 提供 selfmodel 快照 |
| `src/admin/goal_dashboard_provider.py` | 共享 | 提供 goal 快照 |
| `src/admin/initiative_dashboard_provider.py` | 共享 | 提供 initiative 快照 |
| `src/admin/life_state_provider.py` | 共享 | 提供 life_state 快照 |
| `src/admin/life_timeline_provider.py` | 共享 | 提供 life_timeline 快照 |
| `src/admin/life_graph_provider.py` | 共享 | 提供 life_graph 快照 |

---

## 3. Dashboard v2 详细清单

### 3.1 前端文件(20 个)

#### HTML(2 个)
- `static/admin/dashboard_v2/index.html` — 入口
- `static/admin/dashboard_v2/pages/overview.html` — 概览子页

#### CSS(2 个)
- `static/admin/dashboard_v2/css/dashboard_v2.css`
- `static/admin/dashboard_v2/css/dashboard_v2.css.bak`
- `static/admin/dashboard_v2/css/event_stream_additions.css`

#### JS(16 个)
- `static/admin/dashboard_v2/js/api.js`
- `static/admin/dashboard_v2/js/ws.js`
- `static/admin/dashboard_v2/js/router.js`
- `static/admin/dashboard_v2/js/overview.js`
- `static/admin/dashboard_v2/js/runtime.js`
- `static/admin/dashboard_v2/js/selfmodel.js`
- `static/admin/dashboard_v2/js/memory.js`
- `static/admin/dashboard_v2/js/reflection.js`
- `static/admin/dashboard_v2/js/goal.js`
- `static/admin/dashboard_v2/js/initiative.js`
- `static/admin/dashboard_v2/js/life_graph.js`
- `static/admin/dashboard_v2/js/life_state.js`
- `static/admin/dashboard_v2/js/life_timeline.js`
- `static/admin/dashboard_v2/js/trace_panel.js`
- `static/admin/dashboard_v2/js/audit.js`
- `static/admin/dashboard_v2/js/event_stream.js`

### 3.2 后端依赖

#### v2 独有 Blueprint(12 个)
- `src/admin/dashboard/runtime_router.py` → `runtime_v2_bp`
- `src/admin/dashboard/selfmodel_router.py` → `selfmodel_v2_bp`
- `src/admin/dashboard/memory_router.py` → `memory_v2_bp`
- `src/admin/dashboard/growth_router.py` → `growth_v2_bp`
- `src/admin/dashboard/reflection_router.py` → `reflection_v2_bp`
- `src/admin/dashboard/initiative_router.py` → `initiative_v2_bp`
- `src/admin/dashboard/goal_router.py` → `goal_v2_bp`
- `src/admin/dashboard/life_graph_router.py` → `life_graph_v2_bp`
- `src/admin/dashboard/life_state_router.py` → `life_state_v2_bp`
- `src/admin/dashboard/life_timeline_router.py` → `life_timeline_v2_bp`
- `src/admin/dashboard/event_stream_router.py` → `event_stream_v2_bp`
- `src/admin/dashboard/security_router.py` → `security_v2_bp`

#### v2 框架层(11 个)
- `src/admin/dashboard/router.py` → `dashboard_v2_bp`
- `src/admin/dashboard/provider.py` — Provider 聚合
- `src/admin/dashboard/event_hub.py` — 事件中心
- `src/admin/dashboard/snapshot.py` — 快照基础
- `src/admin/dashboard/trace.py` — Trace
- `src/admin/dashboard/response.py` — 响应构造
- `src/admin/dashboard/audit.py` — 审计
- `src/admin/dashboard/governance.py` — 治理
- `src/admin/dashboard/_security.py` — 安全
- `src/admin/dashboard/_meta.py` — 元数据
- `src/admin/dashboard/ws.py` — WebSocket

---

## 4. 引用 6 大核心模块分析

| 核心模块 | v1 引用 | v2 引用 | 引用方式 | 是否只读 |
|---|---|---|---|---|
| Runtime | 间接 | 间接 | Provider 快照 | ✅ 只读 |
| Memory | 几乎无 | 间接 | memory_dashboard_provider | ✅ 只读 |
| Personality | UI 显示 | 间接 | selfmodel_dashboard_provider | ✅ 只读 |
| Growth | 间接 | 间接 | growth_dashboard_provider | ✅ 只读 |
| SelfModel | 间接 | 间接 | selfmodel_dashboard_provider | ✅ 只读 |
| Initiative | 无 | 间接 | initiative_dashboard_provider | ✅ 只读 |
| QQ | 无 | 无 | (无直接引用) | N/A |

**关键结论:**
- 所有 Dashboard 引用**只通过 Provider**
- Provider 全部为**只读快照**
- 无任何 Dashboard 代码直接 import 核心业务模块
- AstrBot / QQ 接口完全独立

---

## 5. 引用测试分析

### 5.1 v1/v2 引用测试(将被废弃)

| 测试文件 | 依赖 |
|---|---|
| `tests/test_admin_ui_integration.py` | v1 UI |
| `tests/test_admin_selfmodel_ui_integration.py` | v1 selfmodel UI |
| `tests/test_admin_selfmodel_health_ui.py` | v1 selfmodel health UI |
| `tests/test_admin_selfmodel_timeline_ui.py` | v1 selfmodel timeline UI |
| `tests/test_admin_selfmodel_api.py` | v1 selfmodel API |
| `tests/test_admin_selfmodel_provider.py` | v1 selfmodel provider |
| `tests/test_runtime_live2d_dashboard.py` | v1 live2d 集成 |
| `tests/test_dashboard_provider.py` | v2 provider |
| `tests/test_dashboard_security.py` | v2 security |
| `tests/test_phase_c1_dashboard_v2.py` | v2 集成 |
| `tests/test_selfmodel_dashboard_provider.py` | selfmodel provider |
| `tests/test_runtime_dashboard_provider.py` | runtime dashboard provider |

### 5.2 v2 数据源测试(必须保留)

| 测试文件 | 保留理由 |
|---|---|
| `tests/test_admin_governance.py` | Provider 单元测试 |
| `tests/test_admin_phase0.py` | Admin 基础 |
| `tests/test_admin_phase05_integration.py` | Admin 集成 |
| `tests/test_admin_phase1.py` | Admin Phase 1 |
| `tests/test_admin_runtime_integration.py` | runtime 集成(核心) |
| `tests/test_admin_expansion_phaseA.py` | 扩展 |
| `tests/audit/phase_c0_audit_result.json` | 审计 |
| `tests/audit/test_phase_c0_full_system_validation.py` | 验证 |

### 5.3 其他间接引用测试

下列测试间接引用 dashboard 路径,但核心是测试核心模块(可保留):

- `tests/test_runtime_phase_b13.py`
- `tests/test_runtime_lifecycle_e2e.py`
- `tests/test_growth_proposal_integration.py`
- `tests/test_growth_proposal_adapter.py`
- `tests/test_audit_completeness.py`
- `tests/test_understanding_layer.py`

---

## 6. 删除安全条件

每个文件/目录的删除必须满足:

1. ❌ 无业务代码 import 该路径
2. ❌ 无 src/runtime/**、src/growth/**、src/personality/**、src/self_model/**、src/relationship/**、src/memory/** 依赖
3. ❌ 除 test_admin_*.py 外,无其他 tests/*.py 直接 import
4. ❌ 无 Provider 文件被删除(Provider 是 Desktop 数据源)

---

## 7. 阶段 1 可立即删除清单

### 7.1 整个 static/admin/ 目录(除 Provider 引用外)

```
static/admin/index.html                                          # v1 入口
static/admin/css/                                               # v1 CSS 整个
static/admin/css/components/                                    # v1 组件 CSS
static/admin/images/                                            # 资源
static/admin/themes/                                            # 主题
static/admin/icons/                                             # 图标
static/admin/js/                                                # v1 JS 整个(24 文件)
static/admin/dashboard_v2/                                      # v2 整个(20 文件)
```

**总计:** 65 个文件 + 整个 `static/admin/` 目录

### 7.2 入口代码修改(配套)

```
src/admin/api/routes.py                                         # 移除 admin_index / admin_dashboard_v2 路由
api_server.py                                                   # 移除 dashboard_v2_bp 等 Blueprint 注册
```

---

## 8. 阶段 2 需重构后删除

不能直接删除,但需要在 Desktop 数据层独立化后,从 api_server.py 移除 Blueprint 注册:

```
src/admin/dashboard/router.py                                   # dashboard_v2_bp
src/admin/dashboard/runtime_router.py
src/admin/dashboard/selfmodel_router.py
src/admin/dashboard/memory_router.py
src/admin/dashboard/growth_router.py
src/admin/dashboard/reflection_router.py
src/admin/dashboard/initiative_router.py
src/admin/dashboard/goal_router.py
src/admin/dashboard/life_graph_router.py
src/admin/dashboard/life_state_router.py
src/admin/dashboard/life_timeline_router.py
src/admin/dashboard/event_stream_router.py
src/admin/dashboard/security_router.py
```

**处理方式:** 这些 `*_router.py` 是 Flask 包装,内部逻辑是 Provider 调用。Desktop 直接 import Provider,绕过 Router 即可。

---

## 9. 永不删除清单(强约束)

### 9.1 核心 AI 模块(全部)

```
src/runtime/**
src/growth/**
src/personality/**
src/self_model/**
src/relationship/**
src/memory/**
src/emotion/**
src/agreement/**
src/identity/**
src/permission/**
src/audit/**
src/context/**
src/contracts/**
src/control/**
src/core/**
src/dream/**
src/events/**
src/goal/**
src/live2d/**
src/orchestrator/**
```

### 9.2 Provider 层(Desktop 数据源)

```
src/admin/runtime_dashboard_provider.py
src/admin/selfmodel_dashboard_provider.py
src/admin/memory_dashboard_provider.py
src/admin/growth_dashboard_provider.py
src/admin/initiative_dashboard_provider.py
src/admin/goal_dashboard_provider.py
src/admin/reflection_dashboard_provider.py
src/admin/life_state_provider.py
src/admin/life_timeline_provider.py
src/admin/life_graph_provider.py
src/admin/runtime_provider.py
src/admin/event_stream_provider.py
src/admin/self_model_provider.py
src/admin/selfmodel_consumer.py
src/admin/selfmodel_consumer_audit.py
src/admin/selfmodel_diagnostic.py
src/admin/life_graph_explanation.py
src/admin/normalized_proposal_translator.py
src/admin/audit.py
src/admin/config_manager.py
```

### 9.3 桌面软件基础设施(Phase C.10.1+ 新建)

```
yuyi_desktop/                                                   # 新建
```

### 9.4 AstrBot / QQ 接口

```
astrbot_plugins/astrbot_plugin_yuyi/                            # 保留
initiative_sender.py                                            # 保留
A.雪芽2.0/                                                      # Live2D 模型,保留
VTube Studio/                                                   # Live2D 渲染,保留
l2d_desktop/                                                    # 保留
```

### 9.5 配置

```
config.yaml                                                     # 禁止修改
config.yaml.example
config.yaml.save
```

### 9.6 测试与文档

```
tests/                                                          # 全部保留(可废弃旧测试,但 Phase C.10.0 不删)
docs/                                                           # 全部保留
README.md
CHANGELOG_YUYI.md
PHASE*.md
```

---

## 10. 总结

| 项 | 数量 | 状态 |
|---|---|---|
| 可立即删除文件 | 65 | 全部为 v1/v2 前端 |
| 需重构后删除 | 13 个 Blueprint | 改 import 方式 |
| 永不删除(核心) | src/runtime/** 等 21+ 模块 | 强约束 |
| 永不删除(Provider) | 20 个 Provider | Desktop 数据源 |
| 永不删除(测试) | tests/ 全部 | Phase C.10.0 不删 |

**审计结论:旧 Web Dashboard 与核心 AI 系统无强耦合,可安全删除,核心 AI 系统完全不受影响。**

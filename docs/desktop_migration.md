# Yuyi Desktop Migration Plan

> Phase C.10.0 — Yuyi Desktop Control Platform Migration Planning
> 旧网页控制面板 → 桌面软件控制中心
> 生成时间:2026-08-04
> 状态:**审计完成,删除未执行,等待批准**

---

## 1. 背景

### 1.1 历史遗留

项目早期制作了两个网页控制面板:

| 面板 | 位置 | 类型 | 状态 |
|---|---|---|---|
| **Dashboard v1** | `static/admin/index.html` | 老式单页(羽依 AI 控制中心) | 已过时,UI 与 Runtime 架构脱节 |
| **Dashboard v2** | `static/admin/dashboard_v2/index.html` | 重构版(羽依生命状态控制中心) | 部分模块化,但仍为 Web 形态 |

两者均依赖 Flask Blueprint + 静态资源服务,均部署在 `admin_bp` 下。

### 1.2 当前架构现状

随着 Runtime 架构升级至 C.7.3 + Initiative C.9.x,旧 Web Dashboard 出现以下问题:

- **数据陈旧**:Provider 接口在 C.1 ~ C.9 阶段多次迭代,旧 UI 字段对不上
- **权限不清**:Web 端无法安全区分"读"与"审核"操作
- **ActiveX 限制**:无法直接调用 Windows 资源(Live2D、系统托盘、快捷键)
- **跨平台受阻**:浏览器内核碎片化,Live2D 性能瓶颈
- **缺乏插件能力**:没有可扩展的 Python 插件机制

### 1.3 目标

- **废弃**两个旧 Web Dashboard
- **保留**后端 Provider 层作为 Desktop 数据源
- **新建** `yuyi_desktop/` 桌面软件(PySide6)
- **保证**核心 AI 系统完全不受影响

---

## 2. 旧系统清单

### 2.1 Dashboard v1 (废弃)

**前端(45 个文件):**
- `static/admin/index.html`
- `static/admin/css/`(12 文件)
- `static/admin/js/`(24 文件,含 yuyi-radar / yuyi-cognitive / yuyi-reasoning / yuyi-relationship-timeline / runtime_dashboard / governance_dashboard / selfmodel_dashboard / selfmodel_health / selfmodel_timeline)
- `static/admin/images/`(1 文件)
- `static/admin/themes/`(1 文件)
- `static/admin/icons/`(25+ SVG)

**后端(共享):**
- `src/admin/api/routes.py::admin_index` 路由
- `src/admin/dashboard/{governance,runtime_router,selfmodel_router}.py`

### 2.2 Dashboard v2 (废弃)

**前端(20 个文件):**
- `static/admin/dashboard_v2/index.html`
- `static/admin/dashboard_v2/pages/overview.html`
- `static/admin/dashboard_v2/css/`(2 文件)
- `static/admin/dashboard_v2/js/`(16 文件,含 api / ws / router / overview / runtime / selfmodel / memory / reflection / goal / initiative / life_graph / life_state / life_timeline / trace_panel / audit / event_stream)

**后端(共享 + v2 特有):**
- `src/admin/api/routes.py::admin_dashboard_v2` 路由
- `src/admin/dashboard/router.py`(dashboard_v2_bp)
- `src/admin/dashboard/{provider,event_hub,snapshot,trace,response,audit,governance,_security,_meta,ws}.py`
- 12 个 v2 子蓝图:`{runtime_router,selfmodel_router,memory_router,growth_router,reflection_router,initiative_router,goal_router,life_graph_router,life_state_router,life_timeline_router,event_stream_router,security_router}.py`

### 2.3 共享后端 - Provider 层(全部保留)

下列 10 个 Provider 文件**保留**,作为 Desktop 数据源:

| Provider | 职责 |
|---|---|
| `src/admin/runtime_dashboard_provider.py` | Runtime 快照 |
| `src/admin/selfmodel_dashboard_provider.py` | SelfModel 快照 |
| `src/admin/memory_dashboard_provider.py` | Memory 快照 |
| `src/admin/growth_dashboard_provider.py` | Growth 快照 |
| `src/admin/initiative_dashboard_provider.py` | Initiative 快照 |
| `src/admin/goal_dashboard_provider.py` | Goal 快照 |
| `src/admin/reflection_dashboard_provider.py` | Reflection 快照 |
| `src/admin/life_state_provider.py` | 生命状态 |
| `src/admin/life_timeline_provider.py` | 生命时间线 |
| `src/admin/life_graph_provider.py` | 生命图谱 |

---

## 3. 迁移目标

### 3.1 总体目标

| 维度 | 目标 |
|---|---|
| 形态 | PySide6 桌面软件(Windows / macOS / Linux) |
| 与 Runtime 通信 | 同进程直接 import(零 IPC) |
| 部署 | PyInstaller --onefile 单文件(30-50MB) |
| 风格 | 玻璃拟态 + 柔色 + Live2D(复用 `A.雪芽2.0/`) |
| 权限 | 只读消费核心 + 白名单写入 |
| 后续能力 | 插件机制(后续 Phase) |

### 3.2 七大功能模块

| # | 模块 | 主要数据源 |
|---|---|---|
| 1 | Dashboard 主页 | runtime_dashboard_provider |
| 2 | Runtime 监控 | RuntimeObserver + audit |
| 3 | 人格管理 | personality_resolver + evolution_engine |
| 4 | 成长管理 | growth_proposal_* |
| 5 | 主动行为中心 | initiative_decision_engine + delivery |
| 6 | 记忆浏览器 | memory_store / retriever |
| 7 | 系统设置 | ConfigManager |

---

## 4. 删除列表(必须满足全部条件才能删除)

### 删除必须满足:
1. ❌ 无业务依赖(`src/runtime/**`,`src/growth/**`,`src/personality/**`,`src/self_model/**`,`src/relationship/**`,`src/memory/**`)
2. ❌ 无测试依赖(`tests/*.py` 仅 `test_admin_*.py` 引用)
3. ❌ 无 Runtime 依赖

### 🟢 阶段 1 安全可删除(Phase C.10.0 批准后执行)

#### A. Dashboard v1 前端(45 个文件)

```
static/admin/index.html
static/admin/css/                                              (整个目录)
static/admin/css/components/                                   (整个目录)
static/admin/images/                                           (整个目录)
static/admin/themes/                                           (整个目录)
static/admin/icons/                                            (整个目录)
static/admin/js/                                               (整个目录)
```

#### B. Dashboard v2 前端(20 个文件)

```
static/admin/dashboard_v2/                                     (整个目录)
static/admin/dashboard_v2/index.html
static/admin/dashboard_v2/pages/                               (整个目录)
static/admin/dashboard_v2/css/                                 (整个目录)
static/admin/dashboard_v2/js/                                  (整个目录)
```

#### C. 整个 `static/admin/` 目录

> ⚠️ 删除前需确认:整个 `static/admin/` 目录除 v1/v2 入口外**没有任何业务代码引用**。
> 仅 `src/admin/api/routes.py` 通过 `send_from_directory(str(STATIC_DIR), ...)` 引用,删除前必须修改该文件。

### 🟡 阶段 2 需要重构后删除(Phase C.10.1+)

下列文件**不能直接删除**,但需要在 Desktop 数据层独立化后,**从 `api_server.py` 移除 Blueprint 注册**:

```
src/admin/dashboard/router.py                                  (整体蓝图)
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

> 处理方式:这些 `*_router.py` 文件本身只是 Flask 包装,内部逻辑是 Provider 调用。Desktop 直接 import Provider,绕过 Router 即可。

### 🟡 阶段 2 - 框架层(Desktop 复用,不删)

```
src/admin/dashboard/__init__.py
src/admin/dashboard/provider.py
src/admin/dashboard/event_hub.py
src/admin/dashboard/snapshot.py
src/admin/dashboard/trace.py
src/admin/dashboard/response.py
src/admin/dashboard/audit.py
src/admin/dashboard/governance.py
src/admin/dashboard/_security.py
src/admin/dashboard/_meta.py
src/admin/dashboard/ws.py
```

> 保留作为 Desktop 数据访问层基础工具。

### 🟢 阶段 1 需修改的入口(配套删除)

```
src/admin/api/routes.py                                        (修改:移除 admin_index / admin_dashboard_v2 路由)
api_server.py                                                  (修改:移除 dashboard_v2_bp 等 Blueprint 注册)
```

---

## 5. 保留列表(禁止删除)

### 5.1 核心业务模块(全部保留)

```
src/runtime/**                ← 禁止修改/删除
src/growth/**                 ← 禁止修改/删除
src/personality/**            ← 禁止修改/删除
src/self_model/**             ← 禁止修改/删除
src/relationship/**           ← 禁止修改/删除
src/memory/**                 ← 禁止修改/删除
src/emotion/**                ← 禁止修改/删除
src/agreement/**              ← 禁止修改/删除
src/identity/**               ← 禁止修改/删除
src/permission/**             ← 禁止修改/删除
src/audit/**                  ← 禁止修改/删除
src/context/**                ← 禁止修改/删除
src/contracts/**              ← 禁止修改/删除
src/control/**                ← 禁止修改/删除
src/core/**                   ← 禁止修改/删除
src/dream/**                  ← 禁止修改/删除
src/events/**                 ← 禁止修改/删除
src/goal/**                   ← 禁止修改/删除
src/live2d/                   ← 禁止修改/删除
src/orchestrator/             ← 禁止修改/删除
```

### 5.2 Provider 层(全部保留)

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

### 5.3 桌面软件基础设施(Phase C.10.1+ 新建)

```
yuyi_desktop/                                                  (新建)
  main.py
  app.py
  ui/
  services/
  core/
  config/
```

### 5.4 AstrBot / QQ 接口(全部保留)

```
astrbot_plugins/astrbot_plugin_yuyi/                           (保留)
initiative_sender.py                                           (保留)
A.雪芽2.0/                                                     (保留,Live2D 模型)
VTube Studio/                                                  (保留,Live2D 渲染)
l2d_desktop/                                                   (保留)
```

### 5.5 配置(禁止修改)

```
config.yaml                                                    (禁止修改)
config.yaml.example
config.yaml.save
```

### 5.6 测试资产(全部保留)

```
tests/                                                         (全部保留)
```

### 5.7 文档(全部保留)

```
docs/                                                          (全部保留)
README.md
CHANGELOG_YUYI.md
PHASE*.md
```

---

## 6. 迁移步骤

### 6.1 Phase C.10.0(本阶段)— 规划

- ✅ 完成旧 Dashboard 审计
- ✅ 完成 Desktop 架构设计
- ✅ 产出本文档
- ⏳ **待用户批准后进入 Phase C.10.1**

### 6.2 Phase C.10.1 — Desktop 骨架

- 创建 `yuyi_desktop/` 目录
- 实现 `QApplication` + 主窗口 + 7 Tab 占位
- 实现 Service 层骨架(只读 Provider 调用)
- 编写测试 `tests/test_yuyi_desktop_smoke.py`
- **不删除任何旧文件**

### 6.3 Phase C.10.2 ~ C.10.7 — 模块实现

每阶段实现一个模块 + 配套测试 + 文档。

### 6.4 Phase C.10.8 — 集成 + 打包

- Live2D 集成(OpenGL)
- PyInstaller 打包
- 全模块 E2E 测试
- 桌面版发布包

### 6.5 旧文件删除时机

**严格条件(全部满足):**

1. ✅ Phase C.10.7 全部模块已上线(7 个模块可用)
2. ✅ `tests/test_yuyi_desktop_*.py` 全绿
3. ✅ 旧 `test_admin_*.py` 已迁移至 `test_yuyi_desktop_*.py` 或废弃
4. ✅ `api_server.py` 中所有 dashboard Blueprint 已移除且测试通过
5. ✅ 用户最终验收通过

**删除顺序:**
1. 先删 `static/admin/dashboard_v2/`(v2 入口)
2. 再删 `static/admin/js/`(v1 JS 视图)
3. 再删 `static/admin/css/`、`static/admin/index.html` 等
4. 最后清空 `static/admin/` 整个目录

**禁止删除**:
- 任何 Provider 文件
- 任何 Dashboard 框架层文件(`provider.py` 等)
- 任何核心 AI 模块
- 任何测试

---

## 7. 风险与回滚

### 7.1 风险

| 风险 | 缓解 |
|---|---|
| 删除过早,Desktop 未完成 | 分阶段删除,Phase C.10.0 仅审计 |
| Provider 接口变更影响 Desktop | Phase C.10.x 期间冻结 Provider 接口 |
| Live2D 性能问题 | 先用静态图,Live2D 后续 Phase |
| PySide6 学习曲线 | Qt Designer + QSS,渐进实现 |

### 7.2 回滚

- 整个 Phase C.10.0 仅产出文档,**无任何代码删除**
- 即使后续 Phase 出问题,可直接 `git revert` 文档
- 代码删除在 Phase C.10.8 之后,届时已具备完整 Desktop 替代

---

## 8. 检查清单(批准前)

- [ ] 用户已阅读 `Old Dashboard Dependency Audit Report`
- [ ] 用户已阅读 `Yuyi Desktop Architecture Proposal`
- [ ] 用户已阅读本文档(`docs/desktop_migration.md`)
- [ ] 用户批准:进入 Phase C.10.1 Desktop 骨架实现
- [ ] 用户保留:删除旧文件的最终批准权(Phase C.10.8 之后)
- [ ] 核心 AI 系统测试(Phase C.9.2.5 全 101 项)未受任何影响

---

## 9. 相关文件

- `docs/desktop_architecture.md` — Desktop 架构详细设计
- `docs/audit/old_dashboard_dependency_audit.md` — 旧 Dashboard 详细审计
- `tests/test_full_system_e2e.py` — Phase C.9.2.5 全系统 E2E 测试
- `api_server.py` — Flask API 入口(Phase C.10.8 后需修改)
- `yuyi_desktop/` — Desktop 软件(Phase C.10.1+ 新建)

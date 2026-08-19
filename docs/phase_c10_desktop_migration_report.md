# Phase C.10.0 Desktop Migration Report

> 浅雾羽依 AI · 桌面控制平台迁移总报告
> 旧 Web Dashboard → Desktop 控制中心
> 生成时间:2026-08-04
> 状态:**审计完成 · 迁移规划完成 · 等待批准进入 Phase C.10.1**

---

## 0. 报告概要

| 项目 | 内容 |
|---|---|
| 阶段 | Phase C.10.0 |
| 目标 | 废弃旧网页控制面板,规划桌面软件控制中心 |
| 范围 | 仅审计与规划,**不删除任何文件**,**不修改任何核心代码** |
| 输出物 | 旧 Dashboard 审计报告 / Desktop 架构提案 / 迁移计划 / 总报告 |
| 状态 | ✅ 全部完成 |

---

## 1. 旧控制面板审计结果

### 1.1 旧控制面板定位

项目早期制作了**两个**网页控制面板,均通过 Flask Blueprint + 静态资源服务部署:

| 面板 | 入口 | 部署路径 | 形态 | 当前状态 |
|---|---|---|---|---|
| **Dashboard v1** | `static/admin/index.html` | `/admin/` | 羽依 AI 控制中心 | UI 与 Runtime 架构脱节 |
| **Dashboard v2** | `static/admin/dashboard_v2/index.html` | `/admin/dashboard_v2` | 羽依生命状态控制中心 | 部分模块化,仍为 Web 形态 |

### 1.2 文件清单(共约 60+ 文件)

#### Dashboard v1 资源(`static/admin/`)

```
static/admin/index.html
static/admin/css/             (12 个 CSS 文件)
static/admin/js/              (24 个 JS 文件)
static/admin/images/          (1 个)
```

#### Dashboard v2 资源(`static/admin/dashboard_v2/`)

```
static/admin/dashboard_v2/index.html
static/admin/dashboard_v2/css/   (5 个 CSS)
static/admin/dashboard_v2/js/    (10+ 个 JS,模块化)
```

#### 后端蓝图(2 个)

```
src/admin/api/routes.py             → admin_bp(根路径 /admin)
src/admin/dashboard/router.py       → dashboard_v2_bp(/admin/dashboard_v2)
```

### 1.3 入口文件

| 入口 | 文件 | 处理方式 |
|---|---|---|
| 旧 v1 入口 | `src/admin/api/routes.py::admin_index()` | Phase C.10.7 后移除路由 |
| 旧 v2 入口 | `src/admin/api/routes.py::admin_dashboard_v2()` | Phase C.10.7 后移除路由 |
| 蓝图注册 | `api_server.py` 中的 `register_blueprint` | Phase C.10.7 移除 |

### 1.4 核心模块引用分析

旧 Web Dashboard 的**前端 JS** 通过 HTTP 接口调用后端 **Provider**,**Provider 层**才是真正引用核心模块的层。

| 核心模块 | 旧 Web 引用 | 引用方式 | 影响 |
|---|---|---|---|
| **Runtime** | 是 | Provider → Runtime | **保留** Provider,不能删 |
| **Memory** | 是 | Provider → Memory | **保留** Provider,不能删 |
| **Personality** | 是 | Provider → Personality | **保留** Provider,不能删 |
| **Growth** | 是 | Provider → Growth | **保留** Provider,不能删 |
| **SelfModel** | 是 | Provider → SelfModel | **保留** Provider,不能删 |
| **Initiative** | 是 | Provider → Initiative | **保留** Provider,不能删 |
| **QQ** | 否 | 旧 Web 未涉及 | — |
| **AstrBot** | 否 | 旧 Web 未涉及 | — |

**关键结论**:
- **前端 JS/CSS/HTML 可以直接删除**(只服务 Web 渲染,不服务 Desktop)
- **后端 Provider 必须保留**(Desktop 将复用)
- **核心 AI 模块绝对不能动**

### 1.5 测试依赖

旧 Dashboard 配套测试位于 `tests/test_admin_*.py`,其**断言只针对 HTTP 响应字段**而非核心模块状态,这些测试可以**迁移到 Desktop** 或在旧 Web 彻底废弃后删除。

详细审计见:[old_dashboard_dependency_audit.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/audit/old_dashboard_dependency_audit.md)

---

## 2. 安全删除列表

### 2.1 删除前置条件(全部必须满足)

1. ✅ Phase C.10.7 全部 7 模块已上线
2. ✅ `tests/test_yuyi_desktop_*.py` 全绿
3. ✅ 旧 `test_admin_*.py` 已迁移或废弃
4. ✅ `api_server.py` 中所有 dashboard Blueprint 已移除
5. ✅ 用户最终验收通过

### 2.2 可立即删除(满足条件后)

#### Dashboard v1 前端(可整目录删除)

```
static/admin/index.html
static/admin/css/                  (整目录)
static/admin/js/                   (整目录)
static/admin/images/               (整目录)
```

#### Dashboard v2 前端(可整目录删除)

```
static/admin/dashboard_v2/         (整目录)
```

#### 旧蓝图与路由(可整文件删除)

```
src/admin/api/routes.py            → 删除 dashboard 相关路由
src/admin/dashboard/router.py      → 整文件删除
```

### 2.3 永不删除(保留列表)

#### 核心业务模块

```
src/runtime/**                ← 禁止
src/growth/**                 ← 禁止
src/personality/**            ← 禁止
src/self_model/**             ← 禁止
src/relationship/**           ← 禁止
src/memory/**                 ← 禁止
src/emotion/**                ← 禁止
src/agreement/**              ← 禁止
src/identity/**               ← 禁止
src/permission/**             ← 禁止
src/audit/**                  ← 禁止
src/context/**                ← 禁止
src/contracts/**              ← 禁止
src/control/**                ← 禁止
src/core/**                   ← 禁止
src/dream/**                  ← 禁止
src/events/**                 ← 禁止
src/goal/**                   ← 禁止
src/live2d/                   ← 禁止
src/orchestrator/             ← 禁止
```

#### Provider 层(Desktop 复用)

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
src/admin/dashboard/provider.py
```

#### AstrBot / QQ 接口

```
src/qq/**                     ← 禁止
src/astrbot/**                ← 禁止
```

#### 测试资产

```
tests/test_admin_*.py         ← 禁止直接删除(必须迁移)
tests/test_growth_*.py        ← 禁止
tests/test_runtime_*.py       ← 禁止
tests/test_personality_*.py   ← 禁止
tests/test_self_model_*.py    ← 禁止
tests/test_initiative_*.py    ← 禁止
```

### 2.4 删除顺序(Phase C.10.7 验收后)

1. 先删 `static/admin/dashboard_v2/`(v2 入口)
2. 再删 `static/admin/js/`、`static/admin/css/`、`static/admin/index.html` 等 v1 资源
3. 再删 `src/admin/dashboard/router.py`
4. 修改 `src/admin/api/routes.py` 删除 dashboard 路由
5. 修改 `api_server.py` 移除 Blueprint 注册
6. 最后清空 `static/admin/` 整个目录

详细清单见:[desktop_migration.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_migration.md)

---

## 3. 桌面软件技术方案

### 3.1 方案对比

| 维度 | A. PySide6 (Python) ⭐ | B. Electron (Node.js) | C. Tauri (Rust + Web) | D. .NET/WPF (C#) |
|---|---|---|---|---|
| **与 Runtime 通信** | ⭐ 直接 import | RPC/IPC | RPC/IPC | RPC/IPC |
| **本地部署** | 简单 | 需打包 node_modules | 需打包 WebView2 | 需 .NET Runtime |
| **性能** | 良好 | 一般 | 优秀 | 优秀 |
| **UI 扩展** | QSS + Qt Designer | React/Vue | Vue/React | XAML |
| **后续插件** | entry_points | npm | Rust lib | NuGet |
| **打包体积** | 30-80 MB | 100-200 MB | 10-30 MB | 50-100 MB |
| **跨平台** | Windows/macOS/Linux | 全平台 | 全平台 | 仅 Windows |
| **学习成本** | 低(同 Python) | 中 | 高 | 中 |
| **Live2D 支持** | ⭐ 原生 Python binding | Cubism Web | Cubism Web | Native SDK |

### 3.2 推荐方案:**Python + PySide6**

**理由**:

1. **同语言优势**:Desktop 与 Runtime 都是 Python,可以直接 `import`,**无 IPC 通信开销**
2. **Provider 复用**:Desktop 直接调用 `src/admin/*_provider.py`,**零迁移成本**
3. **打包体积可控**:PyInstaller 单文件 30-80 MB,优于 Electron
4. **跨平台**:未来可扩展到 macOS / Linux
5. **Live2D 复用**:已有 `src/live2d/` 模块可继续使用
6. **插件能力**:Python `entry_points` 标准机制,扩展简单

### 3.3 备选方案

如未来 Desktop 端需做大量动态 Web UI(如图表库 ECharts/D3),可考虑 Tauri 作为补充,但**主体推荐 PySide6**。

详细对比见:[desktop_architecture.md §3](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_architecture.md)

---

## 4. 软件整体架构图

### 4.1 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│                    yuyi_desktop/ (PySide6)                    │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              UI 层 (QMainWindow + 7 Tabs)              │  │
│  │  ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┐    │  │
│  │  │Dash  │Runtime│Pers.│Growth│Init. │Memory│Setting│   │  │
│  │  │board │Monitor│Mgmt │Mgmt  │Center│Brow. │      │    │  │
│  │  └──────┴──────┴──────┴──────┴──────┴──────┴──────┘    │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│                              ▼ QThread + Qt Signal            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │           Service 层 (只读 + 白名单写入)                │  │
│  │  ┌──────────┬──────────┬──────────┬──────────┐         │  │
│  │  │Runtime   │Personality│Growth   │Initiative│ ...     │  │
│  │  │Service   │Service    │Service  │Service   │         │  │
│  │  └──────────┴──────────┴──────────┴──────────┘         │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│                              ▼                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │             Core 层 (桌面端基础设施)                    │  │
│  │  EventBridge / ReadOnlyGuard / SnapshotCache / Plugin  │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│                              ▼ (直接 import)                  │
└──────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│                  现有 Runtime 系统(不变)                      │
│  src/runtime/**  src/growth/**  src/personality/**            │
│  src/self_model/**  src/initiative/**  src/memory/**         │
│  src/admin/*_provider.py  src/admin/dashboard/provider.py    │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 目录结构

```
yuyi_desktop/
├── app.py                     # 入口:启动 QApplication
├── main_window.py             # 主窗口 + 7 Tab 容器
├── ui/                        # UI 组件
│   ├── dashboard/
│   ├── runtime_monitor/
│   ├── personality/
│   ├── growth/
│   ├── initiative/
│   ├── memory/
│   └── settings/
├── services/                  # Service 层
│   ├── runtime_service.py
│   ├── personality_service.py
│   ├── growth_service.py
│   ├── initiative_service.py
│   ├── memory_service.py
│   └── settings_service.py
├── core/                      # 桌面端基础设施
│   ├── event_bridge.py        # Qt Signal ↔ Runtime EventHub
│   ├── readonly_guard.py      # 只读边界检查
│   ├── snapshot_cache.py      # 快照缓存
│   └── plugin_loader.py       # 插件机制
├── resources/                 # 图标、主题、Live2D
├── tests/                     # 单元测试
└── pyproject.toml             # 打包配置
```

### 4.3 七大模块功能

| 模块 | 主要功能 | 允许操作 |
|---|---|---|
| **1. Dashboard 主页** | Runtime 状态、人格版本、SelfModel、Memory、Growth、Initiative、健康检查 | 只读 |
| **2. Runtime 监控** | Cycle 历史、Adapter 状态、错误日志、Audit、接入 RuntimeObserver | 只读 |
| **3. 人格管理** | Personality snapshot、Trait 变化、Evolution history、版本回滚 | 查看 / 审核 / **回滚** |
| **4. 成长管理** | Growth Proposal、Review、Approval、History | 查看 / 审核 / 拒绝 / 批准 |
| **5. 主动行为中心** | Initiative 决策、触发原因、confidence、priority、发送记录、策略调整 | 查看 / 调整 |
| **6. 记忆浏览器** | Memory、重要事件、关联关系 | 只读 |
| **7. 系统设置** | QQ 连接 / AstrBot 状态 / 模型配置 / LLM 配置 / 日志级别 / 备份策略 | 配置 |

### 4.4 安全边界

Desktop 仅允许通过 **白名单 Service** 写入:

- ✅ `PersonalityService.rollback(version)`
- ✅ `GrowthService.approve(proposal_id)` / `reject(proposal_id)`
- ✅ `InitiativeService.adjust_strategy(strategy_id, params)`
- ✅ `SettingsService.update(setting_key, value)`

**禁止**:

- ❌ 直接修改 Personality 数据
- ❌ 直接修改 Growth 数据
- ❌ 直接修改 SelfModel 数据
- ❌ 直接调用 Runtime 控制接口
- ❌ 绕过 Service 直接访问核心模块

详细架构见:[desktop_architecture.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_architecture.md)

---

## 5. 开发阶段规划

### 5.1 阶段总览

| 阶段 | 名称 | 周期建议 | 范围 | 关键交付 |
|---|---|---|---|---|
| **C.10.0** | **迁移规划** ✅ | 1 步 | 仅审计/规划 | 审计报告 + 架构设计 + 迁移文档 + 本总报告 |
| C.10.1 | Desktop 骨架 | 1 步 | QApplication + 主窗口 + 7 Tab 占位 + Service 骨架 | `yuyi_desktop/` 目录 + smoke test |
| C.10.2 | Dashboard 主页 | 1 步 | Runtime 状态总览、健康检查 | 主页 Tab + 测试 |
| C.10.3 | Runtime 监控 | 1 步 | Cycle 历史、Adapter 状态、错误日志 | 监控 Tab + 接入 RuntimeObserver + 测试 |
| C.10.4 | 人格管理 | 1 步 | Snapshot、Trait 变化、回滚 | 人格 Tab + 审核工作流 + 测试 |
| C.10.5 | 成长管理 | 1 步 | Proposal、Review、Approval、History | 成长 Tab + 审核 + 测试 |
| C.10.6 | 主动行为 + 记忆 | 1 步 | Initiative 中心 + Memory 浏览器 | 主动 Tab + 记忆 Tab + 测试 |
| C.10.7 | 系统设置 | 1 步 | QQ / AstrBot / 模型 / LLM / 日志 / 备份 | 设置 Tab + 测试 |
| C.10.8 | 集成 + 打包 | 1 步 | Live2D + PyInstaller + E2E | 桌面版发布包 |

### 5.2 详细实施步骤

#### Phase C.10.0(已完成)

- ✅ 旧 Dashboard 审计
- ✅ Desktop 架构设计
- ✅ 产出 4 份核心文档

#### Phase C.10.1(下一步)

- 创建 `yuyi_desktop/` 目录
- 实现 `QApplication` + 主窗口 + 7 Tab 占位
- 实现 Service 层骨架(只读 Provider 调用)
- 编写 `tests/test_yuyi_desktop_smoke.py`
- **不删除任何旧文件**

#### Phase C.10.2 ~ C.10.7(模块实现)

每阶段实现一个模块 + 配套测试 + 文档。

#### Phase C.10.8(集成)

- Live2D 集成(OpenGL)
- PyInstaller 打包
- 全模块 E2E 测试
- 桌面版发布包

#### 旧文件删除时机(Phase C.10.7 验收后)

详细步骤见 [desktop_migration.md §6.5](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_migration.md)

### 5.3 测试策略

- **单元测试**:`tests/test_yuyi_desktop_*.py`,Service 层为主
- **集成测试**:Provider + Service 链路测试
- **UI 测试**:QTest 模拟用户操作
- **E2E 测试**:Phase C.10.8 完整工作流
- **回归测试**:每阶段确保 `tests/test_full_system_e2e.py` 仍 101/101 通过

### 5.4 风险与回滚

| 风险 | 缓解措施 | 回滚方案 |
|---|---|---|
| Desktop 与 Runtime 版本不兼容 | 锁定版本,版本号联动 | 回退 Desktop 至上一稳定版 |
| 旧 Web 误删导致依赖缺失 | 删除前全量 grep 依赖 | 完整 git revert + 重新部署 |
| Live2D 在 PySide6 中性能差 | 用 OpenGL Widget + 帧率控制 | 关闭 Live2D 降级显示 |
| Provider 不可序列化 | 在 Service 层做 DTO 转换 | 调整 Service 返回结构 |

---

## 6. 是否可以进入 Desktop 实现阶段

### 6.1 准入检查

| 检查项 | 状态 | 备注 |
|---|---|---|
| 旧 Dashboard 审计完成 | ✅ | [old_dashboard_dependency_audit.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/audit/old_dashboard_dependency_audit.md) |
| Desktop 架构设计完成 | ✅ | [desktop_architecture.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_architecture.md) |
| 迁移计划完成 | ✅ | [desktop_migration.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_migration.md) |
| 旧文件保留策略明确 | ✅ | 核心模块、Provider 层、测试资产全部保留 |
| 删除条件明确 | ✅ | 5 项前置条件 + 严格删除顺序 |
| 核心 AI 系统零影响 | ✅ | Desktop 通过 Provider 只读/白名单访问 |
| 测试资产保护 | ✅ | 旧测试迁移至 Desktop 测试,不删核心测试 |
| Runtime 依赖保护 | ✅ | Desktop 不直接修改 Runtime |
| 现有 E2E 测试保持 | ✅ | 101/101 不受影响 |
| 风险缓解措施就绪 | ✅ | 详见 §5.4 |

### 6.2 结论

**✅ Phase C.10.0 全部规划完成,可以进入 Phase C.10.1(D)esktop 骨架实现阶段。**

**关键保证**:

1. ✅ 当前阶段(本报告)只做规划,**不删除任何文件**,**不修改任何核心代码**
2. ✅ 旧 Web Dashboard 暂不删除,**新 Desktop 与旧 Web 并行运行**
3. ✅ 核心 AI 系统(`src/runtime/**`, `src/growth/**` 等)**完全不受影响**
4. ✅ 现有 101/101 E2E 测试保持通过
5. ✅ 删除旧文件需在 Phase C.10.7 验收后,严格按 5 项条件执行

### 6.3 进入 Phase C.10.1 的前置条件

**用户需要确认的事项**:

1. ⏳ 批准技术方案:**Python + PySide6**
2. ⏳ 批准架构分层:**UI 层 / Service 层 / Core 层 / 复用 Runtime**
3. ⏳ 批准安全边界:**Desktop 仅可调用白名单 Service**
4. ⏳ 批准 7 模块规划:Dashboard 主页 / Runtime 监控 / 人格管理 / 成长管理 / 主动行为中心 / 记忆浏览器 / 系统设置
5. ⏳ 批准开发阶段:C.10.1 ~ C.10.8 共 8 阶段

**用户确认后**,即可创建 `yuyi_desktop/` 目录,启动 Phase C.10.1。

---

## 7. 文档清单

| 文档 | 路径 | 用途 |
|---|---|---|
| **总报告(本文件)** | [phase_c10_desktop_migration_report.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/phase_c10_desktop_migration_report.md) | Phase C.10.0 完整总报告 |
| 旧 Dashboard 审计 | [old_dashboard_dependency_audit.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/audit/old_dashboard_dependency_audit.md) | 旧文件清单 + 删除安全分析 |
| 桌面架构设计 | [desktop_architecture.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_architecture.md) | Desktop 详细架构 |
| 迁移计划 | [desktop_migration.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/desktop_migration.md) | 删除/保留/迁移步骤 |

---

## 8. 最终声明

> **当前任务(Phase C.10.0)是安全迁移规划,不是 Desktop 开发。**
>
> **本阶段产出 4 份核心文档,包含完整的审计、架构、迁移计划与总报告。**
>
> **核心 AI 系统(Runtime C.1-C.7.3 / Growth / Initiative / SelfModel / Personality / Memory)完全不受影响。**
>
> **等待用户批准后,进入 Phase C.10.1 Desktop 骨架实现。**

---

**报告结束 · Phase C.10.0 ✅ 完成**

# Yuyi Desktop Architecture

> Phase C.10.0 — Yuyi Desktop Control Platform Architecture
> 详细架构设计文档(供 Phase C.10.1+ 实现参考)
> 状态:**设计完成,未实现**

---

## 1. 总体目标

构建一个**羽依 AI Agent 控制中心**桌面应用,作为旧 Web Dashboard 的替代品。

### 1.1 核心目标

| 目标 | 描述 |
|---|---|
| 形态 | PySide6 (Qt 6.5+) 桌面软件 |
| 跨平台 | Windows / macOS / Linux |
| 与 Runtime 通信 | **同进程直接 import**(零 IPC,零 HTTP) |
| 部署 | PyInstaller --onefile 单文件 |
| Live2D | 复用现有 `A.雪芽2.0/` 模型 |
| 后续能力 | Python 插件机制(后续 Phase) |

### 1.2 设计原则

1. **只读消费核心** — Desktop 仅通过 Provider/Router 只读快照
2. **白名单写入** — 仅在白名单内允许调用写接口
3. **Glassmorphism UI** — 玻璃拟态 + 柔色 + 圆角 + 微动画
4. **单进程单窗口** — 启动即主窗口,不依赖外部服务
5. **可测试** — pytest-qt 覆盖所有 UI 组件

---

## 2. 技术选型

### 2.1 决策矩阵

| 方案 | 与 Runtime 集成 | 本地部署 | 性能 | UI 扩展 | 插件 | 跨平台 | 体积 | 评分 |
|---|---|---|---|---|---|---|---|---|
| **A. PySide6** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ | 50MB | **推荐** |
| B. Electron | ⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ✅ | 100MB+ | 否决 |
| C. Tauri | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ✅ | 5MB | 否决 |
| D. .NET/WPF | ⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ❌ | 80MB | 否决 |

**推荐 PySide6 的核心理由:**

- 与 Runtime 同语言(都是 Python),**零 IPC、零 HTTP、零序列化**
- 所有 Provider 文件(`src/admin/*_provider.py`)**直接 import 复用**
- PySide6 LGPL 协议友好
- 跨平台一致体验
- Qt Designer + QSS 与羽依玻璃拟态风格匹配

### 2.2 内部技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| GUI 框架 | PySide6 6.5+ | LGPL,Qt 6 |
| 图表 | QtCharts + QCustomPlot | 雷达图、折线图 |
| Live2D | OpenGL 包装 | 复用 `A.雪芽2.0/` |
| 配置 | QSettings + ConfigManager 包装 | 跨平台 |
| 打包 | PyInstaller 6.x | --onefile 模式 |
| 测试 | pytest + pytest-qt | 与项目一致 |
| 类型检查 | mypy --strict | 严格类型 |
| 代码风格 | black + flake8 | PEP 8 |

---

## 3. 整体架构图

```
+================================================================+
|                Yuyi Desktop (yuyi_desktop/)                   |
+================================================================+
|                                                                |
|  +---------------- Main Window (QMainWindow) ---------------+  |
|  |                                                          |  |
|  |  +---- Top Bar (logo, health, refresh, settings) ----+  |  |
|  |  +---- Tab Bar (7 Tabs) -----------------------------+  |  |
|  |  |                                                    |  |  |
|  |  |  [Dashboard] [Runtime] [Personality] [Growth]      |  |  |
|  |  |  [Initiative] [Memory] [Settings]                  |  |  |
|  |  |                                                    |  |  |
|  |  |  +---- Active Tab Content (QStackedWidget) -----+  |  |  |
|  |  |  |                                              |  |  |  |
|  |  |  |  Tab 1: Dashboard Home                       |  |  |  |
|  |  |  |  - 7 status cards                            |  |  |  |
|  |  |  |  - recent cycles list                        |  |  |  |
|  |  |  |  - Live2D widget                             |  |  |  |
|  |  |  |                                              |  |  |  |
|  |  |  +----------------------------------------------+  |  |  |
|  |  +----------------------------------------------------+  |  |
|  +----------------------------------------------------------+  |
|                                                                |
+================================================================+

+================================================================+
|                Service Layer (yuyi_desktop/services/)          |
+================================================================+
|  +-------------+  +-------------+  +-------------+            |
|  | Runtime     |  | Personality |  | Growth      |            |
|  | Service     |  | Service     |  | Service     |            |
|  +------+------+  +------+------+  +------+------+            |
|         |                |                |                    |
|  +------v------+  +------v------+  +------v------+            |
|  | Initiative  |  | Memory      |  | Config      |            |
|  | Service     |  | Service     |  | Service     |            |
|  +------+------+  +------+------+  +------+------+            |
|         |                |                |                    |
+=========|================|================|====================+
          | (直接 import)   |                |
+=========v================v================v====================+
|        Yuyi Core (src/, in-process)                           |
+================================================================+
|  +-------------+  +-------------+  +-------------+            |
|  |  Runtime    |  |  Growth     |  |  Initiative |            |
|  |  Observ-    |  |  Proposal/  |  |  Decision/  |            |
|  |  ability    |  |  Approval/  |  |  Delivery/  |            |
|  |             |  |  History    |  |  Memory     |            |
|  +-------------+  +-------------+  +-------------+            |
|                                                                |
|  +-------------+  +-------------+  +-------------+            |
|  |  Memory     |  |  Personality|  |  SelfModel  |            |
|  |  Store      |  |  Resolver   |  |  Store      |            |
|  +-------------+  +-------------+  +-------------+            |
+================================================================+
```

---

## 4. 目录结构

```
yuyi_desktop/
├── __init__.py
├── main.py                                # 入口
├── app.py                                 # QApplication 初始化
├── ui/
│   ├── __init__.py
│   ├── main_window.py                     # QMainWindow
│   ├── tabs/
│   │   ├── __init__.py
│   │   ├── dashboard_home.py              # 模块 1
│   │   ├── runtime_monitor.py             # 模块 2
│   │   ├── personality_manager.py         # 模块 3
│   │   ├── growth_manager.py              # 模块 4
│   │   ├── initiative_center.py           # 模块 5
│   │   ├── memory_browser.py              # 模块 6
│   │   └── system_settings.py             # 模块 7
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── status_card.py                 # 通用状态卡
│   │   ├── timeline_view.py               # 时间线
│   │   ├── radar_widget.py                # 雷达图
│   │   ├── audit_table.py                 # 审计表
│   │   ├── live2d_widget.py               # Live2D
│   │   ├── glass_panel.py                 # 玻璃拟态容器
│   │   └── toast.py                       # 通知
│   └── styles/
│       ├── glass.qss
│       ├── palette.qss
│       └── components.qss
├── services/
│   ├── __init__.py
│   ├── runtime_service.py
│   ├── personality_service.py
│   ├── growth_service.py
│   ├── initiative_service.py
│   ├── memory_service.py
│   ├── config_service.py
│   └── audit_service.py
├── core/
│   ├── __init__.py
│   ├── event_bridge.py                    # Qt Signal/Slot ↔ event_hub
│   ├── readonly_guards.py                 # 只读边界
│   ├── boot.py                            # 启动流程
│   └── exception_handler.py
└── config/
    ├── desktop.yaml
    └── default_settings.py

tests/
├── yuyi_desktop/                          # 新建
│   ├── test_app_smoke.py
│   ├── test_main_window.py
│   ├── test_dashboard_home.py
│   ├── test_runtime_monitor.py
│   ├── test_personality_manager.py
│   ├── test_growth_manager.py
│   ├── test_initiative_center.py
│   ├── test_memory_browser.py
│   ├── test_system_settings.py
│   ├── test_services/
│   │   ├── test_runtime_service.py
│   │   ├── test_personality_service.py
│   │   ├── test_growth_service.py
│   │   ├── test_initiative_service.py
│   │   ├── test_memory_service.py
│   │   └── test_config_service.py
│   └── test_core/
│       ├── test_event_bridge.py
│       └── test_readonly_guards.py
```

---

## 5. 七大模块设计

### 5.1 模块 1 — Dashboard 主页

**职责:** 全局状态概览。

**UI 结构:**
```
DashboardHome (QWidget)
├── TopBar
│   ├── Logo
│   ├── HealthScore (圆形进度)
│   └── RefreshButton
├── StatusGrid (QGridLayout, 4x2)
│   ├── RuntimeCard
│   ├── MemoryCard
│   ├── EmotionCard
│   ├── PersonalityCard
│   ├── GrowthCard
│   ├── SelfModelCard
│   ├── InitiativeCard
│   └── QQCord
├── Live2DSection
│   └── Live2DWidget (A.雪芽2.0)
└── RecentCyclesList
    └── LastNList (10 items)
```

**Service 调用:**
```python
class DashboardHome(QWidget):
    def __init__(self, services: DesktopServices):
        self.runtime_card.set_data(services.runtime.get_overview())
        self.memory_card.set_data(services.memory.get_count())
        # ...
```

**权限:** 全部只读。

---

### 5.2 模块 2 — Runtime 监控

**UI 结构:**
```
RuntimeMonitor (QWidget)
├── LeftPanel
│   ├── FilterBar
│   └── CycleTimeline (timeline view)
├── CenterPanel
│   └── AdapterMatrix (5 step + initiative)
├── RightPanel
│   └── ErrorLogStream (live tail)
└── BottomPanel
    └── AuditTable (last 50)
```

**实时刷新:** QTimer(1s) 轮询 Service。

---

### 5.3 模块 3 — 人格管理

**UI 结构:**
```
PersonalityManager (QWidget)
├── LeftPanel
│   └── PersonalitySnapshot (radar + version)
├── CenterPanel
│   └── EvolutionHistory (table)
│       - version
│       - timestamp
│       - before/after diff
│       - rollback button
└── RightPanel
    └── VersionDetail (full diff)
```

**写操作白名单:**
```python
# 仅以下接口可调用
personality_evolution_engine.rollback(version_id)  # OK
personality_resolver.resolve()                      # ❌ 禁止
personality_adapter.apply_proposal()                # ❌ 禁止
trait_state_updater.apply()                         # ❌ 禁止
```

---

### 5.4 模块 4 — 成长管理

**UI 结构:**
```
GrowthManager (QWidget)
├── TopTabBar
│   ├── Pending
│   ├── Reviewing
│   ├── Approved
│   ├── Rejected
│   └── All
├── ProposalList (QListWidget)
├── DetailPanel
│   ├── EvidenceSummary
│   ├── ConfidenceScore
│   └── ActionButtons
│       ├── Approve
│       ├── Reject
│       └── Defer
```

**写操作白名单:**
```python
growth_proposal_runtime.create_proposal(...)        # OK
growth_proposal_approval.approve(proposal_id)       # OK
growth_proposal_approval.reject(proposal_id)        # OK
growth_proposal_approval.revoke(proposal_id)        # OK
# 禁止直接改 GrowthState
```

---

### 5.5 模块 5 — 主动行为中心

**UI 结构:**
```
InitiativeCenter (QWidget)
├── TopSection
│   └── TriggerStatus (5 types current values)
├── MiddleLeft
│   └── InitiativeHistory (table)
├── MiddleRight
│   └── StrategyParams (form, editable)
│       - cooldown
│       - frequency_limit
│       - priority_threshold
│       - min_relationship_level
│       - user_preference
└── BottomSection
    └── QQSendingLog (sent / failed / blocked)
```

**写操作白名单:**
```python
initiative_delivery_policy.set_param(key, value)    # OK
# 禁止:
initiative_message_delivery.deliver(...)            # ❌ 禁止主动调用
```

---

### 5.6 模块 6 — 记忆浏览器

**UI 结构:**
```
MemoryBrowser (QWidget)
├── TopBar
│   ├── SearchBox
│   └── UserFilter
├── LeftPanel
│   └── MemoryList
├── CenterPanel
│   └── MemoryDetail
└── RightPanel
    └── RelationGraph (simplified)
```

**权限:** 全部只读。

---

### 5.7 模块 7 — 系统设置

**UI 结构:**
```
SystemSettings (QWidget)
├── Tabs
│   ├── QQSettings (URL / Token / Timeout)
│   ├── AstrBotSettings (status display)
│   ├── ModelSettings (model select)
│   ├── LLMSettings (temperature / max_tokens)
│   ├── LogSettings (level)
│   └── BackupSettings (path / frequency)
└── SaveBar
    ├── SaveButton
    └── ResetButton
```

**写操作白名单:**
```python
config_manager.update_section("desktop", {...})     # OK
# 禁止:
直接读写 config.yaml                                 # ❌ 禁止
config.yaml 中的 API Key 字段(UI 隐藏)              # ❌ 禁止
```

---

## 6. Service 层设计

### 6.1 Service 通用接口

```python
class BaseService:
    """所有 Service 的基类,统一只读访问模式。"""
    def __init__(self, read_only: bool = True):
        self._read_only = read_only

    def assert_read_only(self):
        if not self._read_only:
            raise PermissionError("Service is in read-only mode")
```

### 6.2 RuntimeService

```python
class RuntimeService(BaseService):
    def get_overview(self) -> Dict[str, Any]:
        from src.admin.runtime_dashboard_provider import (
            get_runtime_dashboard_provider,
        )
        return get_runtime_dashboard_provider().get_overview_snapshot()

    def get_cycle_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        from src.runtime.observability.runtime_observer import RuntimeObserver
        return RuntimeObserver.get_recent_cycles(limit=limit)

    def get_adapter_status(self) -> Dict[str, Any]:
        from src.admin.runtime_dashboard_provider import (
            get_runtime_dashboard_provider,
        )
        return get_runtime_dashboard_provider().get_adapter_status()

    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        from src.audit.query import query_audit
        return query_audit(limit=limit)
```

### 6.3 PersonalityService

```python
class PersonalityService(BaseService):
    def get_snapshot(self) -> Dict[str, Any]:
        from src.personality.personality_resolver import PersonalityResolver
        # 间接只读:不能直接修改
        return PersonalityResolver().resolve().to_dict() if False else {}

    def get_evolution_history(self) -> List[Dict[str, Any]]:
        from src.runtime.evolution.personality_evolution_record import (
            get_evolution_history,
        )
        return get_evolution_history()

    def rollback(self, version_id: str) -> bool:
        # 白名单写入
        from src.runtime.evolution.personality_evolution_engine import (
            PersonalityEvolutionEngine,
        )
        return PersonalityEvolutionEngine().rollback(version_id)
```

---

## 7. Core 层设计

### 7.1 EventBridge

```python
class DesktopEventBridge(QObject):
    """Qt Signal ↔ Runtime EventHub 桥接。"""
    cycle_completed = Signal(dict)
    initiative_decided = Signal(dict)
    growth_proposal_created = Signal(dict)
    audit_recorded = Signal(dict)

    def __init__(self):
        super().__init__()
        from src.admin.dashboard.event_hub import get_dashboard_event_hub
        hub = get_dashboard_event_hub()
        hub.subscribe("cycle_completed", self._on_cycle)
        hub.subscribe("initiative_decided", self._on_initiative)

    def _on_cycle(self, payload):
        self.cycle_completed.emit(payload)
```

### 7.2 ReadonlyGuards

```python
class ReadOnlyGuard:
    """运行时只读边界检查。"""
    BLOCKED_ATTRS = {
        "PersonalityResolver.resolve",
        "PersonalityAdapter.apply_proposal",
        "TraitStateUpdater.apply",
        "SelfModelStore.update",
        "RelationshipState.save",
        "MemoryStore.modify",
    }

    def __init__(self):
        self._violations: List[str] = []

    def check(self, name: str) -> bool:
        if name in self.BLOCKED_ATTRS:
            self._violations.append(name)
            return False
        return True
```

---

## 8. 启动流程

```python
# yuyi_desktop/main.py
def main():
    # 1. 加载配置
    config = load_config()

    # 2. 创建 QApplication
    app = QApplication(sys.argv)
    apply_styles(app)

    # 3. 创建 Service 容器
    services = DesktopServices(
        runtime=RuntimeService(),
        personality=PersonalityService(),
        growth=GrowthService(),
        initiative=InitiativeService(),
        memory=MemoryService(),
        config=ConfigService(),
    )

    # 4. 启动 EventBridge
    bridge = DesktopEventBridge()
    bridge.cycle_completed.connect(
        main_window.dashboard_home.on_cycle_completed
    )

    # 5. 显示主窗口
    main_window = MainWindow(services=services, bridge=bridge)
    main_window.show()

    # 6. 进入事件循环
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

---

## 9. 测试策略

### 9.1 测试层级

| 层 | 工具 | 覆盖 |
|---|---|---|
| 单元 | pytest | Service 逻辑 |
| 组件 | pytest-qt | UI 组件(QWidget 信号/槽) |
| 集成 | pytest + mock | Service ↔ Provider 集成 |
| E2E | pytest-qt | 主窗口流程 |

### 9.2 测试覆盖率目标

- Service 层:≥ 90%
- UI 组件:≥ 80%
- Core 工具:≥ 95%

---

## 10. 打包与发布

### 10.1 PyInstaller 配置

```python
# yuyi_desktop.spec
a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('A.雪芽2.0', 'A.雪芽2.0'),
        ('config', 'config'),
    ],
    hiddenimports=[
        'PySide6.QtCharts',
        'src.admin.runtime_dashboard_provider',
        # ... 全部 src.admin.* provider
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'pandas',  # 未用
        'matplotlib',  # 未用
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='YuyiDesktop',
    onefile=True,
    console=False,
    icon='A.雪芽2.0/icon.png',
)
```

### 10.2 发布产物

- `YuyiDesktop.exe` — Windows 单文件(~50MB)
- `YuyiDesktop.dmg` — macOS 安装包
- `YuyiDesktop.AppImage` — Linux AppImage

---

## 11. 安全边界(强约束)

### 11.1 Desktop 写白名单

| 模块 | 允许 | 禁止 |
|---|---|---|
| Runtime | 无 | 主动触发 cycle |
| Memory | 无 | 任何 modify 操作 |
| Personality | `EvolutionEngine.rollback()` | `Resolver.resolve()` 主动调用 |
| SelfModel | (走 Evolution) | `SelfModelStore.update()` |
| Relationship | 无 | `RelationshipState.save()` |
| Growth | `create/approve/reject/revoke` proposal | 直接改 GrowthState |
| Initiative | `DeliveryPolicy.set_param()` | `MessageDelivery.deliver()` 主动调用 |
| Config | `ConfigManager.update_section()` | 直接读写 config.yaml |
| AstrBot | 仅状态查询 | 任何写入 |
| QQ | 仅状态查询 | 主动发送(由 AstrBot 负责) |

### 11.2 不参与流程

- **不接收** QQ 消息(由 AstrBot 入口)
- **不发送** QQ 消息(由 `initiative_sender.py`)
- **不修改** Runtime / Core 任何状态
- **不绕过** Policy 直接触发 Initiative
- **不调用** `PersonalityResolver.resolve()`
- **不直接写** trait / store / state

---

## 12. 实施阶段(8 阶段)

| 阶段 | 内容 | 配套测试 |
|---|---|---|
| **C.10.1** | 骨架 + 主窗口 + 7 Tab 占位 | `test_app_smoke.py` |
| **C.10.2** | Dashboard 主页 | `test_dashboard_home.py` |
| **C.10.3** | Runtime 监控 | `test_runtime_monitor.py` |
| **C.10.4** | 人格管理 | `test_personality_manager.py` |
| **C.10.5** | 成长管理 | `test_growth_manager.py` |
| **C.10.6** | 主动行为中心 | `test_initiative_center.py` |
| **C.10.7** | 记忆浏览器 + 系统设置 | `test_memory_browser.py`, `test_system_settings.py` |
| **C.10.8** | Live2D + PyInstaller + E2E | `test_yuyi_desktop_e2e.py` |

每阶段必须:
- 单元测试 + 组件测试全绿
- 旧 E2E(`test_full_system_e2e.py` 101/101)继续全绿
- 核心模块无修改

---

## 13. 总结

| 维度 | 状态 |
|---|---|
| 技术选型 | ✅ PySide6 |
| 架构 | ✅ 7 模块 + Service + Core |
| 通信 | ✅ 同进程直接 import |
| 安全 | ✅ 白名单写入 + 强只读边界 |
| 测试 | ✅ pytest-qt,8 阶段配套 |
| 打包 | ✅ PyInstaller --onefile |
| 跨平台 | ✅ Win/Mac/Linux |

**结论:方案可行,可进入 Phase C.10.1 实现阶段。**

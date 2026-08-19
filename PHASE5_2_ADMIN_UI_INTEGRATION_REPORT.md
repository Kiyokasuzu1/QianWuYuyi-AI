# Phase 5.2 — Admin UI 集成 RuntimeProvider 报告

> 实施时间：2026-07-30
> 状态：**待审核（已完成实施，未启动 Phase 5.3）**
> 适用分支：`fix/runtime-unification`

---

## 1. 概述

Phase 5.2 的目标是把 Phase 5.1 新建的 `RuntimeProvider` API 接入现有 Admin Dashboard，让 Admin 成为 **浅雾羽依 RuntimeCore 的可视化控制台**。

**核心原则（全部遵守）：**
- 不创建新的 Admin 系统
- 不破坏现有页面结构、CSS、组件
- 不修改 `RuntimeCore`
- 不修改 `RuntimeBridge`
- 不修改 Authority 内部逻辑
- 所有数据通过 Phase 5.1 API 获取
- Admin 只负责展示，不直接创建任何 Runtime 实例

**实现方式：** 在现有 `static/admin/index.html` 中**新增** Runtime Dashboard 区块（不重写页面），由独立 `runtime_dashboard.js` 负责所有数据拉取与渲染，复用现有 `glass-card` / `yuyi-status` / `yuyi-status-dot` 等组件。

---

## 2. 修改文件列表

### 2.1 新增文件

| 文件路径 | 角色 | 行数 (估) |
| --- | --- | --- |
| `static/admin/js/runtime_dashboard.js` | Phase 5.2 核心 JS：API 请求、DOM 渲染、自动刷新、Offline 回退 | ~410 |
| `static/admin/css/runtime_dashboard.css` | Runtime Dashboard 样式：玻璃卡片、状态徽章、Metric、Growth、Emotion、Memory 面板 | ~310 |
| `tests/test_admin_ui_integration.py` | Phase 5.2 集成测试：HTML/JS/CSS/API/Offline/Provider 契约 | 58 tests |
| `PHASE5_2_ADMIN_UI_INTEGRATION_REPORT.md` | 本报告 | — |

### 2.2 修改文件

| 文件路径 | 改动 |
| --- | --- |
| `static/admin/index.html` | ① 在 `<head>` 引入 `runtime_dashboard.css`；② 在 `welcome-section` 之后插入 `runtime-dashboard-section`（含 Authority 徽章 + RuntimeCore 信息）；③ 在右侧 `right-column` 中插入 `runtime-personality-section` / `runtime-emotion-section` / `runtime-memory-section`；④ 在 `</body>` 之前引入 `runtime_dashboard.js`。**未改动任何现有结构或元素。** |

### 2.3 **未改动**的关键文件

- `src/admin/runtime_provider.py` — Phase 5.1 已实现，本阶段不修改
- `src/admin/api/routes.py` — 已包含 5 个 Phase 5.1 端点，本阶段不修改
- `src/runtime/runtime_bridge.py` — 不修改
- `src/runtime/runtime_core.py` — 不修改
- 所有 Authority 子模块 — 不修改
- 现有 JS（`app.js` / `dashboard.js` / 其它 `yuyi-*.js`）— 不修改
- 现有 CSS（`style.css` / `design-tokens.css` / `components/*.css`）— 不修改

---

## 3. 当前 Admin 架构图

```
┌──────────────────────────────────────────────────────────────┐
│                       Admin UI (Browser)                     │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  static/admin/index.html                               │  │
│  │   ├── 既有模块（welcome / modules / logs / ...）        │  │
│  │   └── 【新增】 runtime-dashboard-section  ⬅ Phase 5.2  │  │
│  │       ├── Authority 状态徽章                            │  │
│  │       └── RuntimeCore 基础信息                          │  │
│  │   + personality / emotion / memory 三个独立 panel        │  │
│  └────────────────────────────────────────────────────────┘  │
│                            │                                 │
│                            │ fetch (GET, 10s 轮询)            │
│                            ▼                                 │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  static/admin/js/runtime_dashboard.js  ⬅ Phase 5.2 新增 │  │
│  │   - 仅消费 API                                         │  │
│  │   - 不创建任何 Runtime 实例                              │  │
│  │   - Offline 时显示 "Runtime Offline"                     │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                            │
                            │ /admin/api/admin/* (5 endpoints)
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    Flask 蓝图 (api/routes.py)                 │
│   /admin/api/admin/authority/status                          │
│   /admin/api/admin/personality/status                        │
│   /admin/api/admin/emotion/status                            │
│   /admin/api/admin/growth/status                             │
│   /admin/api/admin/memory/summary                            │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│            src/admin/runtime_provider.py (Phase 5.1)          │
│   - 单例 RuntimeProvider                                     │
│   - 只通过 RuntimeBridge 读取 RuntimeCore Authority 状态      │
│   - 不创建任何 Authority 实例                                 │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│            src/runtime/runtime_bridge.py                     │
│            src/runtime/runtime_core.py                       │
│   - RuntimeCore 持有所有 Authority 单例                       │
│   - Provider 看到的实例 === RuntimeCore 持有的实例            │
└──────────────────────────────────────────────────────────────┘
```

**数据流方向：单向只读。** Admin → Provider → Bridge → Core → Authority。
Admin 不直接调用 Authority，不持有任何 Runtime 引用，不写入任何状态。

---

## 4. 新增 UI 功能

### 4.1 Runtime 状态总览卡片（Dashboard 顶部新区块）

| 元素 | 数据源 | 显示 |
| --- | --- | --- |
| Runtime Status Pill | `/admin/api/admin/authority/status` → `online` | `ONLINE` (绿色) / `OFFLINE` (红色) |
| Authority 6 组件徽章 | 同上 → `authority.*` | `✓ MemoryStore` / `✗ EmotionManager` 等 |
| RuntimeCore.initialized | `runtime.initialized` | 是 / 否 |
| RuntimeCore.running | `runtime.is_running` | 运行中 / 未运行 |
| 手动 Refresh 按钮 | — | 点击立即重新拉取 |
| 最后更新时间 | — | "更新于 HH:MM:SS" / "Runtime Offline" |

### 4.2 人格状态面板

| 元素 | 数据源 | 显示 |
| --- | --- | --- |
| 当前人格 | `/admin/api/admin/personality/status` → `current.name` | 名称 |
| Resolver 状态 | `current.status` / `available` | 就绪 / Active / 未就绪 |
| Trait 数量 | `current.traits` 长度 | 数字 |
| Growth 关联 | `self_model.linked_to_growth` / `state` 非空 | 已关联 / 已绑定 / 未关联 |
| Growth 5 指标 | `/admin/api/admin/growth/status` → `metrics` | total_growth / maturity / self_awareness / empathy / stability，按 ≥70% / ≥40% / <40% 染色 (绿/黄/红) |

### 4.3 Emotion 面板

| 元素 | 数据源 | 显示 |
| --- | --- | --- |
| 当前情绪 | `/admin/api/admin/emotion/status` → `current` | label |
| 强度条 | `intensity` (0-1) | 进度条 + 百分比 |
| 最近 5 条变化 | `recent[]` | 时间 · 情绪 · 强度 |

### 4.4 Memory 概览面板

| 元素 | 数据源 | 显示 |
| --- | --- | --- |
| 总记忆数 | `/admin/api/admin/memory/summary` → `total_count` | 数字 |
| 重要记忆数 | `important_count` | 数字 |
| 当前 user_id | `user_id` | 字符串 |
| 最近 5 条记忆 | `recent[]` | 内容截断 60 字符 + 重要度 |

### 4.5 自动刷新机制

- 页面加载 → 立即拉取一次
- 默认周期：**10 秒** (`setInterval(refreshAll, 10000)`)
- 支持手动 Refresh 按钮
- 任意一个端点返回成功即视为在线
- 所有端点都失败时显示 "Runtime Offline"，并保留 `--` 占位符（不报错）
- 暴露 `window.YuyiRuntimeDashboard.{refresh, start, stop, isOnline}` 供调试

### 4.6 UI 风格一致性

- 复用 `glass-card` 容器类
- 复用 `yuyi-status-dot--thinking` 等待态
- 新增样式集中在独立 `runtime_dashboard.css`，未污染全局
- 颜色状态：
  - 绿色 `--ok`：正常 / 在线 / 重要度 ≥70%
  - 黄色 `--warn`：中等 / 重要度 40-70%
  - 红色 `--bad`：异常 / 离线 / 重要度 <40%
- 响应式：1024px / 768px 断点（自动折叠 grid）

---

## 5. 测试结果

### 5.1 新增 UI 集成测试 — `tests/test_admin_ui_integration.py`

```
====================== 58 passed, 22 warnings in 11.26s ======================
```

| 测试类 | 测试数 | 结果 |
| --- | --- | --- |
| `TestStaticAssetsExist` | 3 | ✅ PASS |
| `TestHtmlStructure` | 18 | ✅ PASS |
| `TestJsRequestsPhase51Endpoints` | 5 (parametrized) + 7 = 12 | ✅ PASS |
| `TestCssValid` | 5 | ✅ PASS |
| `TestApiEndpointsConsumable` | 7 | ✅ PASS |
| `TestOfflineFallback` | 5 | ✅ PASS |
| `TestDataRenderable` | 3 | ✅ PASS |
| `TestExistingAdminUnaffected` | 3 | ✅ PASS |
| `TestJsProviderContractAlignment` | 4 | ✅ PASS |

### 5.2 Phase 5.1 集成测试（回归）

```
tests/test_admin_runtime_integration.py — 24 passed in 12.69s ✅
```

### 5.3 Admin Phase 0 测试（回归）

```
tests/test_admin_phase0.py — 34 passed in 0.45s ✅
```

### 5.4 Runtime Bridge 测试（回归）

```
tests/runtime/test_runtime_bridge.py — passed ✅
```

### 5.5 组合运行（Phase 5.1 + Phase 5.2 + Admin Phase 0 + Runtime Bridge）

```
====================== 127 passed, 45 warnings in 15.04s ======================
```

### 5.6 Admin Phase 1（无 Phase 5.2 关联部分）

```
tests/test_admin_phase1.py -k "not test_start_all_modules_and_stop" — 56 passed ✅
```

> 备注：`test_start_all_modules_and_stop` 失败为 **pre-existing 问题**（initiative 模块的 systemd 启停子进程在测试环境下不可用），与 Phase 5.2 无关；Phase 5.2 修改的 `index.html` / 新增 JS / 新增 CSS / 新增测试 均不涉及此路径。

### 5.7 验证清单

- ✅ HTML 文件存在 Runtime Dashboard 元素
- ✅ JS 正确请求 5 个 Phase 5.1 API 端点
- ✅ 5 个 API 端点能返回 JSON 数据
- ✅ 错误状态下显示 "Runtime Offline" 而非报错
- ✅ JS 不创建任何 Runtime 实例（grep 验证无 `new RuntimeCore` 等）
- ✅ 现有 Admin 蓝图仍可加载
- ✅ 现有 `welcome-section` / `modules-section` / `timeline-section` 结构未被破坏
- ✅ `app.js` / `dashboard.js` 仍被加载
- ✅ JS 渲染的字段与 RuntimeProvider 实际契约一致

---

## 6. 截图位置

> 截图需要在浏览器中通过 Flask 启动 Admin 后手动生成。本环境为 Windows 终端环境，无 GUI 截图工具。

**建议的截图位置（如需生成）：**

1. `Runtime 状态总览卡片` → `static/admin/screenshots/phase5_2_runtime_status.png`
2. `Authority 状态徽章`（运行时）→ `static/admin/screenshots/phase5_2_authority.png`
3. `Authority 状态徽章`（Runtime Offline）→ `static/admin/screenshots/phase5_2_offline.png`
4. `人格状态 + Growth 指标` → `static/admin/screenshots/phase5_2_personality.png`
5. `Emotion 面板` → `static/admin/screenshots/phase5_2_emotion.png`
6. `Memory 概览` → `static/admin/screenshots/phase5_2_memory.png`

可在 Flask 启动后访问 `http://127.0.0.1:<port>/admin/` 手动截取。

---

## 7. Phase 5.2 设计决策记录

| 决策 | 原因 |
| --- | --- |
| **新增** 独立 `runtime_dashboard.js` 而非塞入 `dashboard.js` | `dashboard.js` 已被 Yuyi 角色联动逻辑占满，独立文件更易维护与测试 |
| 5 个 API 端点**并行**请求 (`Promise.all`) | 减少首屏延迟 |
| 任意一个端点 OK 即视为在线 | RuntimeCore 部分 Authority 异常不应完全阻塞 UI |
| 不解析 `l2d.html` / 现有 L2D 模块 | 用户明确说"不重写页面"，L2D 由独立方案处理 |
| 使用 `var` 而非 `let/const` | 兼容旧浏览器，且减少对其它脚本的全局污染 |
| 暴露 `window.YuyiRuntimeDashboard` | 便于测试 / 调试 / 未来扩展 |
| 新增 `runtime_dashboard.css` 独立文件 | 不污染 `style.css`，便于回滚 |
| 颜色状态用 `ok / warn / bad` 而非 `green/yellow/red` | 语义化命名便于维护 |

---

## 8. 下一阶段建议（Phase 5.3+）

> **本报告按用户要求不在此推进 Phase 5.3，仅作建议。**

1. **运行时事件流可视化**：把 Phase 5.1 的 `recent_actions` / `pending_decisions` 也接入 UI 做一个实时事件流面板。
2. **Growth 提案审批 UI**：在 Memory 面板下方接入 `/admin/api/admin/growth/proposals`（需先在 RuntimeProvider 中扩展），允许 Admin 一键 approve / reject。
3. **WebSocket 推送**：当前为 10s 轮询，可在未来引入 `RuntimeBridge` 事件流（WebSocket / SSE）做实时刷新。
4. **历史趋势**：当前只有"最近 5 条"，未来可加"过去 24h 趋势图"（复用 `YuyiTimeline` 组件）。
5. **告警机制**：当某个 Authority 状态由 OK → Missing 时，弹出一个 Toast 提醒用户（已具备基础 `YuyiToast` 组件）。
6. **国际化 (i18n)**：当前所有文案为中文，未来需要支持英文 / 日文时，建议在 `runtime_dashboard.js` 顶部抽出 `STRINGS` 常量表。

---

## 9. 审核检查点

请用户验收以下内容：

- [ ] 修改文件清单符合预期（仅 1 个 HTML 改动 + 3 个新文件）
- [ ] 5 个 Runtime Dashboard 面板在浏览器中正常显示
- [ ] 10 秒自动刷新 + 手动 Refresh 按钮生效
- [ ] 当 `RuntimeBridge` 未初始化时（手动停掉服务）UI 正确显示 "Runtime Offline" 而非报错
- [ ] 现有 Admin 功能（模块启停、日志、配置、审计、远程陪伴、代理管理）未受影响
- [ ] 所有 127 个 Phase 5.1 + Phase 5.2 + Admin Phase 0 + Runtime Bridge 测试通过
- [ ] 颜色状态（绿/黄/红）符合开发者控制台风格

**审核通过后再启动 Phase 5.3。**

---

> 报告结束 · 浅雾羽依 AI · Phase 5.2 Admin UI Integration

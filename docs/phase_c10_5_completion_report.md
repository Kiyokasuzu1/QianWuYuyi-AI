# Phase C.10.5 — Yuyi Control Plane Architecture 完成报告

> 完成时间:2026-08-04
> 阶段编号:Phase C.10.5
> 负责范围:Server + Desktop + 端到端

---

## 1. 概述

Phase C.10.5 建立 **Yuyi Control Plane**,让 Desktop 端在"观察羽依状态"的基础上,
第一次获得"**控制羽依运行状态**"的能力,同时严格保持 Runtime 核心不被侵入。

核心设计原则:

1. **Runtime 是羽依核心,绝不修改**。
   业务模块目录 `src/runtime/**`、`src/memory/**`、`src/growth/**`、
   `src/personality/**`、`src/self_model/**` 一律不动。
2. **Control Plane 不直接修改业务对象**。
   所有控制都经过 `ControlState` 间接生效。
3. **Desktop 端不直接 import 业务模块**。
   所有交互走 `ControlApiClient` → HTTP → `ControlManager` → `ControlState`。

数据流:

```
Desktop
  ↓ POST /api/v1/control/...
Control API (Bearer Token + Audit)
  ↓
ControlManager
  ↓
ControlState (持久化 + append-only audit)
  ↓
Runtime / Memory / Growth / ... (只读状态)
```

---

## 2. 架构

### 2.1 模块清单(本阶段新增)

| 编号 | 路径 | 角色 |
|------|------|------|
| C.10.5.1 | `src/control/state/control_state.py` | 状态层:ControlState + Persistence + Audit |
| C.10.5.2 | `src/control/registry/module_registry.py` | 模块注册中心:7 个模块元信息 + readonly/controllable 标记 |
| C.10.5.3 | `src/control/manager/control_manager.py` | 控制入口:enable / disable / toggle / safe_mode / maintenance |
| C.10.5.4 | `src/control/api/control_routes.py` | 独立 namespace `/api/v1/control/` 路由 |
| C.10.5.5 | (并入 `ControlStatePersistence` + `control_routes`) | Audit 集成 |
| C.10.5.6 | `yuyi_desktop/core/control_api_client.py` | Desktop 端 Control HTTP Client (GET + POST) |
| C.10.5.6 | `yuyi_desktop/services/control_service.py` | Desktop 端 ControlService (缓存/事件友好) |
| C.10.5.7 | `yuyi_desktop/ui/widgets/control_center_widget.py` | Desktop Control Tab 骨架 |

### 2.2 文件变更

- 新增 `src/control/state/__init__.py`
- 新增 `src/control/registry/__init__.py`
- 新增 `src/control/manager/__init__.py`
- 新增 `src/control/api/control_routes.py`
- 修改 `src/control/api/routes.py`:在 `register_gateway` 中先注册 control_bp(更具体路径优先匹配),再注册 gateway_bp(包含 wildcard 拒绝写方法)
- 修改 `src/control/api/__init__.py`:暴露 `control_bp`、`register_control_api`
- 新增 `tests/test_control_plane.py`

### 2.3 既有保护

- 既有 `src/control/control_manager.py`(Phase C.10.3 电脑控制模块)**未修改**。
  本阶段新增的 Control Manager 在 `src/control/manager/control_manager.py`,命名空间完全独立。
- 既有 `src/control/api/routes.py` 中的 8 个 GET endpoint **未修改**。
- 既有 `RemoteProviderBridge` **未修改**。它仍是"只读 GET"客户端。
- `yuyi_desktop/core/api_client.py` **未修改**。本阶段新增的 `ControlApiClient` 是独立类,只服务 Control API。

---

## 3. 控制数据流

### 3.1 Server 端控制流

```
HTTP Request
  ↓
Flask Blueprint (control_bp, prefix=/api/v1/control)
  ↓ @require_auth
Bearer Token 校验
  ↓
API Handler (control_routes.py)
  ↓
ControlManager.enable_module() / disable_module() / toggle_module()
  ↓ 校验 readonly / controllable
ControlStatePersistence.set_field()
  ↓
  1) 原子写入 control_state.json
  2) append-only audit (control_state_audit.jsonl)
  ↓
返回 envelope
```

### 3.2 Desktop 端控制流

```
UI Button (ControlCenterWidget)
  ↓
ControlService.enable_module("growth")
  ↓
ControlApiClient.enable_module("growth")
  ↓
HTTP POST /api/v1/control/module/growth/enable
  (Authorization: Bearer <token>)
  ↓
Server 端 (见上)
  ↓ Response
ControlApiClient 包装为 envelope
  ↓
ControlService 解析 + 发布 ControlUpdated 事件
  ↓
EventBus → ControlCenterWidget._on_control_updated
  ↓
_widget._refresh() 重新拉取 status
```

### 3.3 关键不变量

- Control Plane **永远不直接修改** `GrowthEngine` / `MemoryStore` / `PersonalityResolver` 等业务对象。
  它只写 `ControlState` 字段,Runtime 在读取对应字段时决定是否启用。
- 任何状态变更都会产生一条 audit。
- audit 是 append-only,绝不修改/删除历史。
- readonly 模块(runtime)无法被 `disable`。
- 非 controllable 模块无法被任何控制操作修改。

---

## 4. API 契约

Base URL:`{server}/api/v1/control`

所有响应均为标准 envelope:

```json
{
  "success": true,
  "data": { ... },
  "error": "",
  "timestamp": "2026-08-04T...",
  "schema_version": "1.0",
  "degraded": false
}
```

### 4.1 GET /api/v1/control/status

返回状态总览。

**Response.data**:
```json
{
  "state": {
    "runtime_enabled": true,
    "memory_enabled": true,
    "emotion_enabled": true,
    "growth_enabled": true,
    "initiative_enabled": true,
    "dream_enabled": true,
    "live2d_enabled": true,
    "maintenance_mode": false,
    "safe_mode": false,
    "schema_version": "1.0",
    "updated_at": "2026-08-04T...",
    "updated_by": "desktop"
  },
  "modules": [
    {
      "name": "growth",
      "display": "Growth",
      "version": "1.0",
      "description": "羽依成长引擎",
      "state_field": "growth_enabled",
      "readonly": false,
      "controllable": true,
      "category": "evolution",
      "enabled": true
    },
    ...
  ],
  "audit_count": 12,
  "recent_audit": [ {change_id, field, old_value, new_value, operator, reason, timestamp}, ... ],
  "server_status": "ok",
  "version": "0.10.5",
  "schema_version": "1.0",
  "uptime_seconds": 123.4
}
```

### 4.2 GET /api/v1/control/modules

仅返回模块列表(无 state / audit)。

### 4.3 GET /api/v1/control/audit?limit=20&field=growth_enabled

查询审计日志。倒序返回最近 limit 条。

### 4.4 POST /api/v1/control/module/{name}/enable

**Request Body (JSON, 可选)**:
```json
{
  "operator": "alice",
  "reason": "manual enable for test"
}
```

**Headers (可选)**:
- `Authorization: Bearer <token>`(强制)
- `X-Operator: alice`(替代 body 中的 operator)

**Response.data**:
```json
{
  "action": "enable",
  "module": "growth",
  "old_value": false,
  "new_value": true,
  "operator": "alice",
  "reason": "manual enable for test",
  "change": { full ControlStateChange dict },
  "schema_version": "1.0"
}
```

**错误码**:
- 401 Unauthorized(无 token / token 无效)
- 403 Forbidden(模块 readonly 或 not_controllable)
- 404 Not Found(模块不存在)
- 405 Method Not Allowed

### 4.5 POST /api/v1/control/module/{name}/disable

参数同 enable。

### 4.6 POST /api/v1/control/module/{name}/toggle

切换模块状态。返回 `old_value` / `new_value` 表示切换结果。

### 4.7 POST /api/v1/control/safe_mode/enable / disable

进入/退出安全模式。操作字段 `safe_mode`。

### 4.8 POST /api/v1/control/maintenance/enable / disable

进入/退出维护模式。操作字段 `maintenance_mode`。

### 4.9 方法约束

| 方法 | 行为 |
|------|------|
| GET | 允许 |
| POST | 允许(仅 enable / disable / toggle / safe_mode / maintenance) |
| PUT / PATCH / DELETE | 405 Not Allowed |

---

## 5. 权限模型

### 5.1 认证

- 所有写接口必须带 `Authorization: Bearer <token>`。
- `disabled` 模式(测试):任何 token 通过。
- `development` 模式:token 匹配即通过。
- `production` 模式:必须带 token 且匹配。

### 5.2 模块控制矩阵

| 模块 | readonly | controllable | enable | disable | toggle |
|------|----------|--------------|--------|---------|--------|
| runtime | ✓ | ✗ | ✗(405/403) | ✗(403) | ✗(403) |
| memory | ✗ | ✓ | ✓ | ✓ | ✓ |
| emotion | ✗ | ✓ | ✓ | ✓ | ✓ |
| growth | ✗ | ✓ | ✓ | ✓ | ✓ |
| initiative | ✗ | ✓ | ✓ | ✓ | ✓ |
| dream | ✗ | ✓ | ✓ | ✓ | ✓ |
| live2d | ✗ | ✓ | ✓ | ✓ | ✓ |

### 5.3 操作者(operator)

- 来源:Body `operator` / Header `X-Operator` / Query `?operator=`。
- 默认值:`desktop`。
- 长度上限:64 字符。

### 5.4 原因(reason)

- 来源:Body `reason` / Query `?reason=`。
- 长度上限:256 字符。
- 用于审计追踪。

---

## 6. Audit 流程

### 6.1 数据结构

每条 audit 记录:

```json
{
  "change_id": "uuid",
  "timestamp": "2026-08-04T...",
  "field": "growth_enabled",
  "old_value": true,
  "new_value": false,
  "operator": "alice",
  "reason": "test disable",
  "source": "control_plane"
}
```

字段 `field` 是 ControlState 中的字段名(如 `growth_enabled`、`safe_mode`)。
`event` 字段在 API 响应中以 `action` 表达(如 `enable` / `disable` / `enter_safe_mode`)。

### 6.2 存储

- 文件:`{data_dir}/control_state_audit.jsonl`
- 模式:append-only JSONL
- 永不删除、永不修改

### 6.3 写入时机

- `ControlStatePersistence.set_field()` 每次被调用都追加 audit。
- 即使值未变也会记录(便于追踪"试图修改但未生效")。

### 6.4 读取接口

- `GET /api/v1/control/audit?limit=20` (API 层)
- `ControlStatePersistence.list_audit(limit, field)`
- `ControlManager.get_recent_audit(limit)`

---

## 7. 持久化

- 状态文件:`{data_dir}/control_state.json`
- 写入方式:临时文件 + `os.replace`(原子替换)
- 加载方式:启动时自动加载,损坏则使用默认状态
- 目录默认:`data/control`(可通过环境变量 `YUYI_CONTROL_DIR` 覆盖)

---

## 8. Desktop 端集成

### 8.1 ControlApiClient

- 独立于 `ApiClient`,专门服务 Control API。
- 支持 GET / POST。
- 失败返回 degraded envelope,绝不抛错。
- 支持 `token_provider` 注入 Bearer Token。

### 8.2 ControlService

- 提供 `get_status()` / `get_overview()` / `list_modules()`。
- 提供 `enable_module()` / `disable_module()` / `toggle_module()`。
- 提供 `enter_safe_mode()` / `exit_safe_mode()` / `enter_maintenance()` / `exit_maintenance()`。
- 提供 `get_audit()`。
- 成功操作后发布 `ControlUpdated` 事件。

### 8.3 ControlCenterWidget

- 显示模块列表(每行:名称 + 描述 + ON/OFF + Enable/Disable 按钮)。
- 显示系统模式行(Safe Mode / Maintenance,各自独立 Enter/Exit 按钮)。
- 全局状态栏显示在线/离线、Server 版本、audit 数量。
- 每 5 秒自动刷新,事件触发也立即刷新。
- 严格遵循:locked 模块按钮不可点击。

### 8.4 事件

| 事件类型 | 含义 |
|---------|------|
| `ControlUpdated` | 任意控制操作成功完成,UI 应重新拉取 status |

---

## 9. 测试结果

### 9.1 本阶段新增

| 测试文件 | 通过 / 总数 | 状态 |
|----------|------------|------|
| `tests/test_control_plane.py` | **49 / 49** | ✓ |

测试覆盖:

- ControlState(默认 / 修改 / 持久化 / 审计 / 反序列化)
- ModuleRegistry(内置模块 / 状态查询 / 自定义注册 / readonly 标记)
- ControlManager(enable / disable / toggle / safe_mode / maintenance / audit)
- Control API(GET / POST / 401 / 403 / 404 / 405 / production 鉴权 / audit 集成)
- Desktop ControlApiClient(envelope / GET / POST / 401 / token 注入)
- Desktop ControlService(overview / 失败 fallback / 事件发布)
- Desktop ControlCenterWidget(QWidget 启动 / 模块行 / 系统模式行)
- 端到端集成(Flask + Desktop Client)
- 静态检查(Control Plane 不 import 业务核心)

### 9.2 安全测试(无回归)

| 测试文件 | 通过 / 总数 | 状态 |
|----------|------------|------|
| `tests/test_full_system_e2e.py` | **101 / 101** | ✓ |
| `tests/test_server_api_gateway.py` | **53 / 53** | ✓ |
| `tests/test_yuyi_desktop_remote.py` | **46 / 46** | ✓ |
| `tests/test_yuyi_desktop_smoke.py` | **32 / 32** | ✓ |
| `tests/test_yuyi_desktop_infrastructure.py` | **56 / 56** | ✓ |

合计:**337 / 337**(包含本阶段 49 个)

---

## 10. 安全保证

1. **业务核心零侵入**
   - AST 静态扫描已验证 `src/control/{state,registry,manager,api}` 不 import 任何
     `src.runtime.*` / `src.memory.*` / `src.growth.*` / `src.personality.*` / `src.self_model.*`。
2. **Desktop 隔离**
   - Desktop Control Center 走 HTTP,不直接 import 任何 `src.*` 业务模块。
3. **认证强制**
   - 所有写接口必须 Bearer Token 认证。
4. **审计完整**
   - 所有写操作(包括无效尝试)都产生 audit。
5. **readonly 保护**
   - `runtime` 模块 `controllable=False`,`disable` 返回 403,UI 按钮 Locked。
6. **状态可恢复**
   - 控制状态可从 `control_state.json` 重新加载;audit 是 append-only,可追溯历史。
7. **fail-safe**
   - 任何网络/解析/状态异常都返回 degraded envelope,不破坏 UI。

---

## 11. 完成后的能力

Desktop 端第一次拥有完整的"**观察 + 控制**"能力:

| 能力 | 阶段 |
|------|------|
| 观察 Runtime / Memory / Growth / Personality / SelfModel / Initiative / Audit 状态 | C.10.1 ~ C.10.4 ✓ |
| **控制模块启停** | **C.10.5 ✓** |
| **控制安全模式 / 维护模式** | **C.10.5 ✓** |
| **审计可追溯** | **C.10.5 ✓** |

下一阶段(可选):C.10.5.8+ 进一步把 `ControlState` 字段接入 Runtime 的读取路径,
让 Runtime 在 cycle 中检查对应字段,实现真正的"运行时生效"。

---

## 12. 文件清单

### 新增
- `src/control/state/__init__.py`
- `src/control/state/control_state.py`
- `src/control/registry/__init__.py`
- `src/control/registry/module_registry.py`
- `src/control/manager/__init__.py`
- `src/control/manager/control_manager.py`
- `src/control/api/control_routes.py`
- `yuyi_desktop/core/control_api_client.py`
- `yuyi_desktop/services/control_service.py`
- `yuyi_desktop/ui/widgets/control_center_widget.py`
- `tests/test_control_plane.py`

### 修改
- `src/control/api/routes.py`(`register_gateway` 内先注册 control_bp)
- `src/control/api/__init__.py`(暴露 control_bp)

### 未修改(强约束)
- `src/runtime/**`
- `src/memory/**`
- `src/growth/**`
- `src/personality/**`
- `src/self_model/**`
- 既有 `src/control/control_manager.py`(电脑控制模块,与本阶段 Manager 同名异空间)
- 既有 `src/control/api/routes.py` 的 8 个 GET endpoint
- 既有 `yuyi_desktop/core/api_client.py` / `remote_provider_bridge.py`
- 既有任何 desktop service / widget(除新增 widget)
- 任何已有测试

---

> Phase C.10.5 完。

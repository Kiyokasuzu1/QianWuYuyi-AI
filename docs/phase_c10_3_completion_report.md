# Phase C.10.3 — Yuyi Server Control API Gateway — Completion Report

## 阶段定义

**Phase C.10.3 — Yuyi Server Control API Gateway**

本阶段在服务器侧建立稳定、安全、只读优先的 API 层(`src/control/api/`),供 Yuyi Desktop 通过 HTTPS/WebSocket 远程访问,完成"服务器是唯一 AI 运行主体,Desktop 是远程控制中心"的架构隔离。

---

## 1. 背景与目标

- ✅ C.9.2.5 Full Runtime E2E 101/101 通过
- ✅ C.10.1 Desktop 骨架完成
- ✅ C.10.2 Desktop Server Communication Layer 完成
- ✅ Desktop 已禁止直接 import 核心 src 模块
- ✅ Desktop 定位为远程控制客户端

**核心原则**:
- 羽依核心永远运行在服务器。
- Desktop 只负责显示、控制、审核和管理。
- API 默认只读;所有数据通过 Provider 读取,严禁直接修改 src 业务状态。

---

## 2. 新增文件

| 路径 | 作用 |
|------|------|
| `src/control/api/__init__.py` | 包入口,导出 `gateway_bp` / `register_gateway` / `API_SCHEMA_VERSION` / 配置 / 鉴权 |
| `src/control/api/config.py` | `GatewayConfig` / `GatewayAuthConfig` + 从 `config.yaml` 加载,支持 `control_api.*` / `remote.auth_token` / `YUYI_GATEWAY_TOKEN` 环境变量兜底 |
| `src/control/api/envelope.py` | 统一响应信封 `make_envelope` / `make_success_envelope` / `make_error_envelope` / `is_envelope` |
| `src/control/api/auth.py` | Bearer Token 认证:`AuthResult` / `check_bearer_token` / `require_auth` / `_safe_compare`(常量时间比较) |
| `src/control/api/routes.py` | Blueprint `gateway_bp` + 8 个 GET 端点 + 拒绝所有写方法 + `register_gateway(app)` |
| `tests/test_server_api_gateway.py` | 53 项 Gateway 单元测试 |

---

## 3. 修改文件

| 路径 | 改动 |
|------|------|
| `yuyi_desktop/core/remote_provider_bridge.py` | 新增 8 个 v2 端点方法:`get_health` / `get_runtime_overview` / `get_runtime_status_v2` / `get_personality_status_v2` / `get_selfmodel_status_v2` / `get_memory_overview_v2` / `get_growth_status_v2` / `get_initiative_status_v2` / `get_audit_recent` |
| `yuyi_desktop/services/runtime_service.py` | `get_overview()` 合并 v2 字段(online / health / adapters / overview) |
| `yuyi_desktop/services/memory_service.py` | `get_overview()` 优先读取 `v2.total_count` / `v2.important_count` |
| `yuyi_desktop/services/personality_service.py` | `get_overview()` 优先读取 `personality_v2` / `selfmodel_v2`,提供更准确的 identity_name / version / stable |
| `yuyi_desktop/services/growth_service.py` | `get_overview()` 优先读取 `v2.proposal_count` / `v2.pending_count` 等 |
| `yuyi_desktop/services/initiative_service.py` | `get_overview()` 优先读取 `v2.interest_count` 等 |
| `tests/conftest.py` | `mock_yuyi_server` 扩展支持 C.10.3 全部 8 个新端点;`/runtime/status` 同时兼容 C.10.2/C.10.3 数据形态 |

---

## 4. API 契约(8 个只读端点)

| Method | Path | 含义 | 关键字段 |
|--------|------|------|----------|
| GET | `/api/v1/health` | 服务器总健康 | `server_status`, `runtime_status`, `version`, `schema_version`, `uptime_seconds`, `authority`, `runtime` |
| GET | `/api/v1/runtime/status` | Runtime cycle 状态 | `online`, `initialized`, `is_running`, `cycle_state`, `adapters`, `health`, `version` |
| GET | `/api/v1/runtime/overview` | Runtime 总览 | `runtime`, `personality`, `selfmodel`, `emotion`, `memory` |
| GET | `/api/v1/personality/status` | Personality 摘要 | `available`, `snapshot`, `traits`, `evolution_version`, `state` |
| GET | `/api/v1/selfmodel/status` | SelfModel 状态 | `available`, `version`, `identity_name`, `bootstrap`, `health`, `bridge_error` |
| GET | `/api/v1/memory/overview` | Memory 概览 | `available`, `total_count`, `important_count`, `user_id`, `recent[≤limit]` |
| GET | `/api/v1/growth/status` | Growth 状态 | `available`, `section`, `proposal_count`, `pending_count`, `approved_count`, `rejected_count`, `applied_count`, `evolution` |
| GET | `/api/v1/initiative/status` | Initiative 状态 | `available`, `interest_count`, `possible_action_count`, `filtered_count`, `by_trend`, `by_action_status`, `by_action_type`, `last_interest_at`, `last_action_at`, `recent`, `fallback` |
| GET | `/api/v1/audit/recent` | 最近 audit 事件 | `available`, `total`, `limit`, `items[]` |

### 4.1 写方法一律 405

```python
@gateway_bp.route("/<path:any_path>", methods=["POST", "PUT", "PATCH", "DELETE"])
def _reject_write(any_path):
    # 任何写方法一律 405 + Allow: GET
```

### 4.2 统一响应信封(envelope)

```json
{
  "success": true,
  "data": {},
  "error": "",
  "timestamp": "2026-08-04T10:00:00.000000+00:00",
  "schema_version": "1.0",
  "degraded": false
}
```

降级响应(`degraded=true`):

```json
{
  "success": false,
  "data": {},
  "error": "runtime_provider_unavailable",
  "timestamp": "...",
  "schema_version": "1.0",
  "degraded": true
}
```

---

## 5. 鉴权机制

### 5.1 三种模式

| 模式 | 行为 |
|------|------|
| `disabled` | 任何请求都允许(仅测试) |
| `development` | token 匹配即通过;无 token + 无服务端 token 也放行;空 token + 客户端带 token 放行 |
| `production` | 必须存在服务端 token 且匹配;否则 401 |

### 5.2 Token 来源(优先级)

1. `control_api.auth.token`(config.yaml)
2. `remote.auth_token`(向后兼容,自动归一为 development)
3. 环境变量 `YUYI_GATEWAY_TOKEN`
4. 缺省:空 token(配合 `required=False` 放行,生产环境应明确禁用)

### 5.3 安全要求

- API 默认只读 ✅
- 禁止修改人格 ✅(Blueprint 写方法 405)
- 禁止修改 SelfModel ✅
- 禁止修改 Memory ✅
- 禁止修改 Growth ✅
- 禁止直接调用 apply ✅(routes.py 不存在任何 `apply/commit/resolve/approve/reject/modify` 调用)
- 所有数据经由 Provider 读取 ✅(RuntimeProvider / SelfModelProvider / GovernanceProvider / InitiativeDashboardProvider / AuditStorage)

### 5.4 鉴权响应

- 通过: 200 + envelope
- 拒绝: 401 + `{"success": false, "error": "unauthorized: <reason>", ...}`

### 5.5 常量时间比较

`_safe_compare(a, b)` 防止 timing attack;`a` 或 `b` 为空时直接返回 False。

---

## 6. 隔离原则

`src/control/api/routes.py` 仅依赖以下 Provider(全部位于 `src/admin/` 或 `src/audit/` 的只读桥层):

```python
from src.admin.runtime_provider import get_runtime_provider
from src.admin.self_model_provider import get_self_model_provider
from src.admin.governance_provider import get_governance_provider
from src.admin.initiative_dashboard_provider import get_initiative_dashboard_provider
from src.audit.storage import get_audit_storage
```

**禁止**:
- ❌ `from src.runtime.*` (除 provider 内部已封装)
- ❌ `from src.memory.*`
- ❌ `from src.growth.personality.*`
- ❌ `from src.self_model.*`
- ❌ `from src.relationship.*`
- ❌ astrbot QQ 链路

测试中通过 `TestIsolation.test_routes_no_src_business_import` 静态扫描验证。

---

## 7. 异常降级(Provider 异常时)

| 场景 | Gateway 行为 |
|------|--------------|
| `get_runtime_provider()` 返回 `None` | `_err("runtime_provider_unavailable")` → `success=False, degraded=True` |
| `get_status()` 抛 `RuntimeError` | `_err("runtime_status_error: RuntimeError: ...")` |
| `get_memory_summary()` 抛错 | `_err("memory_overview_error: ...")` |
| 部分子字段失败(overview 端点) | 局部降级:失败字段为 `None`,整体仍 `success=True` |
| `audit.load()` 抛错 | `_err("audit_recent_error: ...")` |

Gateway **从不** 向 Desktop 抛 5xx 之外的不可恢复错误;所有异常一律落入 envelope。

---

## 8. Desktop 适配

### 8.1 RemoteProviderBridge 8 个新方法

| 方法 | 调用端点 | 用途 |
|------|----------|------|
| `get_health()` / `get_health_data()` | `/health` | Dashboard 总健康 |
| `get_runtime_overview()` / `..._data()` | `/runtime/overview` | Dashboard 主页总览 |
| `get_runtime_status_v2()` / `..._data()` | `/runtime/status` | 周期状态 |
| `get_personality_status_v2()` / `..._data()` | `/personality/status` | 人格 |
| `get_selfmodel_status_v2()` / `..._data()` | `/selfmodel/status` | 自我模型 |
| `get_memory_overview_v2()` / `..._data()` | `/memory/overview` | 记忆 |
| `get_growth_status_v2()` / `..._data()` | `/growth/status` | 成长 |
| `get_initiative_status_v2()` / `..._data()` | `/initiative/status` | 主动性 |
| `get_audit_recent()` / `..._data()` | `/audit/recent` | 审计 |

### 8.2 Service `get_overview()` 升级

每个 Service 的 `get_overview()` 现在:
1. 优先调用 v2 端点(更准确 / 更结构化)
2. 失败时回退到 C.10.2 legacy 端点(向后兼容)
3. 仍保留 `timeout` / `degraded` / `reconnect` 行为(由 ApiClient 保证)

### 8.3 保留 C.10.2 端点

为保持向后兼容,`ENDPOINTS` 同时保留 C.10.2 旧路径,旧 UI 仍可工作。Desktop 升级时,可逐 Tab 切换到 v2 数据源。

---

## 9. 架构图

```
┌────────────────────────────────────────────────────────┐
│ Yuyi Desktop (本地 PySide6)                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │ PySide6 UI (旧 Dashboard + 未来新 Dashboard)    │  │
│  └────────────────────┬─────────────────────────────┘  │
│                       │                                │
│  ┌────────────────────▼─────────────────────────────┐  │
│  │ Service Layer (只读)                             │  │
│  │  RuntimeService / MemoryService / ...            │  │
│  │  ↓ get_overview() 优先 v2 端点                   │  │
│  └────────────────────┬─────────────────────────────┘  │
│                       │                                │
│  ┌────────────────────▼─────────────────────────────┐  │
│  │ RemoteProviderBridge (C.10.3 新增 8 个 v2 方法)  │  │
│  │  get_health / get_runtime_status_v2 / ...       │  │
│  └────────────────────┬─────────────────────────────┘  │
│                       │                                │
│  ┌────────────────────▼─────────────────────────────┐  │
│  │ ApiClient + ConnectionManager + AuthClient       │  │
│  │  - timeout / retry / degraded / reconnect        │  │
│  │  - Bearer Token 注入                             │  │
│  └────────────────────┬─────────────────────────────┘  │
└───────────────────────┼────────────────────────────────┘
                        │ HTTPS / WebSocket
                        │ (Authorization: Bearer <token>)
                        │ (X-Schema-Version: 1.0)
┌───────────────────────▼────────────────────────────────┐
│ Yuyi Server API Gateway (src/control/api/)             │
│  - 8 个 GET 只读端点(/api/v1/*)                        │
│  - Bearer Token 认证(development / production)        │
│  - 统一 envelope 响应                                   │
│  - 写方法 405 拒绝                                      │
│  - 异常降级(不抛 5xx 之外错误)                          │
│  ↓ Provider 懒加载(避免 import-time 副作用)             │
├────────────────────────────────────────────────────────┤
│ Provider 层(只读)                                       │
│  - RuntimeProvider (RuntimeCore ↔ Admin)               │
│  - SelfModelProvider (SelfModel ↔ Admin)               │
│  - GovernanceProvider (Growth ↔ Admin)                 │
│  - InitiativeDashboardProvider (Initiative ↔ Admin)    │
│  - AuditStorage (Audit 存储只读)                       │
├────────────────────────────────────────────────────────┤
│ 业务核心(只读访问,严禁修改)                              │
│  - src/runtime/**    (C.9.2.5 稳定)                    │
│  - src/memory/**                                       │
│  - src/growth/**                                       │
│  - src/personality/**                                  │
│  - src/self_model/**                                   │
│  - src/relationship/**                                 │
│  - astrbot QQ 链路                                     │
└────────────────────────────────────────────────────────┘
```

---

## 10. 测试覆盖

### 10.1 新增测试 `test_server_api_gateway.py` — 53 项

| 测试类 | 项数 | 覆盖内容 |
|--------|------|----------|
| `TestGatewayModuleLoad` | 4 | 模块导入、默认配置、envelope 形状、auth 模式 |
| `TestGatewayStartup` | 3 | Flask app 启动 + /health 响应 + 包含 authority/uptime |
| `TestResponseSchema` | 2 | 8 端点全部返回标准 envelope;timestamp 为 ISO8601 |
| `TestReadonlyEnforcement` | 5 | POST/PUT/PATCH/DELETE 一律 405;`Allow: GET` header |
| `TestAuth` | 5 | dev_no_token / dev_valid / dev_invalid / missing_required / `_safe_compare` |
| `TestProviderRead` | 11 | 8 个端点都通过 Provider 读数据;`?limit` 参数 clamp |
| `TestExceptionDegradation` | 11 | Provider 抛错 / None 时的降级行为;overview 部分降级 |
| `TestIsolation` | 2 | routes.py 不引入业务模块;无可疑写操作 |
| `TestDesktopIntegration` | 4 | Bridge / Service 端到端调用真实 Gateway |
| `TestEndpointContract` | 2 | 9 个端点全部可路由;路径固定 |
| `TestConfigLoading` | 4 | 缺省配置 / 完整 config.yaml / 环境变量 / `remote.auth_token` 兜底 |

### 10.2 既有测试保持通过

| 测试集 | 数量 | 状态 |
|--------|------|------|
| `tests/test_server_api_gateway.py` | 53 | ✅ 全部通过 |
| `tests/test_yuyi_desktop_remote.py` | 46 | ✅ 全部通过 |
| `tests/test_yuyi_desktop_smoke.py` | 32 | ✅ 全部通过 |
| `tests/test_full_system_e2e.py` | 101 | ✅ 全部通过(C.9.2.5 稳定基线保持) |
| **合计** | **232** | ✅ 全部通过 |

---

## 11. pytest 结果

### 11.1 `test_server_api_gateway.py`

```
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/test_server_api_gateway.py -v
============================= 53 passed in 2.38s ==============================
```

### 11.2 `test_yuyi_desktop_remote.py`

```
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/test_yuyi_desktop_remote.py -v
======================== 46 passed in 67.07s (0:01:07) ========================
```

### 11.3 `test_yuyi_desktop_smoke.py`

```
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/test_yuyi_desktop_smoke.py -v
======================= 32 passed in 150.95s (0:02:30) =======================
```

### 11.4 `test_full_system_e2e.py`

```
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/test_full_system_e2e.py -v
===================== 101 passed, 1970 warnings in 1.18s ======================
```

C.9.2.5 全系统 101 测试全部保持通过 ✅

---

## 12. 完成标准验收

| 项 | 状态 | 证据 |
|----|------|------|
| 服务器成为唯一 AI 运行主体 | ✅ | `src/control/api/` 仅通过 Provider 读取,Desktop 完全不持有 Runtime 状态 |
| Desktop 成为远程控制中心 | ✅ | `yuyi_desktop/services/*` 已切换到 v2 端点;`get_overview()` 优先 v2 |
| 两端职责明确 | ✅ | Server:运行 AI + 提供只读 API;Desktop:显示 + 控制 + 审核 + 管理 |
| 不影响 C.9.2.5 稳定版本 | ✅ | `test_full_system_e2e.py 101/101` 通过 |
| API 默认只读 | ✅ | `TestReadonlyEnforcement` 5 项全过(POST/PUT/PATCH/DELETE 一律 405) |
| 禁止修改人格 / SelfModel / Memory / Growth | ✅ | 0 个写端点;`TestIsolation` 验证 |
| 禁止直接调用 apply | ✅ | `TestIsolation.test_gateway_only_uses_providers` 静态扫描 routes.py |
| 所有数据经由 Provider / snapshot | ✅ | `TestProviderRead` 11 项全过;Provider 懒加载 |
| Bearer Token + config.yaml | ✅ | `TestAuth` 5 项;`TestConfigLoading` 4 项 |
| development mode 支持 | ✅ | `TestAuth.test_dev_mode_*` 3 项全过 |
| Desktop 保留 timeout / degraded / reconnect | ✅ | `TestFailSafe` 3 项 + `TestNoCoreModulePollution` 4 项全过 |
| 新增 `tests/test_server_api_gateway.py` | ✅ | 53 项,覆盖 API 启动 / health / schema / readonly / auth / provider / 降级 |

---

## 13. 下一阶段建议

**Phase C.10.4 — 候选方向(待用户决策):**

1. **生产模式认证** —— 将 `mode=production` 落地:`required=True` + 强制从 secrets manager 读取 token + 审计 401 事件。
2. **WebSocket 长连接** —— 在 ApiClient 之上叠加 `core/ws_client.py`,用于 Runtime cycle 实时推送,替代部分 GET 轮询。
3. **断线缓存层** —— Service 内部为 snapshot 加短期缓存(60s),offline 时继续显示上一次成功的数据。
4. **Desktop schema 校验** —— 根据 `X-Schema-Version` header 拒绝不兼容响应,避免字段漂移。
5. **指标埋点** —— Desktop 上报 latency / degraded 次数 / endpoint 调用分布到 Server `/api/v1/desktop/metrics`。
6. **新 Dashboard 替换** —— 逐步将 7 个 Tab 切到 `RemoteProviderBridge.get_*_v2()`,提供真正的只读 Viewer。
7. **Audit 端点扩展** —— 增加 `?event_type` / `?since` 过滤,支持 Dashboard 审计 Tab 翻页。

> 建议优先做 #3(断线缓存)与 #1(生产模式认证),前者提升离线体验,后者补齐生产部署的安全闭环。

---

**本阶段边界确认:**
- ✅ 仅建立服务器侧 API Gateway
- ✅ 未修改 src/runtime/** / src/memory/** / src/growth/** / src/personality/** / src/self_model/** / src/relationship/**
- ✅ 未触动 astrbot QQ 链路
- ✅ C.9.2.5 Full Runtime E2E 101/101 保持通过
- ✅ C.10.1 / C.10.2 既有测试全部通过
- ✅ Desktop 不再持有任何业务核心状态(完全只读客户端)

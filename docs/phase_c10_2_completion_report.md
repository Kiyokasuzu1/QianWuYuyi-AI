# Phase C.10.2 — Yuyi Desktop Server Communication Layer — Completion Report

## 阶段定义

**Phase C.10.2 — Yuyi Desktop Server Communication Layer**

本阶段完成 Desktop 与 Server 之间的通信层骨架,仅建立通信,不动 UI、不接 Live2D、不删旧 Dashboard、不改 Server Runtime。

---

## 1. 新增文件

| 路径 | 作用 |
|------|------|
| `yuyi_desktop/core/api_client.py` | 核心 HTTP 客户端,处理连接 / 超时 / 重试 / envelope 包装 / FailSafe |
| `yuyi_desktop/core/remote_provider_bridge.py` | 远程 Provider 桥接,提供 6 个域的只读 snapshot 接口 |
| `yuyi_desktop/core/connection_manager.py` | 连接状态 / 心跳 / latency / 重连管理 |
| `yuyi_desktop/core/auth_client.py` | 本地 token 管理与注入 |
| `tests/test_yuyi_desktop_remote.py` | 通信层 46 项测试 |
| `tests/conftest.py` | `mock_yuyi_server` fixture(in-process Flask) |

---

## 2. 修改文件

| 路径 | 改动 |
|------|------|
| `yuyi_desktop/core/desktop_context.py` | 整合 RemoteProviderBridge / ConnectionManager / AuthClient / ApiClient;新增 `full_health_snapshot` |
| `yuyi_desktop/services/runtime_service.py` | 改为通过 RemoteProviderBridge 访问 runtime snapshot,移除 src.* 引用 |
| `yuyi_desktop/services/memory_service.py` | 改为通过 RemoteProviderBridge 访问 memory snapshot,移除 src.* 引用 |
| `yuyi_desktop/services/personality_service.py` | 改为通过 RemoteProviderBridge 访问 personality snapshot,移除 src.* 引用 |
| `yuyi_desktop/services/growth_service.py` | 改为通过 RemoteProviderBridge 访问 growth snapshot,移除 src.* 引用 |
| `yuyi_desktop/services/initiative_service.py` | 改为通过 RemoteProviderBridge 访问 initiative snapshot,移除 src.* 引用 |

> 旧 `provider_bridge.py` 保留(C.10.1 阶段产物),不被本阶段调用,符合 "不要删除旧 Dashboard" 要求。

---

## 3. Desktop 通信架构

```
┌────────────────────────────────────────────────┐
│ Yuyi Desktop (本地 PySide6)                    │
│  ┌──────────────────────────────────────────┐  │
│  │ PySide6 UI (旧 Dashboard,不动)           │  │
│  └──────────────────┬───────────────────────┘  │
│                     │                          │
│  ┌──────────────────▼───────────────────────┐  │
│  │ Service Layer (只读)                     │  │
│  │  RuntimeService / MemoryService / ...    │  │
│  └──────────────────┬───────────────────────┘  │
│                     │                          │
│  ┌──────────────────▼───────────────────────┐  │
│  │ RemoteProviderBridge                     │  │
│  │   get_*_snapshot()  (6 个域)             │  │
│  └──────────────────┬───────────────────────┘  │
│                     │                          │
│  ┌──────────────────▼───────────────────────┐  │
│  │ ApiClient (HTTPS) + ConnectionManager    │  │
│  │ + AuthClient (Bearer Token)              │  │
│  └──────────────────┬───────────────────────┘  │
└─────────────────────┼──────────────────────────┘
                      │ HTTPS / WebSocket
                      │ (Bearer Token + X-Schema-Version)
┌─────────────────────▼──────────────────────────┐
│ Yuyi Server API (远程,不在本仓库)             │
│  GET /api/v1/runtime/status                   │
│  GET /api/v1/memory/overview                  │
│  GET /api/v1/personality/status               │
│  GET /api/v1/selfmodel/status                 │
│  GET /api/v1/growth/status                    │
│  GET /api/v1/initiative/status                │
└─────────────────────┬──────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────┐
│ Runtime / Provider / Memory / Emotion / ...    │
│ (src/* 业务核心,本阶段未修改)                  │
└────────────────────────────────────────────────┘
```

关键点:
- Desktop 不直接 import `src/*`。
- 所有跨进程访问统一通过 ApiClient 走 HTTPS。
- 旧 UI 完全保留;新通信层与旧 UI 解耦。
- ConnectionManager + AuthClient 独立可注入,便于测试。

---

## 4. API 契约 (Desktop 侧定义)

> 当前以 Mock Server 实现;实际 Server 实现由后端团队负责。

| Method | Path | 返回 schema 概要 |
|--------|------|------------------|
| GET | `/api/v1/health/ping` | `{success, data:{pong, server_version}, ...}` |
| GET | `/api/v1/server/info` | `{success, data:{name, version, uptime_s, schema_version}, ...}` |
| GET | `/api/v1/runtime/status` | `{success, data:{state, last_cycle_at, cycle_count, ...}}` |
| GET | `/api/v1/runtime/overview` | `{success, data:{cycles, healthy, ...}}` |
| GET | `/api/v1/memory/overview` | `{success, data:{total, by_layer, health}}` |
| GET | `/api/v1/memory/summary` | `{success, data:{recent, summary}}` |
| GET | `/api/v1/personality/status` | `{success, data:{traits, version}}` |
| GET | `/api/v1/personality/snapshot` | `{success, data:{snapshot}}` |
| GET | `/api/v1/selfmodel/status` | `{success, data:{identity, version}}` |
| GET | `/api/v1/selfmodel/identity` | `{success, data:{name, role, ...}}` |
| GET | `/api/v1/growth/status` | `{success, data:{pending, recent, history}}` |
| GET | `/api/v1/growth/summary` | `{success, data:{summary}}` |
| GET | `/api/v1/initiative/status` | `{success, data:{enabled, last_decision, ...}}` |
| GET | `/api/v1/initiative/summary` | `{success, data:{summary}}` |

统一 envelope:

```json
{
  "success": true,
  "data": {},
  "error": "",
  "timestamp": "2026-08-04T10:00:00Z",
  "schema_version": "1.0",
  "degraded": false,
  "latency_ms": 50.0
}
```

FailSafe envelope (degraded):

```json
{
  "success": false,
  "data": {},
  "error": "timeout: ConnectTimeout",
  "timestamp": "2026-08-04T10:00:00Z",
  "schema_version": "1.0",
  "degraded": true,
  "latency_ms": 300.0
}
```

---

## 5. 数据流图

### 5.1 正常流程 (online)

```
UI Tab
  └─► Service.get_overview()
        └─► RemoteProviderBridge.get_*_snapshot()
              └─► ApiClient.get(path, timeout, retry)
                    └─► HTTPS ─► Yuyi Server
                                        │
                ┌───────────────────────┘
                ▼
          Server 处理
                │
                ▼
        HTTPS Response JSON
                │
                ▼
ApiClient 解析 → _wrap_success() → envelope
                │
                ▼
RemoteProviderBridge 返回 typed dict
                │
                ▼
Service 包装为本地 DTO
                │
                ▼
UI 渲染
```

### 5.2 异常流程 (offline / FailSafe)

```
Service.get_overview()
  └─► RemoteProviderBridge.get_*_snapshot()
        └─► ApiClient.get(...)
              ├─ connection_error
              ├─ timeout
              ├─ http_5xx
              └─ ssl_error
                    │
                    ▼
        _wrap_error() → degraded envelope
        {success:false, degraded:true, error:"..."}
                    │
                    ▼
Service 仍返回本地 fallback dict(不抛错)
                    │
                    ▼
UI 显示 degraded 状态,Desktop 不崩溃
```

### 5.3 心跳 / 重连

```
ConnectionManager (后台线程)
  ├─ heartbeat(每 N 秒):
  │    └─► ApiClient.get("/api/v1/health/ping")
  │         └─► 更新 status{connected, latency_ms, server_version, last_check}
  ├─ reconnect on disconnect
  └─ get_status() → 任意时刻可读
```

---

## 6. 安全边界验证

| 验证项 | 状态 | 证据 |
|--------|------|------|
| Desktop 不 import `src.runtime` | 通过 | `TestNoCoreModulePollution.test_services_no_src_import` |
| Desktop 不 import `src.memory` | 通过 | 同上 |
| Desktop 不 import `src.personality` | 通过 | 同上 |
| Desktop 不 import `src.growth` | 通过 | 同上 |
| Desktop 不 import `src.self_model` | 通过 | 同上 |
| Desktop 不 import `src.relationship` | 通过 | 同上 |
| ApiClient 不 import `src.*` | 通过 | `TestNoCoreModulePollution.test_apiclient_no_src_import` |
| Bridge 不 import `src.*` | 通过 | `TestNoCoreModulePollution.test_bridge_no_src_import` |
| Bridge 不暴露 POST/PUT/PATCH/DELETE | 通过 | `TestEndpointContract.test_no_write_endpoints` |
| Bridge 内部不调用 PersonalityResolver / Adapter | 通过 | `TestRemoteProviderBridge.test_security_self_check` |
| Service 不暴露写方法 | 通过 | `TestServices.test_service_no_write_methods` |
| 导入 Desktop 不触发 Runtime 初始化 | 通过 | `test_desktop_runtime_not_initialized_by_import` |

被禁止的写操作均通过 Service 与 Bridge 静态检查 / 反射检查双重保护:

- POST 修改人格 → 无 endpoint
- apply proposal → 无 endpoint
- update selfmodel → 无 endpoint
- modify memory → 无 endpoint
- change growth state → 无 endpoint

---

## 7. 测试数量

| 测试集 | 数量 | 状态 |
|--------|------|------|
| `tests/test_yuyi_desktop_remote.py` | 46 | ✅ 全部通过 |
| `tests/test_yuyi_desktop_smoke.py` | 32 | ✅ 全部通过 |
| `tests/test_full_system_e2e.py` | 101 | ✅ 全部通过 |
| **合计** | **179** | ✅ 全部通过 |

`test_yuyi_desktop_remote.py` 覆盖项(对应任务清单 10 项):

1. ✅ API Client 创建
2. ✅ Server 连接成功(ping / server_info / runtime status)
3. ✅ Server 离线(connection refused)
4. ✅ Timeout 处理
5. ✅ Retry 机制(失败重试 / max_retries=0 不重试)
6. ✅ RemoteProviderBridge 读取(6 个域 + health_check)
7. ✅ Service 调用远程 API
8. ✅ 返回数据 schema 验证(envelope 字段 / schema_version 常量)
9. ✅ FailSafe(offline / 5xx / 500 错误不抛错)
10. ✅ 无核心模块污染(no_src_import / 导入不触发 Runtime)

---

## 8. pytest 结果

### 8.1 `test_yuyi_desktop_remote.py`

```
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/test_yuyi_desktop_remote.py -v
============================= 46 passed in 52.77s =============================
```

### 8.2 `test_yuyi_desktop_smoke.py`

```
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/test_yuyi_desktop_smoke.py -v
======================== 32 passed in 98.41s (0:01:38) ========================
```

### 8.3 `test_full_system_e2e.py`

```
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests/test_full_system_e2e.py -v
===================== 101 passed, 2105 warnings in 2.12s =====================
```

C.9.2.5 全系统 101 测试全部保持通过 ✅

---

## 9. 下一阶段建议

**Phase C.10.3 — 候选方向(待用户决策):**

1. **真 Server 端实现** —— 由后端团队按本报告 §4 的 API 契约落地真实 Server,移除对 Mock Server 的依赖。
2. **WebSocket 长连接** —— 在 ApiClient 之上叠加 `core/ws_client.py`,用于 Runtime cycle 实时推送。
3. **断线缓存层** —— Service 内部为 snapshot 加短期缓存(60s),offline 时继续显示上一次成功的数据。
4. **Server 端 schema 校验** —— Desktop 端根据 `X-Schema-Version` header 拒绝不兼容响应。
5. **Desktop 端指标埋点** —— 上报 latency、degraded 次数、endpoint 调用分布到 Server `/api/v1/desktop/metrics`。
6. **新 Dashboard(替换旧)** —— 在通信层稳定后,逐步将旧 UI 切到 RemoteProviderBridge,提供真正的只读 Viewer。
7. **Live2D 集成** —— 与 Server emotion/personality 数据绑定,作为可选模块加载。

> 建议优先做 #3(断线缓存),离线体验会更稳;#2 视真实 Server 部署位置决定。

---

**本阶段边界确认:**
- ✅ 仅建立通信层
- ✅ 未制作 Dashboard UI
- ✅ 未接入 Live2D
- ✅ 未删除旧 Dashboard
- ✅ 未修改服务器 Runtime

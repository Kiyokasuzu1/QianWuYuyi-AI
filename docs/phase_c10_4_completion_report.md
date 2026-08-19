# Phase C.10.4 — Yuyi Desktop Data Infrastructure Stabilization — Completion Report

## 阶段定义

**Phase C.10.4 — Yuyi Desktop Data Infrastructure Stabilization**

本阶段建立稳定、可观察、可降级的 Desktop 数据基础设施,实现:
- 离线仍能展示最后状态
- 网络异常可恢复
- API 协议保护
- 可扩展事件系统
- UI 不再主动轮询

---

## 1. 背景与目标

- ✅ C.9.2.5 Full Runtime E2E 101/101 通过
- ✅ C.10.1 Desktop 骨架完成
- ✅ C.10.2 Desktop Remote Communication Layer 完成
- ✅ C.10.3 Server Control API Gateway 完成
- ✅ Desktop 已禁止直接 import 核心 src 模块

**核心原则**:
- 羽依核心永远运行在服务器。
- Desktop 只负责显示、控制、审核、配置。
- 任何 src 业务模块严禁被 Desktop 直接 import。
- 任何 API 写方法严禁在 Desktop 调用。

---

## 2. 新增文件

| 路径 | 作用 |
|------|------|
| `yuyi_desktop/core/cache/__init__.py` | 缓存包入口 |
| `yuyi_desktop/core/cache/remote_snapshot_cache.py` | `RemoteSnapshotCache`:TTL=60s,仅缓存成功响应,深拷贝隔离,线程安全 |
| `yuyi_desktop/core/errors.py` | `DesktopError` 统一错误分类(Network/Server/Auth/Schema/Timeout/Offline) |
| `yuyi_desktop/core/schema_validator.py` | `SchemaValidator` + `SchemaError`:校验 envelope 的 `schema_version` |
| `yuyi_desktop/core/api_client_typed.py` | `TypedApiClient` + `TypedResponse`:在原 ApiClient 之上提供类型化结果与错误分类 |
| `yuyi_desktop/core/service_base.py` | `CachedServiceBase`:Service 公共基类(cache + schema + event) |
| `yuyi_desktop/core/events/__init__.py` | 事件包入口 |
| `yuyi_desktop/core/events/event_bus.py` | `EventBus` + `DesktopEvent` + `EventTypes`:线程安全事件总线 |
| `tests/test_yuyi_desktop_infrastructure.py` | 56 项 Phase C.10.4 单元/集成测试 |

---

## 3. 修改文件

| 路径 | 改动 |
|------|------|
| `yuyi_desktop/core/connection_manager.py` | 扩展:导出 `ConnectionState` 数据契约 + `get_state()` 方法(online / latency_ms / last_success / retry_count / degraded) |
| `yuyi_desktop/core/schema_validator.py` | 统一从 `errors.py` 导入 `SchemaError`(DesktopError 子类) |
| `yuyi_desktop/services/runtime_service.py` | 新增 `refresh()` / `get_snapshot_with_cache()` / `get_cached_snapshot()` / `get_snapshot_status()`,集成 cache+schema+event |
| `yuyi_desktop/services/memory_service.py` | 同上 |
| `yuyi_desktop/services/personality_service.py` | 同上 |
| `yuyi_desktop/services/growth_service.py` | 同上 |
| `yuyi_desktop/services/initiative_service.py` | 同上 |

旧 API(`get_overview()` / `get_summary_envelope()` 等)完全保留,46 + 32 个老测试 0 改动全通过。

---

## 4. 架构变化

### 4.1 数据流(更新后)

```
            Server
              ↓
        src/control/api (Gateway)
              ↓ HTTPS
        ApiClient (envelope-based)
              ↓
        TypedApiClient (类型化 + 错误分类)
              ↓
        RemoteProviderBridge
              ↓
        Service
       ↙   ↓    ↘
   Cache  Schema  EventBus
              ↓
   UI  (订阅事件,显示 offline=true/last_update_time)
```

### 4.2 Service 数据流(关键)

```
Service.refresh() / get_snapshot_with_cache()
        ↓
RemoteProviderBridge.get_xxx_snapshot()
        ↓
SchemaValidator.validate(envelope)        ← 阻断不兼容
        ↓ success
Cache.put_envelope(key, envelope)         ← 写入缓存
        ↓
EventBus.publish(RuntimeUpdated / MemoryUpdated / ...)
        ↓
UI (subscribe)                            ← 不再轮询
```

Server 不可达:
```
Service.refresh()
        ↓
RemoteProviderBridge.get_xxx_snapshot() → error
        ↓
EventBus.publish(ConnectionChanged)
        ↓
Service.get_snapshot_with_cache() → source="cache", online=False, last_update_time=<ts>
        ↓
UI 显示:offline=true, last_update_time=2026-08-04T10:00:00Z
```

### 4.3 错误分类体系

```
DesktopError (基类,error_code 必填)
├── NetworkError          — 网络层(连接失败 / SSL)
│   ├── TimeoutError      — 超时
│   ├── ConnectionError_  — 连接错误
│   └── SSLError          — SSL / TLS
├── ServerError           — 5xx / envelope.success=False
├── AuthError             — 401 / 403
├── SchemaError           — schema_version 不兼容(reason 字段)
└── OfflineError          — 无可用数据且无缓存
```

错误码常量:
- `ERROR_NETWORK = "network_error"`
- `ERROR_TIMEOUT = "timeout"`
- `ERROR_CONNECTION = "connection_error"`
- `ERROR_SSL = "ssl_error"`
- `ERROR_SERVER = "server_error"`
- `ERROR_AUTH = "auth_error"`
- `ERROR_SCHEMA = "schema_mismatch"`
- `ERROR_UNKNOWN = "unknown_error"`
- `ERROR_OFFLINE = "offline"`

### 4.4 事件系统

标准事件类型(常量 `EventTypes`):

| 事件 | 触发时机 | 订阅方 |
|------|----------|--------|
| `RuntimeUpdated` | Runtime snapshot 写入缓存后 | UI runtime tab |
| `MemoryUpdated` | Memory snapshot 写入缓存后 | UI memory tab |
| `PersonalityUpdated` | Personality snapshot 写入缓存后 | UI personality tab |
| `GrowthUpdated` | Growth snapshot 写入缓存后 | UI growth tab |
| `InitiativeUpdated` | Initiative snapshot 写入缓存后 | UI initiative tab |
| `ConnectionChanged` | 远端请求失败 / 连接变化 | UI status bar |
| `SchemaChanged` | Schema 校验失败 | UI warning banner |
| `DataInvalidated` | 缓存被显式失效 | UI 强制刷新 |

特性:
- 不依赖 PySide6 / Qt(保持 core 层纯净)
- handler 异常被隔离,不影响其他订阅者
- 支持 `once` 一次性订阅
- 支持 `*` 通配订阅
- 保留最近 N 条事件历史(默认 200)

### 4.5 Schema 校验

- 默认期望 schema_version = `1.0`
- 缺失 / 格式不合法 / 不匹配均返回 `ok=False`
- `assert_compatible()` 失败时抛 `SchemaError(DesktopError)`
- Service 层校验失败:写入 `ConnectionChanged` 事件,返回 `schema_mismatch` 错误 envelope
- 缓存不被污染(失败响应不写入)

---

## 5. 数据流(组件契约)

### 5.1 RemoteSnapshotCache

| 方法 | 行为 |
|------|------|
| `put(key, data, latency_ms, success)` | 仅当 success=True 且 data 为 dict 时写入;深拷贝;拒绝 success=False |
| `put_envelope(key, envelope)` | 从标准 envelope 写入(envelope.success=True 时) |
| `get(key, allow_stale=True)` | 读取,默认允许过期(标记 is_stale) |
| `get_data(key, allow_stale=True)` | 便捷:仅返回 data |
| `get_status(key)` | 返回 `{has_data, timestamp, age_seconds, is_stale, offline, last_update_time, ttl_seconds, success}` |
| `invalidate(key)` / `clear()` | 失效 / 清空 |
| `stats()` | 命中率统计 |

默认 TTL = 60 秒(可通过构造函数覆盖)。

### 5.2 ConnectionManager.ConnectionState

```python
{
    "online":         bool,    # Server 是否可达
    "latency_ms":     float,   # 最近一次 ping 延迟; -1 表示未知
    "last_success":   str,     # ISO8601 最近成功时间
    "retry_count":    int,     # 连续失败次数
    "degraded":       bool,    # 降级(高延迟 / 连续失败 >= 阈值)
}
```

### 5.3 TypedApiClient

| 方法 | 行为 |
|------|------|
| `get(path)` | 返回 `TypedResponse`,含 `error_code` / `error_class` / `error_message` / `http_status` |
| `raise_for_error()` | 失败时 raise 对应 `DesktopError` 子类 |
| `is_network_error` / `is_auth_error` / `is_server_error` / `is_schema_error` | 便捷属性 |

---

## 6. 测试结果

| 测试文件 | 数量 | 结果 |
|----------|------|------|
| `tests/test_full_system_e2e.py` | 101 | ✅ 全部通过(0 改动) |
| `tests/test_server_api_gateway.py` | 53 | ✅ 全部通过(0 改动) |
| `tests/test_yuyi_desktop_remote.py` | 46 | ✅ 全部通过(0 改动) |
| `tests/test_yuyi_desktop_smoke.py` | 32 | ✅ 全部通过(0 改动) |
| `tests/test_yuyi_desktop_infrastructure.py`(本阶段新增) | 56 | ✅ 全部通过 |
| **合计** | **288** | **✅ 全部通过** |

### 6.1 新增 56 项测试覆盖

**RemoteSnapshotCache (12 项)**:
- put / get 基本行为
- 拒绝 success=False
- 拒绝非 dict data
- 拒绝空 key
- `put_envelope` 自动判断 success
- TTL 过期(allow_stale / not)
- TTL 状态报告
- 深拷贝隔离(外部修改不影响缓存)
- offline fallback
- invalidate / clear
- 多线程并发读写
- 命中率统计

**ConnectionManager (5 项)**:
- `ConnectionState` 数据契约
- offline 行为(连续失败 → degraded)
- online 行为(成功 → retry_count=0)
- reconnect 主动触发
- state dict 序列化

**SchemaValidator (9 项)**:
- version match
- version mismatch
- missing schema_version
- malformed schema_version
- envelope 非 dict
- `assert_compatible` 抛 `SchemaError`
- `assert_compatible` 通过
- 自定义 expected_versions
- 非法 expected_versions 抛 ValueError

**TypedApiClient (11 项)**:
- error_code 分类
- TypedResponse success
- TypedResponse offline(timeout / connection error)
- TypedResponse schema_mismatch
- `raise_for_error` 抛 `NetworkError`
- `raise_for_error` 抛 `AuthError`
- `raise_for_error` 抛 `SchemaError`
- `raise_for_error` 抛 `ServerError`
- `raise_for_error` 成功路径不抛
- 底层 ApiClient retry 行为保留
- DesktopError 分类层级

**EventBus (9 项)**:
- emit / subscribe
- 多个 handler 并发接收
- unsubscribe
- subscribe_once
- handler 异常隔离
- event history
- 通配订阅(`*`)
- subscriber_summary
- DesktopEvent.to_dict

**Service Integration (4 项)**:
- Service 拉取成功后发布事件
- Service `get_snapshot_with_cache` 返回 live 视图
- offline 场景下 Service 返回 cache 视图(`source="cache"`)
- Service schema_mismatch 时发布 SchemaChanged 事件

**Safety Boundaries (1 项)**:
- 自动扫描 yuyi_desktop 目录,严禁 import `src.runtime` / `src.memory` / `src.growth` / `src.personality` / `src.self_model` / `src.control.api`

**Backward Compatibility (5 项)**:
- RuntimeService 旧 API 仍工作
- MemoryService 旧 API 仍工作
- GrowthService 旧 API 仍工作
- InitiativeService 旧 API 仍工作
- PersonalityService 旧 API 仍工作

---

## 7. 安全边界验证

### 7.1 严禁修改的模块
- ✅ `src/runtime/**` 未修改
- ✅ `src/memory/**` 未修改
- ✅ `src/growth/**` 未修改
- ✅ `src/personality/**` 未修改
- ✅ `src/self_model/**` 未修改
- ✅ `src/control/api/**` 未修改(只读由 Desktop 使用)
- ✅ `astrbot` 未修改
- ✅ 未增加任何服务器写接口

### 7.2 Desktop 严禁行为
- ✅ Desktop 不运行 Runtime
- ✅ Desktop 不持有 Memory/Growth/Personality 真状态
- ✅ Desktop 不直接 import 任何 `src.*` 业务模块(自动扫描通过)
- ✅ 只读:旧 API 全部只读,新 API(`refresh` / `get_snapshot_with_cache`)也只读
- ✅ `RemoteProviderBridge.security_self_check()` 仍返回 `all_get_only=True`

### 7.3 错误处理禁止行为
- ✅ `TypedApiClient` 不吞掉异常,所有未预期异常被包装为 `DesktopError`
- ✅ `RemoteSnapshotCache` 不允许外部修改缓存(深拷贝)
- ✅ Schema 不兼容时被阻断(`SchemaError`),不会污染 Service

---

## 8. 完成标准验收

| 标准 | 状态 |
|------|------|
| ✅ 稳定远程通信 | 已验证 46/46 远程测试 + 53/53 网关测试通过 |
| ✅ 网络断开仍显示最后状态 | `get_snapshot_with_cache()` 在 offline 时返回 `source="cache"`,UI 可读 `last_update_time` |
| ✅ API 协议保护 | `SchemaValidator` 校验所有 envelope,失败抛 `SchemaError` 并阻断 Service |
| ✅ 可扩展事件系统 | `EventBus` + 9 种标准事件类型,handler 异常隔离 |
| ✅ UI 不需要主动请求数据 | Service 拉取后自动 publish 事件,UI subscribe 即可 |

---

## 9. 后续阶段预留接口

- `EventTypes.LIFE_UPDATED` / `SELFMODEL_UPDATED` 已在 EventTypes 定义,留待后续 Service 接入
- `CachedServiceBase` 提供统一基类,新 Service 继承即可获得 cache+schema+event
- `TypedApiClient` 可被新组件直接复用,无需重复实现错误分类
- `ConnectionManager.get_state()` 已为 UI status bar 提供标准化契约
- 缓存 TTL(60s)与事件保留(200 条)均可配置

---

## 10. 文件变更清单

**新增 8 个文件**:
1. `yuyi_desktop/core/cache/__init__.py`
2. `yuyi_desktop/core/cache/remote_snapshot_cache.py`
3. `yuyi_desktop/core/errors.py`
4. `yuyi_desktop/core/api_client_typed.py`
5. `yuyi_desktop/core/service_base.py`
6. `yuyi_desktop/core/events/__init__.py`
7. `yuyi_desktop/core/events/event_bus.py`
8. `tests/test_yuyi_desktop_infrastructure.py`

**修改 6 个文件**:
1. `yuyi_desktop/core/connection_manager.py`(扩展 ConnectionState 契约)
2. `yuyi_desktop/core/schema_validator.py`(统一 SchemaError 来源)
3. `yuyi_desktop/services/runtime_service.py`(集成 cache+schema+event)
4. `yuyi_desktop/services/memory_service.py`(同上)
5. `yuyi_desktop/services/personality_service.py`(同上)
6. `yuyi_desktop/services/growth_service.py`(同上)
7. `yuyi_desktop/services/initiative_service.py`(同上)

**新增文档 1 个**:
1. `docs/phase_c10_4_completion_report.md`(本文件)

---

## 11. 总结

Phase C.10.4 完成了 Yuyi Desktop 数据基础设施的稳定化建设:

- **离线容忍**:Service 通过 cache 在网络断开时仍能展示最后状态
- **错误分类**:统一的 `DesktopError` 层级便于 UI 处理和用户提示
- **协议保护**:`SchemaValidator` 防止未来 Server API 修改导致静默错误
- **事件驱动**:Service 发布事件,UI 订阅即可,无需主动轮询
- **向后兼容**:46 + 32 个老测试 0 改动全通过,旧 API 完全保留
- **可扩展**:`CachedServiceBase` + `TypedApiClient` + `EventBus` 为后续阶段提供标准组件

新增 56 项测试,合计 288 项测试全部通过 ✅

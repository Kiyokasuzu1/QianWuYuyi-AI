# -*- coding: utf-8 -*-
"""
tests/test_yuyi_desktop_infrastructure.py

Phase C.10.4 —— Yuyi Desktop Data Infrastructure Stabilization 测试

覆盖:
1. RemoteSnapshotCache
   - 保存 snapshot
   - TTL 行为
   - offline fallback
   - 线程安全
   - 防止外部修改缓存(深拷贝)

2. Connection Health Manager
   - online 检测
   - timeout 处理
   - reconnect
   - degraded 状态

3. Schema Validator
   - version match
   - version mismatch(返回 schema_mismatch)
   - 缺失 schema_version
   - malformed version

4. ApiClient (Typed)
   - retry
   - error classify(Network/Server/Auth/Schema)
   - 转换为 DesktopError

5. Event System
   - emit / subscribe
   - once 订阅
   - 事件历史
   - handler 异常隔离

约束(强):
- 不直接 import 任何 src.* 业务模块
- 不修改 src/runtime / src/memory / src/growth / src/personality / src/self_model
"""
from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

# 路径由 conftest.py 处理


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ============================================================
# 1. RemoteSnapshotCache
# ============================================================
class TestRemoteSnapshotCache:
    """RemoteSnapshotCache 单元测试。"""

    def test_basic_put_get(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache(ttl_seconds=60.0)
        cache.put("runtime", {"online": True, "tick": 100}, latency_ms=12.0)
        entry = cache.get("runtime")
        assert entry is not None
        assert entry.data == {"online": True, "tick": 100}
        assert entry.success is True
        assert entry.latency_ms == 12.0

    def test_put_rejects_failure(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        result = cache.put("k", {"x": 1}, success=False)
        assert result is None
        assert cache.get("k") is None

    def test_put_rejects_non_dict(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        result = cache.put("k", "not a dict")
        assert result is None
        result2 = cache.put("k", [1, 2, 3])
        assert result2 is None
        assert cache.get("k") is None

    def test_put_rejects_empty_key(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        result = cache.put("", {"x": 1})
        assert result is None
        result2 = cache.put(None, {"x": 1})
        assert result2 is None

    def test_put_envelope(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        # 成功 envelope
        env = {
            "success": True,
            "data": {"value": 42},
            "error": "",
            "schema_version": "1.0",
            "latency_ms": 5.0,
            "timestamp": "2026-08-04T00:00:00Z",
        }
        result = cache.put_envelope("k", env)
        assert result is not None
        assert result.data == {"value": 42}
        # 失败 envelope
        env_fail = {
            "success": False,
            "data": {},
            "error": "offline",
            "degraded": True,
        }
        result2 = cache.put_envelope("k2", env_fail)
        assert result2 is None

    def test_ttl_expired(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache(ttl_seconds=0.1)
        cache.put("k", {"x": 1})
        # 立即读 OK
        assert cache.get("k", allow_stale=True) is not None
        # 等过期
        time.sleep(0.15)
        # allow_stale=True 仍能读
        entry = cache.get("k", allow_stale=True)
        assert entry is not None
        assert entry.success is True
        # allow_stale=False 不能读
        assert cache.get("k", allow_stale=False) is None

    def test_ttl_status(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache(ttl_seconds=0.1)
        # 初始状态
        status = cache.get_status("k")
        assert status["has_data"] is False
        assert status["offline"] is True
        # 写入后
        cache.put("k", {"x": 1})
        status = cache.get_status("k")
        assert status["has_data"] is True
        assert status["offline"] is False
        assert status["last_update_time"]
        assert status["ttl_seconds"] == 0.1
        # 过期后
        time.sleep(0.15)
        status = cache.get_status("k")
        assert status["is_stale"] is True
        assert status["offline"] is True

    def test_get_data_returns_deepcopy(self):
        """返回的 data 应是深拷贝,修改不影响缓存。"""
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        cache.put("k", {"nested": {"x": 1}})
        d1 = cache.get_data("k")
        d1["nested"]["x"] = 999  # type: ignore[index]
        d2 = cache.get_data("k")
        assert d2["nested"]["x"] == 1  # type: ignore[index]

    def test_offline_fallback(self):
        """模拟 offline:写入后,服务不可达,仍能读到 cache。"""
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        cache.put("runtime", {"online": True})
        # "server offline" 时
        cached = cache.get_data("runtime", allow_stale=True)
        assert cached is not None
        assert cached["online"] is True

    def test_invalidate_and_clear(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        cache.put("a", {"v": 1})
        cache.put("b", {"v": 2})
        assert cache.invalidate("a") is True
        assert cache.invalidate("a") is False
        n = cache.clear()
        assert n == 1
        assert cache.keys() == []

    def test_thread_safety(self):
        """多线程并发 put/get。"""
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        errors: List[str] = []

        def writer(start: int):
            try:
                for i in range(100):
                    cache.put(f"k{i}", {"v": i})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"writer: {exc}")

        def reader():
            try:
                for i in range(100):
                    cache.get(f"k{i}", allow_stale=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"reader: {exc}")

        threads = [
            threading.Thread(target=writer, args=(0,)),
            threading.Thread(target=writer, args=(100,)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors

    def test_stats(self):
        from yuyi_desktop.core.cache.remote_snapshot_cache import RemoteSnapshotCache
        cache = RemoteSnapshotCache()
        cache.put("k", {"v": 1})
        cache.put("k2", {"v": 2})
        cache.get("k")
        cache.get("nonexistent")
        s = cache.stats()
        assert s["total_puts"] == 2
        assert s["total_hits"] >= 1
        assert s["total_misses"] >= 1


# ============================================================
# 2. Connection Health Manager
# ============================================================
class TestConnectionManager:
    """ConnectionManager 单元测试。"""

    def test_connection_state_dataclass(self):
        from yuyi_desktop.core.connection_manager import ConnectionState
        cs = ConnectionState()
        assert cs.online is False
        assert cs.latency_ms == -1.0
        assert cs.retry_count == 0
        assert cs.degraded is False
        d = cs.to_dict()
        assert "online" in d
        assert "latency_ms" in d

    def test_check_once_offline(self):
        """连接失败时,online=False,retry_count 增加。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.connection_manager import ConnectionManager
        port = _free_port()
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=0.3,
            max_retries=0,
            connect_timeout_seconds=0.3,
        )
        client = ApiClient(config=cfg)
        mgr = ConnectionManager(
            api_client=client,
            degraded_failure_threshold=2,
        )
        mgr.check_once()
        mgr.check_once()
        mgr.check_once()
        state = mgr.get_state()
        assert state.online is False
        assert state.retry_count >= 3
        assert state.degraded is True  # 连续失败 >= 2

    def test_check_once_online(self, mock_yuyi_server):
        """mock server 正常时,online=True,retry_count=0。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.connection_manager import ConnectionManager
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
            max_retries=1,
        )
        client = ApiClient(config=cfg)
        mgr = ConnectionManager(api_client=client, latency_threshold_ms=10000.0)
        mgr.check_once()
        state = mgr.get_state()
        assert state.online is True
        assert state.retry_count == 0
        assert state.last_success != ""
        assert state.degraded is False  # latency 远低于 threshold

    def test_reconnect(self):
        """trigger_reconnect 强制再发一次 ping。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.connection_manager import ConnectionManager
        port = _free_port()
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=0.3,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        mgr = ConnectionManager(api_client=client)
        # 第一次失败
        mgr.check_once()
        # 主动重连(仍然失败)
        mgr.trigger_reconnect()
        state = mgr.get_state()
        assert state.online is False

    def test_state_dict(self):
        from yuyi_desktop.core.connection_manager import ConnectionState
        cs = ConnectionState(
            online=True,
            latency_ms=42.0,
            last_success="2026-08-04T00:00:00Z",
            retry_count=0,
            degraded=False,
        )
        d = cs.to_dict()
        assert d["online"] is True
        assert d["latency_ms"] == 42.0
        assert d["last_success"] == "2026-08-04T00:00:00Z"
        assert d["retry_count"] == 0
        assert d["degraded"] is False


# ============================================================
# 3. Schema Validator
# ============================================================
class TestSchemaValidator:
    """SchemaValidator 单元测试。"""

    def test_version_match(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator()
        env = {
            "success": True,
            "data": {"a": 1},
            "schema_version": "1.0",
        }
        result = v.validate(env)
        assert result.ok is True
        assert result.actual == "1.0"
        assert result.expected == "1.0"

    def test_version_mismatch(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator()
        env = {
            "success": True,
            "data": {"a": 1},
            "schema_version": "2.0",  # 不兼容
        }
        result = v.validate(env)
        assert result.ok is False
        assert result.actual == "2.0"
        assert result.reason == "version_mismatch"

    def test_missing_schema_version(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator()
        env = {"success": True, "data": {}}
        result = v.validate(env)
        assert result.ok is False
        assert result.reason in (
            "missing_schema_version", "malformed_schema_version", "envelope_not_dict"
        )

    def test_malformed_schema_version(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator()
        env = {"success": True, "data": {}, "schema_version": "abc"}
        result = v.validate(env)
        assert result.ok is False
        assert result.reason == "malformed_schema_version"

    def test_envelope_not_dict(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator()
        result = v.validate("not a dict")
        assert result.ok is False
        assert result.reason == "envelope_not_dict"
        result2 = v.validate(None)
        assert result2.ok is False

    def test_assert_compatible_raises(self):
        from yuyi_desktop.core.errors import SchemaError as DesktopSchemaError
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator()
        env = {"success": True, "data": {}, "schema_version": "9.9"}
        with pytest.raises(DesktopSchemaError) as excinfo:
            v.assert_compatible(env)
        assert excinfo.value.expected == "1.0"
        assert excinfo.value.actual == "9.9"
        assert excinfo.value.error_code == "schema_mismatch"

    def test_assert_compatible_passes(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator()
        env = {"success": True, "data": {}, "schema_version": "1.0"}
        result = v.assert_compatible(env)
        assert result.ok is True

    def test_custom_expected_versions(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        v = SchemaValidator(expected_versions=("1.0", "1.1"))
        assert v.validate({"schema_version": "1.1"}).ok is True
        assert v.validate({"schema_version": "1.0"}).ok is True
        assert v.validate({"schema_version": "2.0"}).ok is False

    def test_invalid_expected_versions(self):
        from yuyi_desktop.core.schema_validator import SchemaValidator
        with pytest.raises(ValueError):
            SchemaValidator(expected_versions=("bad",))


# ============================================================
# 4. ApiClient + DesktopError 分类
# ============================================================
class TestTypedApiClient:
    """TypedApiClient / DesktopError 单元测试。"""

    def test_classify_error_code(self):
        from yuyi_desktop.core.errors import (
            classify_error_code,
            ERROR_AUTH,
            ERROR_NETWORK,
            ERROR_SCHEMA,
            ERROR_SERVER,
            ERROR_TIMEOUT,
        )
        assert classify_error_code("timeout: read") == ERROR_TIMEOUT
        assert classify_error_code("connection_error: refused") == "connection_error"
        assert classify_error_code("auth_error: 401") == ERROR_AUTH
        assert classify_error_code("http_500") == ERROR_SERVER
        assert classify_error_code("schema_mismatch: x") == ERROR_SCHEMA
        assert classify_error_code("") == "unknown_error"

    def test_typed_response_success(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.api_client_typed import TypedApiClient
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
            max_retries=1,
        )
        client = TypedApiClient(
            api_client=ApiClient(config=cfg),
        )
        resp = client.get("/health/ping")
        assert resp.success is True
        assert resp.error_code == ""
        assert resp.schema_version == "1.0"
        assert resp.latency_ms >= 0

    def test_typed_response_offline(self):
        """连接到空闲端口,返回失败 + 错误分类。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.api_client_typed import TypedApiClient
        from yuyi_desktop.core.errors import (
            ERROR_NETWORK,
            ERROR_TIMEOUT,
        )
        port = _free_port()
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=0.3,
            max_retries=0,
            connect_timeout_seconds=0.3,
        )
        client = TypedApiClient(
            api_client=ApiClient(config=cfg),
        )
        resp = client.get("/x")
        assert resp.success is False
        # 应该是 network 错误(connection refused 或 timeout)
        assert resp.is_network_error is True
        assert resp.error_code in (
            ERROR_NETWORK, ERROR_TIMEOUT,
            "connection_error", "request_error", "ssl_error",
        )

    def test_typed_response_schema_mismatch(self, mock_yuyi_server):
        """服务端返回错误 schema_version,success=False 且 error_code=schema。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.api_client_typed import TypedApiClient
        # 设置 mock 返回错误 schema
        mock_yuyi_server.set_response("/health/ping", {
            "success": True,
            "data": {"status": "ok"},
            "schema_version": "9.9",  # 不兼容
        })
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = TypedApiClient(
            api_client=ApiClient(config=cfg),
        )
        resp = client.get("/health/ping")
        assert resp.success is False
        assert resp.is_schema_error is True
        assert resp.error_code == "schema_mismatch"

    def test_raise_for_error_network(self):
        from yuyi_desktop.core.api_client_typed import TypedResponse
        from yuyi_desktop.core.errors import NetworkError
        resp = TypedResponse(
            success=False,
            error_code="network_error",
            error_class="NetworkError",
            error_message="connection refused",
        )
        with pytest.raises(NetworkError):
            resp.raise_for_error()

    def test_raise_for_error_auth(self):
        from yuyi_desktop.core.api_client_typed import TypedResponse
        from yuyi_desktop.core.errors import AuthError
        resp = TypedResponse(
            success=False,
            error_code="auth_error",
            error_class="AuthError",
            error_message="401",
            http_status=401,
        )
        with pytest.raises(AuthError):
            resp.raise_for_error()

    def test_raise_for_error_schema(self):
        from yuyi_desktop.core.api_client_typed import TypedResponse
        from yuyi_desktop.core.errors import SchemaError
        resp = TypedResponse(
            success=False,
            error_code="schema_mismatch",
            error_class="SchemaError",
            error_message="version_mismatch",
        )
        with pytest.raises(SchemaError):
            resp.raise_for_error()

    def test_raise_for_error_server(self):
        from yuyi_desktop.core.api_client_typed import TypedResponse
        from yuyi_desktop.core.errors import ServerError
        resp = TypedResponse(
            success=False,
            error_code="server_error",
            error_class="ServerError",
            error_message="http_500",
            http_status=500,
        )
        with pytest.raises(ServerError):
            resp.raise_for_error()

    def test_raise_for_error_passes_on_success(self):
        from yuyi_desktop.core.api_client_typed import TypedResponse
        resp = TypedResponse(success=True, data={"x": 1})
        # 不应抛
        assert resp.raise_for_error().success is True

    def test_retry_behavior_preserved(self):
        """底层 ApiClient 仍支持 retry,max_retries=2 应尝试 3 次。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        port = _free_port()
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=0.3,
            max_retries=2,
            connect_timeout_seconds=0.2,
            retry_backoff_seconds=0.05,
        )
        client = ApiClient(config=cfg)
        start = time.monotonic()
        resp = client.get("/x")
        elapsed = time.monotonic() - start
        assert resp["success"] is False
        # 2 次退避 >= 0.05 + 0.10 = 0.15s
        assert elapsed >= 0.10

    def test_desktop_error_taxonomy(self):
        """DesktopError 子类层级与 error_code 映射。"""
        from yuyi_desktop.core.errors import (
            AuthError,
            DesktopError,
            NetworkError,
            OfflineError,
            ServerError,
            TimeoutError,
        )
        e1 = NetworkError("net")
        e2 = TimeoutError("t")
        e3 = ServerError("s", status_code=500)
        e4 = AuthError("a", status_code=401)
        e5 = OfflineError("o")
        for e in (e1, e2, e3, e4, e5):
            assert isinstance(e, DesktopError)
            assert e.error_code
            d = e.to_dict()
            assert d["type"]
            assert d["error"]


# ============================================================
# 5. Event System
# ============================================================
class TestEventBus:
    """EventBus 单元测试。"""

    def test_publish_and_subscribe(self):
        from yuyi_desktop.core.events.event_bus import DesktopEvent, EventBus, EventTypes
        bus = EventBus()
        received: List[DesktopEvent] = []
        bus.subscribe(
            EventTypes.RUNTIME_UPDATED,
            lambda e: received.append(e),
        )
        bus.publish_typed(
            event_type=EventTypes.RUNTIME_UPDATED,
            source="test",
            data={"x": 1},
        )
        assert len(received) == 1
        assert received[0].data["x"] == 1
        assert received[0].source == "test"

    def test_subscribe_multiple_handlers(self):
        from yuyi_desktop.core.events.event_bus import EventBus, EventTypes
        bus = EventBus()
        a, b = [], []
        bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: a.append(e))
        bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: b.append(e))
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={})
        assert len(a) == 1
        assert len(b) == 1

    def test_unsubscribe(self):
        from yuyi_desktop.core.events.event_bus import EventBus, EventTypes
        bus = EventBus()
        received: list = []
        sub_id = bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: received.append(e))
        assert bus.unsubscribe(sub_id) is True
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={})
        assert len(received) == 0
        # 再次 unsubscribe 返回 False
        assert bus.unsubscribe(sub_id) is False

    def test_subscribe_once(self):
        from yuyi_desktop.core.events.event_bus import EventBus, EventTypes
        bus = EventBus()
        received: list = []
        bus.subscribe_once(EventTypes.RUNTIME_UPDATED, lambda e: received.append(e))
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={})
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={})
        assert len(received) == 1

    def test_handler_exception_isolated(self):
        """一个 handler 抛异常,不应影响其他 handler。"""
        from yuyi_desktop.core.events.event_bus import EventBus, EventTypes
        bus = EventBus()
        received: list = []
        bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: 1 / 0)
        bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: received.append(e))
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={})
        # 第二个 handler 仍收到事件
        assert len(received) == 1
        # handler 错误已记录
        assert bus.stats()["total_handler_errors"] == 1
        assert bus.stats()["total_delivered"] == 1  # 只有成功的算 delivered

    def test_event_history(self):
        from yuyi_desktop.core.events.event_bus import EventBus, EventTypes
        bus = EventBus(history_limit=10)
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={"i": 1})
        bus.publish_typed(EventTypes.MEMORY_UPDATED, source="t", data={"i": 2})
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={"i": 3})
        history = bus.get_history()
        assert len(history) == 3
        runtime_only = bus.get_history(event_type=EventTypes.RUNTIME_UPDATED)
        assert len(runtime_only) == 2
        last_one = bus.get_history(limit=1)
        assert last_one[0].data["i"] == 3

    def test_wildcard_subscribe(self):
        from yuyi_desktop.core.events.event_bus import EventBus, EventTypes
        bus = EventBus()
        received: list = []
        bus.subscribe("*", lambda e: received.append(e))
        bus.publish_typed(EventTypes.RUNTIME_UPDATED, source="t", data={})
        bus.publish_typed(EventTypes.MEMORY_UPDATED, source="t", data={})
        assert len(received) == 2

    def test_subscriber_summary(self):
        from yuyi_desktop.core.events.event_bus import EventBus, EventTypes
        bus = EventBus()
        bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: None)
        bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: None)
        bus.subscribe(EventTypes.MEMORY_UPDATED, lambda e: None)
        summary = bus.subscriber_summary()
        assert summary[EventTypes.RUNTIME_UPDATED] == 2
        assert summary[EventTypes.MEMORY_UPDATED] == 1

    def test_desktop_event_to_dict(self):
        from yuyi_desktop.core.events.event_bus import DesktopEvent
        e = DesktopEvent(
            event_type="X",
            source="src",
            data={"a": 1},
            correlation_id="cid-1",
        )
        d = e.to_dict()
        assert d["event_type"] == "X"
        assert d["source"] == "src"
        assert d["data"]["a"] == 1
        assert d["correlation_id"] == "cid-1"
        assert d["timestamp"]
        assert d["event_id"]


# ============================================================
# 6. Service + Cache + Event 集成
# ============================================================
class TestServiceIntegration:
    """Service 与 Cache/Event/Schema 的集成。"""

    def test_runtime_service_publishes_event(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import (
            EventTypes,
            reset_event_bus_for_testing,
        )
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import (
            reset_runtime_service_for_testing,
            RuntimeService,
        )

        reset_event_bus_for_testing()
        reset_remote_snapshot_cache_for_testing()
        reset_runtime_service_for_testing()

        bus = reset_event_bus_for_testing()
        cache = reset_remote_snapshot_cache_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
            max_retries=1,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = RuntimeService(
            bridge=bridge,
            cache=cache,
            event_bus=bus,
        )

        received: list = []
        bus.subscribe(EventTypes.RUNTIME_UPDATED, lambda e: received.append(e))

        result = svc.refresh()
        assert result.get("success") is True
        assert len(received) == 1
        assert received[0].data["domain"] == "runtime"

    def test_runtime_service_get_snapshot_with_cache(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import reset_event_bus_for_testing
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import (
            reset_runtime_service_for_testing,
            RuntimeService,
        )

        reset_event_bus_for_testing()
        cache = reset_remote_snapshot_cache_for_testing()
        reset_runtime_service_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = RuntimeService(bridge=bridge, cache=cache)

        view = svc.get_snapshot_with_cache()
        assert view["source"] == "live"
        assert view["online"] is True
        assert view["offline"] is False
        assert view["last_update_time"]

    def test_offline_fallback_in_service(self, mock_yuyi_server):
        """首次成功,之后服务器不可达,Service 应返回 cache 数据。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import reset_event_bus_for_testing
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import (
            reset_runtime_service_for_testing,
            RuntimeService,
        )

        reset_event_bus_for_testing()
        cache = reset_remote_snapshot_cache_for_testing()
        reset_runtime_service_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = RuntimeService(bridge=bridge, cache=cache)

        # 1) 拉取成功,缓存有数据
        svc.refresh()
        assert svc.get_cached_snapshot() is not None

        # 2) mock 关闭,模拟 offline
        mock_yuyi_server.stop()
        time.sleep(0.1)

        # 3) 重建连接到不存在的端口
        new_port = _free_port()
        cfg2 = ApiClientConfig(
            base_url=f"http://127.0.0.1:{new_port}",
            timeout_seconds=0.3,
            max_retries=0,
        )
        new_bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg2))
        svc2 = RuntimeService(bridge=new_bridge, cache=cache)

        view = svc2.get_snapshot_with_cache()
        assert view["source"] == "cache"
        assert view["online"] is False
        assert view["offline"] is False
        assert view["last_update_time"]

    def test_service_schema_mismatch_publishes_event(self, mock_yuyi_server):
        """服务端返回错误 schema_version,Service 应发布 SchemaChanged 事件。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import (
            EventTypes,
            reset_event_bus_for_testing,
        )
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import (
            reset_runtime_service_for_testing,
            RuntimeService,
        )

        reset_event_bus_for_testing()
        cache = reset_remote_snapshot_cache_for_testing()
        reset_runtime_service_for_testing()
        bus = reset_event_bus_for_testing()

        mock_yuyi_server.set_response("/runtime/status", {
            "success": True,
            "data": {"online": True},
            "schema_version": "9.9",
        })
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = RuntimeService(bridge=bridge, cache=cache, event_bus=bus)

        received: list = []
        bus.subscribe(EventTypes.SCHEMA_CHANGED, lambda e: received.append(e))

        result = svc.refresh()
        assert result["success"] is False
        assert "schema_mismatch" in result["error"]
        assert len(received) == 1
        assert received[0].data["domain"] == "runtime"


# ============================================================
# 7. 安全自检(确保未污染 src 业务模块)
# ============================================================
class TestSafetyBoundaries:
    """确保 Desktop 未引入 src 业务模块,核心业务不被破坏。"""

    def test_no_src_business_imports_in_yuyi_desktop(self):
        """扫描 yuyi_desktop 目录,严禁 import src 业务模块。"""
        import os
        import re

        forbidden_patterns = [
            r"from\s+src\.runtime",
            r"from\s+src\.memory",
            r"from\s+src\.growth",
            r"from\s+src\.personality",
            r"from\s+src\.self_model",
            r"from\s+src\.control\.api",
            r"import\s+src\.runtime",
            r"import\s+src\.memory",
            r"import\s+src\.growth",
            r"import\s+src\.personality",
            r"import\s+src\.self_model",
        ]
        yuyi_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "yuyi_desktop",
        )
        # 收集 yuyi_desktop 下所有 .py 文件
        violations: list = []
        for root, _, files in os.walk(yuyi_root):
            # 跳过测试目录(若有)和 UI(避免误判)
            if "__pycache__" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                fp = os.path.join(root, f)
                with open(fp, "r", encoding="utf-8") as fh:
                    content = fh.read()
                # 跳过注释和 docstring 中的字面提及
                for pat in forbidden_patterns:
                    for m in re.finditer(pat, content):
                        line_no = content[: m.start()].count("\n") + 1
                        line = content.splitlines()[line_no - 1]
                        # 排除注释行
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            continue
                        violations.append(f"{fp}:{line_no}: {line.strip()}")
        assert not violations, f"yuyi_desktop 禁止引入 src 业务模块:\n" + "\n".join(violations)


# ============================================================
# 8. 兼容性(已有 Services 不破)
# ============================================================
class TestBackwardCompatibility:
    """保证现有 Services 旧 API 仍能工作。"""

    def test_runtime_service_old_api(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import reset_event_bus_for_testing
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import (
            reset_runtime_service_for_testing,
            RuntimeService,
        )

        reset_event_bus_for_testing()
        reset_remote_snapshot_cache_for_testing()
        reset_runtime_service_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = RuntimeService(bridge=bridge)
        # 旧 API 不破
        assert isinstance(svc.get_status_envelope(), dict)
        assert isinstance(svc.get_overview(), dict)
        assert isinstance(svc.get_health(), dict)

    def test_memory_service_old_api(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import reset_event_bus_for_testing
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.memory_service import (
            MemoryService,
            reset_memory_service_for_testing,
        )

        reset_event_bus_for_testing()
        reset_remote_snapshot_cache_for_testing()
        reset_memory_service_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = MemoryService(bridge=bridge)
        assert isinstance(svc.get_summary_envelope(), dict)
        assert isinstance(svc.get_overview(), dict)
        assert isinstance(svc.list_recent(), list)

    def test_growth_service_old_api(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import reset_event_bus_for_testing
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.growth_service import (
            GrowthService,
            reset_growth_service_for_testing,
        )

        reset_event_bus_for_testing()
        reset_remote_snapshot_cache_for_testing()
        reset_growth_service_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = GrowthService(bridge=bridge)
        assert isinstance(svc.get_summary_envelope(), dict)
        assert isinstance(svc.get_proposals(), list)

    def test_initiative_service_old_api(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import reset_event_bus_for_testing
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.initiative_service import (
            InitiativeService,
            reset_initiative_service_for_testing,
        )

        reset_event_bus_for_testing()
        reset_remote_snapshot_cache_for_testing()
        reset_initiative_service_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = InitiativeService(bridge=bridge)
        assert isinstance(svc.get_summary_envelope(), dict)
        assert isinstance(svc.get_actions(), list)

    def test_personality_service_old_api(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.cache.remote_snapshot_cache import (
            reset_remote_snapshot_cache_for_testing,
        )
        from yuyi_desktop.core.events.event_bus import reset_event_bus_for_testing
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.personality_service import (
            PersonalityService,
            reset_personality_service_for_testing,
        )

        reset_event_bus_for_testing()
        reset_remote_snapshot_cache_for_testing()
        reset_personality_service_for_testing()

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = PersonalityService(bridge=bridge)
        assert isinstance(svc.get_snapshot_envelope(), dict)
        assert isinstance(svc.get_traits(), list)
        assert isinstance(svc.get_selfmodel(), dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

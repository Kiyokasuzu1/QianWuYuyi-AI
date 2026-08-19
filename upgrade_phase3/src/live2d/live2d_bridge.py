# -*- coding: utf-8 -*-
"""
live2d_bridge.py
非阻塞 WebSocket 服务（监听 ws://127.0.0.1:8765）
提供 broadcast(emotion, intensity) 方法广播 JSON
如果 websockets 库不可用，优雅降级（打印警告，不抛异常）

使用示例：
    bridge = Live2DBridge()
    bridge.start()
    bridge.broadcast("happy", 0.8)
    bridge.stop()

本文件包含中文注释并在依赖不可用时降级为无网络的打印广播。
"""

import asyncio
import json
import threading
import time

try:
    import websockets
    _HAS_WEBSOCKETS = True
except Exception:
    websockets = None
    _HAS_WEBSOCKETS = False
    # 仅打印警告，不抛异常

class Live2DBridge:
    """
    非阻塞 Live2D WebSocket 桥接器。
    - start(): 在后台线程启动 asyncio loop 与 WebSocket 服务（若可用）
    - stop(): 停止服务
    - broadcast(emotion, intensity): 广播 JSON 到所有已连接客户端
    """

    def __init__(self, host='127.0.0.1', port=8765):
        self.host = host
        self.port = port
        self._loop = None
        self._thread = None
        self._server = None
        self._clients = set()
        self._broadcast_queue = asyncio.Queue() if _HAS_WEBSOCKETS else None
        self._running = False

    def start(self):
        """启动后台服务；若 websockets 不可用则打印警告并继续（降级）"""
        if not _HAS_WEBSOCKETS:
            print("[live2d_bridge] 警告：websockets 库不可用，降级为打印广播模式")
            self._running = True
            return

        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # 等待 loop 启动
        for _ in range(20):
            if self._loop is not None and self._loop.is_running():
                break
            time.sleep(0.05)

    def _run_loop(self):
        """在后台线程中运行 asyncio loop 与 WebSocket server"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        start_server = websockets.serve(self._handler, self.host, self.port)
        self._server = self._loop.run_until_complete(start_server)
        # 启动广播处理任务
        self._loop.create_task(self._broadcast_worker())
        try:
            self._loop.run_forever()
        finally:
            # 清理
            tasks = asyncio.all_tasks(loop=self._loop)
            for t in tasks:
                t.cancel()
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    async def _handler(self, websocket, path):
        """每个客户端连接处理器，保存客户端引用并接收心跳（若有）"""
        self._clients.add(websocket)
        try:
            async for _ in websocket:
                # 我们不特别处理客户端发来的消息
                pass
        except Exception:
            # 忽略单个连接异常
            pass
        finally:
            self._clients.discard(websocket)

    async def _broadcast_worker(self):
        """从队列读取广播并发送给所有已连接的客户端"""
        while self._running:
            try:
                msg = await self._broadcast_queue.get()
                payload = json.dumps(msg)
                # 复制客户端集合以避免并发修改问题
                clients = list(self._clients)
                coros = []
                for c in clients:
                    coros.append(self._safe_send(c, payload))
                if coros:
                    await asyncio.gather(*coros, return_exceptions=True)
            except Exception:
                # 忽略单次错误，继续循环
                pass
            await asyncio.sleep(0)  # 让出控制权

    async def _safe_send(self, client, payload):
        """对单个客户端安全发送"""
        try:
            await client.send(payload)
        except Exception:
            # 发送失败则从列表移除
            try:
                self._clients.discard(client)
            except Exception:
                pass

    def broadcast(self, emotion, intensity):
        """
        广播情绪信息。JSON 格式：{"emotion": emotion, "intensity": intensity, "ts": <iso>}
        如果 websockets 不可用，则降级为打印日志。
        """
        msg = {"emotion": emotion, "intensity": float(intensity), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if not _HAS_WEBSOCKETS:
            # 降级为打印，不抛异常
            print("[live2d_bridge] broadcast (降级):", json.dumps(msg, ensure_ascii=False))
            return
        # 将消息放入 asyncio 队列
        if self._loop and self._broadcast_queue:
            # 在非 asyncio 线程中安全地提交协程
            asyncio.run_coroutine_threadsafe(self._broadcast_queue.put(msg), self._loop)
        else:
            # 若 loop 尚未就绪，则打印并丢弃
            print("[live2d_bridge] 警告：广播服务尚未就绪，消息已丢弃:", msg)

    def stop(self):
        """停止服务并关闭 loop"""
        self._running = False
        if not _HAS_WEBSOCKETS:
            return
        if self._loop:
            # 停止 loop
            def _stop_loop(loop):
                loop.stop()
            try:
                self._loop.call_soon_threadsafe(_stop_loop, self._loop)
                if self._thread:
                    self._thread.join(timeout=1.0)
            except Exception:
                pass
            self._loop = None
            self._thread = None

# 提供模块级默认实例（可选）
_default_bridge = None

def get_default_bridge():
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = Live2DBridge()
    return _default_bridge
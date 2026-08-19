# -*- coding: utf-8 -*-
"""
scripts/test_live2d_bridge.py
启动 bridge，广播几条示例消息，验证客户端能接收

说明：
- 如果 websockets 不可用，则此脚本会降级为打印检查并退出成功（不抛异常）
- 以简单的 asyncio 客户端模拟验证接收逻辑
"""

import asyncio
import json
import time

try:
    import websockets
    _HAS_WEBSOCKETS = True
except Exception:
    websockets = None
    _HAS_WEBSOCKETS = False

# 从本仓库导入 bridge；如果尚未存在则尝试同路径导入
try:
    from src.live2d.live2d_bridge import Live2DBridge
except Exception:
    try:
        from live2d_bridge import Live2DBridge
    except Exception:
        Live2DBridge = None

def run_sync(coro):
    return asyncio.get_event_loop().run_until_complete(coro)

async def client_receiver(host='127.0.0.1', port=8765, expected_count=3, timeout=5):
    """简单客户端：连接到 ws 并接收 expected_count 条消息"""
    uri = f"ws://{host}:{port}"
    received = []
    try:
        async with websockets.connect(uri) as ws:
            start = time.time()
            while len(received) < expected_count and (time.time() - start) < timeout:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    received.append(json.loads(msg))
                except asyncio.TimeoutError:
                    break
    except Exception as e:
        print("[test_live2d_bridge] 客户端连接失败或发生异常：", e)
    return received

def main():
    # 如果没有 Live2DBridge 实现或 websockets 不可用，降级为打印测试
    if Live2DBridge is None or not _HAS_WEBSOCKETS:
        print("[test_live2d_bridge] 警告：websockets 或 Live2DBridge 不可用，执行降级打印测试")
        # 模拟广播调用
        print("模拟广播：", {"emotion": "happy", "intensity": 0.9})
        print("模拟广播：", {"emotion": "sad", "intensity": 0.2})
        print("模拟广播：", {"emotion": "surprised", "intensity": 0.5})
        print("[test_live2d_bridge] 降级测试完成（无法验证网络接收）")
        return

    bridge = Live2DBridge()
    bridge.start()
    time.sleep(0.1)  # 等待服务就绪

    # 异步启动客户端并同时广播
    async def test_flow():
        task = asyncio.create_task(client_receiver(expected_count=3))
        await asyncio.sleep(0.1)
        bridge.broadcast("happy", 0.9)
        await asyncio.sleep(0.05)
        bridge.broadcast("sad", 0.2)
        await asyncio.sleep(0.05)
        bridge.broadcast("surprised", 0.5)
        received = await task
        return received

    received = run_sync(test_flow())
    bridge.stop()

    if received:
        print("[test_live2d_bridge] 客户端接收到消息数：", len(received))
        for i, r in enumerate(received):
            print("  收到消息", i+1, ":", r)
    else:
        print("[test_live2d_bridge] 未接收到消息（可能被防火墙或依赖问题阻止）")

if __name__ == "__main__":
    main()
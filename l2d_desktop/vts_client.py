"""
VTube Studio API 客户端 - 控制羽依表情
使用 VTube Studio 提供的 WebSocket API
"""
import json
import time
import websockets
import asyncio
from pathlib import Path

# VTube Studio API 配置
VTS_WS_URL = "ws://localhost:8001"
PLUGIN_NAME = "QianWuYuyi-AI"
PLUGIN_DEVELOPER = "Yuyi"

# 你的模型表情文件（在 A.雪芽2.0 目录中）
MODEL_DIR = Path(__file__).parent.parent / "A.雪芽2.0"

# 表情映射到 VTS 可用的文件名
# VTS 支持直接通过文件名加载 .exp3.json 表情
EXPRESSION_MAP = {
    "平静": None,  # 重置
    "开心": "",    # 需要在 VTS 中对应
    "害羞": "",
    "哇塞": "",
    "生气": "",
    "难过": "",
    "好奇": "",
    "委屈": "",
}


def get_expression_files():
    """获取模型目录下所有 .exp3.json 表情文件"""
    if not MODEL_DIR.exists():
        return []
    
    expressions = []
    for f in MODEL_DIR.glob("*.exp3.json"):
        expressions.append(f.stem)
    return sorted(expressions)


def scan_expressions():
    """扫描并显示所有可用表情"""
    files = get_expression_files()
    print("\n=== 可用表情文件 ===")
    for i, name in enumerate(files, 1):
        print(f"  {i:2d}. {name}")
    print(f"\n共 {len(files)} 个表情文件")
    return files


class VTSConnector:
    """VTube Studio API 连接器"""
    
    def __init__(self):
        self.ws = None
        self.authenticated = False
        self.plugin_token = None
    
    async def connect(self):
        """连接到 VTube Studio"""
        print(f"[VTS] 正在连接 {VTS_WS_URL}...")
        try:
            self.ws = await websockets.connect(VTS_WS_URL)
            print("[VTS] 已连接!")
            return True
        except Exception as e:
            print(f"[VTS] 连接失败: {e}")
            print("[VTS] 请确保:")
            print("  1. VTube Studio 已启动")
            print("  2. 设置中已启用 '允许插件 API 访问'")
            print("  3. 端口 8001 未被防火墙阻止")
            return False
    
    async def disconnect(self):
        """断开连接"""
        if self.ws:
            await self.ws.close()
            self.ws = None
            self.authenticated = False
            print("[VTS] 已断开")
    
    async def _send(self, message):
        """发送消息并等待响应"""
        if not self.ws:
            raise ConnectionError("未连接到 VTube Studio")
        await self.ws.send(json.dumps(message))
        response = json.loads(await self.ws.recv())
        return response
    
    async def get_state(self):
        """获取 VTube Studio 状态"""
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": "state_req",
            "messageType": "APIStateRequest"
        }
        return await self._send(msg)
    
    async def authenticate(self, token=None):
        """认证插件"""
        if token:
            # 使用已有 token
            msg = {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "auth_req",
                "messageType": "AuthenticationRequest",
                "data": {
                    "pluginName": PLUGIN_NAME,
                    "pluginDeveloper": PLUGIN_DEVELOPER,
                    "authenticationToken": token
                }
            }
        else:
            # 请求新 token
            msg = {
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "token_req",
                "messageType": "AuthenticationTokenRequest",
                "data": {
                    "pluginName": PLUGIN_NAME,
                    "pluginDeveloper": PLUGIN_DEVELOPER
                }
            }
        
        response = await self._send(msg)
        return response
    
    async def set_expression(self, expression_file, active=True, fade_time=0.5):
        """设置表情
        
        Args:
            expression_file: 表情文件名（不含路径和后缀）
            active: True=激活, False=取消
            fade_time: 淡入淡出时间（秒）
        """
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": f"expr_{int(time.time())}",
            "messageType": "ExpressionActivationRequest",
            "data": {
                "expressionFile": expression_file,
                "fadeTime": fade_time,
                "active": active
            }
        }
        return await self._send(msg)
    
    async def trigger_hotkey(self, hotkey_id):
        """触发热键"""
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": f"hotkey_{int(time.time())}",
            "messageType": "HotkeyTriggerRequest",
            "data": {
                "hotkeyID": hotkey_id
            }
        }
        return await self._send(msg)
    
    async def move_model(self, x=None, y=None, rotation=None, size=None, time_s=0.5):
        """移动模型
        
        Args:
            x: 水平位置 (-1000 到 1000)
            y: 垂直位置 (-1000 到 1000)
            rotation: 旋转角度 (-360 到 360)
            size: 缩放大小 (-100 到 100)
            time_s: 动画时间（秒）
        """
        data = {"timeInSeconds": time_s, "valuesAreRelativeToModel": False}
        if x is not None:
            data["positionX"] = x
        if y is not None:
            data["positionY"] = y
        if rotation is not None:
            data["rotation"] = rotation
        if size is not None:
            data["size"] = size
        
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": f"move_{int(time.time())}",
            "messageType": "MoveModelRequest",
            "data": data
        }
        return await self._send(msg)
    
    async def get_art_mesh_list(self):
        """获取 ArtMesh 列表"""
        msg = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": f"mesh_{int(time.time())}",
            "messageType": "ArtMeshListRequest"
        }
        return await self._send(msg)


async def test_connection():
    """测试连接并显示状态"""
    connector = VTSConnector()
    
    if not await connector.connect():
        return
    
    # 获取状态
    state = await connector.get_state()
    print("\n=== VTube Studio 状态 ===")
    print(json.dumps(state, indent=2, ensure_ascii=False))
    
    # 扫描可用表情
    expressions = scan_expressions()
    
    # 如果没有表情，说明 VTS 没有加载模型
    if not expressions:
        print("\n⚠️  没有找到表情文件！")
        print("请确保在 VTube Studio 中加载了模型 'A.雪芽2.0'")
        print("表情文件应该位于:", MODEL_DIR)
    
    await connector.disconnect()


if __name__ == "__main__":
    asyncio.run(test_connection())

import aiohttp
from astrbot.api.all import *

@register("astrbot_plugin_yuyi", "Kiyokasuzu", "浅雾羽依 AI 的 AstrBot 插件", "1.0.0")
class YuyiPlugin(StarlettePluginBundle):
    async def initialize(self):
        # 对齐 api_server.py 的 OpenAI 兼容端点
        self.yuyi_api_url = "http://localhost:5000/v1/chat/completions"

    @command("羽依")
    async def chat_with_yuyi(self, event: AstrBotEvent, message: str):
        '''与浅雾羽依对话'''
        try:
            user_id = event.get_sender_id()
            user_name = event.get_sender_name()
            group_id = event.get_group_id()

            # 构造 OpenAI 兼容请求体
            payload = {
                "user": str(user_id),
                "messages": [
                    {"role": "user", "content": message}
                ]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self.yuyi_api_url, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        # OpenAI 兼容响应结构：choices[0].message.content
                        try:
                            reply = result["choices"][0]["message"]["content"]
                        except (KeyError, IndexError, TypeError):
                            reply = "羽依好像走神了..."
                    else:
                        reply = f"抱歉，连接羽依时出错了 (状态码: {resp.status})"
        except asyncio.TimeoutError:
            reply = "羽依思考时间太长了，请稍后再试试。"
        except Exception as e:
            reply = f"发生了一个错误: {str(e)}"

        yield event.plain_result(reply)

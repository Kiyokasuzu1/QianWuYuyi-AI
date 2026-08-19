import openai

from src.config import get_api_key, get


class LLMClient:
    def __init__(self):
        api_key = get_api_key()

        if not api_key:
            raise ValueError(
                "❌ API Key 未设置！请检查 .env 文件中的 DEEPSEEK_API_KEY"
            )

        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=get(
                "llm.api_base",
                "https://api.deepseek.com/v1"
            )
        )

        # 保留你的模型
        self.model = get(
            "llm.model",
            "deepseek-v4-pro"
        )

        self.temperature = get(
            "llm.temperature",
            0.85
        )

        self.max_tokens = get(
            "llm.max_tokens",
            512
        )


    # ======================================
    # 普通聊天生成（羽依日常回复使用）
    # ======================================
    def generate(self, messages: list) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )

            content = response.choices[0].message.content

            if not content:
                return "走神了一下..."

            return content

        except Exception as e:
            print(f"❌ generate异常: {e}")
            return f"走神了...错误：{str(e)}"


    # ======================================
    # 内部任务生成
    # 记忆整理 / 总结 / 分类使用
    # ======================================
    def generate_raw(self, prompt: str) -> str:
        """
        内部任务专用：
        - 事件提取
        - 记忆整理
        - JSON生成
        """

        try:
            response = self.client.chat.completions.create(

                model=self.model,

                messages=[
                    {
                        "role": "system",
                        "content":
                        "你是一个JSON信息抽取器。"
                        "你的任务是严格按照用户要求输出JSON。"
                        "不要解释，不要聊天，不要添加额外文字。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],

                # 数据抽取降低随机性
                temperature=0,

                # ⚠️ 关键修改：从 1024 改为 4096
                # 原因：deepseek-v4-pro 是推理模型，需要足够的 token 完成思考后再输出 JSON
                max_tokens=4096
            )


            # ==============================
            # 调试信息
            # ==============================

            print("\n========== DeepSeek RAW ==========")

            print("模型:")
            print(response.model)

            print("\nFinish reason:")
            print(
                response.choices[0].finish_reason
            )

            print("\nUsage:")
            print(response.usage)

            print("\nMessage:")
            print(
                response.choices[0].message
            )

            print("==================================\n")


            content = (
                response
                .choices[0]
                .message
                .content
            )


            if content is None:
                print(
                    "⚠️ DeepSeek返回content为空"
                )
                return ""


            print(
                f"🔍 [generate_raw] "
                f"返回长度: {len(content)}"
            )

            print(
                f"🔍 内容预览: "
                f"{content[:300]}"
            )


            return content


        except Exception as e:

            print(
                "❌ generate_raw异常:"
            )

            print(e)

            return ""
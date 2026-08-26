# R2.7.6-DEPLOY: openai SDK 延迟 import —— 没装 openai 包时不阻断 module 加载
#   旧版顶层 `import openai` 导致只要 src.growth.event_extractor / memory_former
#   被 import（即使是测试），整个进程就 ModuleNotFoundError 崩溃。
#   现在改为在 LLMClient.__init__ 里 try import，没装 SDK 时抛 ValueError
#   提示配置 API key（与原行为一致），但模块本身可正常加载。
try:
    import openai as _openai_sdk
    _OPENAI_SDK_AVAILABLE = True
except Exception:
    _openai_sdk = None  # type: ignore
    _OPENAI_SDK_AVAILABLE = False

import logging
import time

from src.config import get_api_key, get

logger = logging.getLogger(__name__)


def _llm_error_category(exc: Exception) -> str:
    """v1.4 Phase A: LLM 错误分类(容忍 openai SDK 缺失/被 mock)。

    返回分类标识: APITimeoutError / APIConnectionError / APIStatusError / ""(未知)。
    优先 isinstance 判定, 兜底按异常类名判定——保证在任何环境下都不抛。
    """
    _sdk = _openai_sdk
    for _attr in ("APITimeoutError", "APIConnectionError", "APIStatusError"):
        _cls = getattr(_sdk, _attr, None) if _sdk is not None else None
        if _cls is not None and isinstance(_cls, type) and isinstance(exc, _cls):
            return _attr
    _name = type(exc).__name__ or ""
    if "Timeout" in _name:
        return "APITimeoutError"
    if "Connect" in _name:
        return "APIConnectionError"
    if "Status" in _name:
        return "APIStatusError"
    return ""


class LLMError(Exception):
    """LLM 调用失败（P1 稳定性修复，2026-08-27）。

    重试耗尽后抛出，替代静默返回 ""——让上层能区分
    "模型没有产生可发送正文" 与 "正常回复"，并留下可审计原因。

    category 取值（与日志分类对齐）：
        llm_empty_content / llm_timeout / llm_connection / llm_http_<code>
        / malformed_response / llm_unknown
    """

    def __init__(self, category: str, message: str = ""):
        self.category = category or "llm_unknown"
        super().__init__(message or self.category)


class LLMClient:
    def __init__(self):
        if not _OPENAI_SDK_AVAILABLE:
            # 与原"缺 API key"行为一致：抛 ValueError，调用方决定是否降级
            raise ValueError(
                "❌ openai SDK 未安装！请运行 `pip install openai>=1.0.0` "
                "或检查 requirements.txt 是否完整安装。"
            )

        api_key = get_api_key()

        if not api_key:
            raise ValueError(
                "❌ API Key 未设置！请检查 .env 文件中的 DEEPSEEK_API_KEY"
            )

        # v1.4 Phase A: LLM 可靠性加固
        # - timeout: 可配置(默认 120s, 覆盖 DeepSeek 高峰期长回复生成)
        # - SDK 内置重试置 0, 重试由本类显式控制(可观测 + 可分类)
        self.timeout = float(get("llm.timeout_seconds", 120.0) or 120.0)
        self.max_retries = int(get("llm.max_retries", 2) or 2)

        self.client = _openai_sdk.OpenAI(
            api_key=api_key,
            base_url=get(
                "llm.api_base",
                "https://api.deepseek.com/v1"
            ),
            timeout=self.timeout,
            max_retries=0,
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
        """普通聊天生成。

        v1.4 Phase A: 可观测重试 + 空回复恢复 + 错误分类。
        P1 稳定性修复（2026-08-27）：重试耗尽后抛出 LLMError（携带 category），
        不再静默返回 ""——上层可区分"模型没有产生正文"与"正常回复"，
        并使 LLM 层故障进入日志与 fallback audit。

        - timeout / 连接 / 5xx / 空 content → 自动重试(有界退避);
        - 重试耗尽 → raise LLMError(category)，绝不向用户泄露内部错误;
        - 分类日志: llm_timeout / llm_connection / llm_http_N / llm_empty_content / llm_unknown。
        """
        last_category = "llm_unknown"
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
                )

                choices = getattr(response, "choices", None) or []
                if not choices:
                    # HTTP 200 但响应结构异常（无 choices）
                    logger.warning(
                        "[LLM] llm_malformed_response (第 %d/%d 次尝试)",
                        attempt + 1, self.max_retries + 1,
                    )
                    if attempt < self.max_retries:
                        time.sleep(min(2.0 ** attempt, 4.0))
                        continue
                    raise LLMError("malformed_response", "LLM 响应缺少 choices（重试耗尽）")

                content = choices[0].message.content

                if not content:
                    logger.warning(
                        "[LLM] llm_empty_content (第 %d/%d 次尝试)",
                        attempt + 1, self.max_retries + 1,
                    )
                    if attempt < self.max_retries:
                        time.sleep(min(2.0 ** attempt, 4.0))
                        continue
                    raise LLMError(
                        "llm_empty_content",
                        "LLM 返回空 content（重试耗尽）",
                    )

                return content

            except LLMError:
                raise
            except Exception as exc:  # noqa: BLE001
                _cat = _llm_error_category(exc)
                if _cat == "APITimeoutError":
                    last_category = "llm_timeout"
                    logger.warning(
                        "[LLM] llm_timeout (第 %d/%d 次尝试): %s",
                        attempt + 1, self.max_retries + 1, exc,
                    )
                elif _cat == "APIConnectionError":
                    last_category = "llm_connection"
                    logger.warning(
                        "[LLM] llm_connection (第 %d/%d 次尝试): %s",
                        attempt + 1, self.max_retries + 1, exc,
                    )
                elif _cat == "APIStatusError":
                    _code = getattr(exc, "status_code", None)
                    last_category = f"llm_http_{_code}" if _code is not None else "llm_unknown"
                    logger.warning(
                        "[LLM] llm_http_%s (第 %d/%d 次尝试): %s",
                        _code if _code is not None else "?", attempt + 1,
                        self.max_retries + 1, exc,
                    )
                    # 5xx / 429 可重试; 其余(4xx 等)不重试直接放弃
                    if not (_code is None or _code >= 500 or _code == 429):
                        raise LLMError(last_category, f"LLM HTTP {_code}（不重试）")
                else:
                    last_category = "llm_unknown"
                    logger.warning(
                        "[LLM] llm_unknown (第 %d/%d 次尝试) %s: %s",
                        attempt + 1, self.max_retries + 1,
                        type(exc).__name__, exc,
                    )

            if attempt < self.max_retries:
                time.sleep(min(2.0 ** attempt, 4.0))
                continue
            raise LLMError(last_category, "LLM 调用失败（重试耗尽）")
        raise LLMError(last_category, "LLM 调用失败（重试耗尽）")


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
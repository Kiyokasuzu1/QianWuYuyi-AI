"""
Phase 4.0 — R2.7.3 DeepSeekAdapter Gates（DLG-1 / DLG-2 / DLG-3 / DLG-4）

验证 DeepSeekAdapter 严格遵守 LLMAdapterProtocol 的信息边界与安全契约。
全部测试通过 MockAPIClient 注入，不调用真实 DeepSeek API。

  DLG-1 Input Boundary Gate
      - 传入 PersonalityState / EvolutionRecord / GrowthProposal 等内部类型 → raise ValueError
      - 传入合法 RenderedPrompt → 正常返回 LLMResponse（validate 通过）
      - 传入 shape 不合法的 dict → 降级为 empty LLMResponse(finish_reason="error_fallback")
      - API Key 为空时 → 降级为 empty LLMResponse

  DLG-2 Output Safety Gate
      - Mock API 返回含 forbidden 关键字（confidence / proposal_id / trait_delta）→ sanitize 后 0 命中
      - Mock API 返回含机器字段名（model / token_usage / latency_ms）→ sanitize 后 0 命中
      - Mock API 返回含 trace_id 原文 → sanitize 后 0 泄漏
      - sanitize 后 validate_llm_response_shape 通过

  DLG-3 Error Resilience Gate
      - API 超时（抛 TimeoutException）→ error_fallback，不抛异常
      - API 返回 429（rate limit）→ error_fallback
      - API 返回 500（server error）→ error_fallback
      - API 返回空 choices → error_fallback
      - API 返回 null content → error_fallback
      - 所有错误场景均返回合法 LLMResponse（validate 通过）

  DLG-4 Trace & Version Gate
      - trace_id 从 trace_context 传入 → LLMResponse.trace_id 一致
      - version 连续 3 次 generate 严格 1→2→3
      - LLMResponse.text 不含 trace_id 原文
      - LLMResponse 所有顶层字段齐全（8 字段白名单）
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional

from tests.support.offline_cognitive_loop_sim import run_offline_loop
from src.response_phase4.prompt_renderer import PromptRenderer
from src.response_phase4.deepseek_adapter import DeepSeekAdapter
from src.response_phase4.llm_response_schema import (
    LLM_RESPONSE_FIELDS,
    _forbidden_flat_hits,
    _machine_field_hits,
    validate_llm_response_shape,
)
from src.response_phase4.llm_adapter_protocol import _FORBIDDEN_INPUT_TYPE_NAMES


# ─────────────────────────────────────────────────────────
# Mock API Client（模拟 httpx.Client.post）
# ─────────────────────────────────────────────────────────
class MockAPIResponse:
    """模拟 httpx.Response。"""

    def __init__(
        self,
        *,
        status_code: int = 200,
        content: str = "你好呀~",
        finish_reason: str = "stop",
        prompt_tokens: int = 100,
        completion_tokens: int = 50,
    ) -> None:
        self.status_code = status_code
        self._content = content
        self._finish_reason = finish_reason
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.text = content[:500] if content else ""

    def json(self) -> Dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": self._content},
                    "finish_reason": self._finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._prompt_tokens + self._completion_tokens,
            },
        }


class MockAPIClient:
    """模拟 DeepSeek API 客户端（注入到 DeepSeekAdapter.api_client）。"""

    def __init__(self, *, response: Optional[MockAPIResponse] = None) -> None:
        self._response = response or MockAPIResponse()
        self.call_count = 0
        self.last_url: str = ""
        self.last_payload: Dict[str, Any] = {}
        self.last_headers: Dict[str, str] = {}

    def post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> MockAPIResponse:
        self.call_count += 1
        self.last_url = url
        self.last_payload = json or {}
        self.last_headers = headers or {}
        return self._response


class MockAPIClientError:
    """模拟 API 调用抛异常。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def post(self, url: str, *, json: Any = None, headers: Any = None, timeout: Any = None) -> Any:
        raise self._exc


class MockAPIClientSequence:
    """模拟多次调用返回不同响应（用于 version 递增测试）。"""

    def __init__(self, responses: List[MockAPIResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def post(self, url: str, *, json: Any = None, headers: Any = None, timeout: Any = None) -> MockAPIResponse:
        resp = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return resp


# ─────────────────────────────────────────────────────────
# 辅助：构建合法 RenderedPrompt
# ─────────────────────────────────────────────────────────
def _build_rp():
    """跑一次 offline loop + render，返回合法 RenderedPrompt。"""
    res = run_offline_loop(trace_id="t_dlg_demo_001", run_ts_ms=1700000020000)
    pr = PromptRenderer()
    rp = pr.build(
        prompt_context=res["after"]["prompt_context"],
        trace_id=res["after"]["runtime_trace"]["trace_id"],
    )
    return rp


# ─────────────────────────────────────────────────────────
# 模拟内部类型（用于 DLG-1 边界测试）
# 类型名必须与 _FORBIDDEN_INPUT_TYPE_NAMES 完全匹配
# ─────────────────────────────────────────────────────────
PersonalityState = type("PersonalityState", (), {})
EvolutionRecord = type("EvolutionRecord", (), {})
GrowthProposal = type("GrowthProposal", (), {})


# ─────────────────────────────────────────────────────────
# DLG-1 Input Boundary Gate
# ─────────────────────────────────────────────────────────
class TestDLG1InputBoundary(unittest.TestCase):

    def setUp(self) -> None:
        self.rp = _build_rp()
        self.mock_client = MockAPIClient(
            response=MockAPIResponse(content="嗯，说到这个～ 最近主要在和朋友聊天。")
        )
        self.adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=self.mock_client,
        )

    def test_dlg_1a_rejects_internal_types(self) -> None:
        """传入内部系统类型 → raise ValueError（fail-fast）。"""
        for fake_obj, type_name in [
            (PersonalityState(), "PersonalityState"),
            (EvolutionRecord(), "EvolutionRecord"),
            (GrowthProposal(), "GrowthProposal"),
        ]:
            with self.assertRaises(
                ValueError,
                msg=f"传入 {type_name} 应该 raise ValueError",
            ):
                self.adapter.generate(fake_obj)

    def test_dlg_1b_rejects_internal_trace_context(self) -> None:
        """trace_context 是内部类型 → raise ValueError。"""
        with self.assertRaises(ValueError):
            self.adapter.generate(self.rp, trace_context=PersonalityState())

    def test_dlg_1c_valid_rendered_prompt_returns_llm_response(self) -> None:
        """合法 RenderedPrompt → 正常返回 LLMResponse。"""
        resp = self.adapter.generate(self.rp)
        self.assertIsInstance(resp, dict)
        # 必须通过 validate
        validate_llm_response_shape(resp)
        self.assertNotEqual(resp.get("finish_reason"), "error_fallback")
        self.assertTrue(resp.get("text", "").strip() != "")

    def test_dlg_1d_invalid_shape_returns_empty(self) -> None:
        """shape 不合法的 dict → 降级为 empty LLMResponse(error_fallback)。"""
        bad_rp = {"version": 1}  # 缺大量必选字段
        resp = self.adapter.generate(bad_rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)  # empty 也要通过 validate

    def test_dlg_1e_no_api_key_returns_empty(self) -> None:
        """API Key 为空 → 降级为 empty LLMResponse(error_fallback)。"""
        adapter = DeepSeekAdapter(
            api_key="",
            api_client=self.mock_client,
        )
        resp = adapter.generate(self.rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)


# ─────────────────────────────────────────────────────────
# DLG-2 Output Safety Gate
# ─────────────────────────────────────────────────────────
class TestDLG2OutputSafety(unittest.TestCase):

    def setUp(self) -> None:
        self.rp = _build_rp()
        # trace_id 用于验证 trace_id 正文隔离
        self.trace_id = self.rp.get("trace_id", "t_dlg_trace_001")

    def _make_adapter(self, content: str) -> DeepSeekAdapter:
        mock = MockAPIClient(response=MockAPIResponse(content=content))
        return DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )

    def test_dlg_2a_forbidden_keywords_sanitized(self) -> None:
        """LLM 返回含 forbidden（confidence / proposal_id / trait_delta）→ sanitize 后 0 命中。"""
        bad_content = (
            "我的confidence提高了，proposal_id是xxx，"
            "这次trait_delta发生了变化，internal_score也涨了。"
        )
        adapter = self._make_adapter(bad_content)
        resp = adapter.generate(self.rp)
        validate_llm_response_shape(resp)  # no raise

        # forbidden 递归扫描 0 命中
        hits = _forbidden_flat_hits(resp)
        self.assertEqual(len(hits), 0, f"forbidden hits: {hits}")

    def test_dlg_2b_machine_field_names_sanitized(self) -> None:
        """LLM 返回含机器字段名（model / token_usage / latency_ms）→ sanitize 后 0 命中。"""
        bad_content = (
            "我的model是deepseek，token_usage是100，"
            "latency_ms很高，finish_reason是stop。"
        )
        adapter = self._make_adapter(bad_content)
        resp = adapter.generate(self.rp)
        validate_llm_response_shape(resp)  # no raise

        # 机器字段名 0 命中
        hits = _machine_field_hits(resp["text"])
        self.assertEqual(len(hits), 0, f"machine field hits: {hits}")

    def test_dlg_2c_trace_id_not_in_text(self) -> None:
        """LLM 返回含 trace_id 原文 → sanitize 后 0 泄漏。"""
        tid = self.trace_id
        bad_content = f"刚才的trace_id是{tid}，对吧？"
        adapter = self._make_adapter(bad_content)
        resp = adapter.generate(
            self.rp,
            trace_context={"trace_id": tid},
        )
        validate_llm_response_shape(resp)  # no raise
        self.assertNotIn(tid, resp["text"])

    def test_dlg_2d_combined_forbidden_all_sanitized(self) -> None:
        """LLM 返回同时含 forbidden + 机器字段 + trace_id → 全部 sanitize。"""
        tid = self.trace_id
        bad_content = (
            f"trace_id={tid}，confidence=0.9，proposal_id=abc，"
            f"trait_delta=0.1，model=deepseek，token_usage=200。"
        )
        adapter = self._make_adapter(bad_content)
        resp = adapter.generate(
            self.rp,
            trace_context={"trace_id": tid},
        )
        validate_llm_response_shape(resp)  # no raise
        # 全部 0 命中
        self.assertEqual(len(_forbidden_flat_hits(resp)), 0)
        self.assertEqual(len(_machine_field_hits(resp["text"])), 0)
        self.assertNotIn(tid, resp["text"])


# ─────────────────────────────────────────────────────────
# DLG-3 Error Resilience Gate
# ─────────────────────────────────────────────────────────
class TestDLG3ErrorResilience(unittest.TestCase):

    def setUp(self) -> None:
        self.rp = _build_rp()

    def test_dlg_3a_timeout_returns_error_fallback(self) -> None:
        """API 超时（抛异常）→ error_fallback，不抛异常给上层。"""
        import httpx
        mock = MockAPIClientError(httpx.TimeoutException("connection timed out"))
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)  # empty 也要通过 validate

    def test_dlg_3b_http_429_returns_error_fallback(self) -> None:
        """API 返回 429（rate limit）→ error_fallback。"""
        mock = MockAPIClient(
            response=MockAPIResponse(status_code=429, content="rate limited")
        )
        # MockAPIClient 的 post 直接返回 response，但 _call_api 会检查 status_code
        # 需要一个会触发 status_code != 200 的 mock
        # MockAPIResponse 有 status_code 属性，_call_api 会检查
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)

    def test_dlg_3c_http_500_returns_error_fallback(self) -> None:
        """API 返回 500（server error）→ error_fallback。"""
        mock = MockAPIClient(
            response=MockAPIResponse(status_code=500, content="internal server error")
        )
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)

    def test_dlg_3d_empty_choices_returns_error_fallback(self) -> None:
        """API 返回空 choices → error_fallback。"""
        class EmptyChoicesResponse(MockAPIResponse):
            def json(self) -> Dict[str, Any]:
                return {"choices": [], "usage": {}}
        mock = MockAPIClient(response=EmptyChoicesResponse())
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)

    def test_dlg_3e_null_content_returns_error_fallback(self) -> None:
        """API 返回 null content → error_fallback。"""
        class NullContentResponse(MockAPIResponse):
            def json(self) -> Dict[str, Any]:
                return {
                    "choices": [
                        {"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
                }
        mock = MockAPIClient(response=NullContentResponse())
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)

    def test_dlg_3f_connection_error_returns_error_fallback(self) -> None:
        """API 连接错误（抛 ConnectionError）→ error_fallback。"""
        mock = MockAPIClientError(ConnectionError("connection refused"))
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp)
        self.assertEqual(resp.get("finish_reason"), "error_fallback")
        validate_llm_response_shape(resp)


# ─────────────────────────────────────────────────────────
# DLG-4 Trace & Version Gate
# ─────────────────────────────────────────────────────────
class TestDLG4TraceAndVersion(unittest.TestCase):

    def setUp(self) -> None:
        self.rp = _build_rp()

    def test_dlg_4a_trace_id_consistency(self) -> None:
        """trace_id 从 trace_context 传入 → LLMResponse.trace_id 一致。"""
        tid = "t_dlg_trace_consistency_001"
        mock = MockAPIClient(
            response=MockAPIResponse(content="嗯，最近在研究 AI 绘画呀~")
        )
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp, trace_context={"trace_id": tid})
        self.assertEqual(resp["trace_id"], tid)
        validate_llm_response_shape(resp)

    def test_dlg_4b_version_monotonic(self) -> None:
        """version 连续 3 次 generate 严格 1→2→3。"""
        mock = MockAPIClientSequence([
            MockAPIResponse(content="第一句"),
            MockAPIResponse(content="第二句"),
            MockAPIResponse(content="第三句"),
        ])
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        v1 = adapter.generate(self.rp)["version"]
        v2 = adapter.generate(self.rp)["version"]
        v3 = adapter.generate(self.rp)["version"]
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)
        self.assertEqual(v3, 3)

    def test_dlg_4c_text_does_not_contain_trace_id(self) -> None:
        """LLMResponse.text 不含 trace_id 原文。"""
        tid = "t_dlg_trace_isolation_004"
        # 让 mock 返回含 trace_id 的内容，验证 sanitize 隔离
        mock = MockAPIClient(
            response=MockAPIResponse(content=f"刚才的{tid}对吧？嗯，最近还好~")
        )
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp, trace_context={"trace_id": tid})
        validate_llm_response_shape(resp)  # no raise → trace_id 已被 sanitize
        self.assertNotIn(tid, resp["text"])

    def test_dlg_4d_all_top_level_fields_present(self) -> None:
        """LLMResponse 所有 8 个顶层字段齐全。"""
        mock = MockAPIClient(
            response=MockAPIResponse(content="嗯，说到这个~ 最近还好。")
        )
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        resp = adapter.generate(self.rp)
        for field in LLM_RESPONSE_FIELDS:
            self.assertIn(field, resp, f"LLMResponse missing field: {field}")
        validate_llm_response_shape(resp)

    def test_dlg_4e_rendered_prompt_to_messages_isolation(self) -> None:
        """DeepSeekAdapter 发给 API 的 messages 不含内部术语（forbidden 0 命中）。

        验证 RenderedPrompt → chat messages 转换不泄漏内部字段名。
        """
        mock = MockAPIClient(
            response=MockAPIResponse(content="好的~")
        )
        adapter = DeepSeekAdapter(
            api_key="sk-test-fake-key-for-dlg",
            api_client=mock,
        )
        adapter.generate(self.rp)

        # 检查发给 API 的 payload
        payload = mock.last_payload
        messages = payload.get("messages") or []
        all_text = " ".join(
            str(m.get("content") or "") for m in messages
        )
        # forbidden 关键字不应出现在发给 API 的 messages 中
        for w in ("proposal_id", "approval_reason", "internal_score", "evaluator_meta", "confidence"):
            self.assertNotIn(
                w,
                all_text.lower(),
                f"forbidden keyword '{w}' 出现在发给 DeepSeek 的 messages 中",
            )
        # 机器字段名不应出现
        for w in ("latency_ms", "token_usage", "finish_reason", "trace_id", "engine_version"):
            # 用简单子串检查（API messages 是自然语言，不应出现这些词）
            self.assertNotIn(
                w,
                all_text,
                f"机器字段名 '{w}' 出现在发给 DeepSeek 的 messages 中",
            )


if __name__ == "__main__":
    unittest.main()

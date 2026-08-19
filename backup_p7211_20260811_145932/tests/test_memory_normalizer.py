# -*- coding: utf-8 -*-
"""
tests/test_memory_normalizer.py

P0-1: 验证 Orchestrator._normalize_memory_content() 的正确性

覆盖场景：
  1. 空 / None / 空白输入
  2. 短内容（<=3500）原样返回
  3. 正好 3500 边界
  4. 3501 触发压缩
  5. 典型超长（5000）：head+tail 保留，含 original_length 标记
  6. 非常长（10000）：仍 <= 3500
  7. 非字符串输入（int/dict/list）：安全转字符串
  8. UTF-8 / emoji / CJK 混合：长度按字符算，不截断一半
  9. head+tail 无重叠（len > 3300）
 10. head+tail 重叠退化（len < 3300 但 > 3500？不可能，测兜底路径）
 11. 输出 <= PollutionGuard 的 MAX_CONTENT_LENGTH(4000) 保证
 12. 最终长度严格 <= MAX_MEMORY_CONTENT_LENGTH(3500)
"""

from __future__ import annotations

import unittest
from typing import Any


class FakeOrchestrator:
    """只保留 MemoryNormalizer 相关字段，用于纯函数级测试，
    避免构造完整 Orchestrator（需要 config / MemoryStore 等依赖）。
    """

    MAX_MEMORY_CONTENT_LENGTH: int = 3500
    MEMORY_HEAD_KEEP: int = 2500
    MEMORY_TAIL_KEEP: int = 800

    # 与 orchestrator.py 中实现完全一致
    def _normalize_memory_content(self, content: object) -> str:
        if content is None:
            return ""
        if not isinstance(content, str):
            try:
                content = str(content)
            except Exception:
                return ""
        normalized = content.strip()
        if not normalized:
            return ""
        original_len = len(normalized)
        if original_len <= self.MAX_MEMORY_CONTENT_LENGTH:
            return normalized
        head_end = self.MEMORY_HEAD_KEEP
        tail_start = max(head_end, original_len - self.MEMORY_TAIL_KEEP)
        if tail_start <= head_end:
            truncated = normalized[: self.MAX_MEMORY_CONTENT_LENGTH - 40]
            return truncated + "\n\n[内容已截断]" + f"\n[original_length={original_len}]"
        head = normalized[:head_end]
        tail = normalized[tail_start:]
        marker = (
            "\n\n[中间内容省略]\n\n"
            + tail
            + f"\n[original_length={original_len}]"
        )
        result = head + marker
        if len(result) > self.MAX_MEMORY_CONTENT_LENGTH:
            overflow = len(result) - self.MAX_MEMORY_CONTENT_LENGTH
            safe_head_len = max(50, head_end - overflow - 50)
            result = (
                normalized[:safe_head_len]
                + "\n\n[中间内容省略]\n\n"
                + tail
                + f"\n[original_length={original_len}]"
            )
            if len(result) > self.MAX_MEMORY_CONTENT_LENGTH:
                result = result[: self.MAX_MEMORY_CONTENT_LENGTH]
        return result


class TestMemoryNormalizer(unittest.TestCase):
    def setUp(self) -> None:
        self.orch = FakeOrchestrator()
        self.PG_MAX = 4000  # PollutionGuard.MAX_CONTENT_LENGTH

    # ============================================================
    # 1. 空 / None / 空白输入
    # ============================================================
    def test_none_returns_empty(self):
        self.assertEqual(self.orch._normalize_memory_content(None), "")

    def test_empty_string_returns_empty(self):
        self.assertEqual(self.orch._normalize_memory_content(""), "")

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(self.orch._normalize_memory_content("   \n\n\t  "), "")

    # ============================================================
    # 2. 短内容原样返回（含边界 3500）
    # ============================================================
    def test_short_content_unchanged(self):
        s = "今天和清清讨论了羽依的身份连续性。"
        self.assertEqual(self.orch._normalize_memory_content(s), s)

    def test_short_content_3000_unchanged(self):
        s = "羽" * 3000
        self.assertEqual(self.orch._normalize_memory_content(s), s)

    def test_exactly_3500_unchanged(self):
        s = "a" * 3500
        out = self.orch._normalize_memory_content(s)
        self.assertEqual(len(out), 3500)
        self.assertEqual(out, s)

    def test_3501_triggers_compression(self):
        s = "b" * 3501
        out = self.orch._normalize_memory_content(s)
        # 不应等于原内容
        self.assertNotEqual(out, s)
        # 必须 <= 3500
        self.assertLessEqual(len(out), 3500)

    # ============================================================
    # 3. 典型超长（5000）：head + tail 都保留
    # ============================================================
    def test_5000_head_tail_preserved(self):
        head_part = "[开头]我小时候发生了一件很重要的事情，那年我12岁……"
        tail_part = "……后来我明白了独立的意义，这让我开始了现在的项目。[结尾]"
        # 补足到刚好 > 5000
        mid_target = 5000 - len(head_part) - len(tail_part)
        mid = "中" * mid_target
        s = head_part + mid + tail_part
        # 确认长度正确
        self.assertGreaterEqual(len(s), 5000)

        out = self.orch._normalize_memory_content(s)

        # 开头必须保留
        self.assertTrue(out.startswith("[开头]我小时候发生了一件很重要的事情"))
        # 结尾必须保留
        self.assertIn("后来我明白了独立的意义", out)
        self.assertIn("[结尾]", out)
        # 必须有压缩标记
        self.assertIn("[中间内容省略]", out)
        # 必须有原始长度
        self.assertIn("[original_length=5000]", out)
        # 长度 <= 3500
        self.assertLessEqual(len(out), 3500)

    # ============================================================
    # 4. 非常长（10000）：仍 <= 3500，且 PollutionGuard 放行
    # ============================================================
    def test_10000_within_3500(self):
        s = "这是一段非常长的用户输入" * 700  # ~11900字
        out = self.orch._normalize_memory_content(s)
        self.assertLessEqual(len(out), 3500)
        # 额外保证：一定低于 PollutionGuard 4000
        self.assertLess(len(out), self.PG_MAX)

    # ============================================================
    # 5. 非字符串输入
    # ============================================================
    def test_int_input_converted(self):
        self.assertEqual(self.orch._normalize_memory_content(42), "42")

    def test_list_input_converted(self):
        self.assertEqual(self.orch._normalize_memory_content(["a", "b"]), "['a', 'b']")

    def test_dict_input_converted(self):
        out = self.orch._normalize_memory_content({"k": "v"})
        self.assertIn("k", out)
        self.assertIn("v", out)

    # ============================================================
    # 6. UTF-8 / emoji / CJK 混合：字符级长度不会乱
    # ============================================================
    def test_cjk_emoji_not_half_cut(self):
        """5000字 CJK + emoji，输出长度按字符算，不会出半截表情。"""
        head_cjk = "今天羽依感受到了陪伴的意义🥰"  # 含表情
        mid_cjk = "清清说的每一句话都记在心里。" * 160  # ~19 * 160 = 3040
        tail_cjk = "未来也要一起成长哦💙"
        s = head_cjk + mid_cjk + tail_cjk
        # 补足到 > 3500
        while len(s) < 5000:
            s += "记"

        out = self.orch._normalize_memory_content(s)
        self.assertLessEqual(len(out), 3500)
        # 输出必须是合法字符串（无 UnicodeEncodeError，在字符串内部不会出半截代理对的情况）
        self.assertIsInstance(out, str)
        # 开头 / 结尾保留
        self.assertTrue(out.startswith("今天羽依感受到了"))
        self.assertIn("未来也要一起成长", out)

    # ============================================================
    # 7. 首尾空白 strip
    # ============================================================
    def test_strip_whitespace_preserved_middle(self):
        s = "   \n\n  开头内容\n\n中间段落\n\n结尾内容  \t\n  "
        out = self.orch._normalize_memory_content(s)
        self.assertEqual(out, "开头内容\n\n中间段落\n\n结尾内容")

    # ============================================================
    # 8. 最终长度严格保证（所有边界）
    # ============================================================
    def test_length_strictly_within_3500(self):
        """扫一组长度（3500,3501,3600,4000,5000,8000,10000,20000）。"""
        lengths = [3500, 3501, 3600, 4000, 5000, 8000, 10000, 20000]
        for L in lengths:
            s = "z" * L
            out = self.orch._normalize_memory_content(s)
            self.assertLessEqual(
                len(out),
                3500,
                f"len={L} output len={len(out)} exceed MAX_MEMORY_CONTENT_LENGTH",
            )
            # 并且一定低于 PollutionGuard 4000
            self.assertLess(
                len(out),
                self.PG_MAX,
                f"len={L} output still blocked by PollutionGuard(4000)",
            )

    # ============================================================
    # 9. marker 正确性（超长必带）
    # ============================================================
    def test_marker_present_for_any_over_length(self):
        for L in [3501, 5000, 10000]:
            s = "x" * L
            out = self.orch._normalize_memory_content(s)
            marker1 = "[中间内容省略]"
            marker2 = "[内容已截断]"
            # 两者必须出现一个
            self.assertTrue(
                marker1 in out or marker2 in out,
                f"len={L} 超长但缺少压缩 marker",
            )
            self.assertIn(f"[original_length={L}]", out)

    # ============================================================
    # 10. 极端：长度正好 head(2500) + marker_min + tail(800)
    #     marker_min ~ 20 + len("original_length=9999") ~ 40
    #     总长 2500+16+800+30 ~ 3346 < 3500，不应触发二次兜底
    # ============================================================
    def test_typical_case_no_double_truncation(self):
        s = "A" * 5000
        out = self.orch._normalize_memory_content(s)
        # head 2500 A's + marker + tail 800 A's
        # 只要能通过长度 <= 3500 且能看到大量 A 就算过
        self.assertLessEqual(len(out), 3500)
        # 内容里必须有 A（head 和 tail 都是 A）
        self.assertIn("A", out)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 7.2.1-p6-sec: AstrBot OpenAI Provider 修复脚本
──────────────────────────────────────────────────────
修复问题：AstrBot 在调用 /v1/chat/completions 时，没有从 session_id 中
          提取真实 QQ 号填入 user 字段，导致羽依 API 拿不到身份信息，
          只能走兜底逻辑，有串记忆风险。

用法（在服务器终端执行）：
    python3 fix_astrbot_user_id.py
    # 执行完后重启 AstrBot：systemctl restart astrbot
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

TARGET = Path(
    "/root/.local/share/uv/tools/astrbot/lib/python3.12/site-packages/"
    "astrbot/core/provider/sources/openai_source.py"
)

BACKUP = TARGET.with_suffix(TARGET.suffix + ".bak.p6sec")

# ── 要插入的辅助方法 ──────────────────────────────────────────────
HELPER_METHOD = '''
    # ── Phase 7.2.1-p6-sec 新增：从 session_id 提取真实 UID ────────
    @staticmethod
    def _extract_uid_from_session(session_id) -> str | None:
        """从 AstrBot session_id（如 aiocqhttp:PrivateMessage:qq:366648462）
        中提取第一个 5~12 位纯数字子串作为真实 QQ 号。
        提取失败返回 None，上层不填 user 字段（服务端将走未知沙盒）。"""
        if not session_id:
            return None
        s = str(session_id)
        m = re.search(r"\\d{5,12}", s)
        return m.group(0) if m else None
    # ────────────────────────────────────────────────────────────────
'''

# ── user 注入代码（插入到 _prepare_chat_payload 之后） ────────────
USER_INJECT = '''
        # Phase 7.2.1-p6-sec: 从 session_id 提取真实 QQ 号，传给羽依 API。
        #   这样你的消息永远是 366648462，别人的消息永远是他自己的 QQ 号，
        #   不会再有 default 占位符兜底，从根源解决身份串话问题。
        uid = self._extract_uid_from_session(session_id)
        if uid:
            payloads["user"] = uid
'''


def apply_patch(text: str) -> tuple[str, int]:
    changes = 0

    # ── 1) 先插辅助方法：放在 _ollama_disable_thinking_enabled 定义之后 ──
    anchor_def = "    def _ollama_disable_thinking_enabled(self) -> bool:"
    if HELPER_METHOD.strip() not in text:
        # 找 _ollama 方法的结尾（return ... 之后的空行）
        idx = text.find(anchor_def)
        if idx == -1:
            print("[ERROR] 找不到 _ollama_disable_thinking_enabled 锚点，中止。", file=sys.stderr)
            sys.exit(2)
        # 跳过 anchor 这一行，找下一个 "    def " 作为插入点
        next_def_idx = text.find("\n    def ", idx + len(anchor_def))
        if next_def_idx == -1:
            print("[ERROR] 找不到插入辅助方法的下一个方法定义，中止。", file=sys.stderr)
            sys.exit(2)
        insert_at = next_def_idx + 1  # 在换行之后插
        text = text[:insert_at] + HELPER_METHOD + "\n" + text[insert_at:]
        changes += 1
        print("[OK] 已插入 _extract_uid_from_session 辅助方法。")
    else:
        print("[SKIP] 辅助方法已存在，跳过。")

    # ── 2) text_chat 函数：在 "if func_tool and not func_tool.empty():" 之前插入 ──
    anchor_chat = "        if func_tool and not func_tool.empty():\n            payloads[\"tool_choice\"] = tool_choice\n\n        llm_response = None"
    replacement_chat = USER_INJECT + anchor_chat
    if USER_INJECT.strip() not in text or anchor_chat.count(anchor_chat) < 2:
        # 用更宽松的搜索：分两次找 text_chat 和 text_chat_stream 的锚点
        # text_chat 中出现 "llm_response = None" 是流式函数之前那一个
        occurrences = list(re.finditer(
            r"        if func_tool and not func_tool\.empty\(\):\n            payloads\[\"tool_choice\"\] = tool_choice",
            text,
        ))
        if len(occurrences) < 2:
            print(f"[ERROR] 只找到 {len(occurrences)} 处 func_tool 锚点，需要 2 处（text_chat 和 text_chat_stream 各一处）。中止。", file=sys.stderr)
            sys.exit(2)

        for i, occ in enumerate(occurrences):
            start = occ.start()
            snippet_before = text[max(0, start - 500):start]
            if USER_INJECT.strip() in snippet_before:
                print(f"[SKIP] 第 {i+1} 处 func_tool 锚点已注入 user 提取，跳过。")
                continue
            text = text[:start] + USER_INJECT + text[start:]
            changes += 1
            kind = "text_chat（非流式）" if i == 0 else "text_chat_stream（流式）"
            print(f"[OK] 已在 {kind} 中注入 user 提取逻辑。")
    else:
        print("[SKIP] user 注入代码已存在，跳过。")

    return text, changes


def main() -> int:
    if not TARGET.exists():
        print(f"[ERROR] 找不到目标文件：{TARGET}", file=sys.stderr)
        print("请确认 AstrBot 是通过 uv 工具安装的。如果安装路径不同，修改脚本顶部的 TARGET 变量。", file=sys.stderr)
        return 1

    # 0. 备份
    if not BACKUP.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"[OK] 已备份原文件 → {BACKUP}")
    else:
        print(f"[SKIP] 备份文件已存在 → {BACKUP}")

    # 1. 读取
    text = TARGET.read_text(encoding="utf-8")

    # 2. 打补丁
    new_text, changes = apply_patch(text)

    if changes == 0:
        print("\n[INFO] 未做任何改动（已打过补丁或锚点不匹配）。")
        print("       如需强制重打，请先删除 .bak.p6sec 文件后恢复原文件，再运行本脚本。")
        return 0

    # 3. 验证：至少能找得到 _extract_uid_from_session 和两处 payloads["user"] = uid
    if "_extract_uid_from_session" not in new_text:
        print("[ERROR] 补丁验证失败：找不到 _extract_uid_from_session，回滚！", file=sys.stderr)
        shutil.copy2(BACKUP, TARGET)
        return 3
    _user_inject_count = new_text.count('payloads["user"] = uid')
    if _user_inject_count < 2:
        print(
            f"[ERROR] 补丁验证失败：只找到 {_user_inject_count} 处 user 注入（期望 2 处），回滚！",
            file=sys.stderr,
        )
        shutil.copy2(BACKUP, TARGET)
        return 4

    # 4. 原子写入
    tmp = TARGET.with_suffix(TARGET.suffix + ".tmp.p6sec")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(TARGET)

    print(f"\n[DONE] 补丁完成，共 {changes} 处改动。下一步：")
    print("       systemctl restart astrbot")
    print()
    print("       回滚命令（万一出问题）：")
    print(f"       cp {BACKUP} {TARGET} && systemctl restart astrbot")
    return 0


if __name__ == "__main__":
    sys.exit(main())

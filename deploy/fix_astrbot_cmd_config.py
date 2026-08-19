#!/usr/bin/env python3
"""Phase 7.2.1-p6 修复 AstrBot cmd_config.json 多重配置损坏：
   - UTF-8 BOM 去头（AstrBot 自己保存时会加，导致 json.load 崩）
   - provider_sources 里所有 api_base 首尾的反引号去掉
   - 源「羽依」端口 5000 → 8000，enable=true
   - providers 数组如果为空，补回「羽依/yuyi」模型实体
   - 最后 bytes 层原子写回，不加 BOM
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

PATH = "/root/data/cmd_config.json"
BOM = b"\xef\xbb\xbf"


def main() -> int:
    if not os.path.exists(PATH):
        print(f"[FATAL] 文件不存在：{PATH}", file=sys.stderr)
        return 1

    # ── 0. 备份 ──────────────────────────────────────────────────
    backup = f"{PATH}.backup.cmdcfg-p6.{int(time.time())}"
    shutil.copy2(PATH, backup)
    print(f"[0] 备份 → {backup}")

    # ── 1. bytes 层去 BOM（不管有没有，走一遍）──────────────────
    with open(PATH, "rb") as f:
        raw = f.read()
    if raw.startswith(BOM):
        raw = raw[3:]
        print(f"[1] 去掉 UTF-8 BOM：{len(raw) + 3}B → {len(raw)}B")
    else:
        print("[1] 文件无 BOM，保持原样")

    # ── 2. 解析 JSON（utf-8-sig 兜底）────────────────────────────
    d = json.loads(raw.decode("utf-8-sig"))
    print("[2] JSON 解析 OK")

    # ── 3. 修 provider_sources ───────────────────────────────────
    need_save = False
    sources = d.get("provider_sources") or []
    for idx, s in enumerate(sources):
        sid = s.get("id", f"<#{idx}>")
        ab = s.get("api_base", "") or ""
        cleaned = ab.strip().strip("`").strip()
        if cleaned != ab:
            s["api_base"] = cleaned
            print(f"  ✓ 源[{idx}] {sid!r} api_base 去反引号/空白：{ab!r} → {cleaned!r}")
            need_save = True

        if s.get("id") == "羽依":
            api_base_now = s.get("api_base", "") or ""
            if "127.0.0.1:5000" in api_base_now:
                s["api_base"] = api_base_now.replace("127.0.0.1:5000", "127.0.0.1:8000")
                print(f"  ✓ 源[{idx}] '羽依' 端口 5000→8000：{s['api_base']}")
                need_save = True
            if not s.get("enable", True):
                s["enable"] = True
                print(f"  ✓ 源[{idx}] '羽依' enable 补为 True")
                need_save = True

    # ── 4. 修 providers 数组 ─────────────────────────────────────
    provs = d.get("providers")
    if not isinstance(provs, list):
        provs = []
    has_yuyi_provider = any(isinstance(p, dict) and "羽依" in (p.get("id") or "") for p in provs)
    if not has_yuyi_provider:
        provs.insert(0, {
            "id": "羽依/yuyi",
            "provider_source_id": "羽依",
            "model": "yuyi",
            "type": "text_image",
            "enable": True,
            "max_context_length": -1,
            "temperature": 0.7,
            "max_tokens": 2048,
            "top_p": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
        })
        d["providers"] = provs
        need_save = True
        print("  ✓ providers 数组补回：羽依/yuyi（绑定 provider_source_id='羽依'，model='yuyi'）")

    # 顺手校验 default_provider_id 对不对
    settings = d.get("provider_settings") or {}
    default_id = settings.get("default_provider_id")
    prov_ids_list = [p.get("id") for p in provs if isinstance(p, dict)]
    if default_id and default_id not in prov_ids_list:
        print(f"  ⚠ default_provider_id={default_id!r} 不在 providers 列表：{prov_ids_list}")
    else:
        print(f"  ✓ default_provider_id={default_id!r} ✔ 存在于 providers")

    # ── 5. 原子写回（bytes 层，不加 BOM）────────────────────────
    if need_save:
        payload_bytes = json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8")
        tmp_path = PATH + ".tmp.cmdcfg-p6"
        with open(tmp_path, "wb") as f:
            f.write(payload_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, PATH)
        print(f"[5] 保存变更：{len(payload_bytes)}B（无 BOM UTF-8）")
    else:
        print("[5] 配置无需修改，跳过写回")

    # ── 6. 最终校验 ──────────────────────────────────────────────
    with open(PATH, "rb") as f:
        final_raw = f.read()
    has_bom_now = final_raw[:3] == BOM
    print("\n==== 最终校验 ====")
    bom_txt = "❌ 仍有 BOM！" if has_bom_now else "✅ 无 BOM"
    print(f"  文件 BOM？ {bom_txt}")
    try:
        d2 = json.loads(final_raw.decode("utf-8"))
        print("  JSON 直接解析：OK（无需 sig）")
    except Exception as e:
        print(f"  JSON 解析失败：{e}")
        d2 = json.loads(final_raw.decode("utf-8-sig"))
        print("  → 用 utf-8-sig 兜底解析成功（检查是否有异常字符）")

    for s in d2.get("provider_sources", []):
        ab = s.get("api_base", "") or ""
        tick_ok = "`" not in ab
        yuyi_port_ok = (s.get("id") != "羽依") or (":8000" in ab) or ("localhost" in ab)
        tag = "✅" if (tick_ok and yuyi_port_ok) else "❌"
        print(f"  {tag} 源 {s.get('id')!r:15s}：api_base={ab!r}")

    final_prov_ids = [p.get("id") for p in d2.get("providers", []) if isinstance(p, dict)]
    final_default = (d2.get("provider_settings") or {}).get("default_provider_id")
    print(f"  providers ids         = {final_prov_ids}")
    print(f"  default_provider_id   = {final_default!r}")
    if final_default in final_prov_ids:
        print("  ✅ 全部配置 OK！下一步：systemctl restart astrbot")
    else:
        print("  ❌ default_provider_id 仍不在 providers 里，需要手动检查。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

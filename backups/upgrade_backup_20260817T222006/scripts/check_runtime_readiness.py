# -*- coding: utf-8 -*-
"""
Phase 3.6.3: Runtime 启动状态检查脚本。

不修改任何核心代码，只负责：
1. 检查环境配置（.env / API Key / config.yaml）
2. 检查数据目录（self_model / growth / memory）
3. 检查 PersonalityResolver 状态（trait 值 / growth_records）
4. 检查 SelfModel 历史记录数
5. 输出完整启动状态报告

用法：
    python scripts/check_runtime_readiness.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _ok(msg): print(f"  ✅ {msg}")
def _warn(msg): print(f"  ⚠️  {msg}")
def _fail(msg): print(f"  ❌ {msg}")
def _info(msg): print(f"  ℹ️  {msg}")


def check_env() -> Dict[str, Any]:
    """检查环境配置。"""
    print("\n" + "=" * 60)
    print("1. 环境配置检查")
    print("=" * 60)

    result = {}

    # .env 文件
    env_file = _REPO / ".env"
    if env_file.exists():
        _ok(f".env 文件存在: {env_file}")
        result["env_file"] = True
    else:
        _warn(f".env 文件不存在（将无法自动加载 DEEPSEEK_API_KEY）")
        result["env_file"] = False

    # API Key（不输出值）
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if deepseek_key:
        _ok("DEEPSEEK_API_KEY 环境变量已设置")
        result["api_key"] = True
    elif openai_key:
        _ok("OPENAI_API_KEY 环境变量已设置（备用）")
        result["api_key"] = True
    else:
        _fail("未检测到 DEEPSEEK_API_KEY 或 OPENAI_API_KEY 环境变量")
        _info("请创建 .env 文件并设置 DEEPSEEK_API_KEY=sk-xxxx")
        result["api_key"] = False

    # Mock 模式
    mock = os.getenv("YUYI_LLM_MOCK", "")
    if mock.lower() in ("1", "true", "yes"):
        _warn("YUYI_LLM_MOCK=1，系统将使用 mock 模式（不调用真实 LLM）")
        result["mock_mode"] = True
    else:
        result["mock_mode"] = False

    # config.yaml
    config_file = _REPO / "config.yaml"
    if config_file.exists():
        _ok(f"config.yaml 存在")
        result["config_file"] = True
    else:
        _fail("config.yaml 不存在")
        result["config_file"] = False

    # adapters_enabled
    try:
        import yaml
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        runtime_cfg = cfg.get("runtime", {})
        adapters_enabled = runtime_cfg.get("adapters_enabled", False)
        if adapters_enabled:
            _ok("config.yaml: runtime.adapters_enabled = true")
        else:
            _warn("config.yaml: runtime.adapters_enabled 未设置（默认 false）")
            _info("Phase 3.5.2 的 TraitRebuilder / GrowthHistoryView 不会执行")
            _info("需要在 config.yaml 的 runtime 段添加: adapters_enabled: true")
        result["adapters_enabled"] = adapters_enabled
    except Exception as e:
        _fail(f"读取 config.yaml 失败: {e}")
        result["adapters_enabled"] = False

    return result


def check_data_dirs() -> Dict[str, Any]:
    """检查数据目录。"""
    print("\n" + "=" * 60)
    print("2. 数据目录检查")
    print("=" * 60)

    result = {}
    dirs = {
        "self_model": _REPO / "data" / "self_model",
        "growth": _REPO / "data" / "growth",
        "memory": _REPO / "data",
        "chroma_db": _REPO / "data" / "chroma_db",
        "runtime_context": _REPO / "data" / "runtime_context",
    }

    for name, path in dirs.items():
        if path.exists():
            files = list(path.iterdir()) if path.is_dir() else []
            _ok(f"{name}: {path} ({len(files)} 个文件)")
            result[name] = {"exists": True, "file_count": len(files)}
        else:
            _warn(f"{name}: {path} 不存在")
            result[name] = {"exists": False, "file_count": 0}

    # 检查 self_model 下的具体文件
    sm_dir = dirs["self_model"]
    if sm_dir.exists():
        for fname in ["history.jsonl", "beliefs.jsonl", "reflection.jsonl", "meta.json"]:
            fpath = sm_dir / fname
            if fpath.exists():
                size = fpath.stat().st_size
                _ok(f"  self_model/{fname}: {size} bytes")
            else:
                _info(f"  self_model/{fname}: 不存在")

    return result


def check_personality() -> Dict[str, Any]:
    """检查 PersonalityResolver 和 SelfModel 状态。"""
    print("\n" + "=" * 60)
    print("3. 人格系统状态")
    print("=" * 60)

    result = {}

    try:
        from src.personality.personality_resolver import PersonalityResolver
        from src.personality.personality_profile import PersonalityProfile

        resolver = PersonalityResolver()
        base = PersonalityProfile.get_base()

        _info("BASE 人格值:")
        for k, v in sorted(base.items()):
            print(f"     {k:<24s}: {v:.4f}")

        # resolve 一次
        vec = resolver.resolve()
        data = dict(vec.get_all() or {}) if hasattr(vec, "get_all") else {}

        _info("resolve() 后的 PersonalityVector（未注入历史）:")
        for k in sorted(base.keys()):
            v = data.get(k, "?")
            try:
                print(f"     {k:<24s}: {float(v):.4f}")
            except (TypeError, ValueError):
                print(f"     {k:<24s}: {v}")

        result["base_traits"] = base
        result["resolved_traits"] = data
        _ok("PersonalityResolver 初始化成功")

    except Exception as e:
        _fail(f"PersonalityResolver 初始化失败: {e}")
        result["error"] = str(e)

    return result


def check_self_model() -> Dict[str, Any]:
    """检查 SelfModel 历史数据。"""
    print("\n" + "=" * 60)
    print("4. SelfModel 历史数据")
    print("=" * 60)

    result = {}

    try:
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.personality.self_model_persistence import SelfModelPersistence

        adapter = SelfModelAdapter(actor="readiness_check")
        persistence = SelfModelPersistence("data/self_model")
        adapter.attach_persistence(persistence)
        counts = adapter.load_state()

        _info(f"load_state 结果: {counts}")

        history = adapter.get_history()
        events = history.all() if hasattr(history, "all") else []
        _ok(f"SelfHistory 事件数: {len(events)}")

        if events:
            # 统计 affected_traits
            total_deltas = {}
            for event in events:
                affected = getattr(event, "affected_traits", None)
                if not affected and isinstance(event, dict):
                    affected = event.get("affected_traits")
                if isinstance(affected, dict):
                    for trait, delta in affected.items():
                        total_deltas[trait] = total_deltas.get(trait, 0.0) + float(delta)

            if total_deltas:
                _info("历史累计 delta:")
                for trait, delta in sorted(total_deltas.items()):
                    print(f"     {trait:<24s}: {delta:+.4f}")

            # TraitRebuilder 重建
            try:
                from src.runtime.trait_rebuilder import TraitRebuilder
                rb = TraitRebuilder()
                values = rb.rebuild(adapter)
                if values:
                    _ok(f"TraitRebuilder 重建值:")
                    for k, v in sorted(values.items()):
                        print(f"     {k:<24s}: {v:.4f}")
                else:
                    _info("TraitRebuilder 返回空（无 history delta）")
                result["rebuilt_values"] = values
            except Exception as e:
                _warn(f"TraitRebuilder 执行失败: {e}")

            # GrowthHistoryView
            try:
                from src.runtime.growth_history_view import GrowthHistoryView
                view = GrowthHistoryView(adapter)
                count = view.count()
                _ok(f"GrowthHistoryView 记录数: {count}")
                if count > 0:
                    latest = view.latest()
                    if latest:
                        _info(f"最新记录: {latest.get('meaning', '')[:60]}")
                result["growth_view_count"] = count
            except Exception as e:
                _warn(f"GrowthHistoryView 执行失败: {e}")

        result["history_count"] = len(events)
        result["load_counts"] = counts

    except Exception as e:
        _fail(f"SelfModel 加载失败: {e}")
        result["error"] = str(e)

    return result


def check_entry_points() -> Dict[str, Any]:
    """检查启动入口可用性。"""
    print("\n" + "=" * 60)
    print("5. 启动入口检查")
    print("=" * 60)

    result = {}

    entries = {
        "main.py (CLI 终端)": _REPO / "main.py",
        "api_server.py (API 服务)": _REPO / "api_server.py",
        "yuyi.py (独立 legacy)": _REPO / "yuyi.py",
    }

    for name, path in entries.items():
        if path.exists():
            _ok(f"{name}: 存在")
            result[name] = True
        else:
            _warn(f"{name}: 不存在")
            result[name] = False

    # main.py 调试代码检测
    main_py = _REPO / "main.py"
    if main_py.exists():
        content = main_py.read_text(encoding="utf-8")
        if "🔴" in content or "🧪 测试 GrowthPipeline" in content:
            _warn("main.py 包含调试代码（🔴 print / GrowthPipeline 测试），启动时会先执行测试")
            result["main_py_has_debug"] = True
        else:
            result["main_py_has_debug"] = False

    return result


def main() -> int:
    print("╔" + "═" * 58 + "╗")
    print("║  Phase 3.6.3 Runtime Readiness Check                ║")
    print("╚" + "═" * 58 + "╝")

    env = check_env()
    data = check_data_dirs()
    personality = check_personality()
    self_model = check_self_model()
    entries = check_entry_points()

    # 总结
    print("\n" + "=" * 60)
    print("总结")
    print("=" * 60)

    blockers = []
    if not env.get("api_key"):
        blockers.append("缺少 DEEPSEEK_API_KEY")
    if not env.get("adapters_enabled"):
        blockers.append("config.yaml 未设 adapters_enabled: true（P0-P2.5 不执行）")
    if entries.get("main_py_has_debug"):
        blockers.append("main.py 含调试代码（建议清理）")

    if blockers:
        print("\n🚫 启动阻断项:")
        for b in blockers:
            print(f"   ❌ {b}")
        print("\n请解决以上问题后再启动真实聊天测试。")
    else:
        print("\n✅ 所有检查通过，可以启动真实聊天测试。")
        print("   推荐命令: python main.py")

    return 0 if not blockers else 1


if __name__ == "__main__":
    sys.exit(main())

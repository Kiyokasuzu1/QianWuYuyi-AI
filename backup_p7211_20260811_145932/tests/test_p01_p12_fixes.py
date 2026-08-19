# -*- coding: utf-8 -*-
"""
P0-1 + P1-2 修复单元测试

P0-1: _ok() 函数加 _to_json_safe 预处理,防止 provider 返回的 dataclass /
      自定义对象导致 jsonify → TypeError → 500。

P1-2: 清理硬编码"浅雾羽依",后端无数据时显示空状态而非假人格名。
"""
import json
import dataclasses
from datetime import datetime
from enum import Enum
from typing import Any, Dict

import pytest


# ============================================================
# P0-1: _to_json_safe + _ok 修复测试
# ============================================================

class _FakeEnum(Enum):
    ACTIVE = "active"
    OFFLINE = "offline"


@dataclasses.dataclass
class _FakePersonalityVector:
    """模拟 PersonalityResolver.resolve() 返回的 dataclass。"""
    openness: float = 0.72
    label: str = "温暖"
    status: _FakeEnum = _FakeEnum.ACTIVE
    created_at: datetime = dataclasses.field(
        default_factory=lambda: datetime(2026, 8, 7, 12, 0, 0)
    )


class _FakeCustomObj:
    """模拟不可 JSON 序列化的自定义对象。"""
    def __init__(self):
        self.secret = 42
        self.name = "test"


class TestToJsonSafe:
    """测试 _to_json_safe 能正确处理各种不可序列化类型。"""

    def test_dict_with_dataclass(self):
        """dataclass 实例应被递归转换为 dict。"""
        from src.control.api.routes import _to_json_safe

        data = {
            "available": True,
            "current": _FakePersonalityVector(),
        }
        result = _to_json_safe(data)
        assert isinstance(result, dict)
        assert result["available"] is True
        assert isinstance(result["current"], dict)
        assert result["current"]["openness"] == 0.72
        assert result["current"]["label"] == "温暖"

    def test_dict_with_enum(self):
        """Enum 应被转换为 .value。"""
        from src.control.api.routes import _to_json_safe

        data = {"status": _FakeEnum.ACTIVE}
        result = _to_json_safe(data)
        assert result["status"] == "active"

    def test_dict_with_datetime(self):
        """datetime 应被转换为 ISO 字符串。"""
        from src.control.api.routes import _to_json_safe

        dt = datetime(2026, 8, 7, 12, 0, 0)
        data = {"created_at": dt}
        result = _to_json_safe(data)
        assert isinstance(result["created_at"], str)
        assert "2026-08-07" in result["created_at"]

    def test_dict_with_custom_obj(self):
        """自定义对象应通过 __dict__ 转换。"""
        from src.control.api.routes import _to_json_safe

        data = {"obj": _FakeCustomObj()}
        result = _to_json_safe(data)
        assert isinstance(result["obj"], dict)
        assert result["obj"]["secret"] == 42
        assert result["obj"]["name"] == "test"

    def test_nested_structure(self):
        """嵌套结构 (dict in list in dict) 应递归处理。"""
        from src.control.api.routes import _to_json_safe

        data = {
            "personality": {
                "available": True,
                "current": _FakePersonalityVector(),
            },
            "memories": [
                {"id": 1, "vec": _FakePersonalityVector()},
                {"id": 2, "status": _FakeEnum.OFFLINE},
            ],
        }
        result = _to_json_safe(data)
        # 整个结构可以 JSON 序列化
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert parsed["personality"]["current"]["openness"] == 0.72
        assert parsed["memories"][0]["vec"]["label"] == "温暖"
        assert parsed["memories"][1]["status"] == "offline"

    def test_none_and_primitives(self):
        """None 和基础类型应原样返回。"""
        from src.control.api.routes import _to_json_safe

        assert _to_json_safe(None) is None
        assert _to_json_safe(True) is True
        assert _to_json_safe(42) == 42
        assert _to_json_safe("hello") == "hello"
        assert _to_json_safe(3.14) == 3.14

    def test_json_serializable_after_safe(self):
        """模拟 _ok() 场景: _to_json_safe 后的结果可以 json.dumps。"""
        from src.control.api.routes import _to_json_safe

        # 模拟 api_runtime_overview 的 data
        data: Dict[str, Any] = {
            "runtime": {"online": True, "initialized": True},
            "personality": {
                "available": True,
                "current": _FakePersonalityVector(),  # 不可序列化!
                "state": None,
                "self_model": None,
            },
            "memory": {"available": False},
            "emotion": None,
            "selfmodel": None,
        }
        # 修复前: json.dumps(data) 会抛 TypeError
        with pytest.raises(TypeError):
            json.dumps(data)

        # 修复后: _to_json_safe 后可以正常序列化
        safe_data = _to_json_safe(data)
        json_str = json.dumps(safe_data)
        parsed = json.loads(json_str)
        assert parsed["personality"]["current"]["openness"] == 0.72


# ============================================================
# P1-2: 硬编码"浅雾羽依"清理测试
# ============================================================

class TestNoHardcodedIdentityName:
    """验证关键文件中不再有硬编码的"浅雾羽依"作为 fallback 值。"""

    def test_life_snapshot_default_empty(self):
        """DEFAULT_IDENTITY_NAME 应为空字符串,不是"浅雾羽依"。"""
        from yuyi_desktop.core.life_snapshot import DEFAULT_IDENTITY_NAME
        assert DEFAULT_IDENTITY_NAME == ""

    def test_self_model_provider_fallback_empty(self):
        """SelfModelProvider.get_identity() fallback 时 identity_name 应为空。"""
        from src.admin.self_model_provider import SelfModelProvider
        provider = SelfModelProvider.__new__(SelfModelProvider)
        provider._bridge = None
        provider._bridge_error = "test"
        result = provider.get_identity()
        assert result["identity_name"] == ""
        assert result["available"] is False

    def test_selfmodel_dashboard_provider_empty_identity(self):
        """SelfModelDashboardProvider._empty_identity 返回空 identity_name。"""
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        provider = SelfModelDashboardProvider.__new__(SelfModelDashboardProvider)
        result = provider._empty_identity(reason="test")
        assert result["identity_name"] == ""

    def test_selfmodel_dashboard_provider_empty_identity_state(self):
        """SelfModelDashboardProvider._empty_identity_state 返回空 identity_name。"""
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        provider = SelfModelDashboardProvider.__new__(SelfModelDashboardProvider)
        result = provider._empty_identity_state(reason="test")
        assert result["identity_name"] == ""


class TestNoHardcodedInSource:
    """通过源码扫描验证关键文件中没有硬编码的"浅雾羽依"作为值。"""

    @pytest.mark.parametrize("file_path,exclude_lines", [
        # yuyi_desktop: 只允许注释/文档字符串中出现,不允许作为值
        ("yuyi_desktop/core/life_snapshot.py", []),
        ("yuyi_desktop/ui/widgets/life_cards.py", []),
        ("yuyi_desktop/ui/widgets/personality_widget.py", []),
        ("yuyi_desktop/services/personality_service.py", []),
        ("src/admin/self_model_provider.py", []),
        ("src/admin/selfmodel_dashboard_provider.py", []),
        ("src/admin/api/routes.py", []),
    ])
    def test_no_hardcoded_yuyi_name_as_value(self, file_path, exclude_lines):
        """文件中不应出现 "浅雾羽依" 作为字符串值 (允许在注释/文档中提及)。"""
        import os
        full_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            file_path,
        )
        if not os.path.exists(full_path):
            pytest.skip(f"File not found: {full_path}")

        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        violations = []
        for i, line in enumerate(lines, 1):
            if i in exclude_lines:
                continue
            # 跳过注释行
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # 检查是否包含 "浅雾羽依" 作为字符串值
            if "浅雾羽依" in line:
                # 允许在 docstring 中的描述性文本
                if '"""' in line or "'''" in line:
                    continue
                violations.append(f"  L{i}: {line.rstrip()}")

        assert not violations, (
            f"Found hardcoded '浅雾羽依' in {file_path}:\n"
            + "\n".join(violations)
        )

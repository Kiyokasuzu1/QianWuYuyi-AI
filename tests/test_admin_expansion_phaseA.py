"""
Phase A 扩展单元测试

验证：
1. module/reload 路由真正执行重载（不只是记录审计）
2. growth proposal approve/reject 端点正常工作
3. handleProposal 前端逻辑接入 API（通过路由测试间接验证）
"""

import sys
import os
from unittest.mock import MagicMock, patch

# Mock 外部依赖
class _PackageMock(MagicMock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__path__ = []
        self.__all__ = []


def _install_mock(name):
    if name not in sys.modules:
        sys.modules[name] = _PackageMock()


for _mod in (
    "openai", "yaml", "websockets", "PIL", "pytesseract",
    "chromadb", "chromadb.utils", "chromadb.utils.embedding_functions",
    "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
    "psutil",
):
    _install_mock(_mod)

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)


def _make_app():
    """构造一个 Flask test app 用于路由测试"""
    from flask import Flask
    from src.admin.api.routes import admin_bp, init_admin

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    return app


def _init_admin_with_mocks():
    """初始化 admin 模块（注入 mock orchestrator 和 module_loader）"""
    from src.admin.api import routes as routes_module

    # Mock orchestrator
    mock_orch = MagicMock()
    mock_orch.screen_context_manager = None
    mock_orch.control_manager = None
    mock_orch.config = {}

    # Mock module_loader
    mock_loader = MagicMock()
    mock_info = MagicMock()
    mock_info.display = "TestModule"
    mock_info.reload_mode.value = "hot"
    mock_loader.get_module.return_value = mock_info

    routes_module._orchestrator = mock_orch
    routes_module._module_loader = mock_loader


# ============================================================
# 1. module/reload 真正执行重载
# ============================================================

def test_module_reload_actually_reloads_screen():
    """reload screen 模块应重新初始化 screen_context_manager"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module

    # 模拟 config.yaml 包含 screen.enabled: true
    mock_config = {
        "screen": {"enabled": True, "capture_timeout": 15},
        "control": {"enabled": False},
    }

    with patch("builtins.open", MagicMock()):
        with patch("yaml.safe_load", return_value=mock_config):
            with patch("src.screen.screen_context.ScreenContextManager") as MockScreen:
                # 先确保 orchestrator 的 screen 是 None
                routes_module._orchestrator.screen_context_manager = None

                response = client.post("/admin/api/module/screen/reload")

                assert response.status_code == 200
                data = response.get_json()
                assert data["success"] is True
                # 应该实例化了 ScreenContextManager
                assert MockScreen.called
                # 应该赋值给 orchestrator
                assert routes_module._orchestrator.screen_context_manager is not None


def test_module_reload_disables_screen_when_config_disabled():
    """当 config 中 screen.enabled=False 时，reload 应将 manager 设为 None"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module

    mock_config = {
        "screen": {"enabled": False},
        "control": {"enabled": False},
    }

    with patch("builtins.open", MagicMock()):
        with patch("yaml.safe_load", return_value=mock_config):
            # 先有一个 mock screen manager
            routes_module._orchestrator.screen_context_manager = MagicMock()

            response = client.post("/admin/api/module/screen/reload")

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            # manager 应被设为 None（禁用）
            assert routes_module._orchestrator.screen_context_manager is None


def test_module_reload_normal_module_returns_success():
    """reload 普通模块应返回成功（配置已重载提示）"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    mock_config = {}

    with patch("builtins.open", MagicMock()):
        with patch("yaml.safe_load", return_value=mock_config):
            response = client.post("/admin/api/module/memory/reload")

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True
            assert "memory" in data["module"]


def test_module_reload_updates_orchestrator_config():
    """reload 应更新 orchestrator.config 引用"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module

    mock_config = {"new_key": "new_value"}

    with patch("builtins.open", MagicMock()):
        with patch("yaml.safe_load", return_value=mock_config):
            response = client.post("/admin/api/module/memory/reload")

            assert response.status_code == 200
            assert routes_module._orchestrator.config == mock_config


# ============================================================
# 2. growth proposal approve/reject 端点
# ============================================================

def test_proposal_approve_with_no_engine_returns_audit_success():
    """没有 growth_engine 时，approve 应记录审计并返回成功"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module
    routes_module._orchestrator.growth_engine = None

    response = client.post(
        "/admin/api/cognitive/proposal/test-001/approve",
        json={"user_id": "user1", "reason": "同意"}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["proposal_id"] == "test-001"
    assert data["action"] == "approve"


def test_proposal_reject_with_no_engine_returns_audit_success():
    """没有 growth_engine 时，reject 应记录审计并返回成功"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module
    routes_module._orchestrator.growth_engine = None

    response = client.post(
        "/admin/api/cognitive/proposal/test-002/reject",
        json={"reason": "不需要"}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["proposal_id"] == "test-002"
    assert data["action"] == "reject"


def test_proposal_approve_calls_engine_approve_growth():
    """有 growth_engine 时，approve 应调用 engine.approve_growth"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module

    mock_engine = MagicMock()
    mock_engine.approve_growth.return_value = True
    routes_module._orchestrator.growth_engine = mock_engine

    response = client.post(
        "/admin/api/cognitive/proposal/prop-123/approve",
        json={"user_id": "user1", "reason": "同意"}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    mock_engine.approve_growth.assert_called_once_with("prop-123", "user1", "同意")


def test_proposal_reject_calls_engine_reject_growth():
    """有 growth_engine 时，reject 应调用 engine.reject_growth"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module

    mock_engine = MagicMock()
    mock_engine.reject_growth.return_value = True
    routes_module._orchestrator.growth_engine = mock_engine

    response = client.post(
        "/admin/api/cognitive/proposal/prop-456/reject",
        json={"reason": "不同意"}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    mock_engine.reject_growth.assert_called_once_with("prop-456", "不同意")


def test_proposal_approve_handles_engine_failure():
    """engine.approve_growth 返回 False 时应返回失败"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module

    mock_engine = MagicMock()
    mock_engine.approve_growth.return_value = False
    routes_module._orchestrator.growth_engine = mock_engine

    response = client.post(
        "/admin/api/cognitive/proposal/nonexistent/approve",
        json={}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is False


def test_proposal_approve_handles_exception():
    """engine 抛异常时应返回失败，不抛 500"""
    _init_admin_with_mocks()
    app = _make_app()
    client = app.test_client()

    from src.admin.api import routes as routes_module

    mock_engine = MagicMock()
    mock_engine.approve_growth.side_effect = RuntimeError("boom")
    routes_module._orchestrator.growth_engine = mock_engine

    response = client.post(
        "/admin/api/cognitive/proposal/prop-x/approve",
        json={}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is False
    assert "boom" in data["message"]


# ============================================================
# 3. 路由注册完整性
# ============================================================

def test_proposal_routes_registered():
    """批准/拒绝路由应正确注册"""
    app = _make_app()
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert "/admin/api/cognitive/proposal/<proposal_id>/approve" in rules
    assert "/admin/api/cognitive/proposal/<proposal_id>/reject" in rules


def test_module_reload_route_still_registered():
    """reload 路由仍应存在"""
    app = _make_app()
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert "/admin/api/module/<module_name>/reload" in rules

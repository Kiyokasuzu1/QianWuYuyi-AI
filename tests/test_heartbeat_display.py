"""
心跳状态显示逻辑测试

验证 _normalize_hb_status 函数：
- 没有心跳数据时，根据模块 enabled 状态兜底
- 心跳状态为 stopped/error/unknown/degraded 时，根据 enabled 兜底
- 心跳状态正常（running/idle）时，保持原样
"""

import sys
import os
from unittest.mock import MagicMock

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
):
    _install_mock(_mod)


def _mock_psutil():
    import sys
    if "psutil" not in sys.modules:
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 5.0
        mock_mock = MagicMock()
        mock_mock.percent = 30.0
        mock_psutil.virtual_memory.return_value = mock_mock
        mock_disk = MagicMock()
        mock_disk.percent = 40.0
        mock_psutil.disk_usage.return_value = mock_disk
        sys.modules["psutil"] = mock_psutil


_mock_psutil()

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

from src.core.module_loader import ModuleInfo, ReloadMode


def _make_info(enabled: bool, name: str = "test_module") -> ModuleInfo:
    return ModuleInfo(
        name=name,
        display=f"Test {name}",
        version="1.0",
        reload_mode=ReloadMode.HOT,
        config_key=name,
        enabled=enabled,
        loaded=True,
    )


# ============================================================
# 导入要测试的函数
# ============================================================
# 因为 routes.py 里有全局变量 _module_loader / _orchestrator，
# 直接导入可能会有问题，用 exec 方式取函数
from src.admin.api import routes as routes_module

_normalize = routes_module._normalize_hb_status


# ============================================================
# 测试用例
# ============================================================

def test_no_hb_data_enabled_returns_running():
    """没有心跳数据 + enabled → running"""
    info = _make_info(enabled=True)
    result = _normalize(info, {})
    assert result["status"] == "running"
    assert result["last_tick_ago"] == 0
    assert result["error_count"] == 0


def test_no_hb_data_disabled_returns_stopped():
    """没有心跳数据 + disabled → stopped"""
    info = _make_info(enabled=False)
    result = _normalize(info, {})
    assert result["status"] == "stopped"
    assert result["last_tick_ago"] == -1


def test_error_status_enabled_returns_running():
    """心跳 error + enabled → running（配置优先）"""
    info = _make_info(enabled=True)
    hb = {"status": "error", "last_tick_ago": 999, "error_count": 5}
    result = _normalize(info, hb)
    assert result["status"] == "running"


def test_error_status_disabled_returns_stopped():
    """心跳 error + disabled → stopped"""
    info = _make_info(enabled=False)
    hb = {"status": "error", "last_tick_ago": 999, "error_count": 5}
    result = _normalize(info, hb)
    assert result["status"] == "stopped"


def test_unknown_status_enabled_returns_running():
    """心跳 unknown + enabled → running"""
    info = _make_info(enabled=True)
    hb = {"status": "unknown", "last_tick_ago": -1, "error_count": 0}
    result = _normalize(info, hb)
    assert result["status"] == "running"


def test_stopped_status_disabled_returns_stopped():
    """心跳 stopped + disabled → stopped（一致，不修改）"""
    info = _make_info(enabled=False)
    hb = {"status": "stopped", "last_tick_ago": -1, "error_count": 0}
    result = _normalize(info, hb)
    assert result["status"] == "stopped"


def test_degraded_status_enabled_returns_running():
    """心跳 degraded + enabled → running（配置优先，避免误导）"""
    info = _make_info(enabled=True)
    hb = {"status": "degraded", "last_tick_ago": 30, "error_count": 1}
    result = _normalize(info, hb)
    assert result["status"] == "running"


def test_running_status_stays_running():
    """心跳 running → 保持 running"""
    info = _make_info(enabled=True)
    hb = {"status": "running", "last_tick_ago": 5, "error_count": 0}
    result = _normalize(info, hb)
    assert result["status"] == "running"
    assert result["last_tick_ago"] == 5  # 保留原数据
    assert result["error_count"] == 0


def test_idle_status_stays_idle():
    """心跳 idle → 保持 idle"""
    info = _make_info(enabled=True)
    hb = {"status": "idle", "last_tick_ago": 10, "error_count": 0}
    result = _normalize(info, hb)
    assert result["status"] == "idle"
    assert result["last_tick_ago"] == 10


def test_empty_status_string_treated_as_bad():
    """空字符串状态 → 视为坏状态，走兜底"""
    info = _make_info(enabled=True)
    hb = {"status": "", "last_tick_ago": -1, "error_count": 0}
    result = _normalize(info, hb)
    assert result["status"] == "running"


def test_none_hb_data_enabled_returns_running():
    """hb_data 为 None（dict.get 取不到时的默认 None？）→ 走空数据逻辑"""
    info = _make_info(enabled=True)
    result = _normalize(info, {})
    assert result["status"] == "running"


def test_preserves_extra_fields_when_good():
    """心跳正常时，保留所有原有字段"""
    info = _make_info(enabled=True)
    hb = {
        "status": "running",
        "last_tick_ago": 3,
        "error_count": 0,
        "cpu_percent": 5.0,
        "memory_mb": 100.0,
        "message": "正常运行",
    }
    result = _normalize(info, hb)
    assert result["status"] == "running"
    assert result["cpu_percent"] == 5.0
    assert result["memory_mb"] == 100.0
    assert result["message"] == "正常运行"


def test_preserves_module_info_enabled_state():
    """ModuleInfo 的 enabled 属性被正确读取"""
    info_running = _make_info(enabled=True, name="mod_a")
    info_stopped = _make_info(enabled=False, name="mod_b")

    assert _normalize(info_running, {})["status"] == "running"
    assert _normalize(info_stopped, {})["status"] == "stopped"


# ============================================================
# 模块启动/停止接口的基本测试
# ============================================================

def test_module_start_api_success():
    """模块启动接口返回成功状态"""
    from flask import Flask
    from src.admin.api.routes import admin_bp

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # 设置 mock
    mock_loader = MagicMock()
    info = _make_info(enabled=False, name="memory")
    info.display = "记忆系统"
    mock_loader.get_module.return_value = info

    routes_module._module_loader = mock_loader
    routes_module._orchestrator = MagicMock()

    mock_cfg_mgr = MagicMock()
    mock_cfg_mgr.toggle_module.return_value = {"success": True}

    with _patch_config_manager(mock_cfg_mgr):
        client = app.test_client()
        response = client.post("/admin/api/module/memory/start")

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["module"] == "memory"


def test_module_stop_api_success():
    """模块停止接口返回成功状态"""
    from flask import Flask
    from src.admin.api.routes import admin_bp

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    mock_loader = MagicMock()
    info = _make_info(enabled=True, name="memory")
    info.display = "记忆系统"
    mock_loader.get_module.return_value = info

    routes_module._module_loader = mock_loader
    routes_module._orchestrator = MagicMock()

    mock_cfg_mgr = MagicMock()
    mock_cfg_mgr.toggle_module.return_value = {"success": True}

    with _patch_config_manager(mock_cfg_mgr):
        client = app.test_client()
        response = client.post("/admin/api/module/memory/stop")

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True


def _patch_config_manager(mock_mgr):
    """patch ConfigManager 类"""
    from unittest.mock import patch
    return patch("src.admin.core.config_manager.ConfigManager", return_value=mock_mgr)

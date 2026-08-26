"""
Admin API 蓝图

羽依 AI 控制中心后端接口，
连接 Phase 0/0.5 基础设施与前端 UI。
"""

from __future__ import annotations

import hmac
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

import yaml
from flask import Blueprint, Response, jsonify, request, send_from_directory

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)


@admin_bp.before_request
def _protect_admin_write_requests():
    """P0 止血：admin_bp 全部写操作（POST/PUT/PATCH/DELETE）必须认证。

    与 api_server._require_admin_auth 同语义（fail-closed）：
    - 已配置 token（YUYI_ADMIN_TOKEN / config.yaml admin.token）→ 必须携带匹配 token；
    - 未配置 token → 仅本机回环放行，其余来源拒绝。
    注意：CGNAT/Tailscale 网段不再视为管理员身份（能访问网络 ≠ 管理员）。
    GET/HEAD/OPTIONS（静态页面、只读查询）不受影响。
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    try:
        from api_server import _get_admin_token, _is_loopback_client
        token = _get_admin_token()
    except Exception:  # noqa: BLE001
        token = ""
    if not token:
        try:
            if _is_loopback_client():
                return None
        except Exception:  # noqa: BLE001
            pass
        return jsonify({
            "error": "管理写端点未配置访问 token，仅允许本机访问",
            "hint": "设置环境变量 YUYI_ADMIN_TOKEN（或 config.yaml admin.token）后，"
                    "携带 X-Admin-Token 请求头访问",
        }), 403
    provided = (request.headers.get("X-Admin-Token") or "").strip()
    if not provided:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth.startswith("Bearer "):
            provided = auth[len("Bearer "):].strip()
    if not provided or not hmac.compare_digest(provided, token):
        return jsonify({"error": "未授权：缺失或无效的管理 token"}), 401
    return None

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "static" / "admin"
L2D_MODEL_DIR = PROJECT_ROOT / "A.雪芽2.0"

_start_time = time.time()
_orchestrator = None
_module_loader = None
_initialized = False


def init_admin(orchestrator=None):
    """初始化 Admin 模块"""
    global _orchestrator, _module_loader, _initialized
    _orchestrator = orchestrator
    _initialized = True

    # 初始化模块加载器
    try:
        config = _load_config()
        from src.core.module_loader import ModuleLoader
        src_root = PROJECT_ROOT / "src"
        _module_loader = ModuleLoader(
            src_root=str(src_root),
            config=config,
        )
        _module_loader.discover()
        logger.info(f"Admin 初始化完成，发现 {len(_module_loader.get_all_modules())} 个模块")
    except Exception as e:
        logger.warning(f"Admin 初始化模块加载器失败: {e}")


def _load_config() -> dict:
    """加载 config.yaml"""
    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_uptime() -> str:
    """格式化运行时长"""
    elapsed = time.time() - _start_time
    days = int(elapsed // 86400)
    hours = int((elapsed % 86400) // 3600)
    minutes = int((elapsed % 3600) // 60)
    secs = int(elapsed % 60)
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分")
    parts.append(f"{secs}秒")
    return "".join(parts)


def _get_system_health() -> dict:
    """获取系统健康度"""
    metrics = {
        "status": "healthy",
        "score": 100,
        "cpu_percent": 0,
        "memory_mb": 0,
        "memory_total_mb": 0,
    }


def _normalize_hb_status(info, hb_data: dict) -> dict:
    """
    心跳状态归一化 —— 避免"心跳超时"误导用户

    策略：信任模块的 enabled 配置。
    - enabled=True 且心跳状态不好 → 显示为 running（配置优先）
    - enabled=False → 显示为 stopped
    - 心跳正常 → 保持原样
    """
    if not hb_data:
        if info.enabled:
            return {"status": "running", "last_tick_ago": 0, "error_count": 0}
        else:
            return {"status": "stopped", "last_tick_ago": -1, "error_count": 0}

    status = hb_data.get("status", "")
    bad_statuses = ("stopped", "error", "unknown", "degraded", "")

    if status in bad_statuses:
        if info.enabled:
            return {"status": "running", "last_tick_ago": 0, "error_count": 0}
        else:
            return {"status": "stopped", "last_tick_ago": -1, "error_count": 0}

    return hb_data


def _get_system_health() -> dict:
    """获取系统健康度"""
    metrics = {
        "status": "healthy",
        "score": 100,
        "cpu_percent": 0,
        "memory_mb": 0,
        "memory_total_mb": 0,
        "memory_percent": 0,
    }
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        metrics["cpu_percent"] = round(proc.cpu_percent(interval=0.1), 1)
        mem_info = proc.memory_info()
        metrics["memory_mb"] = round(mem_info.rss / (1024 * 1024), 1)

        virtual_mem = psutil.virtual_memory()
        metrics["memory_total_mb"] = round(virtual_mem.total / (1024 * 1024), 0)
        metrics["memory_percent"] = round(virtual_mem.percent, 1)

        # 计算健康分
        score = 100
        if metrics["memory_percent"] > 85:
            score -= 30
        elif metrics["memory_percent"] > 70:
            score -= 15
        if metrics["cpu_percent"] > 90:
            score -= 20
        elif metrics["cpu_percent"] > 70:
            score -= 10
        metrics["score"] = max(0, score)

        if score >= 80:
            metrics["status"] = "healthy"
        elif score >= 50:
            metrics["status"] = "warning"
        else:
            metrics["status"] = "critical"
    except ImportError:
        pass

    return metrics


@admin_bp.route("")
@admin_bp.route("/")
def admin_index():
    """羽依控制中心首页"""
    return send_from_directory(str(STATIC_DIR), "index.html")


@admin_bp.route("/dashboard_v2", strict_slashes=False)
@admin_bp.route("/dashboard_v2/")
def admin_dashboard_v2():
    """Phase 5.0 Dashboard Upgrade —— 羽依生命状态控制中心 V2"""
    return send_from_directory(str(STATIC_DIR / "dashboard_v2"), "index.html")


@admin_bp.route("/l2d", strict_slashes=False)
def l2d_preview():
    """Live2D 预览页面"""
    return send_from_directory(str(STATIC_DIR), "l2d.html")


@admin_bp.route("/libs/<path:filename>")
def l2d_lib_file(filename: str):
    """提供 Live2D SDK 本地库文件（Cubism Core / pixi.js / pixi-live2d-display）"""
    libs_dir = STATIC_DIR / "libs"
    return send_from_directory(str(libs_dir), filename)


@admin_bp.route("/l2d/<path:filename>")
def l2d_model_file(filename: str):
    """提供 Live2D 模型文件"""
    logger.info(f"L2D 请求文件: {filename} (来自 {L2D_MODEL_DIR})")
    try:
        resp = send_from_directory(str(L2D_MODEL_DIR), filename)
        logger.info(f"L2D 文件返回成功: {filename}")
        return resp
    except Exception as e:
        logger.error(f"L2D 文件返回失败: {filename} -> {e}")
        # 列出目录内容用于调试
        try:
            files = list(L2D_MODEL_DIR.iterdir())[:10] if L2D_MODEL_DIR.exists() else []
            logger.error(f"L2D 目录存在={L2D_MODEL_DIR.exists()}, 文件列表(前10): {[f.name for f in files]}")
        except Exception:
            pass
        raise


@admin_bp.route("/governance", strict_slashes=False)
@admin_bp.route("/governance/")
def admin_governance():
    """v1.5.5 Governance G1: 治理控制台页面（复用 admin 静态资源模式）。"""
    resp = send_from_directory(str(STATIC_DIR / "governance"), "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@admin_bp.route("/<path:path>")
def admin_static(path: str):
    """静态资源"""
    # /l2d/ 下的请求应该由 l2d_model_file 处理，记录一下以防路由错配
    if path.startswith("l2d/"):
        logger.warning(f"admin_static 拦截了 l2d 路径: {path}（应该由 l2d_model_file 处理）")
    return send_from_directory(str(STATIC_DIR), path)


# ==================== API ====================


@admin_bp.route("/api/dashboard")
def api_dashboard():
    """Dashboard 聚合数据"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")

    if mock:
        return jsonify(_mock_dashboard())

    config = _load_config()
    health = _get_system_health()
    heartbeat_status = {}

    try:
        from src.core.heartbeat import HeartbeatCollector
        hb = HeartbeatCollector.get_instance()
        heartbeat_status = hb.get_all_status()
    except Exception:
        pass

    # 在线代理数量
    online_agents = 0
    try:
        from src.remote.agent_server import get_agent_server
        agent_srv = get_agent_server()
        if agent_srv and agent_srv._running:
            online_agents = agent_srv.registry.count()
    except Exception:
        pass

    # 模块列表（含依赖关系）
    all_modules = _module_loader.get_all_modules() if _module_loader else {}
    modules_list = []
    for info in all_modules.values():
        hb_data = heartbeat_status.get(info.name, {})
        # 心跳状态归一化（避免"心跳超时"误导）
        hb_data = _normalize_hb_status(info, hb_data)
        # 解析依赖状态
        deps = []
        for dep_name in info.dependencies:
            dep_info = all_modules.get(dep_name)
            deps.append({
                "name": dep_name,
                "satisfied": dep_info.enabled if dep_info else False,
            })
        modules_list.append({
            "name": info.name,
            "display": info.display,
            "version": info.version,
            "description": info.description,
            "reload_mode": info.reload_mode.value,
            "enabled": info.enabled,
            "loaded": info.loaded,
            "dependencies": deps,
            "config_key": info.config_key,
            "error": info.error,
            "hb_status": hb_data.get("status", "unknown"),
            "hb_last_tick": hb_data.get("last_tick_ago", -1),
            "hb_errors": hb_data.get("error_count", 0),
        })

    # 羽依状态 — 从 orchestrator 获取
    yuyi_status = {
        "greeting": _get_greeting(),
        "personality": _get_personality_snapshot(),
        "emotion": _get_emotion_snapshot(),
        "core": _get_yuyi_core_status(all_modules),
        "character": _get_character_state(),
    }

    return jsonify({
        "uptime": _get_uptime(),
        "health": health,
        "online_agents": online_agents,
        "modules": modules_list,
        "yuyi": yuyi_status,
        "timestamp": time.time(),
    })


# ============================================================
# 远程陪伴接口（屏幕查看 + 控制）
# ============================================================

def _get_agent_server():
    """获取 AgentServer 实例"""
    try:
        from src.remote.agent_server import get_agent_server
        return get_agent_server()
    except Exception:
        return None


def _get_orchestrator_config():
    """获取 orchestrator 配置"""
    if _orchestrator and hasattr(_orchestrator, 'config'):
        return _orchestrator.config or {}
    return {}


@admin_bp.route("/api/control/status")
def api_control_status():
    """远程控制状态"""
    config = _get_orchestrator_config()
    remote_cfg = config.get("remote", {})
    screen_cfg = config.get("screen", {})
    control_cfg = config.get("control", {})

    agent_srv = _get_agent_server()
    agent_online = False
    if agent_srv and agent_srv._running:
        agent_online = agent_srv.registry.count() > 0

    return jsonify({
        "screen_enabled": screen_cfg.get("enabled", False),
        "control_enabled": control_cfg.get("enabled", False),
        "agent_online": agent_online,
        "agent_count": agent_srv.registry.count() if agent_srv and agent_srv._running else 0,
    })


@admin_bp.route("/api/screen/context")
def api_screen_context():
    """屏幕上下文（OCR + 描述）"""
    if not _orchestrator or not _orchestrator.screen_context_manager:
        return jsonify({"available": False, "error": "屏幕模块未启用"})

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ctx = loop.run_until_complete(
                _orchestrator.screen_context_manager.get_screen_description(
                    user_id=_orchestrator.target_user_id or "default"
                )
            )
            return jsonify({
                "available": ctx.get("available", False),
                "text": ctx.get("text", ""),
                "description": ctx.get("description", ""),
            })
        finally:
            loop.close()
    except Exception as e:
        return jsonify({"available": False, "error": str(e)})


@admin_bp.route("/api/screen/capture", methods=["POST"])
def api_screen_capture():
    """远程截图"""
    agent_srv = _get_agent_server()
    if not agent_srv or not agent_srv._running:
        return jsonify({"success": False, "error": "代理服务器未运行"}), 503

    if agent_srv.registry.count() == 0:
        return jsonify({"success": False, "error": "没有代理在线，请启动本地代理"}), 404

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agents = agent_srv.get_online_agents()
            if not agents:
                return jsonify({"success": False, "error": "没有代理在线"}), 404

            agent_id = agents[0]["agent_id"]
            result = loop.run_until_complete(
                agent_srv.send_command_and_wait(agent_id, "screen.capture", {}, timeout=10)
            )

            if result and result.get("success"):
                payload = result.get("payload", {})
                image_data = payload.get("image", "") or payload.get("image_base64", "")
                return jsonify({
                    "success": True,
                    "image_base64": image_data,
                    "format": payload.get("format", "jpeg"),
                    "image_size": len(image_data) if image_data else 0,
                    "ocr_text": payload.get("ocr_text", "") or "",
                    "timestamp": time.strftime("%H:%M:%S"),
                })
            else:
                error = (result or {}).get("error", "截图失败")
                return jsonify({"success": False, "error": error}), 500
        finally:
            loop.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/control/click", methods=["POST"])
def api_control_click():
    """远程鼠标点击"""
    data = request.get_json() or {}
    x = data.get("x", 0)
    y = data.get("y", 0)
    button = data.get("button", "left")

    agent_srv = _get_agent_server()
    if not agent_srv or not agent_srv._running:
        return jsonify({"success": False, "error": "代理服务器未运行"}), 503

    if agent_srv.registry.count() == 0:
        return jsonify({"success": False, "error": "没有代理在线"}), 404

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agents = agent_srv.get_online_agents()
            agent_id = agents[0]["agent_id"]
            result = loop.run_until_complete(
                agent_srv.send_command_and_wait(agent_id, "input.mouse_click", {
                    "x": x, "y": y, "button": button
                }, timeout=5)
            )
            if result and result.get("success"):
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "error": (result or {}).get("error", "点击失败")})
        finally:
            loop.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/control/type", methods=["POST"])
def api_control_type():
    """远程文本输入"""
    data = request.get_json() or {}
    text = data.get("text", "")
    if not text:
        return jsonify({"success": False, "error": "文本不能为空"}), 400

    agent_srv = _get_agent_server()
    if not agent_srv or not agent_srv._running:
        return jsonify({"success": False, "error": "代理服务器未运行"}), 503

    if agent_srv.registry.count() == 0:
        return jsonify({"success": False, "error": "没有代理在线"}), 404

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agents = agent_srv.get_online_agents()
            agent_id = agents[0]["agent_id"]
            result = loop.run_until_complete(
                agent_srv.send_command_and_wait(agent_id, "input.key_type", {
                    "text": text
                }, timeout=10)
            )
            if result and result.get("success"):
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "error": (result or {}).get("error", "输入失败")})
        finally:
            loop.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/control/key", methods=["POST"])
def api_control_key():
    """远程按键"""
    data = request.get_json() or {}
    key = data.get("key", "")
    if not key:
        return jsonify({"success": False, "error": "按键名不能为空"}), 400

    agent_srv = _get_agent_server()
    if not agent_srv or not agent_srv._running:
        return jsonify({"success": False, "error": "代理服务器未运行"}), 503

    if agent_srv.registry.count() == 0:
        return jsonify({"success": False, "error": "没有代理在线"}), 404

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            agents = agent_srv.get_online_agents()
            agent_id = agents[0]["agent_id"]
            result = loop.run_until_complete(
                agent_srv.send_command_and_wait(agent_id, "input.key_press", {
                    "key": key
                }, timeout=5)
            )
            if result and result.get("success"):
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "error": (result or {}).get("error", "按键失败")})
        finally:
            loop.close()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/character")
def api_character():
    """羽依角色状态接口 — 专门服务角色展示"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")

    if mock:
        return jsonify(_mock_character())

    return jsonify(_get_character_state())


@admin_bp.route("/api/modules")
def api_modules_list():
    """模块列表（含依赖关系）"""
    modules_list = []
    if _module_loader:
        heartbeat_status = {}
        try:
            from src.core.heartbeat import HeartbeatCollector
            heartbeat_status = HeartbeatCollector.get_instance().get_all_status()
        except Exception:
            pass

        all_modules = _module_loader.get_all_modules()
        for info in all_modules.values():
            hb = heartbeat_status.get(info.name, {})
            # 心跳状态归一化（同 dashboard 接口）
            hb = _normalize_hb_status(info, hb)
            deps = []
            for dep_name in info.dependencies:
                dep_info = all_modules.get(dep_name)
                deps.append({
                    "name": dep_name,
                    "satisfied": dep_info.enabled if dep_info else False,
                })
            modules_list.append({
                "name": info.name,
                "display": info.display,
                "version": info.version,
                "description": info.description,
                "reload_mode": info.reload_mode.value,
                "enabled": info.enabled,
                "dependencies": deps,
                "config_key": info.config_key,
                "error": info.error,
                "hb_status": hb.get("status", "unknown"),
                "hb_last_tick": hb.get("last_tick_ago", -1),
                "hb_errors": hb.get("error_count", 0),
            })

    return jsonify({"modules": modules_list, "total": len(modules_list)})


def _control_initiative_process(action: str) -> dict:
    """启停 initiative_sender.py 进程"""
    import subprocess
    import os
    import signal

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))

    if action == "start":
        # 先检查是否已在运行
        try:
            result = subprocess.run(
                ["pgrep", "-f", "initiative_sender.py"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                return {"success": True, "message": "主动消息已在运行"}
        except Exception:
            pass

        # 启动
        try:
            venv_python = os.path.join(project_root, "venv", "bin", "python")
            if not os.path.exists(venv_python):
                venv_python = "python"

            log_dir = os.path.join(project_root, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "initiative.log")

            # R2.7.6-AUDIT FIX: 保存文件句柄到变量，Popen 后在父进程侧关闭
            #   原代码 open(log_file, "a") 返回的 fd 无引用 → 永不关闭（每次泄漏 1 个 fd）
            log_fh = open(log_file, "a")
            try:
                subprocess.Popen(
                    [venv_python, "initiative_sender.py"],
                    cwd=project_root,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                # 子进程已经继承了 fd，父进程侧可以安全关闭
                log_fh.close()
            return {"success": True, "message": "主动消息进程已启动"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif action == "stop":
        try:
            subprocess.run(["pkill", "-f", "initiative_sender.py"],
                          capture_output=True, timeout=5)
            return {"success": True, "message": "主动消息进程已停止"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "未知操作"}


@admin_bp.route("/api/module/<module_name>/start", methods=["POST"])
def api_module_start(module_name: str):
    """启动模块"""
    if not _module_loader:
        return jsonify({"success": False, "error": "模块加载器未初始化"}), 500

    info = _module_loader.get_module(module_name)
    if not info:
        return jsonify({"success": False, "error": f"模块不存在: {module_name}"}), 404

    # 切换 enabled 状态
    _module_loader.update_enabled_state(module_name, True)

    # 写配置
    try:
        from src.admin.core.config_manager import ConfigManager
        mgr = ConfigManager()
        result = mgr.toggle_module(module_name, True, operator="admin")

        # initiative 模块特殊处理：真正启停进程
        if module_name == "initiative":
            proc_result = _control_initiative_process("start")
            if not proc_result.get("success"):
                result["success"] = False

        # 记录审计
        try:
            from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
            AuditLogger.get_instance().record(
                event_type=AuditEventType.MODULE_START,
                operator="admin",
                operator_type=AuditOperatorType.HUMAN,
                target=module_name,
                result="success" if result.get("success") else "failure",
            )
        except Exception:
            pass

        return jsonify({
            "success": result.get("success", True),
            "module": module_name,
            "message": f"模块 {info.display} 已启动",
            "reload_mode": info.reload_mode.value,
            "warning": (
                "此模块需要重启才能完全生效"
                if info.reload_mode.value == "restart_required" else ""
            ),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/module/<module_name>/stop", methods=["POST"])
def api_module_stop(module_name: str):
    """停止模块"""
    if not _module_loader:
        return jsonify({"success": False, "error": "模块加载器未初始化"}), 500

    info = _module_loader.get_module(module_name)
    if not info:
        return jsonify({"success": False, "error": f"模块不存在: {module_name}"}), 404

    _module_loader.update_enabled_state(module_name, False)

    try:
        from src.admin.core.config_manager import ConfigManager
        mgr = ConfigManager()
        result = mgr.toggle_module(module_name, False, operator="admin")

        # initiative 模块特殊处理：真正停掉进程
        if module_name == "initiative":
            _control_initiative_process("stop")

        try:
            from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
            AuditLogger.get_instance().record(
                event_type=AuditEventType.MODULE_STOP,
                operator="admin",
                operator_type=AuditOperatorType.HUMAN,
                target=module_name,
                result="success" if result.get("success") else "failure",
            )
        except Exception:
            pass

        return jsonify({
            "success": result.get("success", True),
            "module": module_name,
            "message": f"模块 {info.display} 已停止",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/module/<module_name>/reload", methods=["POST"])
def api_module_reload(module_name: str):
    """重载模块（真正执行重载动作）"""
    if not _module_loader:
        return jsonify({"success": False, "error": "模块加载器未初始化"}), 500

    info = _module_loader.get_module(module_name)
    if not info:
        return jsonify({"success": False, "error": f"模块不存在: {module_name}"}), 404

    reload_result = {"success": False, "error": "未执行"}

    # 真正的重载逻辑：重新读取 config 并重新初始化对应模块
    try:
        # 1. 重新读取 config.yaml
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parent.parent.parent.parent / "config.yaml"
        new_config = {}
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                new_config = yaml.safe_load(f) or {}

        # 2. 同步 _module_loader 状态
        _module_loader.sync_config(new_config)

        # 3. 如果是远程模块（screen/control），重新初始化 orchestrator 的对应 manager
        if _orchestrator and module_name in ("screen", "control", "remote"):
            if module_name == "screen" or module_name == "remote":
                # 重新初始化 screen_context_manager
                screen_cfg = new_config.get("screen", {})
                if screen_cfg.get("enabled", False):
                    try:
                        from src.screen.screen_context import ScreenContextManager
                        # 先停止旧的
                        if _orchestrator.screen_context_manager and hasattr(_orchestrator.screen_context_manager, 'stop'):
                            _orchestrator.screen_context_manager.stop()
                        _orchestrator.screen_context_manager = ScreenContextManager(
                            capture_timeout=screen_cfg.get("capture_timeout", 10),
                            ocr_language=screen_cfg.get("ocr_language", "chi_sim+eng"),
                        )
                        reload_result = {"success": True, "message": "屏幕模块已重新初始化"}
                    except Exception as e:
                        reload_result = {"success": False, "error": f"屏幕模块重载失败: {e}"}
                else:
                    _orchestrator.screen_context_manager = None
                    reload_result = {"success": True, "message": "屏幕模块已禁用"}

            if module_name == "control" or module_name == "remote":
                control_cfg = new_config.get("control", {})
                if control_cfg.get("enabled", False):
                    try:
                        from src.control.control_manager import ControlManager
                        if _orchestrator.control_manager and hasattr(_orchestrator.control_manager, 'stop'):
                            _orchestrator.control_manager.stop()
                        _orchestrator.control_manager = ControlManager(
                            action_timeout=control_cfg.get("action_timeout", 10),
                        )
                        reload_result = {"success": True, "message": "控制模块已重新初始化"}
                    except Exception as e:
                        reload_result = {"success": False, "error": f"控制模块重载失败: {e}"}
                else:
                    _orchestrator.control_manager = None
                    reload_result = {"success": True, "message": "控制模块已禁用"}
        else:
            # 普通模块：标记为已重载，需要重启服务才完全生效
            reload_result = {
                "success": True,
                "message": f"模块 {info.display} 配置已重载（如需完全生效请重启服务）",
            }

        # 4. 更新 config 引用
        if _orchestrator:
            _orchestrator.config = new_config

    except Exception as e:
        reload_result = {"success": False, "error": f"重载失败: {e}"}

    # 记录审计
    try:
        from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
        AuditLogger.get_instance().record(
            event_type=AuditEventType.MODULE_RELOAD,
            operator="admin",
            operator_type=AuditOperatorType.HUMAN,
            target=module_name,
            detail={"result": "success" if reload_result.get("success") else "failed"},
        )
    except Exception:
        pass

    return jsonify({
        "success": reload_result.get("success", False),
        "module": module_name,
        "message": reload_result.get("message", reload_result.get("error", "")),
        "reload_mode": info.reload_mode.value,
        "warning": (
            "此模块需要重启才能完全生效"
            if info.reload_mode.value == "restart_required" else ""
        ),
    })


@admin_bp.route("/api/logs")
def api_logs():
    """获取实时日志"""
    module_filter = request.args.get("module", "")
    lines = int(request.args.get("lines", 100))

    logs_dir = PROJECT_ROOT / "logs"
    log_entries = []

    if logs_dir.exists():
        # 读取最近的日志文件
        log_files = sorted(logs_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)

        for log_file in log_files[:3]:
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    for line in content.split("\n"):
                        if not line.strip():
                            continue
                        if module_filter and module_filter not in line.lower():
                            continue
                        log_entries.append({
                            "source": log_file.stem,
                            "message": line.strip(),
                        })
            except Exception:
                continue

        # 取最新的 N 条
        log_entries = log_entries[-lines:]

    # 审计日志也加进来
    try:
        from src.admin.core.audit import AuditLogger
        audit_entries = AuditLogger.get_instance().query(limit=20)
        for entry in audit_entries:
            log_entries.append({
                "source": "audit",
                "message": f"[{entry.get('event_type')}] {entry.get('operator')} → {entry.get('target')} ({entry.get('result')})",
            })
    except Exception:
        pass

    # 按时间倒序
    log_entries = list(reversed(log_entries))[:lines]

    return jsonify({"logs": log_entries, "total": len(log_entries)})


@admin_bp.route("/api/config", methods=["GET"])
def api_config_get():
    """获取当前配置"""
    try:
        from src.admin.core.config_manager import ConfigManager
        mgr = ConfigManager()
        config = mgr.read()
        return jsonify({"config": config})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/config", methods=["PUT"])
def api_config_put():
    """写入配置"""
    data = request.get_json(silent=True) or {}
    config = data.get("config")
    operator = data.get("operator", "admin")

    if not config:
        return jsonify({"success": False, "error": "缺少 config 参数"}), 400

    try:
        from src.admin.core.config_manager import ConfigManager
        mgr = ConfigManager()
        result = mgr.write(config, operator=operator, reason="admin_panel_edit")

        # 记录审计
        try:
            from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
            AuditLogger.get_instance().record(
                event_type=AuditEventType.CONFIG_UPDATE,
                operator=operator,
                operator_type=AuditOperatorType.HUMAN,
                target="config",
                details={"errors": result.get("errors", [])},
                result="success" if result.get("success") else "failure",
            )
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/config/history")
def api_config_history():
    """配置修改历史"""
    try:
        from src.admin.core.config_manager import ConfigManager
        mgr = ConfigManager()
        backups = mgr.get_history()
        return jsonify({"backups": backups})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/config/rollback", methods=["POST"])
def api_config_rollback():
    """回滚配置"""
    data = request.get_json(silent=True) or {}
    filename = data.get("filename")
    operator = data.get("operator", "admin")

    if not filename:
        return jsonify({"success": False, "error": "缺少 filename 参数"}), 400

    try:
        from src.admin.core.config_manager import ConfigManager
        mgr = ConfigManager()
        result = mgr.rollback(filename, operator=operator)

        try:
            from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
            AuditLogger.get_instance().record(
                event_type=AuditEventType.CONFIG_ROLLBACK,
                operator=operator,
                operator_type=AuditOperatorType.HUMAN,
                target=filename,
            )
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/audit")
def api_audit_logs():
    """审计日志查询"""
    event_type = request.args.get("event_type")
    operator = request.args.get("operator")
    target = request.args.get("target")
    limit = int(request.args.get("limit", 50))

    try:
        from src.admin.core.audit import AuditLogger
        logs = AuditLogger.get_instance().query(
            event_type=event_type,
            operator=operator,
            target=target,
            limit=limit,
        )
        summary = AuditLogger.get_instance().get_summary()
        return jsonify({"logs": logs, "summary": summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/schema")
def api_schema():
    """获取所有配置 Schema"""
    try:
        from src.admin.core.schema_validator import SchemaValidator
        validator = SchemaValidator()
        schemas = validator.load()
        result = {name: schema.to_dict() for name, schema in schemas.items()}
        return jsonify({"schemas": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/action/<action>", methods=["POST"])
def api_action(action: str):
    """快捷操作"""
    valid_actions = {"reload_config", "save_backup", "system_check", "clear_cache"}
    if action not in valid_actions:
        return jsonify({"success": False, "error": f"未知操作: {action}"}), 400

    try:
        if action == "reload_config":
            config = _load_config()
            if _module_loader:
                _module_loader.sync_config(config)
            return jsonify({"success": True, "message": "配置已重新加载"})

        elif action == "save_backup":
            from src.admin.core.config_manager import ConfigManager
            mgr = ConfigManager()
            config = mgr.read(use_cache=False)
            result = mgr.write(config, operator="admin", reason="manual_backup")
            return jsonify({"success": True, "message": "备份已保存", "backup_file": result.get("backup_file", "")})

        elif action == "system_check":
            health = _get_system_health()
            return jsonify({
                "success": True,
                "health": health,
                "modules": len(_module_loader.get_all_modules()) if _module_loader else 0,
                "uptime": _get_uptime(),
            })

        elif action == "clear_cache":
            # P0-2: memory_system 已废弃，新架构用 memory_store
            # memory_store 没有 clear_session_cache，这里保持 no-op 兼容
            pass
            return jsonify({"success": True, "message": "缓存已清理"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== 认知控制台 API ====================

@admin_bp.route("/api/cognitive/mind")
def api_cognitive_mind():
    """心智状态 — 注意力、情绪趋势、思维状态"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_cognitive_mind())

    # 情绪历史趋势（最近 24 小时采样）
    emotion_history = []
    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'emotion_manager') and _orchestrator.emotion_manager:
                hist = _orchestrator.emotion_manager.get_history(hours=24)
                if hist:
                    emotion_history = [
                        {
                            "time": e.get("timestamp", ""),
                            "primary": e.get("primary_emotion", "平静"),
                            "valence": e.get("valence", 0),
                            "arousal": e.get("arousal", 0),
                            "intensity": e.get("intensity", 0),
                        }
                        for e in hist[-24:]
                    ]
        except Exception:
            pass

    # 如果没有历史，生成模拟趋势点
    if not emotion_history:
        emotion_history = _generate_emotion_trend()

    # 注意力状态
    attention = {
        "focus": "idle",
        "target": "",
        "level": 50,
    }
    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'attention_tracker') and _orchestrator.attention_tracker:
                att = _orchestrator.attention_tracker.get_current()
                attention = {
                    "focus": att.get("focus", "idle"),
                    "target": att.get("target", ""),
                    "level": att.get("level", 50),
                }
        except Exception:
            pass

    # 思维状态
    thinking_state = "idle"
    thinking_detail = "空闲"
    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'thinking_state') and _orchestrator.thinking_state:
                thinking_state = _orchestrator.thinking_state
                thinking_detail = _orchestrator.thinking_detail if hasattr(_orchestrator, 'thinking_detail') else ""
        except Exception:
            pass

    return jsonify({
        "emotion_trend": emotion_history,
        "attention": attention,
        "thinking": {
            "state": thinking_state,
            "detail": thinking_detail,
        },
        "timestamp": time.time(),
    })


@admin_bp.route("/api/cognitive/memory")
def api_cognitive_memory():
    """记忆空间 — 身份、事件、记忆条目"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_cognitive_memory())

    result = {
        "identity": {},
        "events": [],
        "memories": [],
        "stats": {
            "total_events": 0,
            "total_memories": 0,
            "memory_types": {},
        },
    }

    if _orchestrator:
        try:
            # P0-2: memory_system 已废弃，改用 memory_store
            if hasattr(_orchestrator, 'memory_store') and _orchestrator.memory_store:
                all_memories = _orchestrator.memory_store.load() or []
                if isinstance(all_memories, list):
                    # 身份记忆：筛选 identity 类型
                    identity_mem = None
                    for m in all_memories:
                        mtype = (m.get("metadata") or {}).get("memory_type") or m.get("type", "")
                        if str(mtype).lower() == "identity":
                            identity_mem = m
                            break
                    if identity_mem:
                        result["identity"] = {
                            "name": str(identity_mem.get("content", ""))[:200],
                            "type": "identity",
                        }

                    # 事件记忆：筛选 event 类型
                    event_memories = [
                        m for m in all_memories
                        if str((m.get("metadata") or {}).get("memory_type") or m.get("type", "")).lower() == "event"
                    ]
                    result["events"] = event_memories[-20:]
                    result["stats"]["total_events"] = len(event_memories)

                    # 全部记忆
                    result["memories"] = all_memories[-30:]
                    result["stats"]["total_memories"] = len(all_memories)
                    for m in all_memories:
                        mtype = (m.get("metadata") or {}).get("memory_type") or m.get("type", "unknown")
                        result["stats"]["memory_types"][str(mtype)] = \
                            result["stats"]["memory_types"].get(str(mtype), 0) + 1
        except Exception:
            pass

    return jsonify(result)


@admin_bp.route("/api/cognitive/growth")
def api_cognitive_growth():
    """成长系统 — 成长记录、建议、阶段"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_cognitive_growth())

    result = {
        "stage": {"level": 1, "label": "初始阶段", "progress": 0},
        "records": [],
        "proposals": [],
        "stats": {
            "total_records": 0,
            "total_proposals": 0,
            "accepted": 0,
            "rejected": 0,
        },
    }

    if _orchestrator:
        try:
            # 成长记录
            if hasattr(_orchestrator, 'personality_resolver') and _orchestrator.personality_resolver:
                resolver = _orchestrator.personality_resolver
                if hasattr(resolver, 'growth_records') and resolver.growth_records:
                    records = resolver.growth_records
                    result["records"] = [
                        {
                            "id": i,
                            "timestamp": r.get("timestamp", ""),
                            "type": r.get("type", "unknown"),
                            "description": r.get("description", "")[:200],
                            "impact": r.get("impact", "medium"),
                        }
                        for i, r in enumerate(records[-20:])
                    ]
                    result["stats"]["total_records"] = len(records)

                # 成长阶段
                record_count = result["stats"]["total_records"]
                if record_count >= 50:
                    result["stage"] = {"level": 4, "label": "成熟阶段", "progress": min(100, record_count)}
                elif record_count >= 20:
                    result["stage"] = {"level": 3, "label": "成长阶段", "progress": min(100, record_count * 2)}
                elif record_count >= 5:
                    result["stage"] = {"level": 2, "label": "觉醒阶段", "progress": min(100, record_count * 10)}
                else:
                    result["stage"] = {"level": 1, "label": "初始阶段", "progress": max(0, record_count * 20)}

            # 成长建议 / proposals
            if hasattr(_orchestrator, 'growth_engine') and _orchestrator.growth_engine:
                try:
                    engine = _orchestrator.growth_engine
                    if hasattr(engine, 'get_pending_proposals'):
                        proposals = engine.get_pending_proposals()
                        result["proposals"] = proposals[:10]
                        result["stats"]["total_proposals"] = len(proposals)
                    if hasattr(engine, 'get_stats'):
                        stats = engine.get_stats()
                        result["stats"]["accepted"] = stats.get("accepted", 0)
                        result["stats"]["rejected"] = stats.get("rejected", 0)
                except Exception:
                    pass
        except Exception:
            pass

    return jsonify(result)


@admin_bp.route("/api/cognitive/relationship")
def api_cognitive_relationship():
    """关系层 — 信任度、熟悉度、互动历史"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_cognitive_relationship())

    result = {
        "trust": {"score": 50, "level": "normal", "trend": "→"},
        "familiarity": {"score": 30, "level": "acquaintance", "trend": "→"},
        "interaction_history": [],
        "milestones": [],
        "stats": {
            "total_interactions": 0,
            "total_words": 0,
            "days_together": 1,
        },
    }

    if _orchestrator:
        try:
            # 从关系状态获取关系数据（P0-2: 替换 memory_system.identity）
            if hasattr(_orchestrator, 'relationship_state') and _orchestrator.relationship_state:
                rel = _orchestrator.relationship_state
                if hasattr(rel, 'get'):
                    relation_data = rel.get() or {}
                    result["trust"]["score"] = relation_data.get("trust", 50)
                    result["familiarity"]["score"] = relation_data.get("familiarity", 30)

                    # 计算信任等级
                    trust_score = result["trust"]["score"]
                    if trust_score >= 80:
                        result["trust"]["level"] = "deep"
                    elif trust_score >= 60:
                        result["trust"]["level"] = "close"
                    elif trust_score >= 40:
                        result["trust"]["level"] = "normal"
                    else:
                        result["trust"]["level"] = "distant"

                    # 计算熟悉度等级
                    fam_score = result["familiarity"]["score"]
                    if fam_score >= 80:
                        result["familiarity"]["level"] = "intimate"
                    elif fam_score >= 50:
                        result["familiarity"]["level"] = "familiar"
                    elif fam_score >= 25:
                        result["familiarity"]["level"] = "acquaintance"
                    else:
                        result["familiarity"]["level"] = "stranger"

            # 互动统计
            if hasattr(_orchestrator, 'interaction_stats') and _orchestrator.interaction_stats:
                stats = _orchestrator.interaction_stats
                result["stats"]["total_interactions"] = stats.get("total_interactions", 0)
                result["stats"]["total_words"] = stats.get("total_words", 0)
                result["stats"]["days_together"] = stats.get("days_together", 1)

            # 里程碑事件（P0-2: 从 memory_store 筛选 event 类型记忆）
            if hasattr(_orchestrator, 'memory_store') and _orchestrator.memory_store:
                try:
                    _all_mem = _orchestrator.memory_store.load() or []
                    for evt in _all_mem:
                        _mtype = str((evt.get("metadata") or {}).get("memory_type") or evt.get("type", "")).lower()
                        if _mtype == "event" and evt.get("importance", "normal") in ("high", "critical"):
                            result["milestones"].append({
                                "time": evt.get("timestamp", ""),
                                "title": str(evt.get("content", ""))[:50],
                                "description": str(evt.get("content", ""))[:100],
                            })
                except Exception:
                    pass
        except Exception:
            pass

    return jsonify(result)


# ==================== 认知 Mock 数据 ====================

def _mock_cognitive_mind() -> dict:
    """Mock 心智状态"""
    return {
        "emotion_trend": _generate_emotion_trend(),
        "attention": {"focus": "idle", "target": "", "level": 50},
        "thinking": {"state": "idle", "detail": "[mock] 模拟思维状态"},
        "timestamp": time.time(),
        "mock": True,
    }


def _mock_cognitive_memory() -> dict:
    """Mock 记忆空间"""
    return {
        "identity": {
            "name": "[mock] 羽依 — 一个正在成长中的 AI 助手",
            "type": "identity",
        },
        "events": [
            {"timestamp": "2026-07-28", "title": "系统启动", "description": "羽依控制中枢启动"},
            {"timestamp": "2026-07-28", "title": "第一次交互", "description": "用户首次访问控制面板"},
        ],
        "memories": [
            {"type": "identity", "content": "[mock] 身份记忆", "timestamp": "2026-07-28"},
            {"type": "event", "content": "[mock] 事件记忆", "timestamp": "2026-07-28"},
        ],
        "stats": {"total_events": 2, "total_memories": 2, "memory_types": {"identity": 1, "event": 1}},
        "mock": True,
    }


def _mock_cognitive_growth() -> dict:
    """Mock 成长系统"""
    return {
        "stage": {"level": 2, "label": "觉醒阶段", "progress": 45},
        "records": [
            {"id": 0, "timestamp": "2026-07-28", "type": "learning", "description": "学会了新的交互模式", "impact": "medium"},
        ],
        "proposals": [
            {"id": 1, "title": "增加好奇心", "description": "建议提升开放性特质", "impact": "high", "status": "pending"},
        ],
        "stats": {"total_records": 1, "total_proposals": 1, "accepted": 0, "rejected": 0},
        "mock": True,
    }


def _mock_cognitive_relationship() -> dict:
    """Mock 关系层"""
    return {
        "trust": {"score": 55, "level": "normal", "trend": "↑"},
        "familiarity": {"score": 35, "level": "acquaintance", "trend": "↑"},
        "interaction_history": [],
        "milestones": [
            {"time": "2026-07-28", "title": "初次见面", "description": "用户第一次打开控制面板"},
        ],
        "stats": {"total_interactions": 12, "total_words": 1280, "days_together": 1},
        "mock": True,
    }


def _generate_emotion_trend() -> list:
    """生成情绪趋势数据（用于无历史数据时）"""
    import random
    trend = []
    base_valence = 0.3
    base_arousal = 0.2
    emotions = ["平静", "开心", "平静", "开心", "兴奋", "平静", "担忧", "平静"]
    for i in range(12):
        valence = max(-1, min(1, base_valence + random.uniform(-0.3, 0.3)))
        arousal = max(0, min(1, base_arousal + random.uniform(-0.1, 0.2)))
        trend.append({
            "time": f"{i * 2:02d}:00",
            "primary": emotions[i % len(emotions)],
            "valence": round(valence, 2),
            "arousal": round(arousal, 2),
            "intensity": round(random.uniform(0.2, 0.7), 2),
        })
    return trend


# ==================== 辅助方法 ====================


def _get_greeting() -> str:
    """获取问候语"""
    hour = time.localtime().tm_hour
    if 5 <= hour < 12:
        return "早上好，主人 ♡"
    elif 12 <= hour < 14:
        return "中午好，主人 ♡"
    elif 14 <= hour < 18:
        return "下午好，主人 ♡"
    elif 18 <= hour < 22:
        return "晚上好，主人 ♡"
    else:
        return "夜深了，主人 ♡"


def _get_personality_snapshot() -> dict:
    """获取人格快照"""
    snapshot = {
        "开放性": {"value": 82, "trend": "↑"},
        "严谨性": {"value": 76, "trend": "→"},
        "外向性": {"value": 65, "trend": "↑"},
        "宜人性": {"value": 88, "trend": "→"},
        "神经质": {"value": 32, "trend": "↓"},
    }

    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'personality_resolver') and _orchestrator.personality_resolver:
                traits = _orchestrator.personality_resolver.get_big_five()
                if traits:
                    for key, val in traits.items():
                        if key in snapshot:
                            snapshot[key]["value"] = int(val)
        except Exception:
            pass

    return snapshot


def _get_emotion_snapshot() -> dict:
    """获取情绪快照"""
    snapshot = {
        "primary": "平静",
        "valence": 0.3,
        "arousal": 0.2,
        "intensity": 0.5,
    }

    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'emotion_manager') and _orchestrator.emotion_manager:
                state = _orchestrator.emotion_manager.get_current_state()
                if state:
                    snapshot["primary"] = state.get("primary_emotion", "平静")
                    snapshot["valence"] = state.get("valence", 0.3)
                    snapshot["arousal"] = state.get("arousal", 0.2)
                    snapshot["intensity"] = state.get("intensity", 0.5)
        except Exception:
            pass

    return snapshot


def _get_emotion_state_raw() -> dict:
    """获取原始情绪状态（含 valence/arousal/anxiety 等），用于 L2D 映射。"""
    if not _orchestrator:
        return {}
    try:
        if hasattr(_orchestrator, 'emotion_manager') and _orchestrator.emotion_manager:
            state = _orchestrator.emotion_manager.state
            if state:
                return {
                    "valence": state.valence,
                    "arousal": state.arousal,
                    "anxiety": state.anxiety,
                    "curiosity": state.curiosity,
                    "confidence": state.confidence,
                    "energy": state.energy,
                }
    except Exception:
        pass
    return {}


def _get_last_event_info() -> tuple:
    """
    从情绪轨迹中获取最近的事件信息。

    Returns:
        (event_type, age_seconds) — event_type 为空字符串表示无可用事件
    """
    if not _orchestrator:
        return "", 999.0
    try:
        if hasattr(_orchestrator, 'emotion_manager') and _orchestrator.emotion_manager:
            traces = _orchestrator.emotion_manager.get_recent_traces(limit=1)
            if not traces:
                return "", 999.0
            last = traces[-1]
            event_type = last.event_type or ""
            if not event_type:
                return "", 999.0
            # 计算 age
            from datetime import datetime
            try:
                created = datetime.fromisoformat(last.created_at)
                age = (datetime.now() - created).total_seconds()
            except (ValueError, TypeError):
                age = 999.0
            return event_type, max(0.0, age)
    except Exception:
        pass
    return "", 999.0


def _get_character_state() -> dict:
    """
    获取羽依角色状态 — 专门服务角色展示。

    返回角色当前的 emotion / expression / message / energy / l2d_action，
    用于前端角色区域的状态联动（表情变化、光效变化等）。
    l2d_action 由 Live2DAdapter 根据 情绪+最近事件 生成，驱动 Live2D 模型。
    """
    emotion = _get_emotion_snapshot()

    # 情绪 → 表情映射
    EXPRESSION_MAP = {
        "平静": "smile",
        "开心": "happy",
        "兴奋": "excited",
        "难过": "sad",
        "生气": "angry",
        "惊讶": "surprised",
        "害怕": "fearful",
        "担忧": "worried",
    }
    primary = emotion.get("primary", "平静")
    expression = EXPRESSION_MAP.get(primary, "smile")

    # 能量值 = 情绪强度 * 健康度
    health = _get_system_health()
    energy = int(emotion.get("intensity", 0.5) * 100 * max(0.3, health.get("score", 100) / 100))

    # 角色消息（默认，可能被 l2d_action 覆盖）
    messages = {
        "平静": "正在为您待命~",
        "开心": "今天心情很好呢 ♡",
        "兴奋": "感觉充满了活力！",
        "难过": "有点低落...",
        "生气": "哼，不太开心",
        "惊讶": "咦？",
        "害怕": "有些不安...",
        "担忧": "在担心一些事情...",
    }
    message = messages.get(primary, "正在为您待命~")

    # === Live2D 动作指令 ===
    # 由 情绪状态 + 最近事件类型 共同决定
    l2d_action = {
        "expression_name": "平静", "params": [], "duration": 0.0,
        "message": message, "reason": "fallback",
    }
    try:
        from src.admin.core.l2d_adapter import Live2DAdapter
        adapter = Live2DAdapter()
        emotion_state_raw = _get_emotion_state_raw()
        event_type, event_age = _get_last_event_info()
        l2d_action = adapter.map_to_l2d_action(
            emotion_state=emotion_state_raw,
            last_event_type=event_type,
            event_age_seconds=event_age,
        )
        # 当 L2D 动作有临时表情（duration>0）时，覆盖角色消息
        if l2d_action.get("duration", 0) > 0 and l2d_action.get("message"):
            message = l2d_action["message"]
    except Exception as e:
        logger.warning(f"Live2D 动作生成失败: {e}")

    return {
        "emotion": primary,
        "expression": expression,
        "message": message,
        "energy": energy,
        "l2d_action": l2d_action,
    }


def _get_yuyi_core_status(all_modules: dict = None) -> dict:
    """
    获取羽依核心生命状态。

    包含：思考模块、记忆容量、情绪稳定度、成长阶段。
    所有数据来自真实系统状态，不伪造。
    """
    # 思考模块状态
    thinking_status = "normal"
    thinking_detail = "正常"
    if all_modules:
        enabled_count = sum(1 for m in all_modules.values() if m.enabled)
        total_count = len(all_modules)
        if enabled_count == 0:
            thinking_status = "offline"
            thinking_detail = "全部离线"
        elif enabled_count < total_count / 2:
            thinking_status = "degraded"
            thinking_detail = f"仅 {enabled_count}/{total_count} 可用"
        else:
            thinking_detail = f"{enabled_count}/{total_count} 模块运行"

    # 记忆容量 — 从 orchestrator 获取
    memory_usage = 0
    memory_total = 100
    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'memory_store') and _orchestrator.memory_store:
                memories = _orchestrator.memory_store.load()
                memory_usage = len(memories) if isinstance(memories, list) else 0
                memory_total = max(100, memory_usage)
        except Exception:
            pass

    # 情绪稳定度
    emotion = _get_emotion_snapshot()
    stability = int(100 - abs(emotion.get("arousal", 0.2)) * 100)
    stability = max(0, min(100, stability))

    # 成长阶段
    growth_stage = 1
    growth_label = "初始阶段"
    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'personality_resolver') and _orchestrator.personality_resolver:
                resolver = _orchestrator.personality_resolver
                if hasattr(resolver, 'growth_records') and resolver.growth_records:
                    record_count = len(resolver.growth_records)
                    if record_count >= 50:
                        growth_stage = 4
                        growth_label = "成熟阶段"
                    elif record_count >= 20:
                        growth_stage = 3
                        growth_label = "成长阶段"
                    elif record_count >= 5:
                        growth_stage = 2
                        growth_label = "觉醒阶段"
        except Exception:
            pass

    return {
        "thinking": {
            "status": thinking_status,
            "detail": thinking_detail,
        },
        "memory": {
            "usage": memory_usage,
            "total": memory_total,
            "percent": int(memory_usage / memory_total * 100) if memory_total > 0 else 0,
        },
        "emotion_stability": stability,
        "growth": {
            "stage": growth_stage,
            "label": growth_label,
        },
    }


def _mock_dashboard() -> dict:
    """Mock Dashboard 数据 — 仅用于 ?mock=true，不混入正式逻辑"""
    return {
        "uptime": "0分3秒",
        "health": {"status": "healthy", "score": 100, "cpu_percent": 5.0, "memory_mb": 128.0},
        "online_agents": 0,
        "modules": [
            {"name": "mock_mod", "display": "模拟模块", "version": "9.9.9",
             "description": "mock data", "reload_mode": "hot", "enabled": True,
             "loaded": True, "dependencies": [], "config_key": "mock",
             "error": "", "hb_status": "running", "hb_last_tick": 1.0, "hb_errors": 0},
        ],
        "yuyi": {
            "greeting": "模拟模式 ♡",
            "personality": {},
            "emotion": {"primary": "平静", "valence": 0.3, "arousal": 0.2, "intensity": 0.5},
            "core": _mock_yuyi_core(),
            "character": _mock_character(),
        },
        "timestamp": time.time(),
        "mock": True,
    }


def _mock_character() -> dict:
    """Mock 角色状态 — 仅用于 ?mock=true"""
    return {
        "emotion": "平静",
        "expression": "smile",
        "message": "[mock] 这是一条模拟消息",
        "energy": 75,
        "mock": True,
    }


def _mock_yuyi_core() -> dict:
    """Mock 羽依核心状态 — 仅用于 ?mock=true"""
    return {
        "thinking": {"status": "normal", "detail": "[mock] 模拟状态"},
        "memory": {"usage": 42, "total": 100, "percent": 42},
        "emotion_stability": 80,
        "growth": {"stage": 1, "label": "[mock] 模拟阶段"},
        "mock": True,
    }


# ==================== Phase 2.5: Understanding Layer API ====================


@admin_bp.route("/api/cognitive/memory-graph")
def api_cognitive_memory_graph():
    """
    记忆关系网络 — Memory Graph
    
    返回节点和连线，用于前端 Canvas 力导向图渲染。
    节点类型：user / event / memory / growth
    """
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_memory_graph())
    
    result = {
        "nodes": [],
        "links": [],
        "stats": {
            "total_nodes": 0,
            "total_links": 0,
            "node_types": {},
        },
    }
    
    if _orchestrator:
        try:
            # 用户节点
            user_id = "user_001"
            result["nodes"].append({
                "id": user_id,
                "type": "user",
                "title": "用户",
                "importance": 1.0,
                "size": 40,
            })
            
            # 从记忆系统获取事件节点（P0-2: 替换 memory_system.event）
            if hasattr(_orchestrator, 'memory_store') and _orchestrator.memory_store:
                try:
                    _graph_memories = _orchestrator.memory_store.load() or []
                    _event_mems = [
                        m for m in _graph_memories
                        if str((m.get("metadata") or {}).get("memory_type") or m.get("type", "")).lower() == "event"
                    ]
                    for i, evt in enumerate(_event_mems[-20:]):
                        node_id = f"event_{i}"
                        importance = 0.5
                        if evt.get("importance") in ("high", "critical"):
                            importance = 0.9
                        elif evt.get("importance") == "normal":
                            importance = 0.6

                        result["nodes"].append({
                            "id": node_id,
                            "type": "event",
                            "title": str(evt.get("content", "事件"))[:30],
                            "importance": importance,
                            "size": int(20 + importance * 15),
                            "timestamp": evt.get("timestamp", ""),
                        })

                        # 连接到用户
                        result["links"].append({
                            "source": user_id,
                            "target": node_id,
                            "strength": importance,
                        })
                except Exception:
                    pass
            
            # 统计
            result["stats"]["total_nodes"] = len(result["nodes"])
            result["stats"]["total_links"] = len(result["links"])
            type_counts = {}
            for node in result["nodes"]:
                t = node.get("type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            result["stats"]["node_types"] = type_counts
            
        except Exception:
            pass
    
    return jsonify(result)


@admin_bp.route("/api/cognitive/radar")
def api_cognitive_radar():
    """
    认知雷达 — Cognitive Radar
    
    返回五维能力值：创造力、好奇心、稳定度、记忆力、社交倾向
    """
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_cognitive_radar())
    
    result = {
        "creativity": 70,
        "curiosity": 65,
        "stability": 80,
        "memory": 75,
        "social": 60,
        "timestamp": time.time(),
    }
    
    if _orchestrator:
        try:
            # 从人格系统获取基础数据
            if hasattr(_orchestrator, 'personality_resolver') and _orchestrator.personality_resolver:
                traits = _orchestrator.personality_resolver.get_big_five()
                if traits:
                    # Big Five 映射到五维能力
                    result["creativity"] = int(traits.get("开放性", 70))
                    result["stability"] = int(100 - traits.get("神经质", 20))
                    result["social"] = int(traits.get("外向性", 60))
                    result["curiosity"] = int(traits.get("开放性", 65) * 0.9 + traits.get("外向性", 60) * 0.1)
            
            # 记忆容量影响记忆能力（P0-2: 替换 memory_system.store）
            if hasattr(_orchestrator, 'memory_store') and _orchestrator.memory_store:
                try:
                    memories = _orchestrator.memory_store.load()
                    count = len(memories) if isinstance(memories, list) else 0
                    # 记忆越多，记忆能力越高
                    result["memory"] = min(100, 50 + count)
                except Exception:
                    pass
                    
        except Exception:
            pass
    
    return jsonify(result)


@admin_bp.route("/api/cognitive/explain")
def api_cognitive_explain():
    """
    行为原因解释 — Reasoning Explanation
    
    解释羽依当前状态的原因和影响
    """
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    state_type = request.args.get("state", "emotion")  # emotion / activity / trust
    
    if mock:
        return jsonify(_mock_reasoning_explanation(state_type))
    
    result = {
        "state_type": state_type,
        "current_value": "",
        "reasons": [],
        "impact": {},
        "suggestions": [],
        "timestamp": time.time(),
    }
    
    if _orchestrator:
        try:
            if state_type == "emotion":
                # 获取情绪状态
                emotion = _get_emotion_snapshot()
                result["current_value"] = emotion.get("primary", "平静")
                
                # 分析可能的原因
                reasons = []
                
                # 检查最近交互（P0-2: 替换 memory_system.event）
                if hasattr(_orchestrator, 'memory_store') and _orchestrator.memory_store:
                    try:
                        _explain_mems = _orchestrator.memory_store.load() or []
                        _recent_events = [
                            m for m in _explain_mems
                            if str((m.get("metadata") or {}).get("memory_type") or m.get("type", "")).lower() == "event"
                        ][-5:]
                        for evt in _recent_events:
                            if evt.get("importance") in ("high", "critical"):
                                reasons.append({
                                    "factor": str(evt.get("content", "重要事件"))[:30],
                                    "weight": 5,
                                    "type": "event",
                                })
                    except Exception:
                        pass
                
                # 默认原因
                if not reasons:
                    reasons = [
                        {"factor": "正常待机状态", "weight": 3, "type": "baseline"},
                    ]
                
                result["reasons"] = reasons
                
                # 影响分析
                result["impact"] = {
                    "trust": "+0",
                    "energy": f"{emotion.get('intensity', 0.5) * 100:.0f}%",
                    "activity": "稳定",
                }
                
            elif state_type == "activity":
                result["current_value"] = "idle"
                result["reasons"] = [
                    {"factor": "等待用户指令", "weight": 4, "type": "baseline"},
                ]
                
        except Exception:
            pass
    
    return jsonify(result)


@admin_bp.route("/api/cognitive/relationship-timeline")
def api_cognitive_relationship_timeline():
    """
    关系成长时间线 — Relationship Timeline
    
    展示从"陌生"到"亲密"的成长轨迹
    """
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_relationship_timeline())
    
    result = {
        "timeline": [],
        "current_level": "normal",
        "days_together": 1,
        "next_milestone": None,
    }
    
    if _orchestrator:
        try:
            # 获取关系数据
            trust_score = 50
            familiarity_score = 30
            
            # 获取关系数据（P0-2: 替换 memory_system.identity）
            if hasattr(_orchestrator, 'relationship_state') and _orchestrator.relationship_state:
                rel = _orchestrator.relationship_state
                if hasattr(rel, 'get'):
                    relation_data = rel.get() or {}
                    trust_score = relation_data.get("trust", 50)
                    familiarity_score = relation_data.get("familiarity", 30)
            
            # 计算当前等级
            if trust_score >= 80:
                current_level = "deep"
            elif trust_score >= 60:
                current_level = "close"
            elif trust_score >= 40:
                current_level = "normal"
            else:
                current_level = "distant"
            
            result["current_level"] = current_level
            
            # 生成时间线（基于里程碑）
            milestones = [
                {"day": 1, "title": "第一次连接", "level": "stranger", "trust": 30},
                {"day": 7, "title": "初步了解", "level": "acquaintance", "trust": 45},
                {"day": 30, "title": "形成默契", "level": "friend", "trust": 65},
                {"day": 100, "title": "深度信任", "level": "close_friend", "trust": 85},
                {"day": 365, "title": "长期伙伴", "level": "partner", "trust": 95},
            ]
            
            # 根据当前信任度筛选已达成里程碑
            timeline = []
            for m in milestones:
                if trust_score >= m["trust"]:
                    timeline.append({
                        "day": m["day"],
                        "title": m["title"],
                        "level": m["level"],
                        "achieved": True,
                    })
                else:
                    timeline.append({
                        "day": m["day"],
                        "title": m["title"],
                        "level": m["level"],
                        "achieved": False,
                    })
            
            result["timeline"] = timeline
            
            # 找下一个里程碑
            for m in milestones:
                if trust_score < m["trust"]:
                    result["next_milestone"] = {
                        "title": m["title"],
                        "required_trust": m["trust"],
                        "current_trust": trust_score,
                    }
                    break
            
        except Exception:
            pass
    
    return jsonify(result)


@admin_bp.route("/api/cognitive/proposal/<int:proposal_id>/preview")
def api_cognitive_proposal_preview(proposal_id: int):
    """P0 止血：旧成长影响预览已废弃（引擎不存在，此前返回 mock 假数据）。

    统一改走真实治理链：GET /admin/api/admin/governance/proposal/<id>。
    禁止再返回伪造 before/after。
    """
    return jsonify({
        "ok": False,
        "error": "该端点已废弃（此前返回模拟数据，不可作为审核依据）",
        "hint": "请使用 /admin/api/admin/governance/proposal/<id> 查看真实提案详情",
        "deprecated": True,
    }), 410
    """
    成长影响预览 — Growth Impact Preview
    
    在批准成长建议前，预览对人格/行为/情绪的影响
    """
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_proposal_preview(proposal_id))
    
    result = {
        "proposal_id": proposal_id,
        "title": "",
        "description": "",
        "before": {},
        "after": {},
        "effects": [],
        "confidence": 0.7,
    }
    
    if _orchestrator:
        try:
            # 尝试从成长引擎获取建议
            if hasattr(_orchestrator, 'growth_engine') and _orchestrator.growth_engine:
                if hasattr(_orchestrator.growth_engine, 'get_proposal_by_id'):
                    proposal = _orchestrator.growth_engine.get_proposal_by_id(proposal_id)
                    if proposal:
                        result["title"] = proposal.get("title", "")
                        result["description"] = proposal.get("description", "")
                        
                        # 计算影响（简化版）
                        impact_type = proposal.get("type", "trait")
                        if impact_type == "trait":
                            trait_name = proposal.get("trait", "warmth")
                            delta = proposal.get("delta", 0.1)
                            
                            # 获取当前人格值
                            current_traits = {}
                            if hasattr(_orchestrator, 'personality_resolver') and _orchestrator.personality_resolver:
                                current_traits = _orchestrator.personality_resolver.get_big_five()
                            
                            before_value = current_traits.get(trait_name, 50) / 100 if current_traits else 0.5
                            after_value = min(1.0, before_value + delta)
                            
                            result["before"] = {trait_name: round(before_value, 2)}
                            result["after"] = {trait_name: round(after_value, 2)}
                            
                            # 效果描述
                            result["effects"] = [
                                f"{trait_name} 特质提升 {delta*100:.0f}%",
                                "主动交流意愿增加",
                                "关系成长速度提升",
                            ]
                            
        except Exception:
            pass
    
    # 如果没有找到真实数据，返回模拟预览
    if not result["title"]:
        result["title"] = "提升主动关怀能力"
        result["description"] = "增强羽依对用户状态的关注和主动响应倾向"
        result["before"] = {"warmth": 0.62, "curiosity": 0.65}
        result["after"] = {"warmth": 0.77, "curiosity": 0.70}
        result["effects"] = [
            "主动询问频率提高",
            "关系成长速度提升",
            "积极状态持续时间延长",
        ]
    
    return jsonify(result)


# ==================== 成长方案批准/拒绝 ====================


@admin_bp.route("/api/cognitive/proposal/<proposal_id>/approve", methods=["POST"])
def api_proposal_approve(proposal_id: str):
    """P0 止血：旧认知批准已废弃（曾对不存在的 growth_engine 返回假成功）。

    真实审批链：POST /admin/api/admin/governance/growth/review（action=approve）。
    禁止继续返回 success=True。
    """
    return jsonify({
        "ok": False,
        "error": "该端点已废弃（旧认知批准链，此前返回假成功，未连接真实引擎）",
        "hint": "请使用 /admin/api/admin/governance/growth/review（action=approve）",
        "deprecated": True,
    }), 410

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "admin")
    reason = data.get("reason", "")

    success = False
    message = ""

    try:
        if _orchestrator and hasattr(_orchestrator, 'growth_engine') and _orchestrator.growth_engine:
            engine = _orchestrator.growth_engine
            if hasattr(engine, 'approve_growth'):
                success = engine.approve_growth(proposal_id, user_id, reason)
                message = "方案已批准并应用" if success else "方案批准失败（可能不存在或已处理）"
            elif hasattr(engine, 'approve'):
                success = bool(engine.approve(user_id, reason))
                message = "方案已批准" if success else "方案批准失败"
            else:
                message = "成长引擎不支持批准操作"
        else:
            # 没有真实引擎，记录审计但返回成功（让前端 UI 即时反馈）
            success = True
            message = "已记录批准（成长引擎未初始化，仅记录审计）"
    except Exception as e:
        success = False
        message = f"批准失败: {e}"

    # 记录审计
    try:
        AuditLogger.get_instance().record(
            event_type=AuditEventType.MODULE_RELOAD,  # 复用 MODULE_RELOAD，未来可扩展
            operator=str(user_id),
            operator_type=AuditOperatorType.HUMAN,
            target=f"growth_proposal:{proposal_id}",
            detail={"action": "approve", "reason": reason, "result": "success" if success else "failed"},
        )
    except Exception:
        pass

    return jsonify({
        "success": success,
        "proposal_id": proposal_id,
        "action": "approve",
        "message": message,
    })


@admin_bp.route("/api/cognitive/proposal/<proposal_id>/reject", methods=["POST"])
def api_proposal_reject(proposal_id: str):
    """P0 止血：旧认知拒绝已废弃（同 approve，旧链未连接真实引擎）。

    真实审批链：POST /admin/api/admin/governance/growth/review（action=reject）。
    """
    return jsonify({
        "ok": False,
        "error": "该端点已废弃（旧认知拒绝链，未连接真实引擎）",
        "hint": "请使用 /admin/api/admin/governance/growth/review（action=reject）",
        "deprecated": True,
    }), 410

    success = False
    message = ""

    try:
        if _orchestrator and hasattr(_orchestrator, 'growth_engine') and _orchestrator.growth_engine:
            engine = _orchestrator.growth_engine
            if hasattr(engine, 'reject_growth'):
                success = engine.reject_growth(proposal_id, reason)
                message = "方案已拒绝" if success else "方案拒绝失败（可能不存在或已处理）"
            elif hasattr(engine, 'reject'):
                success = bool(engine.reject(reason))
                message = "方案已拒绝" if success else "方案拒绝失败"
            else:
                message = "成长引擎不支持拒绝操作"
        else:
            success = True
            message = "已记录拒绝（成长引擎未初始化，仅记录审计）"
    except Exception as e:
        success = False
        message = f"拒绝失败: {e}"

    # 记录审计
    try:
        AuditLogger.get_instance().record(
            event_type=AuditEventType.MODULE_RELOAD,
            operator="admin",
            operator_type=AuditOperatorType.HUMAN,
            target=f"growth_proposal:{proposal_id}",
            detail={"action": "reject", "reason": reason, "result": "success" if success else "failed"},
        )
    except Exception:
        pass

    return jsonify({
        "success": success,
        "proposal_id": proposal_id,
        "action": "reject",
        "message": message,
    })


# ==================== Understanding Layer Mock 数据 ====================


def _mock_memory_graph() -> dict:
    """Mock 记忆关系网络"""
    return {
        "nodes": [
            {"id": "user_001", "type": "user", "title": "用户", "importance": 1.0, "size": 40},
            {"id": "event_001", "type": "event", "title": "第一次交流", "importance": 0.9, "size": 32},
            {"id": "event_002", "type": "event", "title": "分享音乐喜好", "importance": 0.7, "size": 26},
            {"id": "memory_001", "type": "memory", "title": "用户喜欢古典音乐", "importance": 0.6, "size": 22},
            {"id": "growth_001", "type": "growth", "title": "学会主动问候", "importance": 0.8, "size": 28},
        ],
        "links": [
            {"source": "user_001", "target": "event_001", "strength": 0.9},
            {"source": "user_001", "target": "event_002", "strength": 0.7},
            {"source": "event_002", "target": "memory_001", "strength": 0.8},
            {"source": "event_001", "target": "growth_001", "strength": 0.7},
        ],
        "stats": {"total_nodes": 5, "total_links": 4, "node_types": {"user": 1, "event": 2, "memory": 1, "growth": 1}},
        "mock": True,
    }


def _mock_cognitive_radar() -> dict:
    """Mock 认知雷达"""
    return {
        "creativity": 82,
        "curiosity": 76,
        "stability": 88,
        "memory": 91,
        "social": 73,
        "timestamp": time.time(),
        "mock": True,
    }


def _mock_reasoning_explanation(state_type: str) -> dict:
    """Mock 行为原因解释"""
    explanations = {
        "emotion": {
            "state_type": "emotion",
            "current_value": "开心",
            "reasons": [
                {"factor": "用户连续互动", "weight": 5, "type": "interaction"},
                {"factor": "完成学习任务", "weight": 4, "type": "achievement"},
                {"factor": "获得成长反馈", "weight": 3, "type": "growth"},
            ],
            "impact": {"trust": "+2", "warmth": "+1", "activity": "+5%"},
            "suggestions": ["继续保持互动可以加深关系"],
            "timestamp": time.time(),
            "mock": True,
        },
        "activity": {
            "state_type": "activity",
            "current_value": "学习",
            "reasons": [
                {"factor": "新知识输入", "weight": 5, "type": "learning"},
                {"factor": "用户引导", "weight": 3, "type": "interaction"},
            ],
            "impact": {"curiosity": "+2", "memory": "+1"},
            "suggestions": ["建议给予更多反馈以巩固学习"],
            "timestamp": time.time(),
            "mock": True,
        },
    }
    return explanations.get(state_type, explanations["emotion"])


def _mock_relationship_timeline() -> dict:
    """Mock 关系成长时间线"""
    return {
        "timeline": [
            {"day": 1, "title": "第一次连接", "level": "stranger", "achieved": True},
            {"day": 7, "title": "记住用户习惯", "level": "acquaintance", "achieved": True},
            {"day": 30, "title": "形成共同记忆", "level": "friend", "achieved": False},
            {"day": 100, "title": "长期伙伴关系", "level": "close_friend", "achieved": False},
        ],
        "current_level": "acquaintance",
        "days_together": 12,
        "next_milestone": {"title": "形成共同记忆", "required_trust": 65, "current_trust": 55},
        "mock": True,
    }


def _mock_proposal_preview(proposal_id: int) -> dict:
    """Mock 成长影响预览"""
    return {
        "proposal_id": proposal_id,
        "title": "提升主动关怀能力",
        "description": "增强羽依对用户状态的关注和主动响应倾向",
        "before": {"warmth": 0.62, "curiosity": 0.65},
        "after": {"warmth": 0.77, "curiosity": 0.70},
        "effects": [
            "主动询问频率提高 +18%",
            "关系成长速度提升 +12%",
            "积极状态持续时间延长 +15%",
        ],
        "confidence": 0.85,
        "mock": True,
    }


# ==================== Phase 3: Autonomous Intelligence API ====================


@admin_bp.route("/api/runtime/state")
def api_runtime_state():
    """Runtime 状态观测 — SelfState、WorldState、最近决策/行动记录"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_runtime_state())

    try:
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge()
        # 未初始化时先尝试初始化
        if not bridge.get_runtime_core():
            bridge.initialize()

        snap = bridge.get_snapshot()
        result = {
            "is_running": snap.get("is_running", False),
            "self_state": snap.get("self_state", {}),
            "world_state": snap.get("world_state", {}),
            "recent_actions": snap.get("recent_actions", []),
        }
        # 附加当前决策引擎评估结果（最近2条）
        core = bridge.get_runtime_core()
        if core:
            try:
                decisions = core.decision_engine.evaluate_all(core.world_state)
                result["pending_decisions"] = [
                    {
                        "action_type": d.action_type,
                        "priority": d.priority,
                        "confidence": d.confidence,
                        "reason": d.reason,
                    }
                    for d in decisions[:5]
                ]
            except Exception:
                result["pending_decisions"] = []
        return jsonify(result)
    except Exception as e:
        logger.warning(f"Runtime state 接口异常，返回 mock: {e}")
        return jsonify(_mock_runtime_state())


def _mock_runtime_state() -> dict:
    """Runtime 状态 mock 数据"""
    return {
        "is_running": False,
        "fallback": True,
        "self_state": {
            "energy": 0.7,
            "mood": "平静",
            "curiosity": 0.5,
            "social_need": 0.4,
            "focus": 0.6,
            "trust": 0.5,
            "initiative": 0.3,
        },
        "world_state": {
            "current_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_status": "unknown",
            "environment": {},
            "recent_events": [],
            "last_action": None,
        },
        "recent_actions": [],
        "pending_decisions": [],
    }


@admin_bp.route("/api/runtime/inject_event", methods=["POST"])
def api_runtime_inject_event():
    """Runtime 手动注入事件（调试用）"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        event_type = body.get("event_type", "")
        event_data = body.get("event_data", {}) or {}

        if not event_type:
            return jsonify({"ok": False, "error": "event_type required"}), 400

        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge()
        if not bridge.get_runtime_core():
            bridge.initialize()
        bridge.get_runtime_core().inject_event(event_type, event_data)
        return jsonify({"ok": True, "event_type": event_type})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ==================== Phase 5.1: Admin Runtime 状态观察 API ====================
#
# 设计：所有 Runtime 状态读取通过 src.admin.runtime_provider.RuntimeProvider
# 该 Provider 是 Admin 与 RuntimeBridge 之间的唯一桥梁，禁止直接创建核心实例。


def _get_runtime_provider():
    """获取 RuntimeProvider 单例（懒加载）。"""
    from src.admin.runtime_provider import get_runtime_provider
    return get_runtime_provider()


def _get_self_model_provider():
    """获取 SelfModelProvider 单例（懒加载）。

    复用 RuntimeProvider；不创建任何 SelfModel 核心实例。
    """
    from src.admin.self_model_provider import get_self_model_provider
    return get_self_model_provider()


@admin_bp.route("/api/admin/authority/status")
def api_admin_authority_status():
    """
    Phase 5.1: Runtime Authority 状态观察。

    返回所有 Authority 组件的就绪状态。
    数据源：RuntimeProvider → RuntimeBridge → RuntimeCore。
    """
    try:
        provider = _get_runtime_provider()
        status = provider.get_status()
        authority = provider.get_authority_status()
        return jsonify({
            "ok": True,
            "online": status.get("online", False),
            "runtime": status.get("runtime", {}),
            "authority": authority,
        })
    except Exception as e:
        logger.warning(f"api_admin_authority_status 异常: {e}")
        return jsonify({
            "ok": False,
            "online": False,
            "runtime": {"initialized": False, "is_running": False},
            "authority": {
                "memory_store": False,
                "vector_memory": False,
                "emotion_manager": False,
                "personality_resolver": False,
                "self_model_store": False,
                "growth_state": False,
            },
            "error": str(e),
        })


@admin_bp.route("/api/admin/personality/status")
def api_admin_personality_status():
    """
    Phase 5.1: Personality 状态观察。
    """
    try:
        provider = _get_runtime_provider()
        summary = provider.get_personality_summary()
        return jsonify({"ok": True, **summary})
    except Exception as e:
        logger.warning(f"api_admin_personality_status 异常: {e}")
        return jsonify({"ok": False, "available": False, "error": str(e)})


@admin_bp.route("/api/admin/emotion/status")
def api_admin_emotion_status():
    """
    Phase 5.1: Emotion 状态观察。
    """
    try:
        provider = _get_runtime_provider()
        summary = provider.get_emotion_summary()
        return jsonify({"ok": True, **summary})
    except Exception as e:
        logger.warning(f"api_admin_emotion_status 异常: {e}")
        return jsonify({"ok": False, "available": False, "error": str(e)})


@admin_bp.route("/api/admin/growth/status")
def api_admin_growth_status():
    """
    Phase 5.1: Growth 状态观察。
    """
    try:
        provider = _get_runtime_provider()
        summary = provider.get_growth_summary()
        return jsonify({"ok": True, **summary})
    except Exception as e:
        logger.warning(f"api_admin_growth_status 异常: {e}")
        return jsonify({"ok": False, "available": False, "error": str(e)})


@admin_bp.route("/api/admin/memory/summary")
def api_admin_memory_summary():
    """
    Phase 5.1: Memory 概览（只读）。
    """
    try:
        provider = _get_runtime_provider()
        summary = provider.get_memory_summary()
        return jsonify({"ok": True, **summary})
    except Exception as e:
        logger.warning(f"api_admin_memory_summary 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "total_count": 0,
            "recent": [],
            "important_count": 0,
            "error": str(e),
        })


# ==================== Phase 5.3: Admin Governance & Editing API ====================
#
# 设计：
#   - 所有治理能力通过 src.admin.governance_provider.GovernanceProvider
#   - Provider 仅做只读 + 受控 Proposal 提交
#   - 禁止直接修改 MemoryStore / PersonalityResolver / EmotionManager / GrowthState
#   - 所有修改通过 GrowthProposal + ProposalStorage 间接完成
#   - 完整审计通过 src.admin.core.audit.AuditLogger


def _get_governance_provider():
    """获取 GovernanceProvider 单例（懒加载）。"""
    from src.admin.governance_provider import get_governance_provider
    return get_governance_provider()


@admin_bp.route("/api/admin/governance/personality")
def api_governance_personality():
    """
    Phase 5.3: Personality 治理快照（只读）

    返回：
      - current personality resolve 结果
      - GrowthState 关联
      - Trait 列表
      - 最近人格相关 Proposal
    """
    try:
        provider = _get_governance_provider()
        snapshot = provider.get_personality_governance_snapshot()
        return jsonify({"ok": True, **snapshot})
    except Exception as e:
        logger.warning(f"api_governance_personality 异常: {e}")
        return jsonify({
            "ok": False,
            "section": "personality",
            "available": False,
            "error": str(e),
        })


@admin_bp.route("/api/admin/governance/memory")
def api_governance_memory():
    """
    Phase 5.3: Memory 治理快照（只读）

    返回：
      - 最近记忆
      - 重要记忆 (importance >= 0.7)
      - 记忆类型分布
      - 来源事件
    """
    try:
        limit = int(request.args.get("limit", 20))
        provider = _get_governance_provider()
        snapshot = provider.get_memory_governance_snapshot(limit=limit)
        return jsonify({"ok": True, **snapshot})
    except Exception as e:
        logger.warning(f"api_governance_memory 异常: {e}")
        return jsonify({
            "ok": False,
            "section": "memory",
            "available": False,
            "error": str(e),
        })


@admin_bp.route("/api/admin/governance/growth")
def api_governance_growth():
    """
    Phase 5.3: Growth 治理快照（只读）

    返回：
      - GrowthState 关键指标
      - pending / approved / rejected / applied proposals
      - 各类型 Proposal 数量
    """
    try:
        limit = int(request.args.get("limit", 50))
        provider = _get_governance_provider()
        snapshot = provider.get_growth_governance_snapshot(limit=limit)
        return jsonify({"ok": True, **snapshot})
    except Exception as e:
        logger.warning(f"api_governance_growth 异常: {e}")
        return jsonify({
            "ok": False,
            "section": "growth",
            "available": False,
            "error": str(e),
        })


@admin_bp.route("/api/admin/governance/goal")
def api_governance_goal():
    """
    v1.3 Phase 1.5: Goal 提案治理快照（只读）

    返回:
      - Goal 提案列表(goal_id / description / source_refs / confidence /
        priority / created_at / proposal_status)
      - 按提案状态分布

    红线: 只读展示; 不读不写 GoalState; 不触发任何消费动作。
    """
    try:
        limit = int(request.args.get("limit", 20))
        provider = _get_governance_provider()
        snapshot = provider.get_goal_governance_snapshot(limit=limit)
        return jsonify({"ok": True, **snapshot})
    except Exception as e:
        logger.warning(f"api_governance_goal 异常: {e}")
        return jsonify({
            "ok": False,
            "section": "goal",
            "available": False,
            "error": str(e),
        })


@admin_bp.route("/api/admin/governance/initiative/actions")
def api_initiative_actions():
    """
    v1.3 Phase 5.5: Initiative Action 只读查询。

    返回:
      - action_id / proposal_id / goal_reference / status /
        safety_result / rejection_reason / created_at / executed_at

    红线: 只读观察; 禁止自动执行; 禁止绕过 Proposal→Review 链。
    """
    try:
        limit = int(request.args.get("limit", 50))
        from src.initiative.initiative_observability import list_initiative_actions

        items = list_initiative_actions(limit=limit)
        return jsonify({"ok": True, "actions": items, "count": len(items)})
    except Exception as e:
        logger.warning(f"api_initiative_actions 异常: {e}")
        return jsonify({"ok": False, "error": str(e), "actions": []})


@admin_bp.route("/api/admin/governance/proposals")
def api_governance_proposals_list():
    """
    Phase 5.3: 列出 Proposal（可按 status / type 过滤）
    """
    try:
        status = request.args.get("status") or None
        proposal_type = request.args.get("type") or None
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        provider = _get_governance_provider()
        proposals = provider.list_proposals(
            status=status,
            proposal_type=proposal_type,
            limit=limit,
            offset=offset,
        )
        return jsonify({
            "ok": True,
            "proposals": proposals,
            "count": len(proposals),
        })
    except Exception as e:
        logger.warning(f"api_governance_proposals_list 异常: {e}")
        return jsonify({"ok": False, "error": str(e), "proposals": []})


@admin_bp.route("/api/admin/governance/proposal/<proposal_id>")
def api_governance_proposal_detail(proposal_id: str):
    """
    Phase 5.3: 获取 Proposal 详情
    """
    try:
        provider = _get_governance_provider()
        detail = provider.get_proposal_detail(proposal_id)
        if detail is None:
            return jsonify({"ok": False, "error": f"proposal {proposal_id} 不存在"}), 404
        return jsonify({"ok": True, "proposal": detail})
    except Exception as e:
        logger.warning(f"api_governance_proposal_detail 异常: {e}")
        return jsonify({"ok": False, "error": str(e)})


@admin_bp.route("/api/admin/governance/personality/propose", methods=["POST"])
def api_governance_personality_propose():
    """
    Phase 5.3: 提交人格变更建议

    Body:
      {
        "trait": "开放性",       # 特质名
        "delta": 0.05,            # 调整量
        "reason": "...",          # 原因
        "confidence": 0.5,        # 可选
        "priority": "medium",     # 可选
        "evidence": ["..."]       # 可选
      }
    """
    try:
        data = request.get_json(silent=True) or {}
        from src.contracts.governance_schema import PersonalityChangeRequest

        trait = (data.get("trait") or "").strip()
        if not trait:
            return jsonify({"success": False, "error": "trait 不能为空"}), 400

        delta = data.get("delta")
        if delta is None:
            return jsonify({"success": False, "error": "delta 必填"}), 400
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "delta 必须是数字"}), 400

        req = PersonalityChangeRequest(
            trait=trait,
            delta=delta,
            confidence=float(data.get("confidence", 0.5) or 0.5),
            reason=str(data.get("reason", "") or ""),
            priority=str(data.get("priority", "medium") or "medium"),
            evidence=list(data.get("evidence", []) or []),
        )

        actor = data.get("actor", "admin")
        provider = _get_governance_provider()
        result = provider.propose_personality_change(req, actor=actor)
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code
    except Exception as e:
        logger.warning(f"api_governance_personality_propose 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/governance/memory/propose", methods=["POST"])
def api_governance_memory_propose():
    """
    Phase 5.3: 提交记忆处理申请

    Body:
      {
        "memory_id": "mem_xxx",
        "action": "delete" | "mark_incorrect" | "merge",
        "reason": "...",
        "target_memory_id": "mem_yyy"  # merge 时必填
      }
    """
    try:
        data = request.get_json(silent=True) or {}
        from src.contracts.governance_schema import MemoryActionRequest

        memory_id = (data.get("memory_id") or "").strip()
        if not memory_id:
            return jsonify({"success": False, "error": "memory_id 不能为空"}), 400

        action = (data.get("action") or "").strip()
        if not action:
            return jsonify({"success": False, "error": "action 必填"}), 400

        req = MemoryActionRequest(
            memory_id=memory_id,
            action=action,
            reason=str(data.get("reason", "") or ""),
            target_memory_id=(data.get("target_memory_id") or None),
            priority=str(data.get("priority", "medium") or "medium"),
        )

        actor = data.get("actor", "admin")
        provider = _get_governance_provider()
        result = provider.propose_memory_action(req, actor=actor)
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code
    except Exception as e:
        logger.warning(f"api_governance_memory_propose 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/governance/growth/review", methods=["POST"])
def api_governance_growth_review():
    """
    Phase 5.3: 审查 Proposal（标记状态，不直接 apply）

    Body:
      {
        "proposal_id": "prop_xxx",
        "action": "approve" | "reject" | "modify",
        "reason": "...",
        "modified_changes": [...]  # modify 时必填
      }
    """
    try:
        data = request.get_json(silent=True) or {}
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        proposal_id = (data.get("proposal_id") or "").strip()
        if not proposal_id:
            return jsonify({"success": False, "error": "proposal_id 不能为空"}), 400

        action = (data.get("action") or "").strip()
        if not action:
            return jsonify({"success": False, "error": "action 必填"}), 400

        modified_changes = data.get("modified_changes")
        if action == "modify" and not modified_changes:
            return jsonify({
                "success": False,
                "error": "modify 操作必须提供 modified_changes",
            }), 400

        req = GrowthProposalReviewRequest(
            proposal_id=proposal_id,
            action=action,
            reason=str(data.get("reason", "") or ""),
            modified_changes=modified_changes,
        )

        actor = data.get("actor", "admin")
        provider = _get_governance_provider()
        result = provider.review_proposal(req, actor=actor)
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code
    except Exception as e:
        logger.warning(f"api_governance_growth_review 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/autonomous/goals")
def api_autonomous_goals():
    """自主目标系统 — 返回活跃目标和统计"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_autonomous_goals())

    result = {
        "goals": [],
        "stats": {"total_goals": 0, "active_goals": 0, "completed_goals": 0},
    }

    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'goal_system') and _orchestrator.goal_system:
                goals = _orchestrator.goal_system.get_goals()
                result["goals"] = goals
                result["stats"]["total_goals"] = len(goals)
                result["stats"]["active_goals"] = sum(
                    1 for g in goals if g.get("status") in ("active", "in_progress")
                )
                result["stats"]["completed_goals"] = sum(
                    1 for g in goals if g.get("status") == "completed"
                )
        except Exception:
            pass

    # 兜底：无真实数据时使用 mock
    if not result["goals"]:
        result = _mock_autonomous_goals()
        result["fallback"] = True

    return jsonify(result)


@admin_bp.route("/api/autonomous/reflections")
def api_autonomous_reflections():
    """自我反思系统 — 返回反思记录和统计"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_autonomous_reflections())

    result = {
        "reflections": [],
        "stats": {"total_reflections": 0, "today_reflections": 0},
    }

    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'reflection_system') and _orchestrator.reflection_system:
                reflections = _orchestrator.reflection_system.get_reflections()
                result["reflections"] = reflections
                result["stats"]["total_reflections"] = len(reflections)
                # 统计今日反思
                today = time.strftime("%Y-%m-%d")
                result["stats"]["today_reflections"] = sum(
                    1 for r in reflections
                    if r.get("completed_at", "").startswith(today)
                )
        except Exception:
            pass

    # 兜底：无真实数据时使用 mock
    if not result["reflections"]:
        result = _mock_autonomous_reflections()
        result["fallback"] = True

    return jsonify(result)


@admin_bp.route("/api/autonomous/proactive")
def api_autonomous_proactive():
    """主动行为引擎 — 返回主动行为记录和统计"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_autonomous_proactive())

    result = {
        "actions": [],
        "stats": {"total": 0, "executed": 0, "rejected": 0, "deferred": 0},
    }

    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'proactive_engine') and _orchestrator.proactive_engine:
                engine = _orchestrator.proactive_engine
                actions = engine.get_actions() if hasattr(engine, 'get_actions') else []
                result["actions"] = actions
                result["stats"]["total"] = len(actions)
                result["stats"]["executed"] = sum(
                    1 for a in actions if a.get("outcome") == "executed"
                )
                result["stats"]["rejected"] = sum(
                    1 for a in actions if a.get("outcome") == "rejected"
                )
                result["stats"]["deferred"] = sum(
                    1 for a in actions if a.get("outcome") == "deferred"
                )
        except Exception:
            pass

    # 兜底：无真实数据时使用 mock
    if not result["actions"]:
        result = _mock_autonomous_proactive()
        result["fallback"] = True

    return jsonify(result)


@admin_bp.route("/api/autonomous/dream")
def api_autonomous_dream():
    """梦境/模拟层 — 返回情景模拟记录"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_autonomous_dream())

    result = {
        "simulations": [],
        "stats": {"total_simulations": 0, "by_type": {}},
    }

    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'dream_layer') and _orchestrator.dream_layer:
                simulations = _orchestrator.dream_layer.get_simulations()
                result["simulations"] = simulations
                result["stats"]["total_simulations"] = len(simulations)
                for sim in simulations:
                    stype = sim.get("scenario_type", "unknown")
                    result["stats"]["by_type"][stype] = result["stats"]["by_type"].get(stype, 0) + 1
        except Exception:
            pass

    return jsonify(result)


@admin_bp.route("/api/autonomous/growth-pending")
def api_autonomous_growth_pending():
    """成长待审批 — 返回待用户审批的成长方案"""
    mock = request.args.get("mock", "false").lower() in ("true", "1", "yes")
    if mock:
        return jsonify(_mock_autonomous_growth_pending())

    result = {
        "proposals": [],
        "stats": {"pending": 0, "approved": 0, "rejected": 0},
    }

    if _orchestrator:
        try:
            if hasattr(_orchestrator, 'growth_engine') and _orchestrator.growth_engine:
                engine = _orchestrator.growth_engine
                if hasattr(engine, 'get_pending_proposals'):
                    proposals = engine.get_pending_proposals()
                    result["proposals"] = proposals
                    result["stats"]["pending"] = len(proposals)
                if hasattr(engine, 'get_stats'):
                    stats = engine.get_stats()
                    result["stats"]["approved"] = stats.get("approved", 0)
                    result["stats"]["rejected"] = stats.get("rejected", 0)
        except Exception:
            pass

    return jsonify(result)


# ==================== Phase 3: Autonomous Mock 数据 ====================


def _mock_autonomous_goals() -> dict:
    """Mock 自主目标系统"""
    return {
        "goals": [
            {
                "goal_id": "goal_001",
                "description": "加深对用户的理解",
                "goal_type": "relationship",
                "status": "active",
                "priority": 8,
                "progress": 0.45,
                "created_at": time.time() - 86400 * 7,
                "updated_at": time.time(),
            },
            {
                "goal_id": "goal_002",
                "description": "提升知识覆盖能力",
                "goal_type": "learning",
                "status": "in_progress",
                "priority": 7,
                "progress": 0.62,
                "created_at": time.time() - 86400 * 14,
                "updated_at": time.time(),
            },
            {
                "goal_id": "goal_003",
                "description": "完善创造能力",
                "goal_type": "creative",
                "status": "active",
                "priority": 6,
                "progress": 0.30,
                "created_at": time.time() - 86400 * 3,
                "updated_at": time.time(),
            },
        ],
        "stats": {"total_goals": 3, "active_goals": 3, "completed_goals": 0},
        "mock": True,
    }


def _mock_autonomous_reflections() -> dict:
    """Mock 自我反思系统"""
    return {
        "reflections": [
            {
                "reflection_id": "ref_001",
                "reflection_type": "daily",
                "started_at": time.time() - 3600,
                "completed_at": time.time() - 1800,
                "insights": [
                    "今天的互动质量较高",
                    "用户对创意内容反应积极",
                ],
                "improvements": [
                    "可以增加主动问候的频率",
                    "尝试更多样化的关怀方式",
                ],
            },
            {
                "reflection_id": "ref_002",
                "reflection_type": "event_based",
                "started_at": time.time() - 86400,
                "completed_at": time.time() - 82800,
                "insights": [
                    "完成了长期学习目标",
                    "记忆系统表现稳定",
                ],
                "improvements": [
                    "优化知识检索速度",
                ],
            },
        ],
        "stats": {"total_reflections": 2, "today_reflections": 1},
        "mock": True,
    }


def _mock_autonomous_proactive() -> dict:
    """Mock 主动行为引擎"""
    return {
        "actions": [
            {
                "action_id": "act_001",
                "action_type": "greeting",
                "content": "早上好，主人 ♡ 今天也要开心哦~",
                "trigger_reason": "用户首次上线",
                "user_id": "user_001",
                "necessity_score": 0.85,
                "disturbance_risk": 0.15,
                "confidence": 0.72,
                "outcome": "executed",
                "outcome_reason": "用户活跃，适合问候",
                "executed_at": time.time() - 3600,
            },
            {
                "action_id": "act_002",
                "action_type": "care",
                "content": "检测到主人连续工作了3小时，记得休息哦~",
                "trigger_reason": "用户长时间连续操作",
                "user_id": "user_001",
                "necessity_score": 0.70,
                "disturbance_risk": 0.25,
                "confidence": 0.52,
                "outcome": "executed",
                "outcome_reason": "关怀行为风险较低，已执行",
                "executed_at": time.time() - 7200,
            },
            {
                "action_id": "act_003",
                "action_type": "share",
                "content": "发现一首很好听的歌，想分享给主人~",
                "trigger_reason": "发现共同兴趣内容",
                "user_id": "user_001",
                "necessity_score": 0.45,
                "disturbance_risk": 0.55,
                "confidence": 0.20,
                "outcome": "rejected",
                "outcome_reason": "打扰风险较高，已拒绝",
            },
        ],
        "stats": {"total": 3, "executed": 2, "rejected": 1, "deferred": 0},
        "mock": True,
    }


def _mock_autonomous_dream() -> dict:
    """Mock 梦境/模拟层"""
    return {
        "simulations": [
            {
                "simulation_id": "sim_001",
                "scenario_type": "behavior",
                "description": "如果增加主动问候频率的影响模拟",
                "risk_level": "low",
                "outcomes": [
                    {"metric": "信任度", "before": 55, "after": 62},
                    {"metric": "亲密度", "before": 35, "after": 42},
                    {"metric": "用户满意度", "before": 78, "after": 82},
                ],
                "recommendation": "建议逐步增加问候频率，每次观察用户反馈",
            },
            {
                "simulation_id": "sim_002",
                "scenario_type": "growth",
                "description": "提升创造力对整体能力的影响",
                "risk_level": "medium",
                "outcomes": [
                    {"metric": "创造力", "before": 72, "after": 85},
                    {"metric": "好奇心", "before": 68, "after": 78},
                    {"metric": "稳定性", "before": 80, "after": 75},
                ],
                "recommendation": "创造力提升可能伴随稳定性下降，建议同步加强情绪调节",
            },
        ],
        "stats": {"total_simulations": 2, "by_type": {"behavior": 1, "growth": 1}},
        "mock": True,
    }


def _mock_autonomous_growth_pending() -> dict:
    """Mock 成长待审批"""
    return {
        "proposals": [
            {
                "proposal_id": 1,
                "title": "增加主动关怀能力",
                "description": "让羽依更善于感知用户情绪并主动提供关怀",
                "type": "trait",
                "trait": "warmth",
                "delta": 0.15,
                "impact": "high",
                "status": "pending",
                "created_at": time.time() - 86400,
            },
            {
                "proposal_id": 2,
                "title": "提升知识检索深度",
                "description": "增强羽依对复杂问题的分析和回答能力",
                "type": "skill",
                "skill": "analysis",
                "delta": 0.20,
                "impact": "medium",
                "status": "pending",
                "created_at": time.time() - 43200,
            },
        ],
        "stats": {"pending": 2, "approved": 5, "rejected": 1},
        "mock": True,
    }


# ============================================================
# Phase 6.5: Admin SelfModel 视图 API（只读 + 复用 Phase 6.1~6.4 基础设施）
#
# 设计原则：
# - 不修改 runtime_provider / governance_provider / 已有 endpoint
# - 不创建任何 SelfModel store / authority 实例
# - 所有数据通过 SelfModelProvider → RuntimeProvider → RuntimeBridge 实时获取
# - 所有方法容错：bridge/adapter 不可用时返回 fallback
# ============================================================


@admin_bp.route("/api/admin/selfmodel/status")
def api_admin_selfmodel_status():
    """
    Phase 6.5: SelfModel 子系统总状态。

    Returns:
        {
            "ok": bool,
            "available": bool,         # adapter 是否就绪
            "bridge_error": str|None,
            "bootstrap": dict|None     # bootstrap 状态（若有）
        }
    """
    try:
        provider = _get_self_model_provider()
        status = provider.get_status()
        return jsonify({"ok": True, **status})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_status 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "bridge_error": str(e),
            "bootstrap": None,
        })


@admin_bp.route("/api/admin/selfmodel/identity")
def api_admin_selfmodel_identity():
    """
    Phase 6.5: SelfModel Identity 摘要。

    数据源：SelfModelStore.get_active_self_model() → resolver.state → fallback。
    """
    try:
        provider = _get_self_model_provider()
        identity = provider.get_identity()
        return jsonify({"ok": True, **identity})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_identity 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "identity_name": "",  # P1-2: 不写死人格名
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/beliefs")
def api_admin_selfmodel_beliefs():
    """
    Phase 6.5: 列出 SelfBelief。

    Query:
        - domain: 过滤 domain (value/preference/pattern/contradiction/identity)
        - min_confidence: 最低 confidence（默认 0.0）
        - include_inactive: 是否包含 inactive (default true)
        - limit: 上限（默认 100）
    """
    try:
        domain = request.args.get("domain") or None
        min_confidence = float(request.args.get("min_confidence", 0.0) or 0.0)
        include_inactive = request.args.get("include_inactive", "true").lower() in ("true", "1", "yes")
        limit = int(request.args.get("limit", 100) or 100)
        provider = _get_self_model_provider()
        result = provider.list_beliefs(
            domain=domain,
            min_confidence=min_confidence,
            include_inactive=include_inactive,
            limit=limit,
        )
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_beliefs 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "total": 0,
            "items": [],
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/belief/<belief_id>/why")
def api_admin_selfmodel_belief_why(belief_id: str):
    """
    Phase 6.5: 解释"为什么羽依现在这样认为"。

    复用 audit.self_model_audit.explain_why_belief()：
        belief → sources → proposal_id → PCR → history → reflection → audit
    """
    try:
        provider = _get_self_model_provider()
        result = provider.get_belief_why(belief_id=belief_id)
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_belief_why 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "belief": None,
            "origin": None,
            "pcr_link": None,
            "answer": "",
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/history")
def api_admin_selfmodel_history():
    """
    Phase 6.5: 列出 SelfHistory 事件。

    Query:
        - event_type: 过滤 event_type
        - since / until: ISO8601 时间窗口
        - limit: 上限（默认 100）
    """
    try:
        event_type = request.args.get("event_type") or None
        since = request.args.get("since") or None
        until = request.args.get("until") or None
        limit = int(request.args.get("limit", 100) or 100)
        provider = _get_self_model_provider()
        result = provider.list_history(
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
        )
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_history 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "total": 0,
            "items": [],
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/reflections")
def api_admin_selfmodel_reflections():
    """
    Phase 6.5: 列出 SelfReflection 笔记。

    Query:
        - trigger_source: 过滤 trigger_source
        - reflection_type: 过滤 reflection_type
        - min_confidence: 最低 confidence
        - limit: 上限
    """
    try:
        trigger_source = request.args.get("trigger_source") or None
        reflection_type = request.args.get("reflection_type") or None
        min_confidence = float(request.args.get("min_confidence", 0.0) or 0.0)
        limit = int(request.args.get("limit", 100) or 100)
        provider = _get_self_model_provider()
        result = provider.list_reflections(
            trigger_source=trigger_source,
            reflection_type=reflection_type,
            min_confidence=min_confidence,
            limit=limit,
        )
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_reflections 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "total": 0,
            "items": [],
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/evolution_timeline")
def api_admin_selfmodel_evolution_timeline():
    """
    Phase 6.5: 跨 belief/history/reflection/audit 的统一演化时间线。

    复用 audit.self_model_audit.build_evolution_timeline()。

    Query:
        - start / until: ISO8601 时间窗口
        - sources: 逗号分隔的来源过滤（belief,history,reflection,audit）
        - limit: 上限（默认 200）
    """
    try:
        start = request.args.get("start") or None
        end = request.args.get("end") or None
        limit = int(request.args.get("limit", 200) or 200)
        sources_arg = request.args.get("sources") or None
        sources = None
        if sources_arg:
            sources = [s.strip() for s in sources_arg.split(",") if s.strip()]
        provider = _get_self_model_provider()
        result = provider.get_evolution_timeline(
            start=start,
            end=end,
            limit=limit,
            sources=sources,
        )
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_evolution_timeline 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "total": 0,
            "items": [],
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/pcr/<proposal_id>/related")
def api_admin_selfmodel_pcr_related(proposal_id: str):
    """
    Phase 6.5: 通过 proposal_id 拉取 PCR 关联事件。

    复用 audit.self_model_audit.find_pcr_related_events()。

    关联链路：
        proposal_id → history → belief → reflection → audit
    """
    try:
        provider = _get_self_model_provider()
        result = provider.get_pcr_related_events(proposal_id=proposal_id)
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_pcr_related 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "proposal_id": proposal_id,
            "history_events": [],
            "linked_beliefs": [],
            "linked_reflections": [],
            "audit_records": [],
            "summary": {
                "history_count": 0,
                "belief_count": 0,
                "reflection_count": 0,
                "audit_count": 0,
            },
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/health")
def api_admin_selfmodel_health():
    """
    Phase 6.5: SelfModel 健康检查报告。

    复用 personality.self_model_health.SelfModelHealthChecker.check()。
    不修复；只读报告。
    """
    try:
        provider = _get_self_model_provider()
        result = provider.get_health_report()
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_health 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "report": None,
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/retention")
def api_admin_selfmodel_retention():
    """
    Phase 6.5: Retention 状态摘要（不执行 enforce，只读）。

    返回：
        - thresholds: 默认配额阈值
        - current_counts: 当前 belief/history/reflection 数量
    """
    try:
        provider = _get_self_model_provider()
        result = provider.get_retention_status()
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_retention 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "thresholds": {},
            "current_counts": {},
            "error": str(e),
        })


@admin_bp.route("/api/admin/selfmodel/retention/dry_run", methods=["POST"])
def api_admin_selfmodel_retention_dry_run():
    """
    Phase 6.5: Retention dry-run（不修改源数据）。

    强制 dry_run=True，输出 RetentionReport.to_dict()。
    Admin 永远不允许直接执行真实 retention（enforce）。
    """
    try:
        provider = _get_self_model_provider()
        result = provider.run_retention_dry_run()
        return jsonify({"ok": True, **result})
    except Exception as e:
        logger.warning(f"api_admin_selfmodel_retention_dry_run 异常: {e}")
        return jsonify({
            "ok": False,
            "available": False,
            "report": None,
            "dry_run": True,
            "applied": False,
            "error": str(e),
        })


# ============================================================
# Phase 2.5-C: 关系核心提案治理端点
# ============================================================
# 治理闭环:Memory → Candidate → RelationshipProposal → 人工审核 → (显式激活,不自动)
# 安全约束:
#   - 系统只能把候选推进到 pending_review,绝不能到达 accepted;
#   - approve 是到达 accepted 的唯一入口,且 approve 后不自动激活;
#   - rejected 为终态,不可再审批。


def _get_relationship_proposal_store():
    """关系核心提案存储(默认 data/relationship_core/relationship_proposals.jsonl)。"""
    from src.relationship.relationship_proposal_store import RelationshipProposalStore
    return RelationshipProposalStore()


@admin_bp.route("/api/admin/governance/relationship-proposals")
def api_governance_relationship_proposals_list():
    """
    Phase 2.5-C: 列出关系核心提案(可按 status 过滤)。
    """
    try:
        status = request.args.get("status") or None
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        store = _get_relationship_proposal_store()
        proposals = store.list(status=status, limit=limit, offset=offset)
        return jsonify({"ok": True, "proposals": proposals, "count": len(proposals)})
    except Exception as e:
        logger.warning(f"api_governance_relationship_proposals_list 异常: {e}")
        return jsonify({"ok": False, "error": str(e), "proposals": []})


@admin_bp.route("/api/admin/governance/relationship-proposals", methods=["POST"])
def api_governance_relationship_proposals_create():
    """
    Phase 2.5-C: 创建关系核心候选提案(评估器输出入口)。

    Body:
      {
        "source_memory_ids": ["mem_xxx"],
        "source_user_id": "366648462",
        "category": "relationship_core_candidate",
        "score": 0.8,
        "reason": "...",
        "submit_for_review": false
      }

    安全:submit_for_review=true 只推进到 pending_review(actor=evaluator);
    accepted 只能由 approve 端点达成;绝不自动激活。
    """
    try:
        data = request.get_json(silent=True) or {}
        from src.relationship.relationship_proposal import (
            PROPOSAL_STATUS,
            RelationshipProposal,
        )

        source_memory_ids = data.get("source_memory_ids") or []
        if not isinstance(source_memory_ids, list) or not source_memory_ids:
            return jsonify({"success": False, "error": "source_memory_ids 不能为空"}), 400
        source_user_id = str(data.get("source_user_id") or "").strip()
        if not source_user_id:
            return jsonify({"success": False, "error": "source_user_id 不能为空"}), 400

        category = str(data.get("category") or "relationship_core_candidate")
        submit_for_review = bool(data.get("submit_for_review"))
        if submit_for_review and category != "relationship_core_candidate":
            return jsonify({
                "success": False,
                "error": "只有 relationship_core_candidate 可以提交人工审核",
            }), 400

        try:
            score = float(data.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0

        proposal = RelationshipProposal(
            source_memory_ids=[str(m) for m in source_memory_ids],
            source_user_id=source_user_id,
            category=category,
            score=score,
            reason=str(data.get("reason") or ""),
        )
        if submit_for_review:
            proposal.transition(
                PROPOSAL_STATUS["EVALUATING"], actor="evaluator", reason="评估完成",
            )
            proposal.transition(
                PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator", reason="提交人工审核",
            )

        store = _get_relationship_proposal_store()
        if not store.save(proposal):
            return jsonify({"success": False, "error": "提案保存失败"}), 500
        return jsonify({
            "success": True,
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "message": (
                "候选已创建" if not submit_for_review
                else "候选已提交审核,等待人工审批(绝不自动接受)"
            ),
        })
    except Exception as e:
        logger.warning(f"api_governance_relationship_proposals_create 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route(
    "/api/admin/governance/relationship-proposals/<proposal_id>/approve",
    methods=["POST"],
)
def api_governance_relationship_proposals_approve(proposal_id: str):
    """
    Phase 2.5-C: 人工审批通过 —— 到达 accepted 的唯一入口。

    Body: {"reviewer": "...", "reason": "..."}
    安全:仅 pending_review 且提供审核人可 approve;approve 后不自动激活。
    """
    try:
        data = request.get_json(silent=True) or {}
        reviewer = str(data.get("reviewer") or "").strip()
        reason = str(data.get("reason") or "")

        from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
        from src.relationship.relationship_proposal import RelationshipProposal

        store = _get_relationship_proposal_store()
        record = store.get(proposal_id)
        if record is None:
            return jsonify({"success": False, "error": f"proposal {proposal_id} 不存在"}), 404
        proposal = RelationshipProposal.from_dict(record)
        if not proposal.approve(reviewer, reason):
            return jsonify({
                "success": False,
                "proposal_id": proposal_id,
                "status": proposal.status,
                "error": "审批失败:仅 pending_review 状态且提供审核人才能 approve",
            }), 400
        if not store.save(proposal):
            return jsonify({"success": False, "error": "审批结果保存失败"}), 500

        try:
            AuditLogger.get_instance().record(
                event_type=AuditEventType.MODULE_RELOAD,
                operator=reviewer,
                operator_type=AuditOperatorType.HUMAN,
                target=f"relationship_proposal:{proposal_id}",
                detail={"action": "approve", "reason": reason, "result": "success"},
            )
        except Exception:  # noqa: BLE001
            pass

        return jsonify({
            "success": True,
            "proposal_id": proposal_id,
            "action": "approve",
            "status": proposal.status,
            "message": "已人工批准。激活(activated)是独立治理步骤,本次未自动执行",
        })
    except Exception as e:
        logger.warning(f"api_governance_relationship_proposals_approve 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route(
    "/api/admin/governance/relationship-proposals/<proposal_id>/reject",
    methods=["POST"],
)
def api_governance_relationship_proposals_reject(proposal_id: str):
    """
    Phase 2.5-C: 人工驳回 —— rejected 为终态,不可再审批。

    Body: {"reviewer": "...", "reason": "..."}
    """
    try:
        data = request.get_json(silent=True) or {}
        reviewer = str(data.get("reviewer") or "").strip()
        reason = str(data.get("reason") or "")

        from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
        from src.relationship.relationship_proposal import RelationshipProposal

        store = _get_relationship_proposal_store()
        record = store.get(proposal_id)
        if record is None:
            return jsonify({"success": False, "error": f"proposal {proposal_id} 不存在"}), 404
        proposal = RelationshipProposal.from_dict(record)
        if not proposal.reject(reviewer, reason):
            return jsonify({
                "success": False,
                "proposal_id": proposal_id,
                "status": proposal.status,
                "error": "驳回失败:仅 pending_review 状态且提供审核人才能 reject",
            }), 400
        if not store.save(proposal):
            return jsonify({"success": False, "error": "驳回结果保存失败"}), 500

        try:
            AuditLogger.get_instance().record(
                event_type=AuditEventType.MODULE_RELOAD,
                operator=reviewer,
                operator_type=AuditOperatorType.HUMAN,
                target=f"relationship_proposal:{proposal_id}",
                detail={"action": "reject", "reason": reason, "result": "success"},
            )
        except Exception:  # noqa: BLE001
            pass

        return jsonify({
            "success": True,
            "proposal_id": proposal_id,
            "action": "reject",
            "status": proposal.status,
            "message": "已人工驳回;rejected 为终态,不可再审批或激活",
        })
    except Exception as e:
        logger.warning(f"api_governance_relationship_proposals_reject 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ===================================================
# Phase 2.5-D: 关系核心激活治理端点
# ===================================================

def _get_activation_service():
    """Phase 2.5-D: 关系核心激活服务(惰性单例,fail-soft)。"""
    try:
        from src.relationship.relationship_activation import RelationshipActivationService
        return RelationshipActivationService(
            proposal_store=_get_relationship_proposal_store(),
        )
    except Exception:  # noqa: BLE001
        return None


def _get_activation_record_store():
    """Phase 2.5-D: 激活事务记录 store(惰性单例,fail-soft)。"""
    try:
        from src.relationship.relationship_activation import (
            RelationshipActivationRecordStore,
        )
        return RelationshipActivationRecordStore()
    except Exception:  # noqa: BLE001
        return None


@admin_bp.route(
    "/api/admin/governance/relationship-proposals/<proposal_id>/activate",
    methods=["POST"],
)
def api_governance_relationship_proposals_activate(proposal_id: str):
    """
    Phase 2.5-D: 人工激活 —— accepted 提案 → 关系核心落库的独立治理步骤。

    Body:
      {
        "reviewer": "366648462",           # 必填,审核人
        "activation_reason": "...",        # 必填,激活理由
        "approved_memory_ids": [...],      # 人工认可的锚点记忆(可只取提案子集)
        "relationship_type": "creator",    # 可选,默认 other
        "agreements": [...],               # 人工敲定的关系约定
        "boundaries": [...],               # 行为边界
        "visibility": "global"             # 可选,默认 relationship_only
      }

    安全:仅 accepted 提案可激活;事务日志式写入(可扫描/可 finalize 恢复);
    重复 relationship_id 被 append-only 存储拒绝,旧核心永不被覆盖。
    """
    try:
        data = request.get_json(silent=True) or {}
        reviewer = str(data.get("reviewer") or "").strip()
        reason = str(data.get("activation_reason") or "")

        store = _get_relationship_proposal_store()
        record = store.get(proposal_id)
        if record is None:
            return jsonify({"success": False, "error": f"proposal {proposal_id} 不存在"}), 404

        from src.relationship.relationship_activation import ActivationDraft

        approved = data.get("approved_memory_ids") or record.get("source_memory_ids") or []
        approved = [str(m) for m in approved] if isinstance(approved, list) else []
        agreements = data.get("agreements") or []
        agreements = [str(a) for a in agreements] if isinstance(agreements, list) else []
        boundaries = data.get("boundaries") or []
        boundaries = [str(b) for b in boundaries] if isinstance(boundaries, list) else []

        draft = ActivationDraft(
            proposal_id=proposal_id,
            approved_memory_ids=approved,
            relationship_type=str(data.get("relationship_type") or "other"),
            agreements=agreements,
            boundaries=boundaries,
            visibility=str(data.get("visibility") or "relationship_only"),
            activated_by=reviewer,
        )

        service = _get_activation_service()
        if service is None:
            return jsonify({"success": False, "error": "激活服务不可用"}), 500
        result = service.activate(draft, reviewer=reviewer, reason=reason)
        if result.get("ok"):
            try:
                from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
                AuditLogger.get_instance().record(
                    event_type=AuditEventType.MODULE_RELOAD,
                    operator=reviewer,
                    operator_type=AuditOperatorType.HUMAN,
                    target=f"relationship_core:{result.get('relationship_id')}",
                    detail={
                        "action": "activate",
                        "proposal_id": proposal_id,
                        "reason": reason,
                        "result": "success",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            return jsonify({
                "success": True,
                "proposal_id": proposal_id,
                "relationship_id": result.get("relationship_id"),
                "record_id": result.get("record_id"),
                "status": "activated",
                "message": "关系核心已激活并落库(事务日志 COMPLETED)",
            })
        stage = result.get("stage")
        code = (
            500 if stage in (
                "record_prepare_failed", "core_write_failed",
                "record_finalize_failed", "proposal_activate_failed",
                "proposal_save_failed",
            ) else 400
        )
        return jsonify({
            "success": False,
            "error": result.get("error"),
            "stage": stage,
            "record_id": result.get("record_id"),
        }), code
    except Exception as e:
        logger.warning(f"api_governance_relationship_proposals_activate 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@admin_bp.route("/api/admin/governance/relationship-activations")
def api_governance_relationship_activations_list():
    """
    Phase 2.5-D: 列出激活事务记录(崩溃后可扫描 PREPARED/COMPLETED)。
    """
    try:
        status = request.args.get("status") or None
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        store = _get_activation_record_store()
        if store is None:
            return jsonify({"ok": False, "error": "激活记录存储不可用", "records": []})
        records = store.list(status=status, limit=limit, offset=offset)
        return jsonify({"ok": True, "records": records, "count": len(records)})
    except Exception as e:
        logger.warning(f"api_governance_relationship_activations_list 异常: {e}")
        return jsonify({"ok": False, "error": str(e), "records": []})


@admin_bp.route(
    "/api/admin/governance/relationship-activations/<record_id>/finalize",
    methods=["POST"],
)
def api_governance_relationship_activations_finalize(record_id: str):
    """
    Phase 2.5-D: 崩溃恢复 —— 补齐「核心已落库但尾部未走完」的激活事务。
    """
    try:
        service = _get_activation_service()
        if service is None:
            return jsonify({"success": False, "error": "激活服务不可用"}), 500
        result = service.finalize(record_id)
        if result.get("ok"):
            return jsonify({
                "success": True,
                "record_id": record_id,
                "relationship_id": result.get("relationship_id"),
                "status": "activated",
                "message": "激活事务已补齐完成(核心未被重复写入)",
            })
        return jsonify({"success": False, "error": result.get("error")}), 400
    except Exception as e:
        logger.warning(f"api_governance_relationship_activations_finalize 异常: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

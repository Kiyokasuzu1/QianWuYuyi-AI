#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浅雾羽依 API 服务（OpenAI 兼容）
"""

import os
import json
import asyncio
import logging
import threading
from pathlib import Path

import yaml
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from src.orchestrator import Orchestrator
    _orchestrator_available = True
except (ImportError, ModuleNotFoundError) as e:
    Orchestrator = None
    _orchestrator_available = False
    logger.warning(f"Orchestrator 导入失败，仅提供 Admin 控制台: {e}")

from src.admin.api.routes import admin_bp, init_admin

app = Flask(__name__)
orchestrator = None
_agent_server = None
_agent_server_thread = None

app.register_blueprint(admin_bp, url_prefix="/admin")


def load_config() -> dict:
    """加载 config.yaml 配置"""
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"加载 config.yaml 失败: {e}")
        return {}


def init_orchestrator():
    """初始化 Orchestrator（含远程模块）"""
    global orchestrator, _agent_server, _agent_server_thread

    config = load_config()

    if _orchestrator_available:
        logger.info("初始化羽依 Orchestrator...")
        orchestrator = Orchestrator(config=config)
        logger.info("羽依 Orchestrator 初始化完成")
    else:
        logger.warning("Orchestrator 不可用，跳过核心初始化")

    # 初始化 Admin 控制台
    try:
        init_admin(orchestrator=orchestrator)
        logger.info("Admin 控制台初始化完成")
    except Exception as e:
        logger.warning(f"Admin 控制台初始化失败: {e}")

    # 如果启用了远程模块，启动 Agent Server
    remote_cfg = config.get("remote", {})
    if remote_cfg.get("enabled", False) and _orchestrator_available:
        _start_agent_server(config)


def _start_agent_server(config: dict):
    """在后台线程中启动 Agent Server"""
    global _agent_server, _agent_server_thread

    try:
        from src.remote.agent_server import start_agent_server

        remote_cfg = config.get("remote", {})
        host = remote_cfg.get("host", "0.0.0.0")
        port = remote_cfg.get("port", 8765)
        auth_token = remote_cfg.get("auth_token", "")
        heartbeat_timeout = remote_cfg.get("heartbeat_timeout", 30)

        def _run_agent_server():
            """在独立线程中运行 Agent Server 的事件循环"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                global _agent_server
                _agent_server = loop.run_until_complete(
                    start_agent_server(
                        host=host,
                        port=port,
                        auth_token=auth_token,
                        heartbeat_timeout=heartbeat_timeout,
                    )
                )
                logger.info(f"Agent Server 运行中: {host}:{port}")
                loop.run_forever()
            except Exception as e:
                logger.error(f"Agent Server 启动失败: {e}")

        _agent_server_thread = threading.Thread(
            target=_run_agent_server, daemon=True, name="AgentServer"
        )
        _agent_server_thread.start()
        logger.info("Agent Server 线程已启动")

    except Exception as e:
        logger.error(f"启动 Agent Server 失败: {e}")

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "缺少请求体"}), 400

        messages = data.get('messages', [])
        if not messages:
            return jsonify({"error": "messages 不能为空"}), 400

        last_user_msg = None
        user_id = data.get('user', 'default')
        
        for msg in reversed(messages):
            if msg.get('role') == 'user':
                last_user_msg = msg.get('content', '')
                break

        if not last_user_msg:
            return jsonify({"error": "未找到用户消息"}), 400

        logger.info(f"处理用户 {user_id} 消息: {last_user_msg[:50]}...")

        orchestrator.target_user_id = user_id
        reply = orchestrator.process(last_user_msg)

        response = {
            "id": "chatcmpl-yuyi",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "yuyi",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": reply
                    },
                    "finish_reason": "stop"
                }
            ]
        }
        return jsonify(response)

    except Exception as e:
        logger.error(f"处理请求失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

# ==================== 主动消息端点 ====================
@app.route('/initiative', methods=['POST'])
def initiative():
    try:
        data = request.get_json() or {}
        user_id = data.get('user_id', 'default')
        global orchestrator
        if orchestrator is None:
            init_orchestrator()
        orchestrator.target_user_id = user_id
        # 尝试调用 generate_initiative，如果不存在则降级使用 process
        try:
            msg = orchestrator.generate_initiative(user_id)
        except AttributeError:
            # 如果 generate_initiative 不存在，用 process 模拟
            prompt = "你现在有什么想主动对我说的吗？如果有，请直接说；如果没有，请只回复一个空格。"
            msg = orchestrator.process(prompt)
            # 如果回复是空格或很短，视为无话
            if msg and len(msg.strip()) <= 1:
                msg = ""
        if msg and msg.strip():
            return jsonify({"has_message": True, "content": msg.strip()})
        else:
            return jsonify({"has_message": False, "content": ""})
    except Exception as e:
        logger.error(f"主动消息生成失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/v1/models', methods=['GET'])
def list_models():
    return jsonify({"data": [{"id": "yuyi", "object": "model"}]})


# ============================================================
# 管理端点 —— 统一放在 /admin/api/ 下
# ============================================================


def _run_async(coro):
    """
    在 Flask 同步上下文中执行异步代码

    Agent Server 在独立线程的事件循环中运行，
    需要把协程提交到那个循环执行。
    """
    global _agent_server
    if _agent_server is None:
        return None
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Flask 主线程没有运行中的循环，走到这里说明情况特殊
            return None
        return loop.run_until_complete(coro)
    except RuntimeError:
        # 没有事件循环，创建临时的
        try:
            return asyncio.run(coro)
        except Exception as e:
            logger.error(f"异步执行失败: {e}")
            return None


@app.route('/admin/api/agents', methods=['GET'])
def admin_list_agents():
    """查看所有在线代理列表"""
    if not _agent_server:
        return jsonify({"enabled": False, "agents": [], "message": "远程模块未启用"})
    try:
        agents = _agent_server.get_online_agents()
        return jsonify({
            "enabled": True,
            "total": len(agents),
            "agents": agents,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/api/agents/status', methods=['GET'])
def admin_agent_status():
    """查看代理服务整体状态"""
    if not _agent_server:
        return jsonify({
            "enabled": False,
            "running": False,
            "message": "远程模块未启用",
        })
    try:
        return jsonify({
            "enabled": True,
            "running": _agent_server._running,
            "total_agents": _agent_server.registry.count(),
            "authenticated_agents": _agent_server.registry.count_authenticated(),
            "agents": _agent_server.get_online_agents(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/api/screen/context', methods=['GET'])
def admin_screen_context():
    """获取当前屏幕上下文（OCR 文字 + 描述）"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    try:
        result = orchestrator.get_screen_context()
        return jsonify(result)
    except Exception as e:
        return jsonify({"available": False, "error": str(e)}), 500


@app.route('/admin/api/screen/capture', methods=['POST'])
def admin_screen_capture():
    """手动请求截图"""
    if not _agent_server:
        return jsonify({"success": False, "error": "远程模块未启用"}), 400

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "default")
    region = data.get("region")  # 可选: {"x": 0, "y": 0, "width": 800, "height": 600}

    try:
        from src.remote.protocol import CommandType

        info = _agent_server.registry.get_agent_by_user(user_id)
        if not info:
            return jsonify({"success": False, "error": "代理离线"}), 404

        if region:
            result = _run_async(_agent_server.send_command_and_wait(
                agent_id=info.agent_id,
                cmd_type=CommandType.SCREEN_REGION,
                payload=region,
                timeout=15,
            ))
        else:
            result = _run_async(_agent_server.send_command_and_wait(
                agent_id=info.agent_id,
                cmd_type=CommandType.SCREEN_CAPTURE,
                timeout=15,
            ))

        if not result:
            return jsonify({"success": False, "error": "请求超时或服务不可用"}), 504

        if result.get("success", False):
            payload = result.get("payload", {})
            # 不直接返回 base64 图片（太大），返回元信息 + OCR
            image_base64 = payload.get("image", "")

            # 尝试 OCR
            ocr_text = ""
            try:
                from src.screen.screen_analyzer import ScreenAnalyzer
                analyzer = ScreenAnalyzer()
                analysis = analyzer.analyze(image_base64)
                ocr_text = analysis.get("text", "")
            except Exception:
                pass

            return jsonify({
                "success": True,
                "format": payload.get("format", "jpeg"),
                "image_size": len(image_base64),
                "timestamp": payload.get("timestamp", ""),
                "ocr_text": ocr_text,
                "image_base64": image_base64,  # 前端可以直接用
            })
        else:
            return jsonify({"success": False, "error": result.get("error", "unknown")}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/api/control/click', methods=['POST'])
def admin_control_click():
    """执行鼠标点击"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500

    data = request.get_json(silent=True) or {}
    x = data.get("x")
    y = data.get("y")
    button = data.get("button", "left")

    if x is None or y is None:
        return jsonify({"success": False, "error": "缺少 x 或 y 参数"}), 400

    try:
        result = _run_async(orchestrator.perform_click(x=x, y=y, button=button))
        if result is None:
            return jsonify({"success": False, "error": "异步执行失败（可能事件循环冲突）"}), 500
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/api/control/type', methods=['POST'])
def admin_control_type():
    """执行文本输入"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500

    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    if not text:
        return jsonify({"success": False, "error": "缺少 text 参数"}), 400

    try:
        result = _run_async(orchestrator.perform_type(text=text))
        if result is None:
            return jsonify({"success": False, "error": "异步执行失败"}), 500
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/api/control/key', methods=['POST'])
def admin_control_key():
    """执行按键"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500

    data = request.get_json(silent=True) or {}
    key = data.get("key", "")

    if not key:
        return jsonify({"success": False, "error": "缺少 key 参数"}), 400

    try:
        result = _run_async(orchestrator.perform_key(key=key))
        if result is None:
            return jsonify({"success": False, "error": "异步执行失败"}), 500
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin/api/control/status', methods=['GET'])
def admin_control_status():
    """查看控制模块状态"""
    if not orchestrator:
        return jsonify({"error": "Orchestrator 未初始化"}), 500
    try:
        return jsonify({
            "control_enabled": orchestrator.control_manager is not None,
            "screen_enabled": orchestrator.screen_context_manager is not None,
            "agent_online": orchestrator.is_agent_online(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # 初始化 Orchestrator（启动时加载模型）
    init_orchestrator()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

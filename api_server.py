#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浅雾羽依 API 服务（OpenAI 兼容）
"""

import json
import logging
from flask import Flask, request, jsonify
from src.orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
orchestrator = None

def init_orchestrator():
    global orchestrator
    logger.info("初始化羽依 Orchestrator...")
    orchestrator = Orchestrator()
    logger.info("羽依 Orchestrator 初始化完成")

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

if __name__ == '__main__':
    # 初始化 Orchestrator（启动时加载模型）
    init_orchestrator()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

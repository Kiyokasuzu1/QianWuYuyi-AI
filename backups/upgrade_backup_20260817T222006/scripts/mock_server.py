#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仅启动 Flask + 路由(不加载 LLM/Orchestrator),用于本地 desktop 连接测试。

不修改 api_server.py,只是临时启动一个精简的 mock server。
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

# 设置环境变量强制 mock
os.environ["YUYI_LLM_MOCK"] = "1"
os.environ["YUYI_API_BASE"] = "http://127.0.0.1:5001"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 跳过 init_orchestrator: 直接导入 app 但不调用
import api_server  # noqa
app = api_server.app

# 使用 5001 端口避免冲突
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_server")

if __name__ == "__main__":
    logger.info("Starting mock Yuyi server on :5001 (LLM mock mode)")
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True, use_reloader=False)

# -*- coding: utf-8 -*-
"""
src/runtime/pipeline_server.py

Phase 6.4 —— RuntimePipeline HTTP 桥接(部署桥)。

职责:
    提供最小 HTTP 接口,把外部 POST 请求桥接到 RuntimePipeline,
    不复制任何业务逻辑。

设计原则:
    1. **薄层**:仅做 HTTP ↔ Pipeline 转换,业务逻辑全部在 Pipeline 内部。
    2. **复用项目已有框架**:本项目已使用 Flask,沿用之(不引入新依赖)。
    3. **隔离**:本模块不 import 任何业务 Authority(memory/emotion/...)。
    4. **容错**:Pipeline 异常 → 返回 500 + JSON,而不是 5xx HTML。
    5. **可注入**:Pipeline 实例由外部传入(测试时注入 fake,部署时注入真)。

HTTP 接口:
    GET  /health   -> {"status": "ok", "runtime": "ready"}
    POST /chat     -> {"message": "..."} → {"reply":..., "lifecycle_id":..., ...}

启动方式:
    python -m src.runtime.pipeline_server
    或:
    python -c "from src.runtime.pipeline_server import run; run()"
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

# Flask 是项目已有依赖
try:
    from flask import Flask, jsonify, request  # type: ignore
except Exception as _exc:  # pragma: no cover
    raise ImportError(
        "Flask 未安装。请先安装: pip install flask"
    ) from _exc

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.runtime_pipeline import RuntimePipeline

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
PIPELINE_SERVER_SCHEMA_VERSION = "1.0"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_RESPONSE_TEXT = "抱歉,我遇到了一些问题,请稍后再试。"


# ============================================================
# Pipeline 工厂(可被外部覆盖)
# ============================================================
def default_pipeline_factory() -> "RuntimePipeline":
    """默认 Pipeline 工厂:从 src.orchestrator.Orchestrator 构造。

    失败时返回带 NoOpOrchestrator 的 Pipeline,确保服务仍可启动并返回兜底。
    """
    try:
        from src.orchestrator import Orchestrator
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = Orchestrator()
        return RuntimePipeline(orchestrator=orch)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[PipelineServer] 无法构造真实 Orchestrator(%s),使用 NoOp 兜底",
            exc,
        )
        return _build_noop_pipeline()


def _build_noop_pipeline() -> "RuntimePipeline":
    """构造一个 NoOp Orchestrator 的 Pipeline(部署前 LLM 不可用时仍可启动)。"""
    from src.runtime.runtime_pipeline import RuntimePipeline

    class _NoOpOrchestrator:
        def process(self, user_message: str) -> str:
            return f"(echo) {user_message}"

    return RuntimePipeline(orchestrator=_NoOpOrchestrator())


# ============================================================
# 响应构造器
# ============================================================
def _now_iso() -> str:
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:  # noqa: BLE001
        return "1970-01-01T00:00:00Z"


def _build_chat_response(context: Any, fallback_reply: str = "") -> Dict[str, Any]:
    """从 RuntimeContext 构造 /chat 响应。

    契约:
        {
            "reply": str,
            "lifecycle_id": str,
            "runtime_state": str,    # "success" | "failed" | "running" | ...
            "timestamp": str         # ISO 8601 UTC
            "token_usage": dict      # Phase 6.5: 可选,仅在启用时存在
        }
    """
    state = str(getattr(context, "state", "unknown") or "unknown")
    lifecycle_id = str(getattr(context, "lifecycle_id", "") or "")

    # 从 outputs.snapshot.reply 提取回复
    reply = fallback_reply
    token_usage: Any = None
    effective_user_message: Any = None
    try:
        outputs = getattr(context, "outputs", {}) or {}
        snapshot = outputs.get("snapshot", {}) if isinstance(outputs, dict) else {}
        if isinstance(snapshot, dict):
            r = snapshot.get("reply", "")
            if isinstance(r, str) and r.strip():
                reply = r
            # Phase 6.5: 透传 token_usage(若存在)
            tu = snapshot.get("token_usage")
            if isinstance(tu, dict):
                token_usage = tu
            eum = snapshot.get("effective_user_message")
            if isinstance(eum, str):
                effective_user_message = eum
    except Exception:  # noqa: BLE001
        pass

    response: Dict[str, Any] = {
        "reply": reply,
        "lifecycle_id": lifecycle_id,
        "runtime_state": state,
        "timestamp": _now_iso(),
    }
    # Phase 6.5: token_usage 透传(向后兼容:仅在存在时附加)
    if isinstance(token_usage, dict):
        response["token_usage"] = token_usage
    if isinstance(effective_user_message, str):
        response["effective_user_message"] = effective_user_message
    return response


def _build_error_response(
    error: str,
    *,
    state: str = "failed",
    lifecycle_id: str = "",
) -> Dict[str, Any]:
    """构造错误响应。"""
    return {
        "reply": DEFAULT_RESPONSE_TEXT,
        "lifecycle_id": lifecycle_id,
        "runtime_state": state,
        "timestamp": _now_iso(),
        "error": str(error),
    }


# ============================================================
# 核心:app 工厂
# ============================================================
def create_app(
    pipeline: Optional["RuntimePipeline"] = None,
    *,
    pipeline_factory: Optional[Any] = None,
) -> Any:
    """构造 Flask app(便于测试注入 fake pipeline)。

    Args:
        pipeline: 已构造好的 RuntimePipeline 实例。优先使用。
        pipeline_factory: 当 pipeline=None 时调用此工厂构造。可用于延迟加载。

    Returns:
        Flask app 实例。
    """
    app = Flask(__name__)

    # 1) 解析 pipeline
    if pipeline is None:
        if pipeline_factory is not None:
            try:
                pipeline = pipeline_factory()
            except Exception as exc:  # noqa: BLE001
                logger.error("[PipelineServer] pipeline_factory 失败: %s", exc)
                pipeline = _build_noop_pipeline()
        else:
            pipeline = default_pipeline_factory()

    app.config["PIPELINE"] = pipeline
    app.config["PIPELINE_LOCK"] = threading.RLock()
    app.config["SERVER_STARTED_AT"] = _now_iso()
    app.config["REQUEST_COUNT"] = 0

    # --------------------------------------------------------
    # GET /health
    # --------------------------------------------------------
    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "runtime": "ready",
            "schema_version": PIPELINE_SERVER_SCHEMA_VERSION,
            "started_at": app.config["SERVER_STARTED_AT"],
        })

    # --------------------------------------------------------
    # Phase 6.6 —— GET /runtime/status
    # 仅返回 Runtime 各组件的就绪状态(布尔),不暴露任何业务数据。
    # --------------------------------------------------------
    @app.get("/runtime/status")
    def runtime_status():
        """返回 Runtime 各组件就绪状态(纯健康检查)。"""
        pl = app.config.get("PIPELINE")
        # pipeline: 实例存在 + 内部能取到 orchestrator 即视为 ready
        pipeline_ok = bool(
            pl is not None and getattr(pl, "_orchestrator", None) is not None,
        )
        # persistence: hook 实例存在
        persistence_ok = bool(getattr(pl, "_persistence_hook", None) is not None)
        # token_optimizer: optimizer 实例存在
        token_optimizer_ok = bool(getattr(pl, "_token_optimizer", None) is not None)
        return jsonify({
            "runtime": "ready",
            "pipeline": pipeline_ok,
            "persistence": persistence_ok,
            "token_optimizer": token_optimizer_ok,
        })

    # --------------------------------------------------------
    # POST /chat
    # --------------------------------------------------------
    @app.post("/chat")
    def chat():
        # 1) 解析 body
        try:
            payload = request.get_json(silent=True)
        except Exception:  # noqa: BLE001
            payload = None
        if payload is None:
            # 退化: 尝试 form / raw
            try:
                raw = request.get_data(as_text=True) or ""
                if raw:
                    payload = json.loads(raw)
                else:
                    payload = {}
            except Exception:  # noqa: BLE001
                payload = {}

        if not isinstance(payload, dict):
            return jsonify(_build_error_response(
                "request body must be a JSON object",
            )), 400

        # 2) 提取 message
        message = payload.get("message")
        if not isinstance(message, str):
            return jsonify(_build_error_response(
                "field 'message' is required and must be a string",
            )), 400
        # 允许空字符串(由 Pipeline 处理),但显式 None/缺失已拦截
        msg_clean = message.strip() if isinstance(message, str) else ""

        # 3) 调用 Pipeline(异常隔离)
        try:
            with app.config["PIPELINE_LOCK"]:
                app.config["REQUEST_COUNT"] += 1
                context = pipeline.run({"user_message": msg_clean})
        except Exception as exc:  # noqa: BLE001
            logger.exception("[PipelineServer] pipeline.run 异常: %s", exc)
            return jsonify(_build_error_response(
                f"pipeline error: {type(exc).__name__}",
            )), 500

        # 4) 构造响应
        if context is None:
            return jsonify(_build_error_response("pipeline returned None")), 500

        response = _build_chat_response(context)
        # HTTP 状态: success → 200, failed → 200(业务层失败仍返回响应,客户端可看 state)
        # 仅当 Pipeline 完全无 context 时返回 500
        return jsonify(response), 200

    # --------------------------------------------------------
    # 错误处理
    # --------------------------------------------------------
    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({
            "error": "not found",
            "available_endpoints": ["/health", "/chat"],
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify({
            "error": "method not allowed",
            "available_endpoints": {"GET": ["/health"], "POST": ["/chat"]},
        }), 405

    @app.errorhandler(500)
    def internal_error(e):  # noqa: ARG001
        return jsonify({
            "error": "internal server error",
            "timestamp": _now_iso(),
        }), 500

    return app


# ============================================================
# 启动入口
# ============================================================
def run(
    host: Optional[str] = None,
    port: Optional[int] = None,
    *,
    pipeline: Optional["RuntimePipeline"] = None,
    debug: bool = False,
) -> None:
    """启动 HTTP 服务。

    Args:
        host: 监听地址(默认 127.0.0.1,可由 PIPE_HOST 环境变量覆盖)。
        port: 监听端口(默认 8765,可由 PIPE_PORT 环境变量覆盖)。
        pipeline: 可选 RuntimePipeline(默认用 default_pipeline_factory())。
        debug: Flask debug 模式(默认 False)。
    """
    host = host or os.environ.get("PIPE_HOST") or DEFAULT_HOST
    port = int(port or os.environ.get("PIPE_PORT") or DEFAULT_PORT)

    app = create_app(pipeline=pipeline)
    logger.info(
        "[PipelineServer] 启动 http://%s:%s  (schema=%s)",
        host, port, PIPELINE_SERVER_SCHEMA_VERSION,
    )
    # use_reloader=False 避免重复启动 Orchestrator
    app.run(host=host, port=port, debug=debug, use_reloader=False)


# ============================================================
# CLI: python -m src.runtime.pipeline_server
# ============================================================
def _main() -> int:  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.runtime.pipeline_server",
        description="Phase 6.4 —— RuntimePipeline HTTP 桥接",
    )
    parser.add_argument("--host", default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    parser.add_argument("--debug", action="store_true", help="Flask debug 模式")
    args = parser.parse_args()
    try:
        run(host=args.host, port=args.port, debug=args.debug)
        return 0
    except KeyboardInterrupt:  # noqa: PERF203
        logger.info("[PipelineServer] 收到中断,退出")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("[PipelineServer] 启动失败: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())


__all__ = [
    "PIPELINE_SERVER_SCHEMA_VERSION",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "default_pipeline_factory",
    "create_app",
    "run",
]

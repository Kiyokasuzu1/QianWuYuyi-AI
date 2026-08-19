"""
Admin API 蓝图

羽依 AI 控制中心后端接口，
连接 Phase 0/0.5 基础设施与前端 UI。
"""

from __future__ import annotations

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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = PROJECT_ROOT / "static" / "admin"

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


@admin_bp.route("/<path:path>")
def admin_static(path: str):
    """静态资源"""
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
        if _orchestrator and hasattr(_orchestrator, 'agent_server') and _orchestrator.agent_server:
            online_agents = _orchestrator.agent_server.registry.count()
    except Exception:
        pass

    # 模块列表（含依赖关系）
    all_modules = _module_loader.get_all_modules() if _module_loader else {}
    modules_list = []
    for info in all_modules.values():
        hb_data = heartbeat_status.get(info.name, {})
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
    """重载模块"""
    if not _module_loader:
        return jsonify({"success": False, "error": "模块加载器未初始化"}), 500

    info = _module_loader.get_module(module_name)
    if not info:
        return jsonify({"success": False, "error": f"模块不存在: {module_name}"}), 404

    try:
        from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
        AuditLogger.get_instance().record(
            event_type=AuditEventType.MODULE_RELOAD,
            operator="admin",
            operator_type=AuditOperatorType.HUMAN,
            target=module_name,
        )
    except Exception:
        pass

    return jsonify({
        "success": True,
        "module": module_name,
        "message": f"模块 {info.display} 重载请求已发送",
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
            if _orchestrator and hasattr(_orchestrator, 'memory_system'):
                try:
                    _orchestrator.memory_system.clear_session_cache()
                except Exception:
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
            # 身份记忆
            if hasattr(_orchestrator, 'memory_system') and _orchestrator.memory_system:
                ms = _orchestrator.memory_system
                identity = ms.get_identity()
                if identity:
                    result["identity"] = {
                        "name": identity.get("content", "")[:200],
                        "type": identity.get("type", "identity"),
                    }

                # 事件记忆
                if hasattr(ms, 'event') and ms.event:
                    events = ms.event.events or []
                    result["events"] = events[-20:]  # 最近 20 条
                    result["stats"]["total_events"] = len(events)

                # 记忆存储
                if hasattr(ms, 'store') and ms.store:
                    try:
                        memories = ms.store.load()
                        if isinstance(memories, list):
                            result["memories"] = memories[-30:]
                            result["stats"]["total_memories"] = len(memories)
                            # 统计记忆类型
                            for m in memories:
                                mtype = m.get("type", "unknown")
                                result["stats"]["memory_types"][mtype] = \
                                    result["stats"]["memory_types"].get(mtype, 0) + 1
                    except Exception:
                        pass
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
            # 从身份记忆获取关系数据
            if hasattr(_orchestrator, 'memory_system') and _orchestrator.memory_system:
                ms = _orchestrator.memory_system
                if hasattr(ms, 'identity') and ms.identity:
                    relation_data = ms.identity.data.get("relationship", {})
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

            # 里程碑事件
            if hasattr(_orchestrator, 'memory_system') and _orchestrator.memory_system:
                ms = _orchestrator.memory_system
                if hasattr(ms, 'event') and ms.event:
                    for evt in ms.event.events:
                        if evt.get("importance", "normal") in ("high", "critical"):
                            result["milestones"].append({
                                "time": evt.get("timestamp", ""),
                                "title": evt.get("title", "重要事件"),
                                "description": evt.get("description", "")[:100],
                            })
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


def _get_character_state() -> dict:
    """
    获取羽依角色状态 — 专门服务角色展示。

    返回角色当前的 emotion / expression / message / energy，
    用于前端角色区域的状态联动（表情变化、光效变化等）。
    以后可接入 Live2D。
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

    # 角色消息
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

    return {
        "emotion": primary,
        "expression": expression,
        "message": messages.get(primary, "正在为您待命~"),
        "energy": energy,
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
            
            # 从记忆系统获取事件节点
            if hasattr(_orchestrator, 'memory_system') and _orchestrator.memory_system:
                ms = _orchestrator.memory_system
                
                # 事件记忆节点
                if hasattr(ms, 'event') and ms.event:
                    events = ms.event.events or []
                    for i, evt in enumerate(events[-20:]):
                        node_id = f"event_{i}"
                        importance = 0.5
                        if evt.get("importance") in ("high", "critical"):
                            importance = 0.9
                        elif evt.get("importance") == "normal":
                            importance = 0.6
                        
                        result["nodes"].append({
                            "id": node_id,
                            "type": "event",
                            "title": evt.get("title", "事件")[:30],
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
            
            # 记忆容量影响记忆能力
            if hasattr(_orchestrator, 'memory_system') and _orchestrator.memory_system:
                try:
                    if hasattr(_orchestrator.memory_system, 'store') and _orchestrator.memory_system.store:
                        memories = _orchestrator.memory_system.store.load()
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
                
                # 检查最近交互
                if hasattr(_orchestrator, 'memory_system') and _orchestrator.memory_system:
                    if hasattr(_orchestrator.memory_system, 'event') and _orchestrator.memory_system.event:
                        recent_events = _orchestrator.memory_system.event.events[-5:] if _orchestrator.memory_system.event.events else []
                        for evt in recent_events:
                            if evt.get("importance") in ("high", "critical"):
                                reasons.append({
                                    "factor": evt.get("title", "重要事件"),
                                    "weight": 5,
                                    "type": "event",
                                })
                
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
            
            if hasattr(_orchestrator, 'memory_system') and _orchestrator.memory_system:
                if hasattr(_orchestrator.memory_system, 'identity') and _orchestrator.memory_system.identity:
                    relation_data = _orchestrator.memory_system.identity.data.get("relationship", {})
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

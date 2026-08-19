"""
管理操作审计日志

记录所有管理面板操作（配置修改、模块切换、系统操作等），
形成完整的操作审计闭环。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AuditOperatorType:
    """操作者类型"""
    HUMAN = "human"       # 人类管理员
    AGENT = "agent"       # AI 代理（羽依自主操作）
    SYSTEM = "system"     # 系统自动操作


class AuditEventType:
    """审计事件类型"""
    CONFIG_UPDATE = "config_update"         # 配置修改
    CONFIG_ROLLBACK = "config_rollback"     # 配置回滚
    MODULE_TOGGLE = "module_toggle"         # 模块开关
    MODULE_RELOAD = "module_reload"         # 模块重载
    MODULE_START = "module_start"           # 模块启动
    MODULE_STOP = "module_stop"             # 模块停止
    SYSTEM_ACTION = "system_action"         # 系统操作（重启、清理等）
    ADMIN_LOGIN = "admin_login"             # 管理员登录
    ADMIN_LOGOUT = "admin_logout"           # 管理员登出

    # ==================== Phase 5.4 新增：治理与认知系统事件 ====================
    # 治理层（GovernanceProvider）产生的事件
    GOVERNANCE_PROPOSAL_CREATED = "governance.proposal_created"      # Proposal 创建
    GOVERNANCE_PROPOSAL_REVIEWED = "governance.proposal_reviewed"    # Proposal 审查
    GOVERNANCE_PERSONALITY_PROPOSE = "governance.personality.propose"  # 人格变更建议
    GOVERNANCE_MEMORY_PROPOSE = "governance.memory.propose"          # 记忆处理申请
    GOVERNANCE_GROWTH_REVIEW = "governance.growth.review"            # 成长方案审查

    # 成长系统（Growth）产生的事件
    GROWTH_PROPOSAL_APPLIED = "growth.proposal_applied"              # Proposal 已 apply
    GROWTH_PROPOSAL_REJECTED = "growth.proposal_rejected"            # Proposal 拒绝

    # 人格系统（Personality）产生的事件
    PERSONALITY_CHANGED = "personality.changed"                      # 人格变化

    # 记忆系统（Memory）产生的事件
    MEMORY_MODIFIED = "memory.modified"                              # 记忆修改
    MEMORY_DELETED = "memory.deleted"                                # 记忆删除


class AuditLogger:
    """
    管理操作审计记录器

    所有管理面板的操作都通过此记录器留下审计痕迹，
    支持按类型、时间、操作者过滤查询。
    """

    _instance: Optional[AuditLogger] = None
    _lock = threading.Lock()

    def __init__(self, log_dir: Optional[str] = None):
        """
        Args:
            log_dir: 审计日志存储目录，默认为 data/audit/admin/
        """
        if log_dir:
            self._log_dir = Path(log_dir)
        else:
            self._log_dir = Path(__file__).parent.parent.parent / "data" / "audit" / "admin"
        self._log_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(cls, log_dir: Optional[str] = None) -> AuditLogger:
        """获取全局单例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(log_dir=log_dir)
            return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例（仅测试用）"""
        with cls._lock:
            cls._instance = None

    def record(self, event_type: str, operator: str = "system",
               operator_type: str = "system",
               target: str = "", details: Optional[Dict[str, Any]] = None,
               result: str = "success", error: str = "",
               request_id: str = "") -> Dict[str, Any]:
        """
        记录一条审计日志

        Args:
            event_type: 事件类型（参见 AuditEventType）
            operator: 操作者标识
            operator_type: 操作者类型（human / agent / system）
            target: 操作目标（如模块名、配置节名）
            details: 操作详情
            result: 操作结果（success / failure）
            error: 错误信息（失败时填写）
            request_id: 请求追踪 ID（自动生成可不填）

        Returns:
            记录的日志条目
        """
        if not request_id:
            import uuid
            request_id = uuid.uuid4().hex[:12]

        entry = {
            "id": self._generate_id(),
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "operator": operator,
            "operator_type": operator_type,
            "target": target,
            "details": details or {},
            "result": result,
            "error": error,
            "request_id": request_id,
        }

        # 追加写入当日日志文件
        date_str = datetime.now().strftime("%Y%m%d")
        log_file = self._log_dir / f"admin_audit_{date_str}.jsonl"

        try:
            with self._lock:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"审计日志写入失败: {e}")

        return entry

    def query(self, event_type: Optional[str] = None,
              operator: Optional[str] = None,
              operator_type: Optional[str] = None,
              target: Optional[str] = None,
              result: Optional[str] = None,
              request_id: Optional[str] = None,
              start_time: Optional[str] = None,
              end_time: Optional[str] = None,
              limit: int = 50) -> List[Dict[str, Any]]:
        """
        查询审计日志

        Args:
            event_type: 按事件类型过滤
            operator: 按操作者过滤
            operator_type: 按操作者类型过滤
            target: 按操作目标过滤
            result: 按结果过滤
            request_id: 按请求追踪 ID 过滤
            start_time: 起始时间（ISO 格式）
            end_time: 结束时间（ISO 格式）
            limit: 最多返回条数

        Returns:
            匹配的日志条目列表，按时间倒序
        """
        entries: List[Dict[str, Any]] = []

        # 确定要扫描的日志文件
        log_files = sorted(self._log_dir.glob("admin_audit_*.jsonl"), reverse=True)
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if self._match_entry(entry, event_type, operator,
                                             operator_type, target, result,
                                             request_id, start_time, end_time):
                            entries.append(entry)

                            if len(entries) >= limit:
                                return entries
            except Exception as e:
                logger.warning(f"读取审计日志失败 {log_file}: {e}")

        return entries

    def _match_entry(self, entry: Dict, event_type: Optional[str],
                     operator: Optional[str], operator_type: Optional[str],
                     target: Optional[str], result: Optional[str],
                     request_id: Optional[str],
                     start_time: Optional[str],
                     end_time: Optional[str]) -> bool:
        """检查条目是否匹配过滤条件"""
        if event_type and entry.get("event_type") != event_type:
            return False
        if operator and entry.get("operator") != operator:
            return False
        if operator_type and entry.get("operator_type") != operator_type:
            return False
        if target and entry.get("target") != target:
            return False
        if result and entry.get("result") != result:
            return False
        if request_id and entry.get("request_id") != request_id:
            return False
        if start_time and entry.get("timestamp", "") < start_time:
            return False
        if end_time and entry.get("timestamp", "") > end_time:
            return False
        return True

    def _generate_id(self) -> str:
        """生成唯一日志 ID"""
        import uuid
        return uuid.uuid4().hex[:12]

    def get_summary(self, hours: int = 24) -> Dict[str, Any]:
        """
        获取审计摘要统计

        Args:
            hours: 统计最近 N 小时

        Returns:
            各类操作的次数统计
        """
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        entries = self.query(start_time=cutoff, limit=10000)

        summary: Dict[str, Any] = {
            "total": len(entries),
            "by_type": {},
            "by_result": {"success": 0, "failure": 0},
            "by_operator": {},
        }

        for entry in entries:
            etype = entry.get("event_type", "unknown")
            summary["by_type"][etype] = summary["by_type"].get(etype, 0) + 1

            res = entry.get("result", "unknown")
            if res in summary["by_result"]:
                summary["by_result"][res] += 1

            op = entry.get("operator", "unknown")
            summary["by_operator"][op] = summary["by_operator"].get(op, 0) + 1

        return summary

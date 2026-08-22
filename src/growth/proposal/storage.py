import json
import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.memory.atomic_write import atomic_write_json

from src.growth.proposal.proposal import GrowthProposal
from src.growth.proposal.constants import PROPOSAL_STATUS, MAX_PROPOSALS

logger = logging.getLogger(__name__)

# V1.0: per-path RLock 表（read-modify-write 全程持锁，消除 TOCTOU）
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path) -> threading.RLock:
    _key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        _lock = _PATH_LOCKS.get(_key)
        if _lock is None:
            _lock = threading.RLock()
            _PATH_LOCKS[_key] = _lock
    return _lock


def _backup_corrupt(path: Path) -> None:
    """损坏文件复制备份（不覆盖不删除旧文件）。"""
    try:
        if path.exists():
            _backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            shutil.copy2(str(path), _backup)
            logger.warning("ProposalStorage: 损坏文件已备份为 %s", _backup)
    except Exception:
        pass


class ProposalStorage:
    def __init__(self, data_dir: str = "data/growth/proposals"):
        # V1.1: 构造时 resolve 为绝对路径——单例跨 cwd 切换后读写仍锚定
        # 首次构造位置,不再随进程工作目录漂移（相对路径单例缺陷 F1）。
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.json_file = self.data_dir / "proposals.json"

        if not self.json_file.exists():
            self._init_json_file()

    @classmethod
    def reset_for_testing(cls):
        """V1.1: 清除模块级单例缓存，供测试隔离使用（conftest 每测试调用）。"""
        global _global_storage
        _global_storage = None

    def _init_json_file(self):
        # V1.0: 原子写（替换裸 open("w")+json.dump）
        with _path_lock(self.json_file):
            atomic_write_json(
                str(self.json_file), {"version": "1.0", "proposals": []}
            )

    def _read_data(self) -> Optional[Dict]:
        """读取全部数据；损坏时备份并返回 None（不覆盖旧文件）。"""
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("proposal storage envelope 不是 dict")
            return data
        except Exception as e:
            print(f"[ProposalStorage] 数据加载失败: {e}")
            _backup_corrupt(self.json_file)
            return None

    def save(self, proposal: GrowthProposal):
        # V1.0: read-modify-write 全程持锁 + 原子落盘（原实现有 TOCTOU）
        with _path_lock(self.json_file):
            try:
                data = self._read_data()
                if data is None:
                    data = {"version": "1.0", "proposals": []}

                proposals = data.get("proposals", [])
                found = False
                for i, p in enumerate(proposals):
                    if p["proposal_id"] == proposal.proposal_id:
                        proposals[i] = proposal.to_dict()
                        found = True
                        break

                if not found:
                    proposals.append(proposal.to_dict())

                if len(proposals) > MAX_PROPOSALS:
                    proposals = proposals[-MAX_PROPOSALS:]

                data["proposals"] = proposals

                atomic_write_json(str(self.json_file), data)
            except Exception as e:
                print(f"[ProposalStorage] 保存失败: {e}")

    def load(self, proposal_id: str) -> Optional[GrowthProposal]:
        try:
            data = self._read_data()
            if data is None:
                return None

            for p in data.get("proposals", []):
                if p["proposal_id"] == proposal_id:
                    return GrowthProposal.from_dict(p)
            return None
        except Exception as e:
            print(f"[ProposalStorage] 加载失败: {e}")
            return None

    def list_all(self, limit: int = 50, offset: int = 0) -> List[GrowthProposal]:
        try:
            data = self._read_data()
            if data is None:
                return []

            proposals = data.get("proposals", [])
            proposals.sort(key=lambda x: x["timestamp"], reverse=True)

            start = offset
            end = start + limit
            return [GrowthProposal.from_dict(p) for p in proposals[start:end]]
        except Exception as e:
            print(f"[ProposalStorage] 列表加载失败: {e}")
            return []

    def list_by_status(self, status: str, limit: int = 50) -> List[GrowthProposal]:
        all_proposals = self.list_all(limit=1000)
        return [p for p in all_proposals if p.status == status][:limit]

    def list_by_type(self, proposal_type: str, limit: int = 50) -> List[GrowthProposal]:
        all_proposals = self.list_all(limit=1000)
        return [p for p in all_proposals if p.proposal_type == proposal_type][:limit]

    def list_pending(self, limit: int = 50) -> List[GrowthProposal]:
        return self.list_by_status(PROPOSAL_STATUS["PENDING"], limit=limit)

    def delete(self, proposal_id: str):
        # V1.0: read-modify-write 全程持锁 + 原子落盘
        with _path_lock(self.json_file):
            try:
                data = self._read_data()
                if data is None:
                    return

                proposals = data.get("proposals", [])
                proposals = [p for p in proposals if p["proposal_id"] != proposal_id]
                data["proposals"] = proposals

                atomic_write_json(str(self.json_file), data)
            except Exception as e:
                print(f"[ProposalStorage] 删除失败: {e}")

    def count(self) -> int:
        try:
            data = self._read_data()
            if data is None:
                return 0
            return len(data.get("proposals", []))
        except Exception:
            return 0


_global_storage = None


def get_proposal_storage() -> ProposalStorage:
    global _global_storage
    if _global_storage is None:
        _global_storage = ProposalStorage()
    return _global_storage


def save_proposal(proposal: GrowthProposal):
    get_proposal_storage().save(proposal)


def load_proposal(proposal_id: str) -> Optional[GrowthProposal]:
    return get_proposal_storage().load(proposal_id)


def list_pending_proposals(limit: int = 50) -> List[GrowthProposal]:
    return get_proposal_storage().list_pending(limit=limit)


def list_all_proposals(limit: int = 50) -> List[GrowthProposal]:
    return get_proposal_storage().list_all(limit=limit)


def build_self_model_governance_proposal(
    *,
    source_event_id: str = "",
    confidence: float = 0.5,
    reason: str = "",
    self_model_payload: Dict,
    decision_meta: Dict,
    source: str = "selfmodel_consumer",
) -> Optional[GrowthProposal]:
    """v1.3 RC 1.1 (K1): self_model 治理提案构造兼容层(producer 层)。

    Phase 3.6.4 import 方向规则: legacy GrowthProposal 只允许出现在
    storage/reviewer/governance_provider 兼容层。G-1.2 的提案构造逻辑
    原位于 src/admin/selfmodel_consumer.py(被静态扫描禁止), 此处作为
    兼容层 producer 提供, 行为与逐字段构造完全一致(恒 PENDING)。
    """
    try:
        from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

        return GrowthProposal(
            proposal_type=PROPOSAL_TYPE["SELF_MODEL"],
            status=PROPOSAL_STATUS["PENDING"],
            source=str(source or "selfmodel_consumer"),
            source_event_id=str(source_event_id or ""),
            confidence=float(confidence or 0.5),
            reason=str(reason or "consumer_governance"),
            metadata={
                "self_model_proposal": dict(self_model_payload or {}),
                "governance_decision": dict(decision_meta or {}),
                "source": str(source or "selfmodel_consumer"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ProposalStorage] build_self_model_governance_proposal 失败(已隔离): %s", exc)
        return None


def find_proposal_same_source(
    storage: ProposalStorage,
    proposal_type: str,
    source_event_id: str,
) -> Optional[GrowthProposal]:
    """R-1.5.0: 同源去重——同 proposal_type + 同 source_event_id 的非终态提案
    已存在时返回该提案（调用方应复用而非重复创建）。

    source_event_id 为空时不判定（无法去重, 保持旧行为）。
    """
    if not source_event_id:
        return None
    try:
        for p in storage.list_by_type(proposal_type, limit=1000):
            if (
                str(getattr(p, "source_event_id", "") or "") == source_event_id
                and getattr(p, "status", "") in ("pending", "approved")
            ):
                return p
    except Exception:  # noqa: BLE001
        return None
    return None

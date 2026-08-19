"""
人格成长记录 (PersonalityGrowthRecord) v1.1

职责：
记录一次人格变化背后的意义，连接“经历 → 人格变化 → 自我理解”。

v1.1 修正：
- record_id 使用 uuid 保证唯一性
- changes 类型定义 TraitChange，避免类型冲突
- 增加 validate_record 验证方法（含空 changes 检查）
- PersonalityGrowthHistory 增加 all() 方法
"""

from typing import TypedDict, Dict, List, Optional
from datetime import datetime
import uuid


class TraitChange(TypedDict, total=False):
    """单个人格维度的变化详情"""
    before: float
    after: float
    delta: float
    momentum_before: float
    momentum_after: float
    reason: str


class PersonalityGrowthRecord(TypedDict, total=False):
    """人格成长记录"""

    # ---- 标识 ----
    record_id: str
    timestamp: str

    # ---- 来源 ----
    trigger_events: List[str]

    # ---- 人格变化 ----
    changes: Dict[str, TraitChange]
    affected_dimensions: List[str]

    # ---- 成长理解 ----
    meaning: str
    narrative: str
    confidence: float

    # ---- 元数据 ----
    validation_count: int
    growth_level: str


def create_personality_growth_record(
    trigger_events: List[str],
    changes: Dict[str, TraitChange],
    affected_dimensions: List[str],
    meaning: str,
    narrative: str = "",
    confidence: float = 0.5,
    validation_count: int = 1,
    growth_level: str = "context",
) -> PersonalityGrowthRecord:
    """创建一条人格成长记录"""
    return {
        "record_id": f"pgr_{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now().isoformat(),
        "trigger_events": trigger_events,
        "changes": changes,
        "affected_dimensions": affected_dimensions,
        "meaning": meaning,
        "narrative": narrative,
        "confidence": confidence,
        "validation_count": validation_count,
        "growth_level": growth_level,
    }


def validate_record(record: PersonalityGrowthRecord) -> bool:
    """验证成长记录的有效性"""
    confidence = record.get("confidence", 0)
    if not 0.0 <= confidence <= 1.0:
        return False

    valid_levels = {"context", "preference", "trait"}
    if record.get("growth_level") not in valid_levels:
        return False

    if not record.get("affected_dimensions"):
        return False

    if not record.get("changes"):
        return False

    if not record.get("meaning"):
        return False

    return True


class PersonalityGrowthHistory:
    """人格成长历史管理器"""

    def __init__(self):
        self.records: List[PersonalityGrowthRecord] = []

    def add(self, record: PersonalityGrowthRecord) -> bool:
        """
        添加一条成长记录。
        返回 True 表示添加成功，False 表示验证失败。
        """
        if validate_record(record):
            self.records.append(record)
            return True
        return False

    def all(self) -> List[PersonalityGrowthRecord]:
        """返回所有记录"""
        return self.records

    def latest(self) -> Optional[PersonalityGrowthRecord]:
        """获取最近一条记录"""
        return self.records[-1] if self.records else None

    def get_by_dimension(self, trait: str) -> List[PersonalityGrowthRecord]:
        """获取影响某个维度的所有成长记录"""
        return [
            r for r in self.records
            if trait in r.get("affected_dimensions", [])
        ]

    def get_by_level(self, level: str) -> List[PersonalityGrowthRecord]:
        """获取指定成长层级的所有记录"""
        return [
            r for r in self.records
            if r.get("growth_level") == level
        ]

    def get_high_confidence(self, threshold: float = 0.7) -> List[PersonalityGrowthRecord]:
        """获取高置信度的成长记录"""
        return [
            r for r in self.records
            if r.get("confidence", 0) >= threshold
        ]

    def count(self) -> int:
        """记录总数"""
        return len(self.records)
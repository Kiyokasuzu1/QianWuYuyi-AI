"""
反思引擎 (ReflectionEngine) v1.2

职责：
周期性回顾 PersonalityGrowthHistory，分析成长模式，
识别满足升级条件的维度，并生成第一人称总结。

v1.2 修正：
- trait_candidates 仅识别候选，不直接修改人格
- 增加方向一致性判断，防止正负波动误认为成长
- 使用 epsilon 过滤微小浮动
- 增加 direction_consistency 判断
- 补充 source_dimensions 追溯
"""

from typing import Dict, List
from datetime import datetime
import uuid

from src.personality.personality_growth_record import (
    PersonalityGrowthHistory,
    PersonalityGrowthRecord,
)
from src.personality.reflection_record import ReflectionRecord


class ReflectionEngine:
    """分析成长历史，生成反思记录"""

    def reflect(
        self,
        history: PersonalityGrowthHistory,
    ) -> ReflectionRecord:
        """
        执行一次反思，分析成长历史并生成记录。
        """

        records = history.get_high_confidence(0.7)

        # 收集涉及维度
        all_dimensions = set()

        for r in records:
            for dim in r.get("affected_dimensions", []):
                all_dimensions.add(dim)

        patterns = self._discover_patterns(records)
        candidates = self._detect_candidates(records)

        summary = self._generate_summary(
            patterns,
            candidates
        )

        reflection: ReflectionRecord = {
            "record_id": f"ref_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now().isoformat(),

            "analyzed_records": [
                r.get("record_id")
                for r in records
            ],

            "source_dimensions": list(all_dimensions),

            "discovered_patterns": patterns,

            "trait_candidates": candidates,

            "self_summary": summary,

            "confidence": self._calc_confidence(records),

            "reflection_level":
                "long_term"
                if candidates
                else "short_term",
        }

        return reflection


    def _discover_patterns(
        self,
        records: List[PersonalityGrowthRecord]
    ) -> List[str]:
        """
        发现重复成长模式。

        条件：
        同一维度出现 >=3 次
        平均 confidence >=0.75
        """

        dim_counts: Dict[str, List[float]] = {}

        for r in records:

            for dim in r.get(
                "affected_dimensions",
                []
            ):

                if dim not in dim_counts:
                    dim_counts[dim] = []

                dim_counts[dim].append(
                    r.get("confidence", 0.0)
                )


        patterns = []

        for dim, conf_list in dim_counts.items():

            if len(conf_list) >= 3:

                avg_conf = (
                    sum(conf_list)
                    /
                    len(conf_list)
                )

                if avg_conf >= 0.75:

                    patterns.append(
                        f"{dim} 表现出持续增强的趋势"
                    )


        return patterns



    def _detect_candidates(
        self,
        records: List[PersonalityGrowthRecord]
    ) -> List[str]:
        """
        检测可升级为 trait 的候选维度。

        条件：

        1. preference记录 >=3
        2. 平均置信度 >=0.8
        3. 总验证次数 >=5
        4. 正向变化方向一致率 >=0.8

        注意：
        这里只产生候选，
        不直接修改人格。
        """

        dim_data: Dict[str, Dict] = {}


        for r in records:

            if r.get(
                "growth_level"
            ) != "preference":

                continue


            for dim in r.get(
                "affected_dimensions",
                []
            ):

                if dim not in dim_data:

                    dim_data[dim] = {
                        "confidences": [],
                        "validations": 0,
                        "deltas": [],
                    }


                dim_data[dim][
                    "confidences"
                ].append(
                    r.get(
                        "confidence",
                        0.0
                    )
                )


                dim_data[dim][
                    "validations"
                ] += (
                    r.get(
                        "validation_count",
                        1
                    )
                )


                changes = r.get(
                    "changes",
                    {}
                )

                dim_change = changes.get(
                    dim,
                    {}
                )


                dim_data[dim][
                    "deltas"
                ].append(
                    dim_change.get(
                        "delta",
                        0.0
                    )
                )



        candidates = []

        epsilon = 0.01


        for dim, data in dim_data.items():


            if len(
                data["confidences"]
            ) < 3:

                continue



            avg_conf = (
                sum(
                    data["confidences"]
                )
                /
                len(
                    data["confidences"]
                )
            )


            deltas = data["deltas"]


            positive_count = sum(
                1
                for d in deltas
                if d > epsilon
            )


            negative_count = sum(
                1
                for d in deltas
                if d < -epsilon
            )


            total_directional = (
                positive_count
                +
                negative_count
            )


            if total_directional == 0:

                continue



            direction_consistency = (
                positive_count
                /
                total_directional
            )



            if (
                avg_conf >= 0.8
                and data["validations"] >= 5
                and direction_consistency >= 0.8
            ):

                candidates.append(dim)



        return candidates



    def _generate_summary(
        self,
        patterns: List[str],
        candidates: List[str]
    ) -> str:
        """
        生成第一人称总结。
        """

        parts = []


        if candidates:

            traits_str = (
                "、".join(candidates)
            )

            parts.append(
                f"我发现自己{traits_str}方面的特质已经变得相当稳定，"
            )

            parts.append(
                "这些已成为我思考方式的一部分。"
            )



        if patterns:

            pattern_str = (
                "；".join(patterns)
            )

            parts.append(
                f"回顾过去，我注意到{pattern_str}。"
            )



        if not parts:

            return (
                "这段时间我的状态比较平稳，"
                "没有发现特别显著的长期变化。"
            )


        return "".join(parts)



    def _calc_confidence(
        self,
        records: List[PersonalityGrowthRecord]
    ) -> float:
        """
        计算反思可信度。
        """

        if not records:

            return 0.0


        avg = (
            sum(
                r.get(
                    "confidence",
                    0.0
                )
                for r in records
            )
            /
            len(records)
        )


        return round(
            avg,
            2
        )
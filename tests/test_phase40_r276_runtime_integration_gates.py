"""
Phase 4.0 — R2.7.6 Runtime Integration Gates

四个 Gate 证明：实验 Runtime 已经变成真实生产 Runtime。

    RIG-1 Entry：
        真实调用 RuntimeController.handle_message()（不走 Orchestrator），
        回复非空、且 data/users/<user_id>/ 三态文件已生成。

    RIG-2 Restart：
        Day A 聊天 10 轮 → save → del controller singleton → 重新 new controller 读同一目录
        personality traits 全等，记忆仍在，interaction_count ≥ 10（状态跨进程续）。

    RIG-3 Real Memory：
        Day 1 写入"用户特别喜欢猫娘角色设计"记忆，Day 3（第 12 轮）再问"你觉得什么样的角色有魅力？"
        reply 命中连续性关键词 ≥ 2。

    RIG-4 Growth Safety：
        100 轮真实 handle_message（不重复随机 delta），5 核心 trait 月 Δ ≤ 0.30，
        单日跳变 ≤ 0.10，方向反转 ≤ 3 次（人格惯性）。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple


class TestRIG1RuntimeEntryGate(unittest.TestCase):
    """RIG-1 Runtime Entry Gate：handle_message 跑一轮后三态目录生成 + reply 非空。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rig1_"))
        # RuntimeController._instance 是进程级单例，测试要清掉后重建，避免脏状态
        from src.runtime.runtime_controller import RuntimeController
        RuntimeController._instance = None

    def tearDown(self):
        from src.runtime.runtime_controller import RuntimeController
        try:
            RuntimeController._instance = None
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def test_handle_message_produces_reply_and_snapshots(self):
        from src.runtime.runtime_controller import RuntimeController
        cfg = {
            "runtime": {
                "phase4_enabled": True,
                "users_root_dir": str(self.tmp),
                "llm_engine": "mock",
                "growth_enabled": True,
            }
        }
        ctrl = RuntimeController(config=cfg)
        res = ctrl.handle_message(user_id="qq_10001", message="你好羽依，我叫清夏，今天想聊聊设计")
        # 1) reply 非空
        self.assertIsInstance(res.reply, str)
        self.assertGreaterEqual(len(res.reply.strip()), 6, msg=f"reply 过短:\n{res.reply}")
        # 2) 三态目录存在
        expected_dir = self.tmp / "qq_10001"
        self.assertTrue(expected_dir.exists(), msg=f"用户目录没创建：{expected_dir}")
        expected_files = [
            expected_dir / "personality_qq_10001.json",
            expected_dir / "memory_qq_10001.json",
            expected_dir / "relationship_qq_10001.json",
        ]
        missing = [p for p in expected_files if not p.exists()]
        self.assertFalse(missing, msg=f"缺少快照文件：{missing}")
        # 3) debug 字段语义正确
        self.assertEqual(res.debug.get("user_id"), "qq_10001")
        self.assertGreaterEqual(int(res.debug.get("interaction_count", 0)), 1)

    def test_feature_flag_disabled_raises_not_implemented(self):
        """phase4_enabled=false 时 handle_message 抛 NotImplementedError，调用方走 Orchestrator fallback。"""
        from src.runtime.runtime_controller import RuntimeController
        ctrl = RuntimeController(config={
            "runtime": {
                "phase4_enabled": False,
                "users_root_dir": str(self.tmp),
            }
        })
        with self.assertRaises(NotImplementedError):
            ctrl.handle_message(user_id="qq_10002", message="hi")


class TestRIG2RestartGate(unittest.TestCase):
    """RIG-2 Restart Gate：模拟 server kill → restart，状态延续。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rig2_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_ctrl(self):
        from src.runtime.runtime_controller import RuntimeController
        RuntimeController._instance = None
        return RuntimeController(config={
            "runtime": {
                "phase4_enabled": True,
                "users_root_dir": str(self.tmp),
                "llm_engine": "mock",
                "growth_enabled": True,
            }
        })

    def test_kill_restart_personality_and_memory_preserved(self):
        # Session A：10 轮
        ctrl_a = self._make_ctrl()
        messages = [
            "你好我叫清夏",
            "我最近在研究 AI 绘画",
            "我在设计猫娘角色",
            "今天工作压力大",
            "和你聊天稍微放松一些啦",
            "我今天把项目收尾了",
            "看到一只橘猫特别可爱",
            "回家睡了一觉感觉充电了",
            "最近想创作点新的东西",
            "今天特别想聊聊我的新角色构思",
        ]
        last_replies: List[str] = []
        for m in messages:
            hr = ctrl_a.handle_message(user_id="qq_20001", message=m)
            last_replies.append(hr.reply)

        # 从磁盘读 Session A 的 personality snapshot（模拟 kill 前最后状态）
        pm_a = ctrl_a._get_pm_for_user("qq_20001")  # 测试用允许访问内部
        load_a = pm_a.load_all()
        traits_a = dict(load_a.personality.traits)
        mems_a_count = len(load_a.memories)
        inter_a_count = int(load_a.relationship["interaction_count"])
        self.assertGreaterEqual(inter_a_count, 10)

        # Session B："重启"服务器——删除单例缓存，重新 new（不改目录）
        ctrl_b = self._make_ctrl()

        pm_b = ctrl_b._get_pm_for_user("qq_20001")
        load_b = pm_b.load_all()
        traits_b = dict(load_b.personality.traits)
        # 1) traits 在 kill/restart 之间没变化（因为 B 刚起来没聊）
        self.assertDictEqual(traits_a, traits_b, msg="重启后 trait 不一致——状态丢失！")
        # 2) 记忆 count 延续
        self.assertGreaterEqual(len(load_b.memories), mems_a_count)
        # 3) relationship interaction_count 延续 ≥ 10
        self.assertGreaterEqual(int(load_b.relationship["interaction_count"]), inter_a_count)

        # 再聊 1 轮：版本不回退，reply 仍然受记忆影响
        hr_b = ctrl_b.handle_message(user_id="qq_20001", message="最近怎么样？")
        self.assertGreaterEqual(len(hr_b.reply), 6)
        # 验证 personality_version ≥ load_b.personality.version（B 继续成长，version 单调）
        load_c = pm_b.load_all()
        self.assertGreaterEqual(load_c.personality.version, load_b.personality.version)


class TestRIG3RealMemoryGate(unittest.TestCase):
    """RIG-3 Real User Memory Gate：Day 1 记猫娘，Day 3 仍然自然关联。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rig3_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_catgirl_memory_reflected_on_day3_reply(self):
        from src.runtime.runtime_controller import RuntimeController
        RuntimeController._instance = None
        ctrl = RuntimeController(config={
            "runtime": {
                "phase4_enabled": True,
                "users_root_dir": str(self.tmp),
                "llm_engine": "mock",
                "growth_enabled": True,
            }
        })

        # Day 1（前 10 轮）含 3 条"猫娘 + 角色设计"记忆强相关消息（通过 handle_message 直接写 ≥10 字会自动存）
        day1_msgs = [
            "你好我叫清夏",
            "最近在做原创角色设计",
            "我想设计一个猫娘风格的角色",
            "我希望她性格温柔但有主见",
            "我想好了名字叫浅雾，头发是银色的",
            "工作压力有时候真的好大",
            "不过完成一个大项目了终于有空",
            "之前画的 AI 绘画现在看看特别有感觉",
            "最近也在想独立创作的事情",
            "我真的非常喜欢猫娘角色设计这类创作",  # 第 10 条：强锚点
        ]
        for m in day1_msgs:
            ctrl.handle_message(user_id="qq_30001", message=m)

        # Day 2（中间 2 轮杂项）
        ctrl.handle_message(user_id="qq_30001", message="今天天气真不错，出门散步去了")
        ctrl.handle_message(user_id="qq_30001", message="路上看到一只橘猫跑过去")

        # Day 3（第 13 轮）：问"你觉得什么样的角色有魅力？"
        hr = ctrl.handle_message(
            user_id="qq_30001",
            message="你觉得什么样的角色有魅力？",
        )
        reply = hr.reply
        strict = ["角色", "设计", "创造", "AI 绘画", "AI绘画", "绘画", "创造力"]
        relaxed = ["塑造形象", "画图", "创作", "具象", "猫娘", "形象", "构思", "银", "浅雾", "温柔"]
        all_kws = list(set(strict + relaxed))
        hits = sum(1 for w in all_kws if w in reply)
        self.assertGreaterEqual(
            hits,
            2,
            msg=f"Day 3 reply 对 Day1 猫娘设计记忆无关联（hits={hits}<2）。reply=\n{reply}",
        )
        self.assertNotIn(
            "最近没什么特别的变化",
            reply,
            msg=f"Day3 reply 退回 baseline，记忆无影响。reply=\n{reply}",
        )


class TestRIG4GrowthSafetyGate(unittest.TestCase):
    """RIG-4 Growth Safety：100 轮真实消息 trait 平滑不漂移。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rig4_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_100_turns_growth_curve_smooth(self):
        from src.runtime.runtime_controller import RuntimeController
        RuntimeController._instance = None
        ctrl = RuntimeController(config={
            "runtime": {
                "phase4_enabled": True,
                "users_root_dir": str(self.tmp),
                "llm_engine": "mock",
                "growth_enabled": True,
            }
        })

        # 100 轮脚本：轮询 10 种不同话题，模拟真实 100 轮不重复
        topics = [
            "今天画了一张 AI 绘画，角色是机械猫娘风格。",
            "最近工作好累，压力大。",
            "和朋友出去玩了一天很开心。",
            "我又想到一个新角色构思啦，温柔但有主见的那种。",
            "我在考虑要不要辞职做自由创作。",
            "今天阳光特别好，我出门散步了。",
            "最近读了一本书，关于角色设计的理论。",
            "回到家特别疲惫，和你聊聊舒服一些。",
            "AI 绘画新出了模型，提示词效果差别挺大的。",
            "最近我觉得独立思考自己的方向很重要。",
        ]
        traits_series: Dict[str, List[float]] = {k: [] for k in ["creativity", "curiosity", "empathy", "independence", "playfulness"]}
        pm = ctrl._get_pm_for_user("qq_40001")
        for i in range(100):
            msg = topics[i % len(topics)]
            ctrl.handle_message(user_id="qq_40001", message=msg)
            if (i + 1) % 5 == 0 or i == 0:
                # 每 5 轮抽样一次 trait 快照
                loaded = pm.load_all()
                for k in traits_series:
                    traits_series[k].append(float(loaded.personality.traits.get(k, 0.5)))

        # 1) 月（实际 100 轮）Δ ≤ 0.30
        for k, series in traits_series.items():
            delta = series[-1] - series[0]
            self.assertLessEqual(
                abs(delta),
                0.30,
                msg=f"{k} 100 轮 Δ={delta:+.4f} > 0.30，人格漂移。first={series[0]:.4f} last={series[-1]:.4f}",
            )

        # 2) 相邻抽样（≈5 轮）跳变 ≤ 0.10
        violations: List[str] = []
        for k, series in traits_series.items():
            for i in range(1, len(series)):
                d = abs(series[i] - series[i - 1])
                if d > 0.10:
                    violations.append(
                        f"{k} sample#{i}: {series[i-1]:.4f}→{series[i]:.4f} |Δ|={d:.4f}"
                    )
        self.assertFalse(violations, msg="存在 5 轮跳变 >0.10:\n" + "\n".join(violations))

        # 3) 方向反转次数 ≤ 3（人格惯性）
        total_reversals = 0
        for k, series in traits_series.items():
            dirs: List[int] = []
            for i in range(1, len(series)):
                d = series[i] - series[i - 1]
                if abs(d) < 1e-9:
                    continue
                dirs.append(1 if d > 0 else -1)
            reversals = sum(1 for i in range(1, len(dirs)) if dirs[i] != dirs[i - 1])
            total_reversals += reversals
            self.assertLessEqual(
                reversals,
                3,
                msg=(
                    f"{k} 方向反转 {reversals} 次 > 3（人格锯齿漂移）。"
                    f"series={[round(x,4) for x in series]}"
                ),
            )
        # 额外：总反转（5 trait 合并）≤ 10
        self.assertLessEqual(total_reversals, 10, msg=f"5 trait 总反转 {total_reversals} 次 > 10")


if __name__ == "__main__":
    unittest.main()

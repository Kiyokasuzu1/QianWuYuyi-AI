"""
Scheduler 单元测试
"""

import unittest
import time

from src.runtime.scheduler import Scheduler, ScheduledTask


class TestScheduledTask(unittest.TestCase):
    """测试单个定时任务"""

    def test_task_execution(self):
        results = []

        def callback():
            results.append(1)

        task = ScheduledTask("test", 0.05, callback)
        task.start()
        time.sleep(0.15)
        task.stop()

        self.assertGreaterEqual(len(results), 2)

    def test_task_error_handling(self):
        results = []

        def bad_callback():
            results.append(1)
            raise ValueError("模拟错误")

        task = ScheduledTask("bad", 0.05, bad_callback)
        task.start()
        time.sleep(0.15)
        task.stop()

        # 即使出错也应继续执行
        self.assertGreaterEqual(len(results), 2)
        self.assertGreater(task.error_count, 0)

    def test_task_stop(self):
        results = []

        def callback():
            results.append(1)

        task = ScheduledTask("test", 0.05, callback)
        task.start()
        time.sleep(0.08)
        task.stop()
        old_len = len(results)
        time.sleep(0.1)

        # 停止后不应再增加
        self.assertEqual(len(results), old_len)


class TestScheduler(unittest.TestCase):
    """测试调度器"""

    def test_add_and_start(self):
        scheduler = Scheduler()
        results = []

        scheduler.add_interval_task("task1", 0.05, lambda: results.append(1))
        scheduler.start()
        time.sleep(0.15)
        scheduler.stop()

        self.assertGreaterEqual(len(results), 2)

    def test_list_tasks(self):
        scheduler = Scheduler()
        scheduler.add_interval_task("task1", 1.0, lambda: None)
        scheduler.add_interval_task("task2", 2.0, lambda: None)

        tasks = scheduler.list_tasks()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks["task1"]["interval"], 1.0)
        self.assertEqual(tasks["task2"]["interval"], 2.0)

    def test_remove_task(self):
        scheduler = Scheduler()
        results = []

        scheduler.add_interval_task("task1", 0.05, lambda: results.append(1))
        scheduler.start()
        time.sleep(0.08)
        scheduler.remove_task("task1")
        time.sleep(0.1)

        old_len = len(results)
        time.sleep(0.1)
        self.assertEqual(len(results), old_len)

    def test_multiple_tasks(self):
        scheduler = Scheduler()
        results_a = []
        results_b = []

        scheduler.add_interval_task("a", 0.05, lambda: results_a.append(1))
        scheduler.add_interval_task("b", 0.08, lambda: results_b.append(1))
        scheduler.start()
        time.sleep(0.2)
        scheduler.stop()

        self.assertGreaterEqual(len(results_a), 3)
        self.assertGreaterEqual(len(results_b), 2)


if __name__ == "__main__":
    unittest.main()
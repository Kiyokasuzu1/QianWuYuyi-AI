"""
ActionDispatcher 单元测试
"""

import unittest

from src.runtime.action_dispatcher import ActionDispatcher, Action
from src.runtime.decision_engine import Decision


class TestActionDispatcherRegister(unittest.TestCase):
    """测试注册和分发"""

    def test_register_and_dispatch(self):
        dispatcher = ActionDispatcher()
        results = []

        def handler(action):
            results.append(action.action_type)
            return {"status": "ok", "action_id": action.action_id}

        dispatcher.register("test_action", handler)
        action = Action(action_id="test1", action_type="test_action", payload={"key": "value"})
        result = dispatcher.dispatch(action)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "test_action")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(action.status, "completed")

    def test_dispatch_updates_history(self):
        dispatcher = ActionDispatcher()
        dispatcher.register("test", lambda a: None)

        action = Action(action_id="a1", action_type="test", payload={})
        dispatcher.dispatch(action)

        history = dispatcher.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].action_id, "a1")

    def test_no_handler(self):
        dispatcher = ActionDispatcher()
        action = Action(action_id="test2", action_type="unknown", payload={})
        result = dispatcher.dispatch(action)

        self.assertEqual(result["status"], "no_handler")
        self.assertEqual(action.status, "no_handler")

    def test_handler_exception(self):
        dispatcher = ActionDispatcher()

        def bad_handler(action):
            raise ValueError("模拟错误")

        dispatcher.register("bad", bad_handler)
        action = Action(action_id="test3", action_type="bad", payload={})
        result = dispatcher.dispatch(action)

        self.assertEqual(action.status, "failed")
        self.assertIn("error", result)


class TestActionDispatcherHistory(unittest.TestCase):
    """测试历史记录"""

    def test_get_all_history(self):
        dispatcher = ActionDispatcher()
        dispatcher.register("type_a", lambda a: None)
        dispatcher.register("type_b", lambda a: None)

        dispatcher.dispatch(Action(action_id="1", action_type="type_a", payload={}))
        dispatcher.dispatch(Action(action_id="2", action_type="type_b", payload={}))

        history = dispatcher.get_history()
        self.assertEqual(len(history), 2)

    def test_get_filtered_history(self):
        dispatcher = ActionDispatcher()
        dispatcher.register("type_a", lambda a: None)
        dispatcher.register("type_b", lambda a: None)

        dispatcher.dispatch(Action(action_id="1", action_type="type_a", payload={}))
        dispatcher.dispatch(Action(action_id="2", action_type="type_b", payload={}))
        dispatcher.dispatch(Action(action_id="3", action_type="type_a", payload={}))

        history = dispatcher.get_history("type_a")
        self.assertEqual(len(history), 2)

    def test_history_limit(self):
        dispatcher = ActionDispatcher()
        dispatcher.register("test", lambda a: None)
        dispatcher._max_history = 3

        for i in range(5):
            dispatcher.dispatch(Action(action_id=str(i), action_type="test", payload={}))

        history = dispatcher.get_history()
        self.assertEqual(len(history), 3)


class TestActionFromDecision(unittest.TestCase):
    """测试从决策创建行动"""

    def test_from_decision(self):
        decision = Decision(
            action_type="send_message",
            priority=0.8,
            payload={"text": "hello"},
            reason="test",
            confidence=0.9,
        )
        action = Action.from_decision(decision)

        self.assertEqual(action.action_type, "send_message")
        self.assertEqual(action.payload["text"], "hello")
        self.assertEqual(action.reason, "test")
        self.assertTrue(action.action_id.startswith("act_"))
        self.assertEqual(action.status, "pending")


if __name__ == "__main__":
    unittest.main()
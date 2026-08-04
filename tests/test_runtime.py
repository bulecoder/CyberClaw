import asyncio
import os
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cyberclaw.core.runtime import (
    AgentRunLimits,
    RuntimeLimitConfigError,
    STOP_TASK,
    count_current_turn_model_calls,
    count_current_turn_tool_calls,
    current_turn_messages,
    shutdown_task_queue,
)


class TestAgentRunLimits(unittest.TestCase):
    def test_defaults_leave_existing_local_configuration_unchanged(self):
        limits = AgentRunLimits()

        self.assertEqual(limits.max_model_calls, 20)
        self.assertEqual(limits.max_tool_calls, 50)
        self.assertEqual(limits.recursion_limit, 50)

    def test_loads_optional_integer_environment_overrides(self):
        with patch.dict(
            os.environ,
            {
                "CYBERCLAW_MAX_MODEL_CALLS": "4",
                "CYBERCLAW_MAX_TOOL_CALLS": "7",
                "CYBERCLAW_RECURSION_LIMIT": "12",
            },
        ):
            limits = AgentRunLimits.from_env()

        self.assertEqual(limits, AgentRunLimits(4, 7, 12))

    def test_rejects_invalid_or_conflicting_limits(self):
        with self.assertRaises(RuntimeLimitConfigError):
            AgentRunLimits(max_model_calls=0)
        with self.assertRaises(RuntimeLimitConfigError):
            AgentRunLimits(max_tool_calls=-1)
        with self.assertRaises(RuntimeLimitConfigError):
            AgentRunLimits(max_model_calls=4, recursion_limit=8)

        with patch.dict(
            os.environ,
            {"CYBERCLAW_MAX_MODEL_CALLS": "not-an-integer"},
        ):
            with self.assertRaises(RuntimeLimitConfigError):
                AgentRunLimits.from_env()

    def test_counts_only_the_latest_user_turn(self):
        messages = [
            HumanMessage(content="old turn"),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "old_tool",
                    "args": {},
                    "id": "old-call",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content="old result",
                tool_call_id="old-call",
                name="old_tool",
            ),
            AIMessage(content="old answer"),
            HumanMessage(content="current turn"),
            AIMessage(content="current answer"),
        ]

        self.assertEqual(
            current_turn_messages(messages),
            messages[-2:],
        )
        self.assertEqual(count_current_turn_model_calls(messages), 1)
        self.assertEqual(count_current_turn_tool_calls(messages), 0)


class TestRuntimeShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_stops_producer_then_drains_accepted_tasks(self):
        queue = asyncio.Queue(maxsize=3)
        processed = []
        producer_started = asyncio.Event()
        producer_stopped = asyncio.Event()

        async def consumer():
            while True:
                item = await queue.get()
                try:
                    if item is STOP_TASK:
                        return
                    processed.append(item)
                finally:
                    queue.task_done()

        async def producer():
            producer_started.set()
            try:
                await asyncio.sleep(60)
            finally:
                producer_stopped.set()

        await queue.put("first")
        await queue.put("second")
        consumer_task = asyncio.create_task(consumer())
        producer_task = asyncio.create_task(producer())
        await producer_started.wait()

        clean = await shutdown_task_queue(
            queue,
            consumer_task,
            producers=(producer_task,),
            timeout=1,
        )

        self.assertTrue(clean)
        self.assertEqual(processed, ["first", "second"])
        self.assertTrue(producer_stopped.is_set())
        self.assertTrue(consumer_task.done())
        await asyncio.wait_for(queue.join(), timeout=0.1)

    async def test_timeout_cancels_consumer_and_balances_queue(self):
        queue = asyncio.Queue()
        item_started = asyncio.Event()

        async def blocked_consumer():
            while True:
                item = await queue.get()
                try:
                    if item is STOP_TASK:
                        return
                    item_started.set()
                    await asyncio.Event().wait()
                finally:
                    queue.task_done()

        await queue.put("slow")
        consumer_task = asyncio.create_task(blocked_consumer())
        await item_started.wait()

        clean = await shutdown_task_queue(queue, consumer_task, timeout=0.01)

        self.assertFalse(clean)
        self.assertTrue(consumer_task.cancelled())
        await asyncio.wait_for(queue.join(), timeout=0.1)

    async def test_already_failed_consumer_does_not_leave_join_blocked(self):
        queue = asyncio.Queue()

        async def failed_consumer():
            item = await queue.get()
            try:
                raise RuntimeError(f"failed on {item}")
            finally:
                queue.task_done()

        await queue.put("current")
        await queue.put("pending")
        consumer_task = asyncio.create_task(failed_consumer())
        await asyncio.sleep(0)

        clean = await shutdown_task_queue(queue, consumer_task, timeout=0.1)

        self.assertFalse(clean)
        await asyncio.wait_for(queue.join(), timeout=0.1)


if __name__ == "__main__":
    unittest.main()

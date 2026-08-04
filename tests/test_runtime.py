import asyncio
import unittest

from cyberclaw.core.runtime import STOP_TASK, shutdown_task_queue


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

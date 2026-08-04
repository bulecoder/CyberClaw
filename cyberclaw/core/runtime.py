import asyncio
from collections.abc import Iterable
from typing import Any


STOP_TASK = object()


def _drain_pending(queue: asyncio.Queue[Any]) -> None:
    """Discard queued items while keeping Queue.join() accounting balanced."""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        else:
            queue.task_done()


async def shutdown_task_queue(
    queue: asyncio.Queue[Any],
    consumer: asyncio.Task[Any],
    producers: Iterable[asyncio.Task[Any]] = (),
    timeout: float = 10.0,
) -> bool:
    """Stop producers, drain accepted work, then stop the single consumer."""
    if timeout <= 0:
        raise ValueError("timeout 必须大于 0")

    producer_tasks = tuple(producers)
    for task in producer_tasks:
        task.cancel()
    if producer_tasks:
        await asyncio.gather(*producer_tasks, return_exceptions=True)

    if consumer.done():
        _drain_pending(queue)
        await asyncio.gather(consumer, return_exceptions=True)
        return False

    try:
        await asyncio.wait_for(queue.put(STOP_TASK), timeout=timeout)
        await asyncio.wait_for(queue.join(), timeout=timeout)
    except asyncio.TimeoutError:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        _drain_pending(queue)
        return False

    result = (await asyncio.gather(consumer, return_exceptions=True))[0]
    return not isinstance(result, BaseException)

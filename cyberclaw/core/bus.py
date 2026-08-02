import asyncio


task_queue = asyncio.Queue()    # 用户输入和到期任务共用这个队列

async def emit_task(content: str):
    await task_queue.put(content)
import os
import json
import asyncio
import calendar
from datetime import datetime, timedelta
from .config import TASKS_FILE
from .tools.builtins import tasks_lock

async def pacemaker_loop(task_queue: asyncio.Queue, check_interval: int = 10):  # 异步函数，定义一个协程函数
    """
    后台心脏起搏器协程（带并发锁和循环任务续期功能）
    """
    while True:
        await asyncio.sleep(check_interval) # 主程序传入的间隔是10s，每十秒检查一次
        
        if not os.path.exists(TASKS_FILE):
            continue
            
        now = datetime.now()
        pending_tasks = []      # 本轮结束后仍需要保存在 task.json 的任务
        triggered_tasks = []    # 本轮结束需要送给 Agent 的任务

        #线程锁，防止多线程/多协程同时读写任务文件导致的竞争条件和数据损坏
        with tasks_lock:
            try:
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        continue
                    tasks = json.loads(content)
            except Exception:
                continue
                
            if not tasks:
                continue

            for t in tasks:
                try:
                    #这个target_dt 是任务的目标触发时间
                    target_dt = datetime.strptime(t["target_time"], "%Y-%m-%d %H:%M:%S")
                    if now >= target_dt:    # 到期的任务放入 triggered_tasks

                        triggered_tasks.append(t) #记录为“需要触发”，如果是单词任务则只加入 triggered_tasks

                        #如果是循环任务就把次数减1，次数耗尽就不再触发
                        repeat_freq = t.get("repeat")   # 支持 hourly、daily、weekly、monthly
                        if repeat_freq:
                            repeat_count = t.get("repeat_count")
                            

                            if repeat_count is not None:
                                if repeat_count <= 1:
                                    continue
                                else:
                                    t["repeat_count"] = repeat_count - 1    # 有限重复次数，则重复次数减一


                            if repeat_freq == "hourly":
                                next_dt = target_dt + timedelta(hours=1)
                            elif repeat_freq == "daily":
                                next_dt = target_dt + timedelta(days=1)
                            elif repeat_freq == "weekly":
                                next_dt = target_dt + timedelta(days=7)
                            elif repeat_freq == "monthly":  # 下一个自然月
                                month = target_dt.month + 1
                                year = target_dt.year
                                if month > 12:  # 下一年
                                    month = 1
                                    year += 1
                                last_day = calendar.monthrange(year, month)[1]  # 取下一个月的最后一天
                                day = min(target_dt.day, last_day)
                                next_dt = target_dt.replace(year=year, month=month, day=day)    # bug：next_dt基于旧 target_time而不是当前时间，如果一个任务已经过期5个小时，重启后可能每隔十秒补触发一次，知道时间追上现在
                            else:
                                continue
                                
                            t["target_time"] = next_dt.strftime("%Y-%m-%d %H:%M:%S")    # 对于重复任务，计算下一次事件后，把任务重新放回 pending_tasks
                            pending_tasks.append(t)
                    else:

                        pending_tasks.append(t) #还未到触发时间的任务继续保留在待办列表里
                except Exception:

                    pass

            #将还没到触发时间的任务和续期后的循环任务写回文件，覆盖原有内容
            if triggered_tasks:
                try:
                    with open(TASKS_FILE, "w", encoding="utf-8") as f:
                        json.dump(pending_tasks, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        for t in triggered_tasks:
            system_msg = (  # 虽然这里变量名为 system_msg，但是队列里面消费的时候都会包装为HumanMessage而并非SystemMessage
                f"【系统内部心跳触发】\n"
                f"你设定的定时任务已到期，请立即主动提醒用户或执行动作。\n"
                f"任务内容：{t['description']}"
            )
            await task_queue.put(system_msg)
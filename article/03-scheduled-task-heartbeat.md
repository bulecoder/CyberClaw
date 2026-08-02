# 第 3 课｜定时任务怎样从 JSON 记录变成一次 Agent 提醒

上一课看到了四个任务 Tool：

```text
schedule_task
list_scheduled_tasks
delete_scheduled_task
modify_scheduled_task
```

这些工具只负责管理任务记录。真正让任务“到时间后重新进入 Agent”的，是另一条后台链路：

```text
任务 Tool
→ workspace/tasks.json
→ pacemaker_loop 定期扫描
→ asyncio.Queue
→ agent_worker
→ LangGraph Agent
→ 模型生成提醒或执行动作
```

这一课要回答：

> 当用户说“稍后提醒我”以后，这条任务记录保存在哪里、由谁检查时间、怎样续期，又怎样重新进入 Agent？

本课对应：

- `cyberclaw/core/tools/builtins.py` 第 79～272 行
- `cyberclaw/core/heartbeat.py`
- `cyberclaw/core/bus.py`
- `entry/main.py` 第 149～165、234～252 行
- `tests/test_builtins.py` 任务测试
- `tests/test_heartbeat.py`

## 先纠正一个容易产生的误解

CyberClaw 的 Heartbeat 不是：

- Windows 计划任务；
- 常驻系统服务；
- 独立进程；
- 关闭终端后仍然工作的闹钟；
- 能够唤醒关机或休眠电脑的调度器。

实际启动代码是：

```python
heartbeat_worker = asyncio.create_task(
    pacemaker_loop(task_queue=task_queue, check_interval=10)
)
```

它只是当前 CyberClaw Python 进程中的一个后台协程。

因此：

```text
CyberClaw 正在运行
→ Heartbeat 每隔约 10 秒扫描一次任务

CyberClaw 已关闭
→ tasks.json 仍然存在
→ 但没有任何代码检查它

CyberClaw 再次启动
→ Heartbeat 恢复扫描
→ 已经过期的任务可能被延迟触发
```

这套实现具有文件持久化，但不具有独立后台服务能力。

## 一条任务记录长什么样

`schedule_task()` 创建的任务结构是：

```python
new_task = {
    "id": str(uuid.uuid4())[:8],
    "target_time": target_time,
    "description": description,
    "repeat": repeat,
    "repeat_count": repeat_count
}
```

保存后的 JSON 大致是：

```json
[
  {
    "id": "a1b2c3d4",
    "target_time": "2026-07-31 20:30:00",
    "description": "提醒我休息",
    "repeat": null,
    "repeat_count": null
  }
]
```

各字段含义：

| 字段 | 含义 |
|---|---|
| `id` | 任务标识，UUID 字符串的前 8 位 |
| `target_time` | 下一次目标时间，本地无时区字符串 |
| `description` | 到期后交给 Agent 的任务内容 |
| `repeat` | `hourly`、`daily`、`weekly`、`monthly` 或空 |
| `repeat_count` | 剩余触发次数；空值表示无限重复 |

这个结构没有显式的：

```text
status
last_triggered_at
created_at
updated_at
timezone
delivery_state
```

任务状态主要通过“是否还存在于 `tasks.json`”以及 `target_time` 来隐式表达。

## 第一部分：任务 CRUD 怎样维护 JSON 文件

打开：

```text
cyberclaw/core/tools/builtins.py
```

### 1. 创建任务：`schedule_task`

位置：第 79～145 行。

第一步，严格解析目标时间：

```python
target_dt = datetime.strptime(
    target_time,
    "%Y-%m-%d %H:%M:%S"
)
```

格式错误时返回错误字符串。格式正确后还会检查：

```python
if target_dt <= now:
    return "设定失败：target_time 必须晚于当前时间……"
```

因此创建工具只接受调用当时的未来时间。

第二步，进入共享锁：

```python
with tasks_lock:
```

锁内完成：

```text
读取旧 tasks.json
→ JSON 解析
→ 追加新任务
→ 覆盖写回整个列表
```

如果文件不存在，就从空列表开始。如果文件存在但内容损坏，函数返回读取失败，不会直接覆盖旧文件。

第三步，生成任务 ID：

```python
"id": str(uuid.uuid4())[:8]
```

完整 UUID 的冲突概率很低，但这里只保留前 8 位，也没有在写入前检查重复 ID。对个人项目通常足够，生产系统不应依靠截断 ID 加无冲突假设。

### `repeat` 和 `repeat_count` 的创建边界

docstring 说 `repeat` 支持：

```text
hourly
daily
weekly
```

但 `schedule_task()` 没有实际验证白名单；Heartbeat 中还实现了没有写进该说明的 `monthly`。

`repeat_count` 也没有检查：

- 是否大于 0；
- `repeat` 为空时是否应该禁止填写；
- 是否与重复频率同时存在；
- 是否超过合理上限。

所以当前创建工具完成了时间格式检查，但没有建立完整任务 Schema 约束。

### 2. 查询任务：`list_scheduled_tasks`

位置：第 148～175 行。

它在锁内读取文件，并按：

```python
tasks.sort(key=lambda x: x["target_time"])
```

排序。

`YYYY-MM-DD HH:MM:SS` 是从大单位到小单位的固定宽度格式，所以格式正确时，字符串排序与时间先后顺序一致。

当前输出只展示：

```text
ID
target_time
description
```

没有展示 `repeat` 和 `repeat_count`，因此用户从查询结果中看不到任务是否循环以及还剩几次。

### 3. 删除任务：`delete_scheduled_task`

位置：第 178～215 行。

核心逻辑是：

```python
new_tasks = [t for t in tasks if t["id"] != task_id]
```

如果新旧列表长度相同，说明没有找到该 ID；找到后则覆盖写回。

docstring 中的“模糊删除必须确认”是给模型的行为规则。函数内部只接收具体 `task_id`，不会校验确认过程。这个安全边界已在第 2 课讨论。

### 4. 修改任务：`modify_scheduled_task`

位置：第 218～272 行。

它遍历任务列表，根据 ID 找到目标：

```python
for t in tasks:
    if t["id"] == task_id:
```

可以修改：

```text
target_time
description
```

如果提供新时间，会再次检查格式和是否晚于当前时间。

它不能修改：

```text
repeat
repeat_count
```

因此当前 API 不能把单次任务改成重复任务，也不能修改剩余重复次数。

## 第二部分：`tasks_lock` 保护了什么

锁定义在：

```python
tasks_lock = threading.Lock()
```

创建、查询、删除、修改以及 Heartbeat 扫描都使用同一个锁对象。

它保护的是同一进程内的“读—改—写”临界区：

```text
协程或线程 A 读取旧列表
→ 修改列表
→ 写回文件

在这期间，B 必须等待
```

如果没有锁，可能发生：

```text
A 读取 [task1]
B 也读取 [task1]
A 添加 task2，写入 [task1, task2]
B 添加 task3，写入 [task1, task3]

结果：task2 丢失
```

但当前锁有三个边界。

### 1. 只在当前进程中有效

如果同时启动两个 CyberClaw 进程，每个进程拥有自己的 `threading.Lock`，它们仍可能同时写同一个 `tasks.json`。

### 2. 它不是文件锁

其他脚本或编辑器修改文件时，不会自动遵守这个 Python 锁。

### 3. 同步锁和文件 I/O 可能阻塞事件循环

Heartbeat 是异步协程，却在事件循环线程里执行：

```python
with tasks_lock:
    open(...)
    json.loads(...)
    json.dump(...)
```

任务文件很小时影响不明显；锁等待或文件 I/O 变慢时，会暂时阻塞同一事件循环中的 Spinner、用户输入和其他协程。

## 第三部分：Heartbeat 的扫描循环

打开：

```text
cyberclaw/core/heartbeat.py
```

入口是：

```python
async def pacemaker_loop(
    task_queue: asyncio.Queue,
    check_interval: int = 10
):
```

它是一个永不主动结束的循环：

```python
while True:
    await asyncio.sleep(check_interval)
```

注意先 `sleep`，再扫描。因此刚启动后不会立即扫描，第一次扫描大约发生在 10 秒后。

运行期间的触发延迟通常是：

```text
0～check_interval 秒
+ 排队与 Agent 执行时间
```

它不是精确到秒的实时调度器。

### 读取任务快照

每轮先检查文件：

```python
if not os.path.exists(TASKS_FILE):
    continue
```

然后记录当前时间，并准备两个列表：

```python
now = datetime.now()
pending_tasks = []
triggered_tasks = []
```

这两个列表非常重要：

```text
pending_tasks
→ 本轮结束后仍需要保存在 tasks.json 的任务

triggered_tasks
→ 本轮需要送给 Agent 的任务
```

读取和解析文件发生在 `tasks_lock` 内。文件为空、JSON 解析失败或列表为空时，本轮直接跳过。

当前代码对异常通常使用：

```python
except Exception:
    continue
```

因此调度器不会因一条坏数据崩溃，但也不会记录错误，用户很难知道某个提醒为什么没有触发。

## 第四部分：隐式任务状态机

Heartbeat 没有声明 `Enum` 或 `StateGraph`，但循环中的分支构成了一套隐式状态机。

```text
任务仍在文件且 target_time > now
→ WAITING

任务到期
→ TRIGGERED

重复任务计算出下一次时间并重新写回
→ RESCHEDULED

单次任务触发后不再写回
→ COMPLETED

字段解析失败
→ INVALID
```

对每个任务，先解析：

```python
target_dt = datetime.strptime(
    t["target_time"],
    "%Y-%m-%d %H:%M:%S"
)
```

### 未到期任务

```python
if now < target_dt:
    pending_tasks.append(t)
```

它原样保留在文件中，等待后续扫描。

### 已到期任务

代码先执行：

```python
triggered_tasks.append(t)
```

这意味着任务一旦被判断到期，本轮就会尝试通知 Agent。

随后再判断它是否需要续期。

## 第五部分：单次任务与重复任务

### 单次任务

任务结构：

```json
{
  "repeat": null,
  "repeat_count": null
}
```

到期后：

```text
加入 triggered_tasks
→ 不加入 pending_tasks
→ 从 tasks.json 中移除
→ 消息进入队列
```

### 无限重复任务

例如：

```json
{
  "repeat": "daily",
  "repeat_count": null
}
```

`repeat_count is None` 表示不递减。Heartbeat 计算下一次时间后，把任务重新放回 `pending_tasks`。

### 有限重复任务

例如：

```json
{
  "repeat": "daily",
  "repeat_count": 3
}
```

第一次触发：

```text
先加入 triggered_tasks
3 > 1
→ repeat_count 改为 2
→ 下一次时间写回
```

第二次触发：

```text
先触发
2 > 1
→ repeat_count 改为 1
→ 再次续期
```

第三次触发：

```text
先触发
1 <= 1
→ 不再加入 pending_tasks
→ 任务完成并移除
```

因此 `repeat_count=3` 表示总共触发 3 次，而不是“首次之外再重复 3 次”。

### 下一次时间怎样计算

```python
hourly  → target_dt + 1 小时
daily   → target_dt + 1 天
weekly  → target_dt + 7 天
monthly → 下一个自然月
```

月度任务会取下个月的最后一天，避免 1 月 31 日直接替换成不存在的 2 月 31 日：

```python
last_day = calendar.monthrange(year, month)[1]
day = min(target_dt.day, last_day)
```

但 `monthly` 没写进 `schedule_task` 的 docstring，也没有对应测试，属于实现与公开说明不一致。

### 延迟启动时可能连续补触发

下一次时间基于旧的 `target_dt` 计算，而不是基于当前 `now`：

```python
next_dt = target_dt + timedelta(...)
```

假设一个每小时任务的目标时间已经过期 5 小时。第一次扫描只加 1 小时，得到的时间仍在过去；下一轮扫描又会触发一次。

因此恢复运行后可能出现：

```text
每隔约 10 秒补触发一次
→ 直到 target_time 追上当前时间
```

这是“补齐历史触发”还是“只提醒一次并跳到未来”，当前代码没有显式定义，实际表现偏向前者。

### 非法重复频率会怎样

任务一旦到期，会先执行：

```python
triggered_tasks.append(t)
```

之后才判断 `repeat`。如果频率既不是 `hourly`、`daily`、`weekly`，也不是 `monthly`：

```python
else:
    continue
```

该任务已经进入 `triggered_tasks`，所以仍会提醒一次；但它没有加入 `pending_tasks`，写回后会从文件中移除。

类似地，`repeat_count` 为 0 或负数时，也会先触发一次再被移除。创建工具的成功提示却把假值 0 显示成“无限”，因此输入校验、展示语义和 Heartbeat 行为并不完全一致。

### 格式损坏的单条任务会怎样

每条任务外层使用：

```python
except Exception:
    pass
```

格式损坏的任务不会加入 `pending_tasks`，但文件只在 `triggered_tasks` 非空时写回：

```text
本轮没有任何正常任务触发
→ 不写回文件
→ 损坏任务仍留在原 JSON 中

本轮有其他正常任务触发
→ 用 pending_tasks 覆盖写回
→ 损坏任务因为没进入 pending_tasks 而被顺带移除
```

同一条坏数据的最终状态取决于同轮其他任务，属于隐式且不稳定的错误处理。

## 第六部分：任务文件什么时候写回

扫描完成后，只有发现到期任务时才写回：

```python
if triggered_tasks:
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(pending_tasks, f, ensure_ascii=False, indent=2)
```

这样可以：

- 移除已经完成的单次任务；
- 保存重复任务的新时间和剩余次数；
- 保留未到期任务。

但这不是原子事务。

### 写回失败可能导致重复触发

如果任务已经加入 `triggered_tasks`，但覆盖写文件失败：

```text
旧任务仍留在文件中
→ 本轮消息仍然会进入队列
→ 下一轮再次扫描到同一任务
→ 可能重复提醒
```

因为写入异常被静默忽略，用户也看不到持久化失败。

### 写回成功后进程崩溃可能丢失提醒

代码顺序是：

```text
先从 JSON 移除或续期任务
→ 释放锁
→ 再向 asyncio.Queue 写入提醒
```

如果写回成功后、`queue.put()` 之前进程崩溃：

```text
单次任务已经从文件消失
→ 提醒尚未进入队列
→ 这次提醒可能永久丢失
```

文件更新和消息投递不是同一个事务，因此当前实现不能保证 exactly-once。

## 第七部分：消息怎样进入 Agent

Heartbeat 在锁外构造字符串：

```python
system_msg = (
    "【系统内部心跳触发】\n"
    "你设定的定时任务已到期，请立即主动提醒用户或执行动作。\n"
    f"任务内容：{t['description']}"
)
```

然后：

```python
await task_queue.put(system_msg)
```

把消息放到哪里，需要继续看：

```text
cyberclaw/core/bus.py
```

该文件只有：

```python
task_queue = asyncio.Queue()

async def emit_task(content: str):
    await task_queue.put(content)
```

`task_queue` 是进程内共享的异步 FIFO 队列。`emit_task()` 是一个辅助入口，但当前仓库没有调用它；主程序和 Heartbeat 直接使用队列对象。

### 用户输入和 Heartbeat 共用一个队列

用户输入路径：

```python
await task_queue.put(user_input)
```

Heartbeat 路径：

```python
await task_queue.put(system_msg)
```

消费方都是：

```python
user_input = await task_queue.get()
```

因此队列更准确的名字可能是 `agent_input_queue`，因为里面不只有“任务”，还包含所有用户输入和退出命令。

### 单消费者保证串行处理

`agent_worker()` 是唯一消费者：

```python
while True:
    user_input = await task_queue.get()
```

所以：

- 同一时刻只处理一个 Agent 请求；
- Heartbeat 不会直接并发调用同一张图；
- 用户输入和到期提醒按入队顺序排队；
- 前一个 Agent 调用很慢时，提醒也会延迟。

当前队列没有优先级和长度上限。提醒不会自动插到普通消息前面。

### 名字叫 `system_msg`，实际却是 `HumanMessage`

无论队列中的字符串来自用户还是 Heartbeat，消费端都执行：

```python
inputs = {
    "messages": [HumanMessage(content=user_input)]
}
```

因此 Heartbeat 的内部提醒在 LangGraph 状态里属于：

```text
HumanMessage
```

而不是：

```text
SystemMessage
```

变量名 `system_msg` 只表示文本用途，不代表 LangChain 消息角色。模型会把它当作一条用户角色消息处理。

## 第八部分：运行时怎样启动和停止 Heartbeat

在 `entry/main.py` 中，三个主要协作任务是：

```python
worker = asyncio.create_task(agent_worker())
heartbeat_worker = asyncio.create_task(pacemaker_loop(...))
await user_input_loop()
```

职责分别是：

| 协程 | 职责 |
|---|---|
| `user_input_loop` | 从终端读取用户输入并放入队列 |
| `agent_worker` | 从队列取消息并调用 LangGraph |
| `pacemaker_loop` | 扫描任务文件并把到期任务放入队列 |

退出时：

```python
await task_queue.join()
worker.cancel()
heartbeat_worker.cancel()
```

`task_queue.join()` 等待所有已经入队的消息执行过对应的 `task_done()`。随后两个后台协程被取消。

任务记录仍保存在 `tasks.json`，但进程退出后没有 Heartbeat 继续扫描。

## 第九部分：这套系统的真实持久化语义

需要区分三种数据：

| 数据 | 保存位置 | 重启后是否保留 |
|---|---|---|
| 任务定义 | `workspace/tasks.json` | 保留 |
| 尚未消费的内存队列消息 | `asyncio.Queue` | 不保留 |
| Agent 对话状态 | `workspace/state.sqlite3` | 保留 |

因此：

- 还没到时间的任务能够跨重启保留；
- 已经写入队列但进程突然退出的消息会丢失；
- Heartbeat 提醒进入 Agent 后，会作为对话消息进入同一 `thread_id` 的 checkpoint；
- 文件持久化、队列和 LangGraph checkpoint 是三个相互独立的状态系统。

## 第十部分：测试实际证明了什么

本课需要同时看：

```text
tests/test_builtins.py
tests/test_heartbeat.py
```

### `test_builtins.py` 的任务测试

这些测试直接调用 Tool，实际覆盖了：

- 创建单次任务并写入 JSON；
- 拒绝错误时间格式；
- 查询空列表和非空列表；
- 删除存在或不存在的任务；
- 修改时间与描述；
- 拒绝错误修改时间；
- 找不到任务时返回错误。

它们没有覆盖：

- `repeat` 白名单；
- `repeat_count` 的边界；
- `monthly`；
- 多进程并发；
- 文件写入中断；
- Heartbeat 到期触发。

### `test_heartbeat.py` 大部分没有运行生产循环

虽然测试名称包括：

```text
test_task_due_and_triggered
test_repeating_task_daily
test_multiple_tasks_mixed
```

但这些测试主要只是：

```text
把数据写入临时 JSON
→ 再读出来
→ 断言刚写的字段仍然存在
```

它们没有真正调用 `pacemaker_loop()` 的扫描逻辑。

其他几个明显例子：

```python
self.assertTrue(True)
```

只是占位断言。

```python
valid_freqs = ["hourly", "daily", "weekly"]
self.assertIn(freq, ["hourly", "daily", "weekly"])
```

验证的是测试自己写的列表，不是生产代码。

重复次数测试重新手写了一份递减逻辑，也没有执行 Heartbeat 中的实现。

因此当前测试无法证明：

- 到期任务真的进入队列；
- 单次任务触发后真的删除；
- 重复任务真的续期；
- `repeat_count` 与生产代码一致；
- malformed JSON 和异常写入的行为；
- Agent 真的消费 Heartbeat 消息。

### Windows 临时文件失败的原因

`test_no_tasks_file` 的 `setUp()` 创建并保持打开：

```python
tempfile.NamedTemporaryFile(...)
```

测试随后在文件句柄仍打开时执行：

```python
os.unlink(self.temp_file.name)
```

Unix 通常允许删除仍被进程打开的文件名；Windows 通常不允许删除一个没有共享删除权限的打开文件，因此可能报 `PermissionError`。

这属于测试夹具的跨平台文件句柄问题，不代表 Heartbeat 的业务逻辑失败。

修正思路是先关闭句柄，再删除，或使用 `TemporaryDirectory` 自己创建普通文件并控制生命周期。

### 测试中的月末日期问题

部分任务测试用下面的方式表示“明天”：

```python
future_time = future_time.replace(day=future_time.day + 1)
```

如果当天是一个月的最后一天，例如 7 月 31 日，这会尝试构造 7 月 32 日并抛出 `ValueError`。

正确的跨月日期运算应使用：

```python
future_time = future_time + timedelta(days=1)
```

因此这些测试还依赖运行当天的日期和时间，不是完全稳定的测试夹具。

## 当前实现值得肯定的地方

1. 任务记录使用简单 JSON，容易观察和学习；
2. 创建、修改和 Heartbeat 共享同一个进程内锁；
3. 单次和重复任务的基本状态转移清楚；
4. 支持小时、天、周和自然月续期；
5. Heartbeat 在锁外执行 `queue.put()`，不会持锁等待队列；
6. 用户输入与定时触发统一进入 Agent 入口；
7. 单消费者避免同一进程内多个 Agent 请求并发修改同一会话；
8. 进程重启后能从 JSON 恢复未完成任务。

## 当前实现的主要边界

1. Heartbeat 不是独立服务，CyberClaw 关闭后不会准时提醒；
2. 时间是本地 naive datetime，没有时区和夏令时策略；
3. 首次扫描要先等待一个 interval，触发不是精确实时；
4. `threading.Lock` 不能解决多进程和外部程序并发；
5. 同步锁和文件 I/O 会阻塞事件循环；
6. JSON 覆盖写不是原子事务，崩溃可能损坏或丢失状态；
7. 文件更新与队列投递不是同一事务，可能重复或丢失提醒；
8. 异常被静默吞掉，没有日志和告警；
9. `repeat`、`repeat_count` 缺少强制校验；
10. `monthly` 的实现、说明和测试不一致；
11. 恢复过期重复任务时可能短时间连续补触发；
12. 队列没有持久化、优先级、容量限制和消息状态；
13. Heartbeat 文本最终被包装成 `HumanMessage`，角色命名容易误导；
14. 定时任务描述可以要求 Agent 到期后执行动作，但没有保存审批凭证或重新进行确定性风险审批；
15. 当前 Heartbeat 测试大多没有执行真实生产逻辑；
16. 个别测试依赖 Windows 文件语义、当天日期和当前时刻。

## 本课源码阅读顺序

按数据流阅读，不要先从 `heartbeat.py` 逐行硬啃：

1. `config.py` 第 19 行：确认 `TASKS_FILE` 的真实路径；
2. `builtins.py` 第 79～145 行：任务如何创建；
3. `builtins.py` 第 148～272 行：查询、删除、修改；
4. `heartbeat.py` 第 9～35 行：循环、休眠、读取；
5. `heartbeat.py` 第 37～83 行：任务状态分支；
6. `heartbeat.py` 第 85～99 行：写回和入队；
7. `bus.py`：共享队列；
8. `entry/main.py` 第 149～165 行：队列怎样变成 `HumanMessage`；
9. `entry/main.py` 第 246～252 行：三个协程如何共同运行；
10. 最后对照两个测试文件，判断每个测试真正执行了什么。

读完后应能画出：

```text
schedule_task
→ tasks.json
→ pacemaker_loop
→ triggered_tasks / pending_tasks
→ task_queue
→ agent_worker
→ HumanMessage
→ app.astream
```

## 本课 VS Code 调试实验

### 实验一：观察任务创建与 JSON 持久化

设置断点：

- `builtins.py` 第 104 行：解析目标时间；
- `builtins.py` 第 127 行：创建任务字典；
- `builtins.py` 第 137 行：写入 JSON。

使用 VS Code 调试 `entry/main.py`。先让 CyberClaw 调用 `get_current_time`，再输入：

```text
请设置一个大约一分钟后触发的单次提醒：提醒我检查 Heartbeat
```

在断点处观察：

```text
target_time
target_dt
now
new_task
tasks
```

继续运行后，在 VS Code 中打开：

```text
workspace/tasks.json
```

确认任务的五个字段，并思考：此时只是写入 JSON，提醒还没有执行。

### 实验二：观察 Heartbeat 触发完整链路

任务创建后，取消创建阶段的断点，设置：

- `heartbeat.py` 第 40 行：解析任务时间；
- `heartbeat.py` 第 41 行：判断是否到期；
- `heartbeat.py` 第 43 行：加入触发列表；
- `heartbeat.py` 第 88 行：覆盖写回任务文件；
- `heartbeat.py` 第 99 行：放入队列；
- `entry/main.py` 第 151 行：从队列取消息；
- `entry/main.py` 第 163 行：包装 `HumanMessage`。

按 `F5` 继续运行。每次扫描会停在第 40 行；未到期时继续运行即可。

到期后依次观察：

```text
now >= target_dt
triggered_tasks 中出现任务
pending_tasks 中不再保留单次任务
tasks.json 被写成不含该任务的列表
system_msg 被放进 task_queue
agent_worker 取出内部心跳文本
内部文本被包装成 HumanMessage
```

这一实验是本课最重要的证据。

### 实验三：观察有限重复任务续期

创建一个重复任务后，可以仅为学习目的编辑：

```text
workspace/tasks.json
```

把任务设置为：

```json
{
  "target_time": "一个已经过去的时间",
  "repeat": "daily",
  "repeat_count": 3
}
```

保留第 43、51、55、61、76、88 行断点。

观察一次触发后：

```text
repeat_count: 3 → 2
target_time: 原时间 → 原时间加一天
任务仍在 pending_tasks
任务重新写入 JSON
```

实验结束后，通过任务 ID 删除测试任务，避免它继续触发。

### 实验四：审查测试是否真的调用生产代码

打开 VS Code Testing 面板，逐个点开 `test_heartbeat.py` 的测试。

对每个测试问：

```text
它有没有调用 pacemaker_loop？
有没有让循环执行至少一次扫描？
有没有检查 task_queue.put？
有没有检查写回后的 tasks.json？
```

特别对照：

```text
test_task_due_and_triggered
test_task_queue_put_called
```

你会看到测试名称与真实验证范围之间的差距。

## 写 notes 前的自测题

1. `schedule_task()` 完成后，任务已经在后台执行了吗？
2. Heartbeat 是进程、线程，还是协程？
3. CyberClaw 关闭后，为什么任务存在却不会准时提醒？
4. `pending_tasks` 和 `triggered_tasks` 分别保存什么？
5. 单次任务触发后为什么会从 JSON 中消失？
6. `repeat_count=3` 总共触发几次？每次怎样变化？
7. 为什么 `repeat_count=None` 表示无限重复？
8. 下一次时间基于 `target_dt` 而不是 `now` 有什么影响？
9. `tasks_lock` 能解决两个 CyberClaw 进程同时写文件吗？
10. 为什么异步 Heartbeat 中使用同步锁和文件 I/O 仍可能卡住事件循环？
11. 写回成功但入队前崩溃，会发生什么？
12. 写回失败但继续入队，会发生什么？
13. Heartbeat 变量叫 `system_msg`，为什么最终却是 `HumanMessage`？
14. 用户输入和到期任务为什么不会同时调用 Agent？
15. `task_queue` 中的消息能跨进程重启保留吗？
16. `tasks.json`、`task_queue` 和 SQLite checkpoint 分别保存什么？
17. 当前 `test_heartbeat.py` 为什么不能证明 Heartbeat 正确？
18. Windows 上删除仍打开的 `NamedTemporaryFile` 为什么可能失败？
19. `monthly` 在实现、docstring 和测试之间有什么不一致？
20. 如果要把它改成可靠调度服务，最先应补哪些状态和保证？

## 本课完成标准

完成本课后，应该能不看文章讲清：

```text
Tool 创建任务
→ JSON 保存待办
→ Heartbeat 定期轮询
→ 到期任务触发或续期
→ 消息进入进程内队列
→ 单个 Agent worker 串行消费
→ 以 HumanMessage 进入 LangGraph
```

还应明确三条边界：

- JSON 持久化不等于独立后台调度；
- 进程内锁不等于跨进程一致性；
- 测试名称不等于测试真的执行了该行为。

达到这些标准并完成调试实验后，再编写：

```text
notes/03-scheduled-task-heartbeat.md
```

下一课会继续研究 office 文件与 Shell 工具，并判断项目所谓“沙盒”到底在哪些层面成立。

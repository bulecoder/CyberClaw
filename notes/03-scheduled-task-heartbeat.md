# 03｜定时任务状态机与 Heartbeat 调度

> 对应源码：`cyberclaw/core/tools/builtins.py`、`cyberclaw/core/heartbeat.py`、`cyberclaw/core/bus.py`  
> 运行入口：`entry/main.py`  
> 对应测试：`tests/test_builtins.py`、`tests/test_heartbeat.py`

## 一、本课核心内容

### 1. 定时任务的整体链路

CyberClaw 的定时任务由 JSON 文件、轮询协程和进程内消息队列共同完成：

```text
模型调用 schedule_task
→ 任务写入 workspace/tasks.json
→ pacemaker_loop 每 10 秒扫描
→ 区分待处理与到期任务
→ 单次任务移除，重复任务续期
→ 到期消息写入 task_queue
→ agent_worker 取出消息
→ 包装成 HumanMessage
→ 重新进入 LangGraph Agent
```

Heartbeat 不是 Windows 计划任务、独立进程或系统服务，而是当前 CyberClaw 进程中的一个 `asyncio` 后台协程。

因此：

```text
CyberClaw 运行
→ Heartbeat 持续扫描

CyberClaw 关闭
→ tasks.json 仍存在
→ 但不会准时触发

CyberClaw 重启
→ 恢复扫描过期任务
```

这套实现具有任务定义持久化，但不具有独立后台调度能力。

### 2. 任务数据与隐式状态

任务保存在：

```text
workspace/tasks.json
```

基本结构是：

```python
{
    "id": "a1b2c3d4",
    "target_time": "2026-07-31 20:30:00",
    "description": "提醒我休息",
    "repeat": None,
    "repeat_count": None
}
```

字段含义：

| 字段 | 含义 |
|---|---|
| `id` | 截取前 8 位的 UUID |
| `target_time` | 下一次目标时间 |
| `description` | 到期后交给 Agent 的内容 |
| `repeat` | 重复频率 |
| `repeat_count` | 剩余总触发次数，空值表示无限 |

任务没有显式 `status` 字段，而是通过时间和是否仍在 JSON 中表示状态：

```text
target_time > now 且仍在文件
→ WAITING

target_time <= now
→ TRIGGERED

重复任务更新下一次时间并写回
→ RESCHEDULED

单次任务触发后不再写回
→ COMPLETED
```

这是一套写在条件分支里的隐式状态机，不是 LangGraph 状态图。

### 3. 任务 CRUD 与共享锁

任务 Tool 包括：

```text
schedule_task
list_scheduled_tasks
delete_scheduled_task
modify_scheduled_task
```

`schedule_task()` 的主要过程：

```text
解析时间格式
→ 检查必须晚于当前时间
→ 加锁读取旧 JSON
→ 创建任务记录
→ 追加并覆盖写回
```

`list_scheduled_tasks()` 按 `target_time` 排序，但输出没有展示 `repeat` 和 `repeat_count`。

`delete_scheduled_task()` 根据 ID 过滤任务列表；`modify_scheduled_task()` 只能修改时间和描述，不能修改重复频率和剩余次数。

所有 CRUD 和 Heartbeat 共用：

```python
tasks_lock = threading.Lock()
```

它保护同一进程内的“读取—修改—写回”，避免两个操作读取同一个旧版本后互相覆盖。

但它不是：

- 跨进程锁；
- 操作系统文件锁；
- 数据库事务；
- 对外部编辑器的约束。

同步锁和同步文件 I/O 还会暂时阻塞所在的异步事件循环。

### 4. Heartbeat 扫描与任务分组

核心循环：

```python
async def pacemaker_loop(task_queue, check_interval=10):
    while True:
        await asyncio.sleep(check_interval)
```

它先休眠再扫描，所以首次检查约在启动 10 秒后。触发延迟由以下部分组成：

```text
0～10 秒轮询延迟
+ 队列等待
+ 前一个 Agent 请求的执行时间
```

每轮准备：

```python
pending_tasks = []
triggered_tasks = []
```

分组规则：

```text
未到期
→ 加入 pending_tasks

已到期
→ 先加入 triggered_tasks
→ 再判断是否需要续期
```

扫描完成后，如果存在触发任务，就用 `pending_tasks` 覆盖写回原文件。

文件为空、JSON 解析失败或单条任务格式错误时，代码通常静默跳过，没有日志和用户告警。

### 5. 单次任务与重复任务

单次任务：

```python
"repeat": None
```

到期后只进入 `triggered_tasks`，不再进入 `pending_tasks`，所以触发后从 JSON 中移除。

无限重复任务：

```python
"repeat": "daily",
"repeat_count": None
```

每次触发后计算下一次时间并重新写回。

有限重复任务：

```python
"repeat": "daily",
"repeat_count": 3
```

变化过程：

```text
第一次触发：3 → 2，续期
第二次触发：2 → 1，续期
第三次触发：1，不再续期并移除
```

所以 `repeat_count=3` 表示总共触发三次。

支持的续期方式：

```text
hourly  → 加 1 小时
daily   → 加 1 天
weekly  → 加 7 天
monthly → 下一个自然月
```

`monthly` 已在 Heartbeat 中实现，但没有写进 `schedule_task` 的 docstring，也没有相应测试。

下一次时间基于旧 `target_time` 而不是当前时间。如果系统离线很久，重启后某些重复任务的新时间仍处于过去，可能每隔约 10 秒连续补触发，直到追上当前时间。

### 6. 异常任务的实际行为

到期任务先进入：

```python
triggered_tasks
```

之后才校验重复频率。因此未知的 `repeat` 会：

```text
提醒一次
→ 不续期
→ 从文件中移除
```

`repeat_count` 为 0 或负数时也会触发一次后移除，但创建工具的提示可能把 0 显示成“无限”，存在语义不一致。

格式损坏的单条任务被 `except Exception: pass` 跳过：

```text
本轮无其他正常任务触发
→ 文件不写回
→ 坏任务仍保留

本轮有其他任务触发
→ pending_tasks 覆盖写回
→ 坏任务被顺带移除
```

它的结果取决于同轮其他任务，错误处理不稳定且不可观察。

### 7. 队列怎样把任务交给 Agent

`bus.py` 定义：

```python
task_queue = asyncio.Queue()
```

用户输入和 Heartbeat 消息都进入同一个 FIFO 队列：

```text
user_input_loop → task_queue
pacemaker_loop  → task_queue
```

唯一消费者是：

```python
user_input = await task_queue.get()
```

单个 `agent_worker` 串行处理消息，所以：

- 用户消息和提醒不会同时调用 Agent；
- 早入队的消息先处理；
- Agent 忙碌时提醒会排队；
- 队列没有优先级和容量限制。

Heartbeat 构造的变量名虽然是 `system_msg`，但消费端统一执行：

```python
HumanMessage(content=user_input)
```

所以定时触发在 LangGraph 中是 `HumanMessage`，而不是真正的 `SystemMessage`。

`bus.emit_task()` 是一个队列写入辅助函数，但当前仓库没有调用它。

### 8. 三种状态与可靠性边界

项目中存在三套独立状态：

| 状态 | 保存位置 | 重启后保留 |
|---|---|---|
| 任务定义 | `tasks.json` | 是 |
| 尚未消费的输入 | 内存 `asyncio.Queue` | 否 |
| Agent 对话历史 | SQLite checkpoint | 是 |

文件更新和队列投递不是同一事务：

```text
JSON 写回失败，但消息继续入队
→ 原任务仍在文件
→ 下轮可能重复提醒

JSON 写回成功，入队前进程崩溃
→ 单次任务已经消失
→ 提醒可能永久丢失
```

因此当前系统不能保证 exactly-once。

高风险任务还有额外问题：任务只保存自然语言描述，没有保存审批凭证、调用参数和风险等级。到期后 Agent 可能根据描述重新选择工具，但执行层没有确定性地验证用户当初批准了什么。

### 9. 当前测试的真实覆盖

`test_builtins.py` 的任务测试实际调用了 Tool，覆盖：

- 创建、查询、删除和修改；
- 时间格式错误；
- 不存在的任务 ID；
- JSON 基本读写。

它没有覆盖重复参数边界、并发、原子写入和 Heartbeat 触发。

`test_heartbeat.py` 的多数测试只完成：

```text
写入测试 JSON
→ 再读出 JSON
→ 断言刚写入的数据存在
```

它们没有真正运行一次 `pacemaker_loop()` 扫描。其他测试还存在：

- `self.assertTrue(True)` 占位断言；
- 在测试中重新手写重复次数递减逻辑；
- 用硬编码频率列表验证同一硬编码列表。

因此测试名称不能证明 Heartbeat 的生产逻辑正确。

跨平台和日期方面还有两个夹具问题：

1. Windows 上不能可靠删除仍保持打开的 `NamedTemporaryFile`；
2. `replace(day=day+1)` 在月末会构造不存在的日期，应改为 `+ timedelta(days=1)`。

## 二、自测题与参考答案

### 1. `schedule_task()` 返回成功后，任务已经执行了吗？

没有。它只把任务定义写入 `tasks.json`。

真正执行依赖仍在运行的 `pacemaker_loop()` 扫描任务、写入队列并由 `agent_worker` 再次调用 Agent。

### 2. Heartbeat 是进程、线程还是协程？

它是当前 Python 进程中的异步协程，通过：

```python
asyncio.create_task(pacemaker_loop(...))
```

与终端输入和 Agent worker 并发运行。关闭 CyberClaw 进程后，该协程也随之停止。

### 3. 为什么任务能跨重启保留，却不能在关闭期间准时提醒？

任务定义保存在磁盘 JSON 中，所以重启后仍存在；检查任务时间的 Heartbeat 只存在于内存进程中，关闭期间没有执行者。

重启后，它可以发现已经过期的记录并延迟触发，但不能补偿关机期间的准时提醒。

### 4. 为什么 Heartbeat 先创建 `pending_tasks` 和 `triggered_tasks`？

这是一次扫描后的状态分区：

- `pending_tasks` 决定下一版 JSON 保留什么；
- `triggered_tasks` 决定本轮向 Agent 投递什么。

单次任务只进入触发列表，重复任务可以同时进入两个列表：本轮触发，同时保存下一次计划。

### 5. 单次任务为什么触发后会消失？

它到期后加入 `triggered_tasks`，但因为没有 `repeat`，不会加入 `pending_tasks`。

随后代码用 `pending_tasks` 覆盖写回 `tasks.json`，因此该任务从文件中移除。

### 6. `repeat_count=3` 表示什么？

表示总共触发三次：

```text
3 → 第一次触发后保存为 2
2 → 第二次触发后保存为 1
1 → 第三次触发后不再保存
```

它不是首次触发之外再重复三次。

### 7. 为什么 `repeat_count=None` 表示无限重复？

Heartbeat 只有在计数不为 `None` 时才递减和判断结束。空值会跳过次数耗尽逻辑，只要重复频率有效，就持续计算下一次时间并写回。

### 8. 基于旧 `target_time` 续期有什么影响？

它保持原计划节奏，但系统离线或严重延迟时，增加一个周期后的时间可能仍早于当前时间。

Heartbeat 后续扫描会继续将其判断为到期，形成短时间连续补触发。系统需要明确采用补发、跳过还是合并策略。

### 9. `tasks_lock` 保护了什么？

它保护当前进程内任务文件的读—改—写临界区，避免线程或工具调用互相覆盖。

它不能协调两个进程，也不能阻止外部程序修改文件，因此不等于跨进程一致性或数据库事务。

### 10. 为什么异步 Heartbeat 仍可能阻塞事件循环？

因为锁是同步 `threading.Lock`，文件读写和 JSON 处理也是同步操作。协程在这些代码中没有 `await`，事件循环线程必须等它们完成。

文件很小时影响较小；锁竞争或慢磁盘会影响 Spinner、输入和其他协程。

### 11. JSON 写回失败但消息继续入队会怎样？

到期任务仍保留在旧 JSON 中，但本轮提醒已经进入队列。下次扫描会再次看到它，可能造成重复触发。

由于写入异常被静默吞掉，用户无法直接看到失败原因。

### 12. JSON 写回成功但入队前崩溃会怎样？

单次任务已经从文件删除，但提醒还没有进入内存队列。重启后没有任务记录可以恢复，因此提醒可能永久丢失。

这说明文件状态变更和消息投递需要事务或 Outbox 等一致性机制。

### 13. 为什么 `system_msg` 最终不是 `SystemMessage`？

`system_msg` 只是局部变量名。`agent_worker` 对队列里的所有字符串统一执行：

```python
HumanMessage(content=user_input)
```

LangChain 消息角色由实例类型决定，不由变量名和文本前缀决定。

### 14. 为什么用户输入和提醒不会同时调用 Agent？

它们共用同一个 FIFO 队列，而且只有一个 `agent_worker` 消费。

这种设计保证串行处理和消息顺序，但长任务会让后面的提醒延迟。

### 15. `tasks.json`、`task_queue` 和 SQLite 分别保存什么？

```text
tasks.json
→ 尚待调度的任务定义

task_queue
→ 当前进程内等待 Agent 处理的输入

SQLite checkpoint
→ 已经进入 LangGraph 的对话状态
```

三者生命周期和故障恢复能力不同，不能混为“记忆”。

### 16. 未知 `repeat` 为什么仍可能提醒一次？

代码先把到期任务加入 `triggered_tasks`，之后才判断重复频率。未知频率无法计算下一次时间，所以不进入 `pending_tasks`，最终表现为触发一次后移除。

### 17. 格式损坏的任务为什么有时保留、有时消失？

损坏任务不会进入 `pending_tasks`，但代码只在存在其他触发任务时才覆盖写回文件。

没有其他任务触发时原文件不变；有其他任务触发时，新文件只包含 `pending_tasks`，损坏任务被顺带删除。

### 18. 为什么当前 Heartbeat 测试不能证明调度正确？

多数测试没有真正调用 `pacemaker_loop()`，只是验证自己写入的 JSON 能重新读取；占位测试甚至只有恒真断言。

要证明调度正确，必须执行至少一次真实扫描，并断言文件状态、队列消息、续期字段和异常行为。

### 19. Windows `NamedTemporaryFile` 测试为什么会失败？

测试保留临时文件句柄打开，又尝试 `os.unlink()`。Windows 通常不允许删除仍被打开且未共享删除权限的文件，因此抛出 `PermissionError`。

应先关闭句柄再删除，或使用 `TemporaryDirectory` 明确控制文件生命周期。

### 20. 为什么 `replace(day=day+1)` 不是可靠的“明天”？

它只修改当前月份中的日字段。月末会尝试创建 7 月 32 日等非法日期。

跨日运算应使用：

```python
datetime_value + timedelta(days=1)
```

由日期库负责跨月和跨年。

## 三、面试追问与回答思路

### 1. 怎样把当前 Heartbeat 改成可靠调度服务？

我会把调度从终端进程中拆出，使用持久化数据库保存：

```text
task_id
status
next_run_at
timezone
repeat_rule
remaining_runs
last_run_at
version
risk_policy
```

独立 worker 通过数据库锁或租约领取到期任务，记录执行尝试和结果；终端关闭不影响调度，多个 worker 也能安全扩展。

### 2. 怎样减少“文件已改但消息未投递”的不一致？

可以采用 Transactional Outbox：

```text
同一个数据库事务
→ 更新任务下一次状态
→ 写入待投递 outbox 记录

独立投递器
→ 读取 outbox
→ 发送消息
→ 标记 delivered
```

这样进程崩溃后仍能恢复尚未投递的消息。消费端还需使用幂等键去重。

### 3. 定时任务如何处理时区和夏令时？

任务应保存：

- 用户时区，如 `Asia/Shanghai`；
- 经过时区转换的时间；
- 重复规则基于墙上时间还是固定时长；
- DST 不存在或重复时刻的处理策略。

存储时通常使用带时区时间或 UTC，展示和日历重复时再结合用户时区计算。

### 4. 服务停机期间错过的任务应该怎么处理？

需要定义 misfire policy：

```text
fire_once
→ 恢复后只补一次

catch_up_all
→ 补齐所有错过次数

skip
→ 跳到下一次未来时间

coalesce
→ 合并多次为一次摘要
```

当前实现隐式接近逐轮补触发，但没有明确配置，容易造成恢复后的提醒风暴。

### 5. 为什么数据库比共享 JSON 更适合并发调度？

数据库能够提供：

- 原子事务；
- 行级锁或乐观版本控制；
- 条件更新领取任务；
- 索引查询到期时间；
- 多进程协调；
- 执行历史和失败重试记录。

JSON 更适合单用户学习项目，但覆盖写和进程内锁难以支撑多个执行者。

### 6. 怎样为 Heartbeat 写有效测试？

先把“无限循环”和“扫描一次”拆开：

```python
async def scan_due_tasks(now, repository, queue):
    ...
```

测试注入固定时钟、临时仓库和 `AsyncMock` 队列，断言：

- 未到期任务保留；
- 单次任务触发并删除；
- 有限与无限任务正确续期；
- 不同频率的下一次时间；
- 坏数据和写入失败；
- 队列消息及去重 ID。

循环本身只需少量测试休眠、取消和调用频率。

### 7. 如何实现可持久化的 Agent 输入队列？

可以使用数据库队列表、Redis Streams、RabbitMQ 等持久化消息系统。消息需要：

```text
message_id
task_id
payload
created_at
delivery_attempts
status
```

消费者成功处理后确认，失败可以重试并进入死信队列；通过幂等键防止重复执行外部副作用。

### 8. 定时执行高风险工具应怎样审批？

创建任务时应保存明确的动作类型、结构化参数、风险等级和审批范围，而不是只保存自然语言描述。

到期时重新检查：

- 审批是否仍有效；
- 参数是否与批准内容一致；
- 环境和权限是否改变；
- 是否需要二次确认；
- 工具是否具备幂等保护。

普通提醒可以直接发送，高风险文件、Shell 或外部写操作应进入独立审批节点。

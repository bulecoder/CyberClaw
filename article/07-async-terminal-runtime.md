# 第 7 课｜异步终端与运行时协调

> 主要源码：`entry/main.py`  
> 辅助源码：`cyberclaw/core/bus.py`、`cyberclaw/core/heartbeat.py`、`examples/basic_usage.py`

## 一、本课要解决的问题

CyberClaw 运行时不只有“等待模型回答”这一件事。

同一个终端进程还要协调：

```text
接收用户输入
执行 Agent 图
接收心跳产生的到期任务
刷新 Spinner
打印工具调用和最终回答
持久化异步 checkpoint
处理退出
```

如果所有操作都写进一个普通 `while` 循环，模型调用期间输入界面、Spinner 和心跳都会停止。

CyberClaw 使用 `asyncio` 把这些工作拆成多个协程，再通过一个共享队列连接生产者与消费者。

本课重点不是记住所有 `async/await` 语法，而是理解：

1. 事件循环怎样调度协程；
2. `task_queue` 怎样统一用户输入和心跳消息；
3. 为什么只有一个 Agent consumer；
4. `app.astream()` 怎样把图节点更新交给终端；
5. `patch_stdout()` 为什么对交互式终端重要；
6. 当前取消、退出和背压处理有哪些边界；
7. “入口是异步的”为什么不等于内部所有操作都是原生异步。

## 二、阅读顺序

建议按入口到并发任务的顺序阅读：

1. `entry/main.py` 的 `main()`
2. `async_main()` 的前半部分
3. `bus.py` 的 `task_queue`
4. `agent_worker()`
5. `user_input_loop()`
6. `redraw_timer()`
7. 创建和回收三个后台 Task 的位置
8. `heartbeat.py` 的 `pacemaker_loop()`
9. `examples/basic_usage.py` 的同步版本

同步示例可以帮助理解异步入口究竟多解决了哪些运行时协调问题。

## 三、`asyncio.run()` 创建事件循环

程序入口：

```python
def main():
    asyncio.run(async_main())
```

`async_main()` 是协程函数：

```python
async def async_main():
    ...
```

调用协程函数不会像普通函数一样立刻执行完全部代码，而是创建一个协程对象。

`asyncio.run()` 负责：

1. 创建事件循环；
2. 在事件循环中运行 `async_main()`；
3. 等待它完成；
4. 收尾异步生成器；
5. 关闭事件循环。

可以把事件循环理解为一个协作式调度器：

```text
某个协程运行
→ 遇到 await，暂时交出控制权
→ 事件循环运行其他已就绪协程
→ 被等待的操作完成
→ 原协程继续
```

它不是每个协程创建一个操作系统线程。

## 四、协程、Task 与线程的区别

### 1. 协程函数

用 `async def` 定义，例如：

```python
async def user_input_loop():
    ...
```

它描述一段可以暂停和恢复的工作。

### 2. 协程对象

调用：

```python
user_input_loop()
```

得到协程对象，但不保证它已经被调度执行。

### 3. Task

执行：

```python
asyncio.create_task(agent_worker())
```

会把协程包装成 Task 并交给当前事件循环调度。

Task 表示“已经被安排运行的一项异步工作”，还记录完成、异常或取消状态。

### 4. 线程

线程由操作系统调度，能执行同步函数。

协程只有在运行到 `await` 等协作点时才主动让出事件循环。如果一个协程内部直接运行耗时同步代码，事件循环所在的线程仍会被占住。

所以：

```text
async def 包装
≠ 同步内部代码自动变成非阻塞
```

## 五、启动阶段先构建运行资源

### 1. Banner 仍是同步代码

`print_banner()` 内部使用：

```python
time.sleep(...)
```

它会阻塞当前线程。

不过 Banner 在创建 Agent worker、心跳任务和输入循环之前运行，因此主要影响启动动画，不会在正常对话期间阻塞这些并发任务。

### 2. 加载当前模型配置

`async_main()` 读取：

```text
DEFAULT_PROVIDER
DEFAULT_MODEL
```

随后创建模型和 Agent 图。

### 3. 异步 SQLite checkpointer

```python
async with AsyncSqliteSaver.from_conn_string(DB_PATH) as memory:
```

`async with` 会调用异步资源的进入与退出协议。

它让数据库连接的建立和关闭具有明确生命周期：

```text
进入代码块 → 打开并准备 saver
离开代码块 → 异步清理资源
```

Agent 在这个上下文内部编译：

```python
app = create_agent_app(
    ...,
    checkpointer=memory
)
```

所以 Agent 的使用期不会超过 checkpointer 资源的有效期。

## 六、`task_queue` 是运行时消息总线

`bus.py` 中：

```python
task_queue = asyncio.Queue()
```

队列存放的不是 LangChain Message 对象，而是普通字符串。

它有两个主要生产者：

```text
user_input_loop
    → 用户输入字符串

pacemaker_loop
    → 到期任务提示字符串
```

只有一个消费者：

```text
agent_worker
```

数据流为：

```text
用户输入 ─────┐
              ├→ asyncio.Queue → agent_worker → HumanMessage → Agent
心跳任务 ─────┘
```

### `emit_task()` 的位置

`bus.py` 还定义：

```python
async def emit_task(content: str):
    await task_queue.put(content)
```

它只是对 `put()` 的薄封装。当前主流程直接把 `task_queue` 传给心跳并直接调用 `put()`，所以 `emit_task()` 实际没有参与当前运行链路。

## 七、为什么队列能解耦生产者与消费者

用户输入与心跳只需要负责：

```python
await task_queue.put(content)
```

它们不需要知道：

- Agent 是否正在调用模型；
- 当前图执行到哪个节点；
- 工具是否正在运行；
- 回答如何打印。

Agent worker 只需要负责：

```python
user_input = await task_queue.get()
```

它不需要知道字符串来自键盘还是心跳。

这种结构把“消息从哪里来”和“消息怎样被 Agent 处理”分离了。

## 八、`agent_worker()` 是唯一消费者

### 1. 持续取队列消息

```python
while True:
    user_input = await task_queue.get()
```

队列为空时，`await` 会暂停 worker，让事件循环执行输入、心跳或刷新协程。

### 2. 退出消息也是队列消息

如果字符串是：

```text
/exit
/quit
```

worker 会：

```python
task_queue.task_done()
break
```

因此退出命令会按照队列顺序处理。它前面已经排队的输入通常会先执行。

### 3. 所有来源统一包装成 `HumanMessage`

```python
inputs = {
    "messages": [
        HumanMessage(content=user_input)
    ]
}
```

无论字符串来自用户还是心跳，Agent 图看到的都是一条用户角色消息。

因此到期任务不是特殊事件类型，而是一段由心跳生成的自然语言提示。

### 4. 单消费者保证串行

同一时刻只有一个 `agent_worker()`。

所以同一个 `thread_id` 中的输入按队列顺序逐个执行：

```text
输入 A 完整跑完图
→ task_done()
→ 再取输入 B
```

这避免两个 Agent 执行同时更新同一会话 checkpoint，却也意味着长请求会阻塞后面所有用户输入和到期任务。

## 九、`app.astream()` 怎样连接图与终端

核心调用：

```python
async for event in app.astream(
    inputs,
    config=config,
    stream_mode="updates"
):
```

### 1. 为什么使用 `async for`

`astream()` 返回异步事件流。图中每个节点完成后，可以逐步产生更新。

`async for` 每次等待下一项时会让出事件循环，事件到达后继续处理。

### 2. `stream_mode="updates"`

收到的是每个节点产生的增量更新，而不是只在整张图结束后拿最终状态。

典型过程：

```text
{"agent": {"messages": [AIMessage(tool_calls=...)]}}
{"tools": {"messages": [ToolMessage(...)]}}
{"agent": {"messages": [AIMessage(content=...)]}}
```

终端可以因此及时显示：

```text
Tool Call: read_office_file
```

而不必等最终答案生成后才一次性打印。

### 3. 节点事件和 Token 流不是同一件事

当前流式模式展示“节点完成后的状态更新”。

模型调用仍使用：

```python
llm_with_tools.invoke(...)
```

终端没有逐 Token 输出模型内容。它是在 Agent 节点得到完整 `AIMessage` 后打印。

## 十、异步图入口不等于内部全部原生异步

`entry/main.py` 调用异步：

```python
app.astream(...)
```

但 `agent_node()` 内部调用的是同步接口：

```python
llm_with_tools.invoke(...)
```

这意味着：

1. 外部以异步方式消费图事件；
2. 节点内部并没有显式调用 `ainvoke()`；
3. 同步节点是否被放到工作线程，由 LangGraph 对同步 runnable 的执行实现决定；
4. 仅从 CyberClaw 源码不能宣称整个模型调用链都是原生异步 I/O。

如果要让边界更明确，可以把 Agent 节点改成 `async def`，并优先使用模型的：

```python
await llm_with_tools.ainvoke(...)
```

但还要检查工具、checkpointer 和底层客户端是否真正支持异步。

## 十一、`user_input_loop()` 怎样保持终端可交互

### 1. `PromptSession.prompt_async()`

```python
user_input = await session.prompt_async(...)
```

程序在等待键盘输入时不会占住事件循环，Agent worker 和心跳仍可继续。

### 2. 输入被放入队列

```python
await task_queue.put(user_input)
```

放入后，输入循环会回到下一次 `prompt_async()`。

因此用户理论上可以在前一个请求仍在运行时继续输入，后续内容会排在队列中。

### 3. 队列没有容量上限

`asyncio.Queue()` 没有传 `maxsize`，所以它是无界队列。

当生产速度长期高于 Agent 消费速度时，队列会不断增长。个人终端中风险较低，但服务化后必须考虑：

- 最大待处理数量；
- 拒绝或合并策略；
- 用户级配额；
- 取消某一项请求；
- 排队状态反馈。

## 十二、Spinner 是另一个协程负责刷新的

`redraw_timer()`：

```python
while True:
    if spinner.is_spinning:
        get_app().invalidate()
    await asyncio.sleep(0.08)
```

`asyncio.sleep()` 不会阻塞线程，它把控制权还给事件循环，约 80 毫秒后再恢复。

Spinner 的文字、开始时间和工具状态保存在同一个 `SpinnerState` 对象中。Agent worker 更新状态，底部工具栏读取状态。

这是一种共享内存协作：

```text
agent_worker 写 SpinnerState
redraw_timer 读 SpinnerState
```

由于这些协程都在同一个事件循环线程中，单次 Python 语句之间不会发生普通多线程式抢占；但每个 `await` 仍可能让其他协程运行，所以跨多个步骤的状态一致性仍需设计。

## 十三、`patch_stdout()` 解决什么问题

交互提示符正在等待输入时，Agent worker 可能打印：

- 工具调用；
- 最终回答；
- 异常信息；
- 心跳相关内容。

普通 `print()` 可能把正在编辑的输入行冲乱。

```python
with patch_stdout():
```

让 `prompt_toolkit` 在后台输出发生时临时维护和重绘提示符，使异步输出与用户正在输入的文字能够相对协调。

它解决的是终端显示一致性，不是并发安全或业务状态同步。

## 十四、`Queue.task_done()` 与 `Queue.join()`

每次：

```python
await queue.put(item)
```

队列内部的“未完成任务计数”增加。

消费者处理完后调用：

```python
queue.task_done()
```

计数减少。

退出前：

```python
await task_queue.join()
```

会一直等待计数归零。

因此 `join()` 的意义不是等待“队列目前看起来为空”，而是等待所有已放入的项都由消费者明确标记完成。

### 当前实现的风险

`task_done()` 没有放在覆盖整个单项处理的 `finally` 中。

如果 worker 在某个未捕获异常或取消路径中提前离开，该项可能永远不被标记完成，`join()` 就可能一直等待。

## 十五、任务创建与退出流程

主程序创建：

```python
worker = asyncio.create_task(agent_worker())
heartbeat_worker = asyncio.create_task(
    pacemaker_loop(...)
)
```

`redraw_timer()` 则在 `user_input_loop()` 内部创建。

运行关系：

```text
async_main
├── agent_worker Task
├── heartbeat_worker Task
└── await user_input_loop
    └── redraw_timer Task
```

输入循环结束后：

```python
await task_queue.join()
worker.cancel()
heartbeat_worker.cancel()
```

### 当前取消逻辑的边界

代码调用了 `cancel()`，但没有继续：

```python
await worker
await heartbeat_worker
```

也没有集中使用 `try/finally` 确保异常时始终取消所有后台任务。

`redraw_task.cancel()` 后同样没有等待它确认结束。

更完整的关闭流程应当：

1. 停止生产新消息；
2. 决定处理完还是丢弃排队项；
3. 通知消费者退出；
4. 等待所有 Task 完成或取消；
5. 收集 `CancelledError` 与其他异常；
6. 最后关闭数据库和终端资源。

### 退出与心跳存在竞争窗口

用户的 `/exit` 进入队列后，心跳任务仍在运行，直到 `queue.join()` 之后才被取消。

如果 worker 处理 `/exit` 后退出，而心跳恰好又放入一条新消息：

```text
队列新增未完成项
+ 已没有 consumer
→ join() 可能无法结束
```

这是当前关闭顺序中的真实竞态。

## 十六、同步示例与正式入口的区别

`examples/basic_usage.py` 使用：

```text
input()
app.stream()
普通 while 循环
```

它没有：

- 异步输入；
- 心跳并发；
- 共享队列；
- Spinner 刷新；
- SQLite checkpointer；
- `thread_id` 恢复。

它适合展示图节点事件，但不是正式终端运行时的等价实现。

示例还维护本地：

```python
state = {"messages": []}
```

每轮只向其中追加用户消息，没有把图产生的 AIMessage 和 ToolMessage 合并回本地 `state`。因此它不能正确展示完整的多轮对话记忆。

## 十七、本课运行链总结

```text
main()
→ asyncio.run(async_main())
→ 打开 AsyncSqliteSaver
→ 创建 Agent app
→ 创建 agent_worker
→ 创建 pacemaker_loop
→ 进入 user_input_loop

用户输入：
prompt_async()
→ queue.put(text)
→ agent_worker.queue.get()
→ HumanMessage
→ app.astream()
→ 逐节点更新终端
→ task_done()

心跳到期：
pacemaker_loop
→ queue.put(reminder_text)
→ 走同一 agent_worker 流程

退出：
/exit 入队
→ 输入循环结束
→ 等待 queue.join()
→ cancel 后台任务
→ 离开 SQLite 上下文
→ asyncio.run() 关闭事件循环
```

## 十八、学完本课应能回答

1. 协程对象和 Task 有什么区别？
2. `await` 为什么能让 Spinner 和心跳继续运行？
3. 用户输入和到期任务怎样汇入同一 Agent？
4. 单消费者带来什么好处和限制？
5. `stream_mode="updates"` 返回的是什么？
6. 为什么当前输出不是逐 Token 流式输出？
7. `task_done()` 与 `join()` 怎样配合？
8. 当前退出顺序为什么可能和心跳形成竞争？
9. 为什么 `app.astream()` 不足以证明内部全部是原生异步？


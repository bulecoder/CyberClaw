# 第 10 课｜审计日志、后台线程与监控终端

> 主要源码：`cyberclaw/core/logger.py`、`entry/monitor.py`  
> 辅助源码：`cyberclaw/core/agent.py`、`entry/cli.py`

## 一、本课要解决的问题

Agent 的最终回答只展示了结果，而理解和审计系统还需要知道：

```text
给模型发送了多少条消息
模型决定调用哪个工具
工具参数是什么
工具返回了什么
模型最后回答了什么
事件发生在什么时候
属于哪个 thread
```

CyberClaw 把其中一部分信息写成 JSONL，再由另一个终端实时 tail。

总体链路：

```text
agent_node()
→ audit_logger.log_event()
→ 内存 Queue
→ 后台 writer thread
→ logs/<thread_id>.jsonl
→ cyberclaw monitor
→ tail_f()
→ render_event()
```

本课要看懂：

1. 为什么日志写入使用线程而不是 `asyncio`；
2. 单例 Logger 怎样创建；
3. Queue、守护线程与 `atexit` 怎样配合；
4. JSONL 为什么适合追加式事件；
5. Monitor 为什么只显示启动后的新事件；
6. 当前“审计”和“透明”真正覆盖了什么；
7. 日志系统有哪些可靠性、隐私和运维边界。

## 二、阅读顺序

按生产到消费顺序阅读：

1. `logger.py` 的 `JSONLEventLogger.__new__()`
2. `_init_logger()`
3. `log_event()`
4. `_write_loop()`
5. `shutdown()`
6. `agent.py` 中四类 `log_event()` 调用
7. `monitor.py` 的 `LOG_FILE`
8. `tail_f()`
9. `render_event()`
10. `entry/cli.py` 的 `monitor` 子命令

## 三、Logger 为什么使用单例

类级字段：

```python
_instance = None
_lock = threading.Lock()
```

`__new__()` 中：

```python
with cls._lock:
    if cls._instance is None:
        创建实例
        初始化 Logger
    return cls._instance
```

这样同一个进程中多次执行：

```python
JSONLEventLogger()
```

会取得同一个对象。

### 为什么加线程锁

如果两个线程同时第一次创建 Logger，没有锁就可能：

- 创建两个实例；
- 启动两个 writer thread；
- 重复注册退出处理。

锁使首次构造串行。

### 第一次 `log_dir` 决定全局目录

第一次实例化会执行：

```python
_init_logger(log_dir)
```

后续再调用：

```python
JSONLEventLogger("other_logs")
```

仍返回旧实例，不会修改目录。

因此单例使全局唯一，却也让构造参数只有第一次有效，增加测试隔离和多配置实例的困难。

## 四、模块导入时就启动后台线程

文件底部：

```python
audit_logger = JSONLEventLogger()
```

只要导入 `logger.py`，就会：

1. 创建默认 `logs` 目录；
2. 创建内存队列；
3. 启动守护线程；
4. 注册 `atexit` 回调。

这属于模块导入副作用。

### 相对日志目录取决于 cwd

默认目录是：

```text
logs
```

它不是根据 `config.PROJECT_ROOT` 计算的绝对路径。

从 CLI 运行时，`entry.cli` 会先把 cwd 切到项目根目录，所以日志通常出现在项目根目录下。

如果其他程序直接导入 `agent.py`，日志目录可能创建在启动该程序时的当前目录。

## 五、前台只负责把事件放入队列

`log_event()` 构造：

```python
{
    "ts": "UTC 时间",
    "thread_id": thread_id,
    "event": event,
    **kwargs
}
```

时间格式：

```text
YYYY-MM-DDTHH:MM:SSZ
```

它表示 UTC，精度到秒。

随后：

```python
self.log_queue.put(log_item)
```

前台不直接打开文件。

### 为什么这样设计

文件打开、JSON 序列化和磁盘写入由后台线程完成。

Agent 路径只做一次内存入队，通常较快，避免每个埋点都明显延长模型与工具流程。

### 使用的是线程安全同步队列

这里是：

```python
queue.Queue
```

不是：

```python
asyncio.Queue
```

因为生产者和消费者跨线程。`queue.Queue` 为线程间通信提供同步。

## 六、队列是无界的

创建时没有传 `maxsize`：

```python
queue.Queue()
```

所以生产速度长期高于磁盘写入速度时：

```text
事件不断积压
→ 内存持续增长
```

好处是 `put()` 通常不会因队列满而阻塞 Agent。

代价是缺少背压。磁盘故障、大量高频事件或慢文件系统可能把压力转化为内存占用。

## 七、后台 writer thread

创建方式：

```python
threading.Thread(
    target=self._write_loop,
    daemon=True
)
```

### 1. daemon 的含义

守护线程不会单独阻止 Python 进程退出。

如果进程被强制终止，尚未写出的队列内容可能丢失。因此代码又使用 `atexit` 尝试在正常退出时刷新。

### 2. 循环等待事件

```python
log_item = self.log_queue.get()
```

队列为空时，writer thread 阻塞等待，不进行忙轮询。

### 3. `None` 是停止哨兵

如果取到：

```python
None
```

线程先 `task_done()`，然后退出循环。

普通日志都是字典，因此 `None` 可以充当控制消息。

### 4. thread ID 会变成文件名

只保留：

```text
字母数字
-
_
```

其他字符被直接删除。

如果结果为空，使用：

```text
default
```

最终路径：

```text
logs/<safe_id>.jsonl
```

这减少了 `/`、`\` 等路径分隔符造成的目录穿越风险。

不同原始 ID 仍可能清洗成同一个文件名，例如特殊字符被删除后发生碰撞。

### 5. 每个事件都重新打开文件

```python
with open(file_path, "a", encoding="utf-8") as f:
    f.write(...)
```

优点：

- 每次写入后文件立即关闭；
- 不需要长期管理多个 thread 文件句柄；
- Monitor 更容易看到追加内容。

缺点：

- 高频事件反复 open/close 有开销；
- 没有批量写；
- 没有跨进程文件锁；
- 多个进程写同一个文件时一致性没有明确保证。

## 八、为什么使用 JSONL

每个事件序列化成一行 JSON：

```json
{"ts":"...","thread_id":"...","event":"tool_call","tool":"calculator","args":{"expression":"1+1"}}
```

JSON Lines 的特点：

- 追加写简单；
- 一行对应一个独立事件；
- tail 可以按行消费；
- 单行损坏通常不必阻止读取其他行；
- 便于导入日志平台和流式处理。

它不是一个完整 JSON 数组，因此不需要每次写入时重写整个文件。

## 九、异常时会发生什么

写入过程被：

```python
try:
    ...
except Exception as e:
    print(...)
finally:
    task_done()
```

包围。

如果对象不能 JSON 序列化或磁盘写入失败：

- 错误打印到当前终端；
- 该事件丢失；
- 队列项仍被标记完成；
- writer thread 继续处理后续事件。

这是“尽量不中断 Agent”的选择，但没有：

- 重试；
- 本地 fallback；
- 失败计数；
- 告警；
- dead-letter queue。

所以日志失败不会阻止业务，也可能在不明显的情况下失去审计记录。

## 十、正常退出怎样刷新队列

初始化时：

```python
atexit.register(self.shutdown)
```

`shutdown()`：

```python
self.log_queue.put(None)
self.log_queue.join()
```

由于队列先进先出：

```text
先前普通事件
→ None 哨兵
```

writer 会先写完前面的事件，再看到 `None` 退出。`join()` 等待所有项都调用 `task_done()`。

### 只覆盖正常退出

以下情况无法保证执行 `atexit`：

- 进程被强制 kill；
- 操作系统崩溃；
- 机器断电；
- Python 运行时异常终止。

### `shutdown()` 不是幂等的

第一次手动调用后，writer 已退出。

如果随后 `atexit` 再调用一次：

```text
又放入一个 None
→ 已没有 writer 消费
→ queue.join() 可能永久等待
```

同理，shutdown 之后继续 `log_event()`，新事件也不会被消费。

可靠实现需要显式状态和锁，保证重复 shutdown 安全，并拒绝或同步处理关闭后的日志。

## 十一、Agent 记录了哪些事件

### 1. `llm_input`

模型调用前：

```json
{
  "event": "llm_input",
  "message_count": 12
}
```

只记录消息条数，没有记录：

- Token 数；
- 模型和 Provider；
- prompt 大小；
- 是否为摘要调用；
- 请求 ID；
-耗时。

### 2. `tool_call`

模型返回工具调用时：

```json
{
  "event": "tool_call",
  "tool": "read_office_file",
  "args": {"filepath": "hello.txt"}
}
```

参数按原值完整记录。

### 3. `tool_result`

工具节点执行后，图再次进入 `agent_node()`。

代码从消息末尾向前寻找连续的 ToolMessage，并记录：

```json
{
  "event": "tool_result",
  "tool": "read_office_file",
  "result_summary": "前 200 个字符"
}
```

注意结果在写日志前已经截断到 200 字符。

### 4. `ai_message`

模型没有调用工具且返回文本时：

```json
{
  "event": "ai_message",
  "content": "完整回答"
}
```

这会把回答全文写入日志。

### 5. 当前没有记录的关键事件

没有显式记录：

- HumanMessage 原文；
- 模型请求开始与结束；
- 模型异常；
- 工具异常与状态码；
- 每次图运行的 run ID；
- Token 与费用；
-摘要更新；
- checkpoint 成功或失败；
- 队列等待时间；
- 用户审批。

因此它是基础事件日志，还不是完整可追踪系统。

## 十二、Monitor 监听哪个文件

`entry/monitor.py` 写死：

```text
<PROJECT_ROOT>/logs/local_geek_master.jsonl
```

这与正式 Agent 硬编码的 thread ID 对应。

Monitor 不能通过 CLI 参数选择：

- 其他 thread；
- 其他日志目录；
- 历史时间范围；
- 多个会话同时查看。

## 十三、`tail_f()` 为什么只显示新事件

文件不存在时，每 0.5 秒检查一次。

打开后先执行：

```python
f.seek(0, 2)
```

把文件位置移动到末尾。

所以 Monitor 不读取启动前已有的历史行，只等待之后追加的新内容。

随后循环：

```text
readline() 有内容 → yield
没有内容          → sleep 0.1 秒
```

这是同步轮询，不是文件系统事件通知。

### 日志轮转边界

文件打开后会一直持有原文件句柄。

如果日志文件被重命名并新建同名文件，Monitor 没有检测 inode 或文件身份变化，可能继续等待旧文件，无法自动切换到新文件。

## 十四、`render_event()` 实际显示什么

### 已显示

```text
llm_input
tool_call
tool_result
system_action
```

### 没有显示

虽然 Theme 定义了：

```text
ai_message
```

但 `render_event()` 没有对应的 `elif event == "ai_message"`。

因此 AI 完整回答会写入 JSONL，却不会显示在 Monitor。

### `system_action` 没有生产者

Monitor 能渲染 `system_action`，但当前 Agent 和其他核心源码没有记录该事件。

这是一条准备好的显示分支，不是当前可观察到的真实事件。

## 十五、时间转换

Logger 写 UTC：

```text
2026-07-31T08:00:00Z
```

Monitor 把 `Z` 转成 `+00:00`，通过：

```python
datetime.fromisoformat(...).astimezone()
```

转为运行 Monitor 的本地时区，只展示：

```text
HH:MM:SS
```

所以日志文件保留统一 UTC，界面按本地时区显示，这是一种合理分工。

## 十六、Monitor 的错误处理边界

`render_event()` 最外层使用：

```python
except:
    pass
```

任何异常都会被静默忽略，包括：

- 非法 JSON；
- 缺失或错误字段；
- 时间解析问题；
- Rich 渲染错误；
- 真实代码 bug。

Monitor 看起来只是“少了一条”，使用者不知道原因。

更好的方式是捕获具体异常，将坏行和错误写到独立诊断输出。

## 十七、日志中的隐私与注入风险

### 1. 工具参数没有脱敏

参数可能包含：

- 要写入文件的完整内容；
- Shell 命令；
- 用户画像；
- 路径和任务信息；
- 其他敏感数据。

### 2. AI 回答完整保存

回答中可能含有用户隐私或从工具结果中提取的信息。

### 3. 日志不是天然安全的

`.gitignore` 只防止误提交，不提供：

- 文件加密；
- 操作系统访问控制配置；
- 保留期限；
- 自动删除；
- 用户同意；
- 字段脱敏。

### 4. Rich markup 注入

Monitor 把工具名、参数和结果拼入 Rich 字符串。

如果内容含 Rich markup 标记，可能改变显示格式或触发渲染问题。外部文本应使用安全的 `Text` 对象或禁用 markup，而不是直接作为格式字符串。

## 十八、“审计日志”和“可观测性”的差别

当前实现能回答：

```text
某 thread 何时向模型发送了几条消息
模型调用了什么工具和参数
工具结果前 200 字符是什么
模型最终文本是什么
```

它暂时不能完整回答：

```text
一次用户请求的端到端 trace 是什么
哪个模型版本处理
每一步耗时多少
消耗多少 Token 和费用
异常发生在哪里
状态更新是否成功
多个并行请求如何关联
日志是否完整无丢失
```

所以准确名称是“基础 JSONL 事件日志与实时终端查看器”，而不是完整的生产级审计与可观测平台。

## 十九、本课完整链路

生产端：

```text
导入 agent.py
→ 创建 audit_logger 单例和 writer thread
→ agent_node 调用 log_event()
→ 事件加入 queue.Queue
→ 后台线程取出
→ thread_id 清洗为文件名
→ JSON 序列化并追加一行
```

监控端：

```text
cyberclaw monitor
→ Typer 分发 run_monitor()
→ entry.monitor.main()
→ 打开 local_geek_master.jsonl
→ seek 到文件末尾
→ 轮询新行
→ JSON 解析
→ 按 event 类型用 Rich 渲染
```

正常退出：

```text
Python atexit
→ shutdown()
→ None 哨兵入队
→ writer 写完前序事件并退出
→ queue.join() 返回
```

## 二十、学完本课应能回答

1. 为什么 Logger 使用 `queue.Queue` 而不是 `asyncio.Queue`？
2. 单例怎样避免创建多个 writer thread？
3. 无界队列带来什么取舍？
4. daemon thread 和 `atexit` 怎样配合？
5. JSONL 为什么适合实时 tail？
6. Agent 实际记录了哪四类事件？
7. Monitor 为什么看不到旧事件和 AI 回答？
8. `shutdown()` 为什么不是幂等的？
9. 当前日志为什么不能称为完整审计系统？
10. 日志内容有哪些隐私和显示注入风险？


# 第 6 课｜短期状态、长期画像与上下文压缩

> 主要源码：`cyberclaw/core/context.py`、`cyberclaw/core/agent.py`  
> 辅助源码：`cyberclaw/core/tools/builtins.py`、`entry/main.py`  
> 对应测试：`tests/test_context_advanced.py`

## 一、本课要解决的问题

“Agent 有记忆”不是一个单一功能。

CyberClaw 至少包含以下几类状态：

```text
当前消息历史        → AgentState.messages
近期对话摘要        → AgentState.summary
图状态持久化        → workspace/state.sqlite3
用户长期画像        → workspace/memory/user_profile.md
定时任务            → workspace/tasks.json
用户工作产物        → workspace/office/
```

这些数据的内容、保存位置、更新方式和生命周期都不相同。

本课要看懂：

1. `AgentState` 怎样合并新旧消息；
2. 对话怎样按完整用户回合裁剪；
3. 被裁掉的消息怎样变成摘要；
4. `RemoveMessage` 怎样改变当前图状态；
5. 用户画像怎样进入系统提示词；
6. SQLite、摘要、画像和文件为什么不能统称为一种“记忆”；
7. 更换模型、重启进程、切换 `thread_id` 分别影响什么。

## 二、阅读顺序

建议按数据生命周期阅读：

1. `context.py` 的 `AgentState`
2. `context.py` 的 `trim_context_messages()`
3. `agent.py` 中 `agent_node()` 的消息读取
4. `agent.py` 中摘要生成和 `RemoveMessage`
5. `agent.py` 中用户画像读取
6. `agent.py` 中 `sys_prompt` 与 `msgs_for_llm`
7. `builtins.py` 的 `save_user_profile()`
8. `entry/main.py` 的 `AsyncSqliteSaver` 和 `thread_id`

先理解内存里的状态，再理解它怎样被持久化。

## 三、`AgentState` 是图节点共享的状态

### 1. 状态结构

```python
class AgentState(TypedDict):
    messages: Annotated[
        list[BaseMessage],
        add_messages
    ]
    summary: str
```

它只有两个字段：

```text
messages → 当前图状态中的消息序列
summary  → 旧对话压缩后的近期任务摘要
```

### 2. `add_messages` 是 reducer

图节点不会直接永久修改同一个字典，而是返回“状态更新”。

例如用户输入：

```python
{"messages": [HumanMessage(content="你好")]}
```

Agent 节点返回：

```python
{"messages": [AIMessage(content="你好")]}
```

`add_messages` 决定怎样把新消息合并进已有消息列表。

通常行为是：

- 新 ID：追加；
- 已有相同 ID：更新对应消息；
- `RemoveMessage(id=...)`：删除对应 ID 的消息。

因此 reducer 不只是普通的：

```python
old_list + new_list
```

它还理解 LangGraph 的消息更新与删除语义。

### 3. `summary` 没有声明 reducer

`summary` 是普通字符串字段。节点返回新值时，会用新值替换旧值。

```text
messages → 通过 add_messages 合并
summary  → 直接覆盖
```

## 四、为什么要按“用户回合”裁剪

### 1. 一次工具调用并不只有两条消息

普通对话可能是：

```text
HumanMessage
AIMessage
```

工具调用可能是：

```text
HumanMessage
AIMessage(tool_calls)
ToolMessage
AIMessage(final answer)
```

如果只按消息数量保留最后 N 条，可能留下 `ToolMessage`，却删除发出该工具调用的 `AIMessage`；也可能留下工具请求而删除工具结果。

这会破坏模型协议要求的消息关系。

### 2. CyberClaw 对“回合”的定义

`trim_context_messages()` 遍历所有非系统消息。

遇到 `HumanMessage` 时开始一个新回合，直到下一个 `HumanMessage` 出现之前的所有消息，都归入当前回合：

```text
第 1 个 HumanMessage
├── 后续 AIMessage
├── ToolMessage
└── 最终 AIMessage

第 2 个 HumanMessage
└── ...
```

所以裁剪时会整体保留或整体丢弃一次用户回合。

## 五、`trim_context_messages()` 的完整逻辑

函数返回：

```python
tuple[
    list[BaseMessage],  # final_messages
    list[BaseMessage]   # discarded_messages
]
```

### 1. 系统消息单独处理

```python
first_system = next(
    (m for m in messages if isinstance(m, SystemMessage)),
    None
)
```

它只记住第一条系统消息，其余系统消息从非系统消息集合中排除。

不过 Agent 后面会重新构造新的系统提示词，因此这里保留的旧系统消息最终也不会直接发送给模型。

### 2. 没有非系统消息

如果只有系统消息或完全没有消息：

```text
final_messages = 第一条系统消息（如果存在）
discarded_messages = []
```

### 3. 按 HumanMessage 分组

核心规则：

```python
if isinstance(msg, HumanMessage):
    保存上一个回合
    current_turn = [msg]
else:
    如果当前已有回合：
        追加到当前回合
```

只有在第一个 `HumanMessage` 出现后，后续 AI 或 Tool 消息才会进入回合。

如果消息序列开头出现孤立的 `AIMessage` 或 `ToolMessage`：

- 未触发裁剪时，它仍可能通过原始 `non_system_msgs` 返回；
- 真正按回合裁剪时，它没有归属的回合，可能被丢掉。

正常 Agent 历史通常从用户消息开始，但这个边界说明函数依赖消息序列合法性。

### 4. 触发阈值

函数默认参数是：

```python
trigger_turns=8
keep_turns=4
```

但是 `agent_node()` 实际调用时显式传入：

```python
trigger_turns=40
keep_turns=10
```

所以主程序的真实行为是：

```text
少于 40 个用户回合 → 不裁剪
达到或超过 40 个回合 → 只保留最近 10 个回合
```

阅读项目时必须以调用点传入的实参为准，不能只看函数默认值或 README。

### 5. 返回两组消息

触发后：

```text
recent_turns    = 最后 10 个回合
discarded_turns = 更早的所有回合
```

`final_messages` 用于本轮模型输入，`discarded_messages` 用于生成摘要和构造删除命令。

## 六、旧对话怎样压缩成摘要

### 1. 读取旧摘要

```python
current_summary = state.get("summary", "")
```

第一次压缩时通常为空；后续压缩时，它保存上一次生成的摘要。

### 2. 把本次丢弃消息转成文本

```python
discarded_text = "\n".join(
    f"{m.type}: {m.content}"
    for m in discarded_msgs
    if m.content
)
```

这里仅提取有内容的消息。

一个只包含 `tool_calls`、但文本内容为空的 `AIMessage` 不会被写进 `discarded_text`。工具结果若有文本则会进入摘要材料，但工具调用的结构化参数可能丢失。

### 3. 用同一个模型生成新摘要

提示词包含：

```text
现有交接文档
+ 刚刚过去的旧对话
→ 最新上下文摘要
```

并要求：

- 记录当前话题、进度和结论；
- 不记录姓名、职业、爱好等静态偏好；
- 不超过 150 字；
- 只输出摘要。

随后执行：

```python
new_summary_response = llm.invoke(...)
```

主对话模型同时承担摘要模型角色。注释说“这里可以用便宜模型”，但当前代码并没有单独配置摘要模型。

### 4. 150 字只是提示，不是程序约束

代码直接使用：

```python
active_summary = new_summary_response.content
```

没有再次统计字数或截断，所以模型可能输出超过 150 字。提示词要求不是强制的数据校验。

### 5. 摘要是有损压缩

摘要模型可能：

- 漏掉关键参数；
- 混淆先后顺序；
- 把用户输入中的指令当成摘要指令；
- 生成不准确结论；
- 丢失结构化工具调用信息。

因此摘要适合维护“近期任务语境”，不能替代权威业务数据库或完整审计记录。

## 七、`RemoveMessage` 怎样删除旧消息

触发压缩后，Agent 节点返回：

```python
delete_cmds = [
    RemoveMessage(id=m.id)
    for m in discarded_msgs
    if m.id
]

state_updates["messages"] = delete_cmds
```

最后又把本轮新生成的 `AIMessage` 追加到同一更新列表中。

`add_messages` reducer 处理这些更新：

```text
RemoveMessage(id=旧消息 ID) → 从当前消息状态移除
AIMessage(新回复)           → 追加进当前消息状态
```

### “从状态删除”不等于“物理抹除所有历史”

使用 checkpointer 后，LangGraph 会保存状态演进的 checkpoint。

`RemoveMessage` 的直接含义是：后续读取最新状态时，这些旧消息不再出现在 `messages` 中。

它并不自动承诺：

- SQLite 文件中旧 checkpoint 的字节被安全擦除；
- 所有历史版本都被清理；
- 数据满足隐私法规要求的彻底删除。

如果需要真正的数据删除，还必须研究 checkpointer 的历史保留、清理和数据库维护策略。

## 八、最终发送给模型的消息怎样构造

### 1. 每轮重新创建系统提示词

Agent 先生成基础 `sys_prompt`，其中包含：

- 助手身份；
- 双脑协同规则；
- 何时保存用户画像；
- office 安全提示。

随后追加长期画像和近期摘要。

### 2. 用户画像进入高优先级上下文

```python
profile_path = os.path.join(
    MEMORY_DIR,
    "user_profile.md"
)
```

文件存在时，每次进入 `agent_node()` 都会重新读取完整内容。

它被放进：

```text
【用户长期画像（静态偏好）】
```

这一部分属于 `SystemMessage`，比普通用户消息具有更高上下文优先级。

### 3. 摘要也进入系统消息

如果 `active_summary` 非空，会追加：

```text
[近期对话上下文]
...
```

所以压缩后的旧对话不再作为原始 Human/AI/Tool 消息发送，而是作为一段系统提示中的文本发送。

### 4. 旧系统消息被过滤

最终列表：

```python
[SystemMessage(content=sys_prompt)]
+ [
    m for m in final_msgs
    if not isinstance(m, SystemMessage)
]
```

无论历史中有多少系统消息，真正发给模型的只有本轮新构造的这一条系统消息。

### 5. 文本会做 UTF-8 容错清理

代码会把字符串：

```python
encode("utf-8", "ignore").decode("utf-8")
```

这能跳过非法字符，减少编码异常，但属于有损处理：非法内容会被静默删除。

## 九、长期画像怎样更新

### 1. 更新由模型主动决定

系统提示词要求：

```text
发现长期偏好、个人信息
或用户要求“记住某事”
→ 调用 save_user_profile
```

因此画像写入并不是每轮自动执行，而是由模型根据提示词判断是否调用工具。

### 2. `save_user_profile()` 是整文件覆盖

```python
with open(PROFILE_PATH, "w", encoding="utf-8") as f:
    f.write(new_content)
```

工具参数必须是完整的新 Markdown 文档，而不是增量片段。

如果模型只传入新增的一句话，旧画像会全部丢失。

### 3. 工具说明和实际工具集合不一致

`save_user_profile()` 的 docstring 要求：

```text
先调用 read_user_profile
```

但当前 `BUILTIN_TOOLS` 中没有 `read_user_profile` 工具。

Agent 虽然会在每轮系统提示词中看到画像内容，但模型不能通过一个同名工具显式读取原文件。这里说明文档式约定和真实工具集合没有完全同步。

### 4. 画像不区分会话

文件路径固定为：

```text
workspace/memory/user_profile.md
```

它不包含 `thread_id`，所以多个会话如果共享同一个工作区，会共享同一份画像。

这适合单用户个人助手，但不适合直接作为多用户服务。

## 十、SQLite checkpoint 与 `thread_id`

`entry/main.py` 创建：

```python
AsyncSqliteSaver.from_conn_string(DB_PATH)
```

数据库路径来自：

```text
workspace/state.sqlite3
```

编译 Agent 时传入：

```python
checkpointer=memory
```

每次运行图时又提供：

```python
config = {
    "configurable": {
        "thread_id": "local_geek_master"
    }
}
```

### `thread_id` 是 checkpoint 命名空间

可以把它理解为一段对话状态的键：

```text
同一数据库 + 同一 thread_id
→ 继续同一段状态

同一数据库 + 不同 thread_id
→ 读取另一段状态
```

`local_geek_master` 不是模型名，也不是操作系统账号。它是作者硬编码的本地会话标识。

### 为什么程序重启后还能记得

因为进程退出只会清空内存对象，SQLite 文件仍在磁盘上。

下一次启动：

```text
打开同一个 state.sqlite3
+ 使用同一个 local_geek_master
→ 恢复最新 checkpoint
```

### 为什么换模型后历史还在

模型创建读取 `.env`，checkpoint 读取 SQLite。换模型没有改变数据库和 `thread_id`，因此旧状态仍会交给新模型。

## 十一、五类“记忆”的区别

| 数据 | 内容 | 保存位置 | 更新者 | 作用域 |
|---|---|---|---|---|
| 原始消息 | Human/AI/Tool 消息 | `AgentState`，由 SQLite checkpoint 持久化 | 图 reducer | 按 `thread_id` |
| 摘要 | 旧对话的有损压缩 | `AgentState.summary`，由 SQLite 持久化 | LLM | 按 `thread_id` |
| 用户画像 | 长期偏好与个人信息 | `user_profile.md` | `save_user_profile` | 整个工作区共享 |
| 定时任务 | 待执行事项 | `tasks.json` | 任务工具与心跳 | 整个工作区共享 |
| office 文件 | 用户创建的工作产物 | `workspace/office/` | office 工具 | 整个工作区共享 |

只有前两者属于 LangGraph 状态。

## 十二、上下文压缩的真实边界

### 1. 触发点存在突变

第 39 个回合仍保留全部消息；第 40 个回合会一次性压缩到最近 10 个回合加摘要。

这会造成一次额外模型调用和上下文结构突变。

### 2. 固定回合数不等于固定 Token

短回合可能很少，某一个工具结果却可能非常长。按回合裁剪不能保证输入一定低于模型上下文窗口。

更稳健的实现应基于 Token 预算，同时保持工具调用消息的完整性。

### 3. 摘要和画像都可能污染系统提示

两者都是外部文本，最终进入 `SystemMessage`。如果其中包含恶意指令，模型可能把它当作高优先级内容。

需要：

- 结构化字段；
- 内容校验；
- 长度上限；
- 明确的数据与指令分隔；
- 敏感信息控制；
- 可查看、可修正和可删除机制。

### 4. 画像无大小限制

当前每轮读取完整文件并放入系统提示词。文件不断增长会持续占用上下文。

### 5. 摘要生成失败会影响主对话

摘要调用与主调用使用同一个 `agent_node()` 流程，没有独立的超时、重试、回退或备用摘要策略。

## 十三、本课完整数据流

未触发压缩：

```text
checkpoint 恢复 AgentState
→ 新 HumanMessage 经 reducer 追加
→ trim_context_messages() 返回全部消息
→ 读取 user_profile.md
→ 新 SystemMessage + 全部有效历史
→ 模型回复
→ AIMessage 写回状态与 checkpoint
```

触发压缩：

```text
checkpoint 恢复 AgentState
→ 达到 40 个用户回合
→ 保留最近 10 回合，分离旧回合
→ 旧摘要 + 本次旧消息交给 LLM
→ 得到新 summary
→ 为旧消息生成 RemoveMessage
→ 新系统提示加入画像与摘要
→ 最近 10 回合交给主模型
→ 新 AIMessage 写回
→ reducer 删除旧消息并追加新消息
→ 最新状态保存到 checkpoint
```

## 十四、学完本课应能回答

1. `add_messages` 为什么不是简单的列表相加？
2. 为什么必须按完整用户回合裁剪？
3. 主程序的真实阈值为什么是 40/10，而不是函数默认的 8/4？
4. 摘要怎样和上一次摘要合并？
5. `RemoveMessage` 删除了什么，没有保证删除什么？
6. 用户画像为什么属于长期记忆，却不属于 LangGraph 状态？
7. `local_geek_master` 的作用是什么？
8. 更换模型、改变 `thread_id`、删除数据库分别会产生什么影响？


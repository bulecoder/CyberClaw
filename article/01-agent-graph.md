# CyberClaw 的核心不是一个函数，而是一张会循环的状态图

我们已经亲手看到过这样一次交互：

```text
用户：读取 hello.txt
CyberClaw：Tool Call: read_office_file
CyberClaw：文件内容是 Hello CyberClaw
```

表面上只有一次输入和一次回答，内部实际上调用了两次模型，中间还执行了一次本地 Python 工具：

```text
用户消息
→ 第一次调用模型
→ 模型请求 read_office_file
→ 本地工具读取文件
→ 工具结果加入消息
→ 第二次调用模型
→ 模型组织最终回答
```

在很多简单 Agent 中，这段流程会写成一个 `while` 循环。CyberClaw 没有手写这个循环，而是用 LangGraph 把它建成一张状态图。本课要读懂的不是 LangGraph 的全部 API，而是三个问题：

1. 图中的状态是什么？
2. 图为什么会从 Agent 走向工具，再走回 Agent？
3. 图在什么条件下结束？

## 先认清三个角色

打开 `cyberclaw/core/agent.py`，`create_agent_app()` 是 Agent 的装配入口。它最终构造三个关键对象：

```text
AgentState       图在节点之间传递的状态
agent_node       调用模型并产生 AIMessage 的节点
ToolNode         执行工具调用并产生 ToolMessage 的节点
```

图本身只有两个业务节点：

```text
START
  ↓
agent
  ├── 没有工具调用 → END
  └── 有工具调用   → tools
                         ↓
                       agent
```

代码集中在 `create_agent_app()` 的末尾：

```python
workflow = StateGraph(AgentState)

workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", tools_condition)
workflow.add_edge("tools", "agent")

app = workflow.compile(checkpointer=checkpointer)
```

这几行不是在立即执行对话，而是在声明运行规则。`compile()` 才把声明转换成可调用的 LangGraph 应用。

## 状态不是普通 list

`AgentState` 定义在 `cyberclaw/core/context.py`：

```python
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str
```

它目前只有两个字段：

- `messages`：用户消息、AI 消息和工具结果；
- `summary`：旧对话被压缩后形成的近期上下文摘要。

真正重要的是：

```python
Annotated[list[BaseMessage], add_messages]
```

`add_messages` 是 reducer。节点返回：

```python
{"messages": [response]}
```

并不意味着用一个只含 `response` 的新列表覆盖全部历史。LangGraph 会通过 reducer 把新消息合并进已有状态。以后遇到 `RemoveMessage` 时，这个 reducer 还会按照消息 ID 删除指定消息。

所以阅读 LangGraph 节点时，不能只问“返回了什么”，还要问“这个字段的 reducer 会怎样解释返回值”。

## 工具从哪里进入图

`create_agent_app()` 支持两种工具来源：

```python
if tools is None:
    dynamic_tools = load_dynamic_skills()
    actual_tools = BUILTIN_TOOLS + dynamic_tools
else:
    actual_tools = tools
```

正常启动时，工具集合由两部分组成：

```text
BUILTIN_TOOLS
├── 时间、计算器、用户画像
├── 定时任务
└── office 文件与 Shell

dynamic_tools
└── 从 workspace/office/skills 扫描到的 Skill
```

测试可以显式传入 `tools`，用一个很小的工具集合隔离 Agent 行为。

同一份 `actual_tools` 被交给两个对象：

```python
tool_node = ToolNode(actual_tools)
llm_with_tools = llm.bind_tools(actual_tools)
```

这两行看起来相似，职责完全不同：

- `bind_tools()` 把工具名称、描述和参数 schema 告诉模型；
- `ToolNode` 保存真正可执行的本地工具。

模型不会直接执行 Python 函数。它只返回一条结构化请求，例如：

```text
name = read_office_file
args = {"filepath": "hello.txt"}
```

随后 LangGraph 才根据名称找到工具并在本机执行。

这就是 Tool Calling 的基本安全边界：模型负责提出动作，程序负责决定有哪些动作真的存在。

## `agent_node` 每轮做了什么

`agent_node(state, config)` 是整个项目最密集的函数。先不要逐行陷进去，可以把它拆成七步：

```text
1. 从 config 取 thread_id
2. 读取 state 中的历史 messages 和 summary
3. 记录上一轮工具结果
4. 必要时裁剪旧消息并生成摘要
5. 读取长期用户画像，组装 System Prompt
6. 调用绑定工具后的模型
7. 记录响应，并把 AIMessage 返回给图状态
```

核心模型调用只有一行：

```python
response = llm_with_tools.invoke(msgs_for_llm)
```

但这行之前完成了上下文构造，这行之后完成了审计日志和状态更新。模型调用本身只是 Agent 节点的一部分。

## 为什么查询文件会调用两次模型

第一次进入 `agent` 节点时，状态大致是：

```text
messages:
  HumanMessage("读取 hello.txt")
```

模型看到工具 schema 后返回带 `tool_calls` 的 `AIMessage`。`agent_node` 将它加入状态。

接下来：

```python
workflow.add_conditional_edges("agent", tools_condition)
```

`tools_condition` 检查最后一条 AIMessage：

- 有 `tool_calls`：路由到 `tools`；
- 没有 `tool_calls`：路由到结束节点。

`ToolNode` 执行 `read_office_file`，把结果保存为 `ToolMessage`。图中又声明了：

```python
workflow.add_edge("tools", "agent")
```

所以工具执行后一定回到 `agent`。第二次模型调用看到：

```text
HumanMessage("读取 hello.txt")
AIMessage(tool_calls=[read_office_file])
ToolMessage("Hello CyberClaw")
```

此时信息已经足够，模型返回普通文本，不再请求工具。`tools_condition` 将图路由到结束。

注意：CyberClaw 没有自行判断“文件已经读完，所以任务完成”。是否结束仍由模型通过“还要不要调用工具”表达。

## 一次真实调用的消息形状

为了真正理解工具循环，需要记住三种消息：

```text
HumanMessage
用户输入

AIMessage
模型文本，或者模型发起的 tool_calls

ToolMessage
本地工具的执行结果，并带有对应的 tool_call_id
```

工具调用不是普通文本约定。`AIMessage.tool_calls` 和后续 `ToolMessage` 必须形成合法配对。模型再次调用时，LangChain 会把这些消息转换为 Provider 所需的协议格式。

CyberClaw 把这部分协议处理交给 LangChain 和 LangGraph，因此核心代码比手写循环短，但协议约束并没有消失。

## Checkpointer 为什么能让图跨重启恢复

`entry/main.py` 创建图时传入：

```python
async with AsyncSqliteSaver.from_conn_string(DB_PATH) as memory:
    app = create_agent_app(..., checkpointer=memory)
```

调用图时又传入：

```python
config = {
    "configurable": {
        "thread_id": "local_geek_master"
    }
}
```

Checkpointer 根据 `thread_id` 保存和加载 `AgentState`。程序重启后仍使用相同 ID，所以你问“刚才创建的文件叫什么”时，图加载到了旧消息。

这里要区分两件事：

```text
state.sqlite3
保存对话状态，所以模型记得之前聊过什么

workspace/office/hello.txt
保存真实文件，所以 read_office_file 能再次读到内容
```

第一次询问历史时没有出现 Tool Call，证明模型从 checkpoint 恢复的消息中找到了答案。第二次要求确认文件时出现 Tool Call，证明文件系统状态也仍然存在。

## 这个实现有哪些值得肯定的地方

第一，图结构很小。Agent 和工具的职责分开，主循环一眼可见。

第二，工具集合同时用于 schema 绑定和 ToolNode，降低了“模型知道一个工具但执行器没有”这类配置不一致。

第三，状态使用 LangGraph reducer 管理，为后续消息追加、删除和 checkpoint 留出了统一语义。

第四，测试允许传入自定义工具和内存 checkpointer，核心图不依赖终端 UI。

## 这个实现有哪些边界

### 1. 没有显式设置图递归上限

如果模型不断请求工具，图会持续在：

```text
agent → tools → agent
```

之间循环，直到 LangGraph 默认递归限制、模型错误或外部中断。项目没有像 CoreCoder 那样把单次任务的最大工具轮数暴露成清晰配置。

### 2. `agent_node` 承担了太多职责

它同时负责：

- 工具结果审计；
- 上下文裁剪；
- 摘要模型调用；
- 用户画像读取；
- System Prompt 构造；
- 主模型调用；
- 响应日志。

这使主链路短，但单个节点难以独立测试和替换。

### 3. 模型调用是同步的

主终端运行在 asyncio 中，但节点内部调用：

```python
llm_with_tools.invoke(...)
```

这是同步请求。模型响应期间可能阻塞事件循环，影响终端刷新或其他异步任务。更完整的实现会考虑 `ainvoke()` 或把同步调用移到线程中。

### 4. 会话 ID 固定

所有启动都使用 `local_geek_master`。它实现了自动续聊，却没有真正的多会话、新建会话或会话选择。

这些边界不是现在立刻重写的理由，而是后续工程改造的候选问题。

## 本课源码阅读顺序

不要从 `agent.py` 第一行机械读到最后一行。按下面顺序更容易建立结构：

1. `cyberclaw/core/context.py`：先看 `AgentState`；
2. `cyberclaw/core/agent.py` 的 `create_agent_app()` 开头：看工具装配；
3. `agent.py` 末尾：先画出状态图；
4. 回到 `agent_node()`：按七个阶段拆开；
5. `entry/main.py`：找 checkpointer 和 `thread_id`；
6. `tests/test_agent.py`：看图如何脱离 CLI 被测试。

## 本课实验

### 实验一：观察有工具和无工具的两条路径

普通输入：

```text
你好
```

预期：

```text
agent → END
```

工具输入：

```text
必须调用 read_office_file 读取 hello.txt
```

预期：

```text
agent → tools → agent → END
```

### 实验二：观察模型是否自行结束

让模型连续完成两个动作：

```text
先读取 hello.txt，再调用 calculator 计算 12 * 34，最后总结结果
```

观察它可能形成：

```text
agent → tools → agent → tools → agent → END
```

也可能一次返回多个工具调用。记录真实行为，不要根据预期替模型编造轨迹。

### 实验三：验证 checkpoint 与文件持久化是两件事

退出并重启后：

1. 询问之前的文件内容，观察是否不调用工具；
2. 明确要求重新读取，观察是否调用工具。

这个实验你已经完成，可以直接把终端现象写进笔记作为证据。

## 写 notes 前的自测题

先关闭 article，尝试独立回答：

1. `AgentState.messages` 为什么不是普通 list 字段？
2. `bind_tools()` 和 `ToolNode` 的职责有什么区别？
3. `tools_condition` 根据什么决定下一条边？
4. 为什么一次文件读取通常需要调用两次模型？
5. 工具执行结束后为什么必须回到 `agent`？
6. 模型通过什么方式表示任务已经结束？
7. SQLite 中保存的是什么，`office` 中保存的又是什么？
8. 为什么更换模型后历史对话仍然能够继续？
9. 固定 `thread_id` 带来了什么便利和限制？
10. 同步 `invoke()` 放在异步终端中可能产生什么问题？

## 本课结束时你应该能说清什么

CyberClaw 的核心 Agent 是一张两节点循环图。`agent` 节点调用绑定工具后的模型；如果 AIMessage 包含工具请求，`tools_condition` 把状态路由给 `ToolNode`；工具结果以 ToolMessage 加回状态后，图再次调用模型；当模型不再请求工具时，图结束。`add_messages` 决定消息如何合并，SQLite checkpointer 决定状态如何跨进程保存，`thread_id` 决定恢复哪一条会话。

能不用看代码讲清这一段，再去写 `notes/01-agent-graph.md`。

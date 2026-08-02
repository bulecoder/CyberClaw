# 01｜LangGraph Agent 状态图

> 对应源码：`cyberclaw/core/agent.py`、`cyberclaw/core/context.py`  
> 运行入口：`entry/main.py`  
> 对应测试：`tests/test_agent.py`

## 一、本课核心内容

### 1. Agent 的核心结构

CyberClaw 用 LangGraph 表达模型与工具之间的反馈循环：

```text
START
  ↓
agent
  ├── 无 tool_calls → END
  └── 有 tool_calls → tools
                         ↓
                       agent
```

- `agent`：调用模型，产生 `AIMessage`；
- `tools`：执行工具，产生 `ToolMessage`；
- `tools_condition`：检查最后一条 AIMessage 是否包含工具调用；
- 模型不再请求工具时，本轮任务结束。

### 2. Agent 状态

```python
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str
```

- `messages`：保存用户、模型和工具消息；
- `summary`：保存旧对话压缩后的摘要；
- `add_messages`：消息字段的 reducer，负责追加、按 ID 更新以及处理 `RemoveMessage`。

节点返回的是状态增量：

```python
{"messages": [response]}
```

LangGraph 通过 reducer 将它合并进旧状态，而不是简单覆盖全部消息。

### 3. 工具绑定与执行

实际工具由内置工具和动态 Skill 组成：

```python
actual_tools = BUILTIN_TOOLS + load_dynamic_skills()
```

同一份工具交给：

```python
llm_with_tools = llm.bind_tools(actual_tools)
tool_node = ToolNode(actual_tools)
```

二者区别：

```text
bind_tools()
→ 把名称、描述和参数 schema 告诉模型

ToolNode
→ 真正在本地执行模型请求的工具
```

模型只能生成结构化请求，不能通过 `bind_tools()` 直接执行 Python。

### 4. `agent_node()` 的流程

```text
读取 thread_id
→ 读取 messages 和 summary
→ 记录上一轮工具结果
→ 必要时裁剪并总结旧消息
→ 读取长期用户画像
→ 构造 SystemMessage
→ 调用绑定工具后的模型
→ 记录响应
→ 返回消息状态更新
```

核心模型调用：

```python
response = llm_with_tools.invoke(msgs_for_llm)
```

返回普通文本时：

```python
AIMessage(content="最终回答")
```

返回工具请求时：

```python
AIMessage(
    tool_calls=[
        {
            "name": "read_office_file",
            "args": {"filepath": "hello.txt"},
        }
    ]
)
```

`agent_node` 只产生 AIMessage；下一步去哪由 `tools_condition` 决定。

### 5. 状态图的声明

```python
workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", tools_condition)
workflow.add_edge("tools", "agent")
app = workflow.compile(checkpointer=checkpointer)
```

`compile()` 将图声明变成可执行应用，并绑定可选 checkpointer。

### 6. 一次文件读取的完整过程

输入：

```text
读取 hello.txt
```

运行轨迹：

```text
HumanMessage("读取 hello.txt")
→ agent
→ AIMessage(tool_calls=[read_office_file])
→ tools_condition 选择 tools
→ ToolNode 读取文件
→ ToolMessage("Hello CyberClaw")
→ agent 再次调用模型
→ AIMessage("文件内容是 Hello CyberClaw")
→ tools_condition 选择 END
```

因此文件读取通常需要两次模型调用：

1. 第一次决定调用什么工具；
2. 第二次解释工具结果并回答用户。

`tool_call_id` 用于将每个 ToolMessage 与具体工具调用一一对应。

### 7. `local_geek_master` 与持久化

主程序设置：

```python
config = {
    "configurable": {
        "thread_id": "local_geek_master"
    }
}
```

`local_geek_master` 只是项目作者选择的固定字符串，不是模型名、用户名或特殊关键字。

它作为 LangGraph checkpoint 的会话键：

```text
thread_id
→ 在 state.sqlite3 中定位对应会话
→ 恢复 messages 和 summary
→ 执行本轮图
→ 将新状态保存到同一会话
```

同一 ID 自动续聊；换一个 ID 会开始另一条逻辑会话，旧状态仍保留在数据库中。

它还被审计日志用作标识，最终形成类似：

```text
logs/local_geek_master.jsonl
```

当前硬编码的优点是简单续聊，缺点是没有多会话、多用户隔离，并且多个终端可能共享同一状态。

### 8. Checkpoint 与真实文件

```text
workspace/state.sqlite3
→ 保存消息和摘要
→ 让模型记得之前聊过什么

workspace/office/
→ 保存真实文件
→ 让工具能够重新读取文件内容
```

重启后直接询问历史且没有 Tool Call，证明 checkpoint 恢复成功；要求重新读取并出现 Tool Call，证明真实文件仍存在。

### 9. 当前测试与边界

`tests/test_agent.py` 主要验证：

- `AgentState` 可以初始化；
- Mock Provider 下可以编译图；
- 可以传入自定义工具；
- 可以绑定 `MemorySaver`。

它没有完整执行：

```text
agent → tools → agent → END
```

也没有自动验证 SQLite 重启恢复。真实模型和文件工具实验补充了端到端证据。

当前主要边界：

- 完成条件由模型是否继续请求工具决定；
- 没有清晰暴露任务级工具轮次、token 和费用预算；
- `agent_node` 同时承担上下文、Prompt、模型和日志职责；
- `thread_id` 固定，不能管理多会话。

---

## 二、自测题与参考答案

### 1. CyberClaw 为什么需要 Agent 循环？

模型第一次只拥有用户目标、历史消息、System Prompt 和工具 schema，并不能直接看到本地文件、当前时间或工具执行结果。

因此复杂请求需要经过反馈循环：

```text
模型判断缺少什么信息
→ 生成 tool_calls
→ 程序执行工具
→ ToolMessage 返回真实结果
→ 模型根据新信息继续决策
```

当模型认为信息已经足够，不再返回 tool_calls，`tools_condition` 才将图路由到结束状态。

### 2. `AgentState` 保存什么？

`AgentState` 当前有两个字段：

- `messages`：保存 HumanMessage、AIMessage 和 ToolMessage 等动态消息；
- `summary`：保存被裁剪旧对话的摘要。

`messages` 提供近期原始上下文，`summary` 用较短文本保留更早的任务进展。用户画像不属于 AgentState，而是每轮从 `workspace/memory/user_profile.md` 重新读取。

### 3. `add_messages` 有什么作用？

它是 `messages` 字段的 reducer，定义节点返回的新消息怎样与旧状态合并。

主要行为：

- 新 ID 的消息追加到历史；
- 相同 ID 的消息更新原消息；
- `RemoveMessage(id=...)` 删除指定消息。

所以 `agent_node` 可以只返回：

```python
{"messages": [response]}
```

而不必复制完整历史。如果缺少合适的 reducer，节点增量可能覆盖旧消息，工具调用链和会话上下文就会丢失。

### 4. `bind_tools()` 和 `ToolNode` 有什么区别？

`bind_tools()` 面向模型，将工具名称、描述和参数 schema 转成 Provider 能理解的 Tool Calling 定义。它只让模型能够生成结构化请求，不会执行本地函数。

`ToolNode` 面向运行时，根据 AIMessage 中的工具名称和参数找到真实工具，执行后生成 ToolMessage。

可以概括为：

```text
bind_tools：提供能力说明
ToolNode：落实真实行动
```

同一份 `actual_tools` 同时交给二者，可以减少“模型知道某工具，但执行器中没有该工具”的配置不一致。

### 5. `tools_condition` 根据什么路由？

它检查状态中最后一条 AIMessage 是否包含非空的 `tool_calls`：

```text
有 tool_calls
→ 返回 tools
→ ToolNode 执行工具

没有 tool_calls
→ 返回 LangGraph 结束标识
→ 本轮图执行完成
```

它不检查用户目标是否客观完成，也不验证模型答案质量。当前完成条件本质上仍是“模型是否决定停止调用工具”。

### 6. 工具执行后为什么要回到 `agent`？

工具只负责执行确定动作。例如 `read_office_file` 返回文件文本，`calculator` 返回计算结果，但它们不知道用户完整意图，也不负责组织最终回答。

工具结果必须回到 `agent`，让模型综合：

```text
用户原始问题
+ 自己发起的工具调用
+ ToolMessage 中的真实结果
```

模型随后可能生成最终答案，也可能发现还缺少信息并请求另一个工具。

### 7. 为什么读取文件通常调用两次模型？

第一次调用时模型还看不到文件内容，只能根据用户目标生成：

```python
read_office_file(filepath="hello.txt")
```

ToolNode 执行后把内容放入 ToolMessage。第二次调用时，模型才能看到真实文件内容并生成自然语言回答。

所以典型路径是：

```text
第一次模型调用：决定行动
工具执行：获得观察结果
第二次模型调用：解释结果并回答
```

如果第二次模型又请求其他工具，循环还会继续。

### 8. `tool_call_id` 有什么作用？

它把每个工具结果与 AIMessage 中的具体调用一一对应：

```text
AIMessage.tool_calls[n].id
↕
ToolMessage.tool_call_id
```

一次模型响应可能并行请求多个工具，也可能多次调用同名工具，只依靠工具名无法准确配对。OpenAI 兼容协议还要求工具调用后必须存在对应的工具结果，否则下一次模型请求可能因消息结构不合法而失败。

### 9. `local_geek_master` 是什么？

它是作者硬编码的普通字符串：

```python
config = {
    "configurable": {
        "thread_id": "local_geek_master"
    }
}
```

它不是 LangGraph 保留字，也不是模型名或用户名。它被用作：

- SQLite Checkpointer 的会话键；
- `agent_node` 中的会话标识；
- 审计日志中的 thread ID；
- 日志文件名的一部分。

程序每次都使用同一个值，因此重启后会恢复同一段历史。

### 10. 更换 thread ID 会发生什么？

换成一个数据库中没有的新 ID 时，Checkpointer 找不到旧状态，会从本轮输入开始建立新会话。

例如：

```text
local_geek_master
→ 原来的历史会话

lesson_01_debug
→ 新的独立会话
```

这不会删除旧 checkpoint。以后重新使用 `local_geek_master`，原历史仍可恢复。若两个进程同时使用同一 ID，还可能产生并发状态竞争。

### 11. 更换模型后为什么历史仍存在？

模型配置与对话状态分开保存：

```text
.env
→ Provider、模型名、API Key、Base URL

workspace/state.sqlite3
→ messages、summary 等图状态
```

更换模型只改变后续由哪个 ChatModel 处理请求，不会删除 SQLite 中的 checkpoint。只要 thread ID 不变，新模型仍会收到恢复后的历史消息。

### 12. 当前测试为什么不能证明完整 Agent 正确？

`tests/test_agent.py` 主要使用 Mock Provider 验证：

- AgentState 可以初始化；
- 图能够编译；
- 可以传入自定义工具；
- 可以绑定 MemorySaver。

它没有真正运行一个可预测的：

```text
agent → tools → agent → END
```

因此没有自动断言工具是否被执行、ToolMessage 是否正确配对、条件边是否走对、SQLite 是否能跨重启恢复。现有手动测试提供了端到端证据，但还不能替代自动回归测试。

---

## 三、面试追问与回答思路

### 1. 为什么用 LangGraph，而不是手写 `while`？

LangGraph 显式描述节点、条件边和状态 reducer，并提供 Checkpointer。新增审批、规划、重试或验证节点时，可以继续扩展图。

手写 `while` 的优点是直接、依赖少、调试路径清楚；当状态、分支和持久化越来越复杂时，维护成本会上升。因此选择取决于 Agent 复杂度，不是图框架一定优于循环。

### 2. 怎样实现多会话？

为每个会话生成独立 thread ID，并额外保存：

- 会话标题；
- 创建和更新时间；
- 使用的模型配置；
- 用户或租户 ID；
- 是否归档。

CLI 增加新建、列出、恢复和归档命令。多用户环境还要校验访问权限，不能只依赖一个可猜测的字符串。

### 3. 怎样防止模型无限调用工具？

需要多层限制：

- LangGraph recursion limit；
- 单次任务最大工具轮数；
- 相同工具和参数的重复失败阈值；
- token 与费用预算；
- 总运行时间；
- 单工具超时。

达到限制时应返回明确、结构化的停止原因，避免模型把预算耗尽误认为普通工具错误后继续重试。

### 4. 怎样测试完整工具循环？

使用可预测的 Fake ChatModel：

```text
第一次调用
→ 固定返回 mock_tool 的 tool_calls

第二次调用
→ 检查输入中存在 ToolMessage
→ 返回固定最终文本
```

然后断言：

- 工具只执行一次；
- 工具参数正确；
- ToolMessage 与 tool_call_id 匹配；
- 消息顺序正确；
- 图最终进入 END。

### 5. 如何增加高风险工具审批？

在 `agent` 和 `tools` 之间增加风险判断：

```text
agent
→ 低风险调用直接进入 tools
→ 高风险调用进入 approval
→ 用户批准后进入 tools
→ 拒绝后返回 agent 或结束
```

审批必须绑定具体工具名称和完整参数，避免批准后执行内容被替换。

### 6. 固定 thread ID 有什么风险？

主要风险包括：

- 不相关任务混入同一上下文；
- 历史持续膨胀并增加摘要成本；
- 多个终端竞争同一 checkpoint；
- 测试会话污染真实会话；
- 多用户之间没有数据隔离。

生产系统应为会话生成唯一 ID，并定义并发写入与访问控制策略。

### 7. 工具已修改文件但 checkpoint 保存失败怎么办？

这会造成外部世界和图状态不一致：文件已经改变，但消息历史不知道工具成功，恢复后可能重复执行。

改进方式：

- 工具调用使用幂等键；
- 执行前记录 pending；
- 成功后保存结果并标记 completed；
- 记录文件前后哈希；
- 使用原子写入、补丁或回滚信息。

### 8. 如何提高 Agent 的完成判断可靠性？

可以在“没有 tool_calls”之外增加：

- 明确任务状态，如 executing、waiting、completed、failed；
- 验证节点检查目标是否完成；
- 确定性验收，例如测试是否通过、目标文件是否存在；
- 最大验证轮数和硬预算。

验证节点如果仍依赖模型，也可能误判，因此确定性规则和预算仍然必要。

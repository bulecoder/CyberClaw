# 06｜短期状态、长期画像与上下文压缩

> 对应源码：`cyberclaw/core/context.py`、`cyberclaw/core/agent.py`  
> 辅助源码：`cyberclaw/core/tools/builtins.py`、`entry/main.py`

## 一、本课核心内容

### 1. CyberClaw 不只有一种记忆

```text
messages  → 当前原始消息历史
summary   → 旧对话的近期任务摘要
SQLite    → 按 thread_id 持久化图状态
画像文件  → 跨会话共享的长期用户信息
tasks     → 独立的定时任务状态
office    → 独立的工作产物
```

`messages` 和 `summary` 属于 `AgentState`；画像、任务和文件是图外部的持久化数据。

### 2. `AgentState` 和 reducer

```python
messages: Annotated[
    list[BaseMessage],
    add_messages
]
summary: str
```

`add_messages` 负责追加新消息、按 ID 更新消息，并处理 `RemoveMessage`。`summary` 则由节点返回的新字符串直接覆盖。

### 3. 按用户回合裁剪

一个回合从 `HumanMessage` 开始，到下一个 `HumanMessage` 之前结束，内部可能包含：

```text
AIMessage(tool_calls)
ToolMessage
AIMessage(final answer)
```

按完整回合裁剪能避免拆散工具请求与工具结果。

主程序实际参数为：

```text
达到 40 回合时触发
只保留最近 10 回合
```

函数默认的 8/4 不代表主程序行为。

### 4. 摘要生成

触发压缩后：

```text
旧 summary
+ 本次被丢弃的旧消息文本
→ 同一个 LLM 生成新 summary
```

“150 字以内”只是提示词要求，没有代码强制。只含结构化 `tool_calls` 而无文本内容的消息也可能不进入摘要材料，因此摘要是有损的。

### 5. 消息删除

旧消息被转换成：

```python
RemoveMessage(id=message.id)
```

随后由 `add_messages` 从最新图状态中移除。

这不等于安全擦除 SQLite 里所有旧 checkpoint。它只保证后续最新状态不再把这些消息作为当前 `messages` 返回。

### 6. 系统提示词的重建

每轮 Agent 都创建新的 `SystemMessage`，其中包含：

```text
固定身份与规则
+ user_profile.md
+ summary
```

历史系统消息会被过滤，最终只保留新构造的一条系统消息，再拼接最近原始消息。

### 7. 长期画像

`save_user_profile()` 把完整 Markdown 内容覆盖写入：

```text
workspace/memory/user_profile.md
```

每轮 Agent 都重新读取整个文件。画像不含 `thread_id`，所以同一工作区中的多个会话会共享它。

工具说明提到 `read_user_profile`，但当前工具列表中没有这个工具，这是说明和实现不一致。

### 8. checkpoint 与 `local_geek_master`

主程序使用：

```text
数据库：workspace/state.sqlite3
thread_id：local_geek_master
```

`thread_id` 是对话状态命名空间，不是模型名或系统用户名。

```text
同一数据库 + 同一 thread_id → 恢复同一段历史
同一数据库 + 不同 thread_id → 另一段会话状态
```

### 9. 主要边界

- 固定回合数无法可靠控制 Token；
- 第 40 回合会发生一次明显的压缩突变；
- 摘要可能遗漏、失真或受到提示注入；
- 用户画像是整文件覆盖，没有版本冲突保护；
- 画像没有长度限制，也没有会话或用户隔离；
- 摘要生成没有独立模型、超时、重试和回退；
- `RemoveMessage` 不等于数据库物理清除。

## 二、自测题与参考答案

### 1. `AgentState` 包含哪些字段？

**参考答案：**

包含 `messages` 和 `summary`。前者是使用 `add_messages` reducer 的消息列表，后者是旧对话压缩得到的普通字符串。

### 2. `add_messages` 的作用是什么？

**参考答案：**

它定义图节点返回的新消息怎样进入旧状态：通常追加新消息、按相同 ID 更新旧消息，并理解 `RemoveMessage` 的删除语义，而不是简单拼接两个列表。

### 3. 为什么不能只保留最后 20 条消息？

**参考答案：**

一次工具回合可能包含用户消息、工具调用 AI 消息、工具结果和最终回答。按固定消息数截断可能拆散这些关联消息，形成非法或不完整的模型上下文。

### 4. `trim_context_messages()` 怎样划分回合？

**参考答案：**

每遇到一个 `HumanMessage` 就开始新回合，之后直到下一个 `HumanMessage` 之前的 AI 和 Tool 消息都归入该回合。

### 5. 主程序何时触发压缩？

**参考答案：**

`agent_node()` 显式传入 `trigger_turns=40`、`keep_turns=10`。少于 40 回合不压缩，达到或超过 40 回合后只保留最近 10 回合。

### 6. 新摘要怎样产生？

**参考答案：**

代码把当前旧摘要和本次被裁掉的消息文本放进摘要提示词，再使用同一个 `llm.invoke()` 生成新的完整摘要，替换状态中的旧 `summary`。

### 7. 为什么摘要是有损的？

**参考答案：**

自然语言总结本身会压缩和选择信息；代码又只拼接有文本内容的消息，可能遗漏空内容 AIMessage 中的结构化工具调用。150 字限制也没有程序强制。

### 8. `RemoveMessage` 的真实作用是什么？

**参考答案：**

它作为状态更新交给 `add_messages`，使指定 ID 的旧消息不再出现在最新 `messages` 状态中。它没有证明旧 checkpoint 已从 SQLite 中物理擦除。

### 9. 用户画像怎样进入模型上下文？

**参考答案：**

`agent_node()` 每轮读取 `workspace/memory/user_profile.md`，将内容拼入新建的系统提示词，再把该系统消息放在最近对话消息之前。

### 10. 用户画像为什么不是按会话隔离的？

**参考答案：**

画像文件路径固定，不使用 `thread_id`。所有共享同一个 `WORKSPACE_DIR` 的会话都会读写同一个 `user_profile.md`。

### 11. `local_geek_master` 是什么？

**参考答案：**

它是传给 LangGraph checkpointer 的固定 `thread_id`，用于在 SQLite 中定位同一段对话状态。它不是本地 Python 环境、模型或操作系统用户。

### 12. 为什么重启后还能记住对话？

**参考答案：**

状态被 `AsyncSqliteSaver` 持久化到 `workspace/state.sqlite3`。重启后程序重新打开同一数据库，并继续使用 `local_geek_master`，因而恢复最新 checkpoint。

### 13. 更换模型后历史为什么仍在？

**参考答案：**

模型配置来自 `.env`，状态来自 SQLite。只换 Provider 或 Model 不会删除数据库，也不会改变固定 `thread_id`，所以新模型仍会得到旧状态。

### 14. 删除 `user_profile.md` 是否等于清空全部记忆？

**参考答案：**

不等于。它只删除长期画像；消息和摘要仍可能保存在 SQLite，任务在 `tasks.json`，文件还在 office 目录中。

### 15. 为什么画像工具存在数据丢失风险？

**参考答案：**

`save_user_profile()` 采用整文件覆盖，没有增量合并和版本检查。如果模型传入的不是完整新档案，旧信息会被直接覆盖。

## 三、面试追问与回答思路

### 1. 你会怎样改进上下文压缩？

**回答思路：**

使用 Token 预算而不是固定回合数；保持工具调用组的原子性；采用结构化摘要字段；对任务、决定、待办和关键参数分别维护；为摘要模型配置超时、重试、回退和质量检查。

### 2. 怎样实现多用户记忆隔离？

**回答思路：**

把 `user_id` 与 `thread_id` 分开：消息和摘要按 thread 隔离，长期画像按 user 隔离。路径或数据库查询必须使用经过认证的用户标识，并加入访问控制，不能只靠用户传入的字符串。

### 3. 怎样防止记忆污染？

**回答思路：**

将记忆视为不可信数据，使用结构化 schema、字段白名单、长度限制和内容审核；在提示词中明确标记为数据而非指令；重要事实要求来源和用户确认；提供查看、修改、撤销与删除能力。

### 4. 怎样实现真正的数据删除？

**回答思路：**

不能只发 `RemoveMessage`。需要明确 checkpoint 保留机制，删除指定用户或 thread 的全部历史版本，清理画像、任务、日志和文件，执行数据库维护，并验证备份与副本的生命周期。

### 5. 为什么摘要不应当作为业务事实库？

**回答思路：**

摘要由概率模型生成且有损，可能遗漏或改写事实。金额、权限、任务状态等权威数据应存在结构化数据库中，摘要只用于帮助语言模型理解近期语境。

### 6. 简历中怎样描述记忆改造才可信？

**回答思路：**

应写自己真正实现并验证的内容，例如“设计按 user/thread 分层的记忆模型，使用 Token 预算压缩上下文，并为摘要一致性和数据删除补充测试”。不能只把原项目已有 SQLite checkpoint 描述成自己完成的“长期记忆系统”。


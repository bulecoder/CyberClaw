# 02｜工具抽象与基础内置工具

> 对应源码：`cyberclaw/core/tools/base.py`、`cyberclaw/core/tools/builtins.py`  
> 辅助源码：`cyberclaw/core/agent.py`  
> 对应测试：`tests/test_builtins.py`

## 一、本课核心内容

### 1. Tool 的本质

模型不能直接执行本地 Python，只能生成结构化工具调用请求。CyberClaw 使用 LangChain Tool，把 Python 函数转换成模型和执行器都能理解的对象。

完整链路是：

```text
Python 函数
→ @cyberclaw_tool
→ Tool 对象
→ BUILTIN_TOOLS
→ actual_tools
→ bind_tools 告诉模型
→ ToolNode 执行
→ ToolMessage 返回结果
```

Tool 对象主要包含：

```text
name          工具名称
description   工具描述
args_schema   参数 Schema
invoke        同步执行入口
ainvoke       异步执行入口
```

模型负责“决定调用什么”，`ToolNode` 负责“真正执行本地代码”。

### 2. `cyberclaw_tool` 装饰器

```python
from langchain_core.tools import tool

cyberclaw_tool = tool
```

`cyberclaw_tool` 只是 LangChain 原生 `tool` 的别名，不是 CyberClaw 自己实现的工具协议。当前它没有额外加入权限、审批、超时或审计。

装饰器根据函数代码生成 Tool 元数据：

```text
函数名
→ name

docstring
→ description

参数名称、类型标注和默认值
→ args_schema

函数体
→ 执行逻辑
```

例如：

```python
@cyberclaw_tool
def calculator(expression: str) -> str:
    """一个简单的数学计算器。"""
```

模型会看到名为 `calculator` 的工具，以及必填字符串参数 `expression`。代码可以通过下面的方式直接执行 Tool：

```python
calculator.invoke({"expression": "25 * 48"})
```

### 3. 装饰器模式与类模式

简单工具采用装饰器模式：

```python
@cyberclaw_tool
def get_current_time() -> str:
    ...
```

复杂工具可以继承：

```python
class CyberClawBaseTool(BaseTool, ABC):
```

子类需要定义：

```python
name
description
args_schema
_run()
```

两种模式的适用场景：

| 模式 | 适用场景 |
|---|---|
| 装饰器模式 | 简单、无状态、初始化成本低 |
| 类模式 | 数据库连接、客户端对象、内部状态、复杂参数校验 |

基类的默认异步实现是：

```python
async def _arun(self, **kwargs):
    return await asyncio.to_thread(self._run, **kwargs)
```

它把同步函数放进线程执行，避免直接阻塞事件循环，但不等于底层业务逻辑变成了真正的异步 I/O。

当前内置工具全部使用装饰器模式；`CyberClawBaseTool` 只是预留的扩展点，尚未被实际使用。

### 4. 基础内置工具

`builtins.py` 中本课重点关注四个基础工具：

| 工具 | 数据来源或副作用 | 主要边界 |
|---|---|---|
| `get_current_time` | 本地操作系统时间 | 无时区、依赖系统时钟 |
| `calculator` | 使用 `eval()` 计算表达式 | 不适合生产级不可信输入 |
| `get_system_model_info` | 读取环境变量 | 只能报告配置，不能验证远端模型 |
| `save_user_profile` | 覆盖写入画像文件 | 错误调用可能丢失旧画像 |

`calculator` 使用：

```python
eval(expression, {"__builtins__": {}}, {})
```

清空内置函数可以拦截部分直接危险调用，但不能证明任意表达式都安全。更可靠的方案是 AST 运算符白名单或专用数学表达式解析器。

计算失败时，工具返回错误字符串：

```python
return "计算出错……"
```

它没有继续抛出异常。因此模型会收到包含失败原因的 `ToolMessage`，再决定如何向用户解释。

`save_user_profile` 不是追加写入，而是覆盖整个：

```text
workspace/memory/user_profile.md
```

其 docstring 提到 `read_user_profile`，但注册表中并没有这个工具。当前实际实现是 `agent_node` 每轮直接读取画像文件。说明文档和 docstring 也必须与真实注册表交叉核对。

### 5. 任务 CRUD 的接口层

任务系统提供四个 Tool：

```text
schedule_task
list_scheduled_tasks
delete_scheduled_task
modify_scheduled_task
```

类型标注和默认值决定模型可以生成的参数。例如：

```python
def schedule_task(
    target_time: str,
    description: str,
    repeat: str = None,
    repeat_count: int = None
) -> str:
```

- `target_time`、`description` 必填；
- `repeat`、`repeat_count` 可选；
- docstring 负责告诉模型时间格式、重复方式和确认规则。

但 docstring 只是模型行为指导，不是程序强制安全控制。例如删除工具的函数内部只根据 `task_id` 删除，并不会验证用户是否真的完成了二次确认。

因此必须区分：

```text
提示词或 docstring 约束
→ 希望模型遵守

函数内部校验和权限控制
→ 程序强制执行
```

任务 JSON、锁、重复续期和 Heartbeat 的内部过程留到第 3 课。

### 6. 工具注册

默认工具统一保存在：

```python
BUILTIN_TOOLS = [
    get_current_time,
    calculator,
    save_user_profile,
    list_office_files,
    read_office_file,
    write_office_file,
    execute_office_shell,
    get_system_model_info,
    schedule_task,
    list_scheduled_tasks,
    delete_scheduled_task,
    modify_scheduled_task
]
```

这里一共注册了 12 个内置工具。只定义函数但不加入注册表，默认 Agent 就看不到它。

`create_agent_app()` 决定实际工具：

```python
if tools is None:
    actual_tools = BUILTIN_TOOLS + load_dynamic_skills()
else:
    actual_tools = tools
```

显式传入 `tools` 会替换默认工具，而不是在默认列表后追加。这一语义便于测试构造最小工具集合，但调用方需要避免无意中移除全部内置工具。

### 7. 模型绑定与本地执行

同一份 `actual_tools` 同时交给：

```python
llm_with_tools = llm.bind_tools(actual_tools)
tool_node = ToolNode(actual_tools)
```

二者职责不同：

```text
bind_tools()
→ 将名称、描述和参数 Schema 提供给模型

ToolNode
→ 根据 tool_call 的名称和参数执行本地 Tool
```

复用同一份列表，可以降低两类配置不一致：

- 模型知道某个工具，但执行器没有；
- 执行器注册了工具，但模型不知道。

模型产生的内容类似：

```python
{
    "name": "calculator",
    "args": {"expression": "25 * 48"},
    "id": "call_xxx"
}
```

`ToolNode` 根据 `name` 查找工具，以 `args` 执行，并把结果和调用 ID 包装成 `ToolMessage`。

### 8. 测试范围与现有边界

工具单元测试直接执行：

```python
calculator.invoke({"expression": expr})
```

它不经过模型和状态图，只能验证：

- 参数能否被接收；
- 工具业务逻辑是否正确；
- 返回结果是否符合断言。

终端对话测试还会额外验证：

- 模型是否选择正确工具；
- 参数是否生成正确；
- LangGraph 是否正确路由；
- 工具结果是否被模型组织成最终答案。

当前测试存在两个典型边界：

1. 时间工具返回英文冒号 `:`，测试却替换中文冒号 `：`。解析失败后只断言结果非空，因此没有严格验证时间格式。
2. 计算器测试只验证几个危险表达式会失败，不能证明所有不可信输入都安全。

测试通过只能证明具体断言覆盖到的性质，不能自动证明完整系统正确或安全。

## 二、自测题与参考答案

### 1. `cyberclaw_tool` 是 CyberClaw 自己实现的装饰器吗？

不是。当前实现只是：

```python
cyberclaw_tool = langchain_core.tools.tool
```

它使用 LangChain 原生工具抽象。项目只是提供了自己的命名入口，尚未增加权限、日志、超时等额外行为。

### 2. 普通函数经过装饰后增加了哪些信息？

它变成了 Tool 对象，主要增加：

- 供模型识别的工具名称；
- 由 docstring 形成的工具描述；
- 由函数签名形成的参数 Schema；
- 统一的 `invoke()` 和 `ainvoke()` 执行接口。

原函数体仍然负责业务逻辑，但模型侧依赖的是结构化元数据。

### 3. 为什么测试调用 `calculator.invoke()`，而不是直接调用函数？

因为装饰后的 `calculator` 已经作为 LangChain Tool 使用。通过 `invoke()` 可以走统一参数解析、Schema 校验和工具执行接口，这与 `ToolNode` 使用的抽象一致。

直接测试内部原始函数只能证明函数体，不能证明 Tool 包装层是否正确。

### 4. 函数名、docstring 和类型标注分别有什么作用？

```text
函数名
→ 工具名称，供模型和 ToolNode 定位

docstring
→ 工具描述，指导模型何时调用以及如何使用

类型标注与默认值
→ 参数 Schema，定义参数类型、必填与可选关系
```

因此修改 docstring 或签名可能改变模型的工具选择和参数生成。

### 5. `bind_tools()` 和 `ToolNode` 有什么区别？

`bind_tools()` 把工具 Schema 绑定到模型请求，让模型能够返回结构化 `tool_calls`；它不执行本地代码。

`ToolNode` 保存可执行 Tool，根据模型返回的工具名称和参数调用本地代码，并产生 `ToolMessage`。

### 6. 为什么模型不能直接执行 Python？

模型服务通常运行在远端，只能根据上下文生成文本或结构化数据。它无法直接获得笔记本进程、文件系统和 Python 运行时权限。

本地 CyberClaw 收到 `tool_calls` 后，由 `ToolNode` 执行对应函数。权限边界和副作用实际位于本地执行层。

### 7. 装饰器模式和类模式分别适合什么场景？

装饰器模式适合简单、无状态、无需复杂初始化的工具，代码短且容易测试。

类模式适合需要数据库连接、客户端复用、内部状态、复杂配置或显式 Pydantic Schema 的工具。它提供更清晰的生命周期和扩展位置。

### 8. `_arun()` 使用 `asyncio.to_thread()` 意味着什么？

它把同步 `_run()` 调度到工作线程，使事件循环不必在当前线程等待同步函数执行。

这是一种异步兼容方案，不会把内部文件、网络或计算逻辑自动改造成原生异步实现；线程池容量、超时和取消语义仍需单独考虑。

### 9. 为什么定义了工具却仍可能无法被 Agent 调用？

工具还必须进入 `actual_tools`。默认内置工具需要加入 `BUILTIN_TOOLS`，动态工具则需要被 Skill Loader 返回。

如果只定义函数但没有注册：

- `bind_tools()` 不会把它告诉模型；
- `ToolNode` 也没有对应执行器；
- 默认 Agent 因而无法调用它。

### 10. 显式传入 `create_agent_app(tools=[...])` 会发生什么？

传入列表会完全替换：

```python
BUILTIN_TOOLS + dynamic_tools
```

因此只传入一个测试工具时，时间、计算器、文件和任务等默认工具都不会注册。这很适合隔离测试，但并不是“追加自定义工具”。

### 11. 为什么 docstring 不是可靠的安全控制？

docstring 会作为自然语言说明影响模型，但模型可能误解、忽略或被冲突上下文干扰。直接调用 Tool 也可以完全绕过模型。

真正的安全控制必须放在执行层，例如：

- 参数白名单；
- 权限判断；
- 用户审批令牌；
- 路径限制；
- 超时和资源预算；
- 操作审计。

### 12. `calculator` 为什么不能用于生产级不可信输入？

它使用 Python `eval()`。即使移除了普通内置函数，允许的语法和对象访问范围仍比数学运算更广，也没有限制表达式复杂度、运行时间和结果大小。

生产实现应解析 AST，只允许数字、括号和白名单运算符，同时限制长度、深度和计算资源。

### 13. `get_system_model_info` 能证明远端真实模型吗？

不能。它只读取本地的：

```text
DEFAULT_PROVIDER
DEFAULT_MODEL
```

这些值代表客户端配置。如果学校网关进行了别名映射或后端路由，该工具无法验证最终实际执行请求的模型。

### 14. `save_user_profile` 有什么覆盖风险？

它会整篇覆盖 `user_profile.md`。如果模型只传入新增的一句话，旧画像可能全部丢失。

更可靠的设计可以采用：

- 读取旧版本后做结构化合并；
- 写入前生成差异并请求确认；
- 保存版本和备份；
- 原子写入；
- 把画像拆成结构化字段，而不是整篇 Markdown 覆盖。

### 15. `save_user_profile` 的 docstring 与实际实现有什么不一致？

docstring 要求先调用 `read_user_profile`，但注册表中没有这个工具。实际由 `agent_node` 在每轮调用模型前直接读取画像，并加入系统提示词。

这说明工具说明也需要测试和一致性检查，不能默认注释一定与实现同步。

### 16. 工具单元测试和终端端到端测试分别证明什么？

工具单元测试直接 `.invoke()`，证明参数处理、业务逻辑和返回值。

终端端到端测试还经过模型、`tools_condition`、`ToolNode` 和第二次模型调用，能观察工具选择、参数生成、路由以及最终回答。

两者应同时存在：单元测试定位快、稳定；端到端测试覆盖真实协作链路，但成本和不确定性更高。

## 三、面试追问与回答思路

### 1. 怎样设计一套可扩展的 Agent 工具系统？

我会把工具拆成四层：

```text
Tool 定义层
→ 名称、描述、参数 Schema

注册层
→ 工具发现、版本、名称冲突检查

策略层
→ 权限、风险等级、审批和预算

执行层
→ 超时、重试、隔离、审计和结构化结果
```

简单工具使用装饰器，复杂工具使用类和依赖注入；模型侧和执行侧必须从同一注册表生成配置，避免 Schema 不一致。

### 2. 怎样避免高风险工具只依赖 Prompt 安全？

在执行前增加确定性策略检查：

```text
模型生成 tool_call
→ 校验参数
→ 计算风险等级
→ 必要时等待用户审批
→ 将审批绑定工具名和完整参数
→ 执行并审计
```

即使模型忽略 docstring，未取得有效审批的高风险调用也无法执行。

### 3. 如何重写安全计算器？

使用 `ast.parse(expression, mode="eval")` 解析表达式，只允许：

- 数字常量；
- `+ - * / // % **` 等白名单运算；
- 一元正负号；
- 合理的括号结构。

拒绝名称、属性访问、函数调用、容器和其他节点，并限制表达式长度、AST 深度、指数大小、执行时间和输出长度。

### 4. 同步工具放进异步 Agent 有什么风险？

同步网络、文件或计算任务可能阻塞事件循环，导致终端刷新、Heartbeat 和其他任务延迟。

可以：

- 优先实现原生异步 `_arun()`；
- 对同步 I/O 使用受控线程池；
- 对 CPU 密集任务使用进程池或独立服务；
- 增加超时、取消和并发上限；
- 记录排队与执行耗时。

### 5. 怎样管理工具注册和版本？

注册时应检查：

- 工具名称是否唯一；
- Schema 是否合法；
- 工具版本和兼容范围；
- 是否允许覆盖内置工具；
- Provider 是否支持所需 Schema；
- 风险等级和权限声明是否完整。

可以用显式 Registry 代替普通列表，并在启动时输出注册报告，遇到重复名称直接失败。

### 6. 怎样测试模型是否正确选择工具？

分三层测试：

```text
单元测试
→ 直接 invoke，验证工具业务逻辑

图集成测试
→ Fake ChatModel 固定返回 tool_calls，验证路由和 ToolMessage

模型评测
→ 使用真实模型和案例集，统计工具选择及参数准确率
```

真实模型评测需要固定输入集，分别记录正确调用、漏调用、误调用和参数错误，不能只靠人工尝试几个问题。

### 7. 工具应该返回字符串还是结构化结果？

字符串实现简单，模型容易直接阅读，但程序难以稳定区分成功、失败和错误类型。

更可靠的结果可以包含：

```json
{
  "ok": false,
  "code": "INVALID_EXPRESSION",
  "data": null,
  "message": "表达式格式错误"
}
```

执行层根据结构化字段决定重试、终止或请求用户补充；展示层再生成自然语言。

### 8. 如何提高工具系统的可观测性？

每次工具调用至少记录：

- `thread_id` 和 `tool_call_id`；
- 工具名称和经过脱敏的参数；
- 开始、结束时间和耗时；
- 成功、失败、超时或拒绝状态；
- 返回大小；
- 风险等级和审批信息。

日志应避免保存 API Key、密码和完整敏感文件内容，同时支持按一次 Agent 运行串联模型请求与工具调用。

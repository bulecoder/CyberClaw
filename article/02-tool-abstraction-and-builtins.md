# 第 2 课｜Python 函数怎样变成模型可以调用的工具

上一课已经看清了 CyberClaw 的图：

```text
START → agent → tools → agent → END
```

但是图只负责控制流程，它并不知道：

- 有哪些工具；
- 每个工具叫什么；
- 工具接收哪些参数；
- 模型如何知道应该传入 `expression` 或 `filepath`；
- Python 函数最终由谁执行。

这一课要回答的核心问题是：

> 一个普通 Python 函数，经过哪些步骤，才会成为模型能够选择、LangGraph 能够执行的 Tool？

本课对应：

- `cyberclaw/core/tools/base.py`
- `cyberclaw/core/tools/builtins.py`
- `cyberclaw/core/agent.py` 第 23～33 行
- `tests/test_builtins.py`

本课会看完 `builtins.py` 中所有工具的公开接口，但不会提前展开三个后续主题：

- 定时任务的 JSON 持久化与 Heartbeat 放到第 3 课；
- office 文件和 Shell 的安全边界放到第 4 课；
- 用户画像如何进入系统提示词放到第 6 课。

## 先建立完整心智模型

以 `calculator` 为例，完整链路是：

```text
普通 Python 函数
→ @cyberclaw_tool 装饰
→ 变成 LangChain Tool 对象
→ 放入 BUILTIN_TOOLS
→ bind_tools 把工具说明发给模型
→ 模型返回 calculator 的 tool_call
→ ToolNode 根据名称查找并执行工具
→ 工具结果变成 ToolMessage
→ 模型根据结果生成最终回答
```

这里最重要的是区分四个角色：

| 角色 | 负责什么 |
|---|---|
| Python 函数 | 编写真正的业务逻辑 |
| Tool 对象 | 保存名称、描述、参数 Schema 和执行入口 |
| 模型 | 根据名称、描述和 Schema 决定是否调用 |
| `ToolNode` | 根据模型生成的调用请求执行本地代码 |

模型本身不会执行 Python，也不会打开你电脑里的文件。模型只能生成一段结构化请求，真正的本地执行发生在 `ToolNode`。

## 第一部分：`cyberclaw_tool` 实际上是什么

先打开：

```text
cyberclaw/core/tools/base.py
```

第 1～5 行导入了两套工具定义所需的类型：

```python
from langchain_core.tools import BaseTool, tool
from abc import ABC, abstractmethod
import asyncio
from pydantic import BaseModel, Field
```

随后是：

```python
cyberclaw_tool = tool
```

这行非常关键。它说明 `cyberclaw_tool` 不是作者重新实现的一套工具系统，只是把 LangChain 原生的 `tool` 换了一个项目内名称。

也就是说：

```python
@cyberclaw_tool
def calculator(...):
    ...
```

在当前项目里，本质上等价于：

```python
from langchain_core.tools import tool

@tool
def calculator(...):
    ...
```

这样命名有一个工程上的好处：业务代码只依赖项目自己的公开名称。未来如果作者想在装饰过程中统一加入权限、日志或超时，可以修改 `base.py`，而不必逐个改工具。

但要注意当前事实：

> 现在的 `cyberclaw_tool` 只是别名，没有额外增加权限、审计、确认或沙盒能力。

## 装饰器做了什么

观察：

```python
@cyberclaw_tool
def calculator(expression: str) -> str:
    """
    一个简单的数学计算器。
    用于计算基础的数学表达式……
    """
```

装饰器会利用函数的三部分信息构造 Tool：

```text
函数名 calculator
→ 工具名

docstring
→ 工具描述

参数名 expression + 类型 str
→ 参数 JSON Schema
```

概念上，模型看到的内容接近：

```json
{
  "name": "calculator",
  "description": "一个简单的数学计算器……",
  "parameters": {
    "type": "object",
    "properties": {
      "expression": {
        "type": "string"
      }
    },
    "required": ["expression"]
  }
}
```

因此，工具的函数名、类型标注和 docstring 都不只是给程序员看的，它们还会影响模型的工具选择。

装饰完成以后，模块中的 `calculator` 已经不再只是原来的普通函数，而是一个 LangChain Tool 对象。项目和测试通过下面的统一入口执行它：

```python
calculator.invoke({"expression": "25 * 48"})
```

异步执行时对应：

```python
await calculator.ainvoke({"expression": "25 * 48"})
```

这也解释了测试为什么没有直接写：

```python
calculator("25 * 48")
```

## 第二部分：简单工具和类工具

`base.py` 提供了两种定义工具的方式。

### 方式一：装饰器模式

适合无状态、逻辑简单的函数：

```python
@cyberclaw_tool
def calculator(expression: str) -> str:
    ...
```

当前所有内置工具都使用这种方式。

### 方式二：继承类模式

复杂工具可以继承：

```python
class CyberClawBaseTool(BaseTool, ABC):
```

子类必须声明：

```python
name: str
description: str
args_schema: Type[BaseModel]
```

并实现同步执行逻辑：

```python
@abstractmethod
def _run(self, **kwargs: Any) -> Any:
    ...
```

这些字段分别解决：

| 字段 | 作用 |
|---|---|
| `name` | 模型和执行器识别工具的唯一名称 |
| `description` | 告诉模型何时使用它 |
| `args_schema` | 用 Pydantic 描述参数类型和约束 |
| `_run()` | 工具的同步业务逻辑 |
| `_arun()` | 工具的异步业务逻辑 |

当前基类给 `_arun()` 提供了默认实现：

```python
return await asyncio.to_thread(self._run, **kwargs)
```

它不是把同步代码变成真正的异步 I/O，而是把 `_run()` 放到线程中执行，避免直接阻塞事件循环。

适合类模式的场景包括：

- 工具需要数据库连接；
- 工具需要维护客户端对象；
- 工具有较复杂的初始化配置；
- 工具需要显式的 Pydantic 参数校验；
- 同一个工具需要维护内部状态。

`base.py` 底部注释中的 `AddTool` 给出了完整示例，但当前仓库没有任何实际内置工具继承 `CyberClawBaseTool`。`builtins.py` 虽然导入了它，却没有使用。

所以需要区分：

```text
项目提供了类模式扩展点
≠
项目当前已经使用类模式实现工具
```

## 第三部分：四个基础工具

打开：

```text
cyberclaw/core/tools/builtins.py
```

### 1. `get_current_time`

位置：第 52～59 行。

```python
@cyberclaw_tool
def get_current_time() -> str:
    ...
    now = datetime.now()
    return f"当前本地系统时间是: {now.strftime('%Y-%m-%d %H:%M:%S')}"
```

它没有输入参数，返回操作系统的本地时间。

边界也很明确：

- 它依赖笔记本系统时钟；
- 没有返回时区；
- 不会访问网络校时；
- 系统时间配置错误时，结果也会错误。

它存在的意义不是 Python 不会读取时间，而是大模型自身并不知道程序运行机器此刻的真实时间。这个工具把外部实时状态提供给模型。

### 2. `calculator`

位置：第 62～76 行。

```python
result = eval(expression, {"__builtins__": {}}, {})
```

它接收一个字符串表达式，通过 `eval()` 求值。清空 `__builtins__` 能阻止普通的 `__import__()` 等直接调用，因此测试中的下面几项会进入异常分支：

```text
2 +
1 / 0
__import__('os')
import os
eval('2+2')
```

但源码自己已经明确警告：这仍然不应被视为生产级安全计算器。`eval()` 解析的是 Python 表达式，攻击面和可接受语法都比真正的数学表达式大。

更可靠的改造方向是：

- 使用 AST，只允许数字和白名单运算符；
- 使用专门的数学表达式解析器；
- 限制表达式长度、运算复杂度和结果大小。

还有一个容易忽略的细节：异常被捕获后，工具返回错误字符串，而不是继续抛出异常。

```python
except Exception as e:
    return f"计算出错……{str(e)}"
```

因此从 `ToolNode` 的角度看，这次工具调用通常已经正常返回，只是返回内容表达了失败。模型需要阅读这段内容并决定怎样回复用户。

### 3. `get_system_model_info`

位置：第 20～32 行。

它读取：

```python
DEFAULT_PROVIDER
DEFAULT_MODEL
```

因此它返回的是 CyberClaw 当前配置的 Provider 名称和模型 ID，而不是向远端 API 查询模型真实身份。

例如 `.env` 中写了：

```text
DEFAULT_PROVIDER=openai
DEFAULT_MODEL=SDU-AI/DeepSeek-V4-Flash
```

它就会返回这两个配置值。即使远端网关后来进行了模型路由，这个工具也无法验证最终实际运行的是哪个模型。

### 4. `save_user_profile`

位置：第 35～49 行。

它把完整的 `new_content` 覆盖写入：

```text
workspace/memory/user_profile.md
```

这不是追加记忆，而是整篇覆盖：

```python
with open(PROFILE_PATH, "w", encoding="utf-8") as f:
    f.write(new_content)
```

这意味着模型必须先掌握旧画像，再生成合并后的完整画像，否则可能丢失旧内容。

这里还有一个源码与说明不一致的地方。docstring 要求模型：

```text
请先调用 read_user_profile
```

但 `BUILTIN_TOOLS` 中并不存在 `read_user_profile`。当前真实做法是 `agent_node` 每轮直接读取画像文件，并把内容放进系统提示词。具体机制留到第 6 课。

这一处说明了：

> docstring 也可能过时或写错，必须以工具注册表和实际代码为准。

## 第四部分：任务 CRUD 的工具接口

`builtins.py` 还定义了四个任务工具：

| 工具 | 输入 | 作用 |
|---|---|---|
| `schedule_task` | 时间、描述、重复方式、次数 | 新建任务 |
| `list_scheduled_tasks` | 无 | 查询任务 |
| `delete_scheduled_task` | `task_id` | 删除任务 |
| `modify_scheduled_task` | `task_id`、新时间、新描述 | 修改任务 |

它们共同操作：

```text
workspace/tasks.json
```

并通过：

```python
tasks_lock = threading.Lock()
```

保护同一进程内的读写临界区。

本课先关注工具接口上的两个问题。

### 类型标注决定参数形状

例如：

```python
def schedule_task(
    target_time: str,
    description: str,
    repeat: str = None,
    repeat_count: int = None
) -> str:
```

其中：

- `target_time` 和 `description` 是必填参数；
- `repeat` 和 `repeat_count` 有默认值，是可选参数；
- 模型必须生成符合该形状的结构化参数。

### docstring 中的规则不等于代码强制规则

`schedule_task` 的 docstring 要求时间有歧义时必须先询问用户；删除和修改工具的 docstring 也要求模型在多个匹配项存在时先确认。

这些内容会发给模型，属于模型行为指导。但函数本身并不知道用户之前是否确认过：

```python
delete_scheduled_task.invoke({"task_id": "task1"})
```

只要 ID 存在，代码就会执行删除。

类似地，docstring 说 `repeat` 只能是：

```text
hourly / daily / weekly
```

但 `schedule_task` 本身没有验证这个白名单。

所以这里存在两层约束：

```text
docstring 中的自然语言规则
→ 希望模型主动遵守

函数内部的参数校验和权限检查
→ 程序强制执行
```

高风险操作不能只依赖第一层。第 3 课会继续分析任务文件、重复规则和 Heartbeat。

## 第五部分：工具是怎样注册的

文件底部的 `BUILTIN_TOOLS` 是内置工具注册表：

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

这里一共有 12 个工具，分成五类：

| 类型 | 工具 |
|---|---|
| 环境信息 | `get_current_time`、`get_system_model_info` |
| 计算 | `calculator` |
| 长期画像 | `save_user_profile` |
| office 操作 | 列出、读取、写入、执行 Shell |
| 定时任务 | 新建、查询、删除、修改 |

定义了工具但没有放进这个列表，默认 Agent 就看不到它，也无法通过 `ToolNode` 执行它。

注册过程发生在：

```python
from .tools.builtins import BUILTIN_TOOLS
```

导入 `builtins.py` 时会：

1. 导入 office 工具；
2. 执行每个 `@cyberclaw_tool` 装饰器；
3. 创建 12 个 Tool 对象；
4. 构造 `BUILTIN_TOOLS` 列表。

这也解释了上一课调试时，为什么程序还没出现输入框，就会命中函数定义行上的断点：模块导入阶段正在创建 Tool 对象，并不代表工具已经被用户调用。

## 第六部分：同一份工具列表为什么使用两次

回到：

```text
cyberclaw/core/agent.py
```

第 23～27 行决定实际工具集合：

```python
if tools is None:
    dynamic_tools = load_dynamic_skills()
    actual_tools = BUILTIN_TOOLS + dynamic_tools
else:
    actual_tools = tools
```

默认情况下：

```text
实际工具 = 内置工具 + 动态 Skill 工具
```

但如果调用者显式传入 `tools`，它会完全替换默认工具，而不是追加到默认列表。这个设计方便测试创建最小工具集合，但调用者如果只传一个自定义工具，也会同时移除全部内置工具。

随后，同一份 `actual_tools` 被使用两次：

```python
tool_node = ToolNode(actual_tools)
llm_with_tools = llm.bind_tools(actual_tools)
```

两者职责不同：

```text
bind_tools(actual_tools)
→ 把名称、描述和参数 Schema 告诉模型

ToolNode(actual_tools)
→ 保存真正可执行的工具对象
```

使用同一份列表能够避免：

- 模型知道某个工具，但执行器中没有；
- 执行器注册了工具，但模型根本不知道；
- 两边工具参数版本不一致。

## 第七部分：测试证明了什么

打开：

```text
tests/test_builtins.py
```

### 测试绕过了模型

例如：

```python
result = calculator.invoke({"expression": expr})
```

这里没有调用 DeepSeek，也没有运行 LangGraph。测试直接执行 Tool 对象，所以它验证的是：

```text
工具参数能否被接收
→ Python 业务逻辑是否正确
→ 返回字符串是否符合预期
```

它不验证：

- 模型是否会在正确时机选择工具；
- 模型是否会生成正确参数；
- ToolNode 路由是否正常；
- 最终自然语言答案是否正确。

这些属于 Agent 集成测试或端到端测试。

### 当前基础测试覆盖

基础工具部分验证了：

- `get_current_time` 返回包含时间提示；
- `calculator` 支持加、乘、除、幂和取模；
- 非法表达式会返回“计算出错”；
- `save_user_profile` 会创建并完整写入画像文件；
- `get_system_model_info` 会读取环境变量。

任务 CRUD 测试会在第 3 课结合 Heartbeat 一起分析。

### 测试通过不代表实现没有问题

`test_get_current_time` 中有一个细节：

```python
time_str = result.replace("当前本地系统时间是：", "").strip()
```

测试尝试替换的是中文冒号 `：`，实际工具返回的是英文冒号 `:`。因此替换没有成功，时间解析会失败，然后测试退化到：

```python
self.assertTrue(len(time_str) > 0)
```

结果是：只要返回了非空字符串，测试就可能通过。它没有严格证明时间格式正确。

同样，计算器测试中的几个危险表达式失败，也不能证明任意输入都安全。

这两个例子体现了一条重要原则：

> 测试通过，只能证明测试断言覆盖到的性质，不能自动证明更大的安全性和正确性。

## 本课源码阅读顺序

不要从 `builtins.py` 第一行机械读到最后一行。按下面的调用关系阅读：

1. `base.py` 第 1～9 行：确认 `cyberclaw_tool` 的来源；
2. `base.py` 第 12～38 行：理解类模式的同步、异步入口；
3. `builtins.py` 第 52～76 行：先看最简单的时间和计算工具；
4. `builtins.py` 第 20～49 行：看环境信息和画像覆盖写；
5. `builtins.py` 第 79～272 行：先只整理任务工具的参数、返回值和规则；
6. `builtins.py` 第 275～288 行：数清 12 个注册工具；
7. `agent.py` 第 23～33 行：连接注册表、模型 Schema 和执行器；
8. `tests/test_builtins.py` 第 18～82 行、第 147～181 行：对照基础工具测试；
9. 最后再快速浏览任务测试的名称，内部细节留到第 3 课。

读完以后，应该能独立画出：

```text
@cyberclaw_tool
→ Tool 对象
→ BUILTIN_TOOLS
→ actual_tools
→ bind_tools + ToolNode
```

## 本课 VS Code 调试实验

### 实验一：观察 Tool 元数据

在 `agent.py` 第 30 行设置断点：

```python
tool_node = ToolNode(actual_tools)
```

用 VS Code 启动 `entry/main.py` 调试。程序会在初始化阶段暂停，这是本实验期望的现象。

在左侧 Variables 中展开 `actual_tools`，或者在 Debug Console 查看：

```python
[t.name for t in actual_tools]
```

然后查看计算器的参数 Schema：

```python
[t for t in actual_tools if t.name == "calculator"][0].args_schema.model_json_schema()
```

你需要亲眼确认：

- 工具是对象，不是只有函数体；
- `actual_tools` 中能看到 12 个内置工具和可能存在的动态 Skill；
- `calculator` 的必填参数是字符串 `expression`。

完成后取消第 30 行断点，避免每次启动都停在初始化阶段。

### 实验二：观察结构化参数进入函数

设置两个断点：

- `agent.py` 第 141 行；
- `builtins.py` 第 73 行。

启动后输入：

```text
请不要口算，必须调用 calculator 计算 (123456 * 789) + 321
```

第一次停在第 141 行时观察：

```python
response.tool_calls
```

你应该看到工具名和参数：

```text
name = calculator
args.expression = (123456 * 789) + 321
```

按 `F5` 后，程序会进入 `calculator`。此时 Locals 中应该出现：

```text
expression = "(123456 * 789) + 321"
```

继续执行后，工具结果变成 `ToolMessage`，Agent 再次调用模型并组织最终答案。

### 实验三：从 VS Code 图形界面运行单元测试

点击左侧烧杯形状的 Testing 面板。如果还没有发现测试：

1. 打开命令面板；
2. 选择 `Python: Configure Tests`；
3. 选择 `unittest`；
4. 测试目录选择 `tests`；
5. 文件模式选择 `test_*.py`。

先只运行：

```text
test_get_current_time
test_calculator_valid_expressions
test_calculator_invalid_expression
```

在测试名称右侧点击调试图标，可以让测试在 `builtins.py` 第 58 或第 73 行的断点停下。

这条路径没有模型参与，可以用来证明：

```text
Tool 本身可以独立运行
Agent 只是 Tool 的调用者之一
```

## 当前实现值得肯定的地方

1. 简单工具用装饰器，定义成本低；
2. 提供了类模式扩展点，可容纳复杂依赖；
3. 模型侧与执行侧复用同一份工具列表；
4. 工具可以脱离模型单独测试；
5. 业务异常多数转换成文本，模型能够继续解释失败原因；
6. 关键工具 docstring 明确描述了使用时机。

## 当前实现的主要边界

1. `cyberclaw_tool` 当前没有增加权限、审批、超时和审计；
2. `calculator` 使用 `eval()`，不适合生产环境；
3. 时间工具没有时区和可靠校时；
4. `get_system_model_info` 只能报告配置，不能验证远端真实模型；
5. `save_user_profile` 是覆盖写，错误调用可能丢失旧画像；
6. docstring 中的确认规则并非程序强制规则；
7. `read_user_profile` 在说明中出现，但没有注册对应工具；
8. 工具注册表是手工维护的，漏加就无法被 Agent 使用；
9. 显式传入 `tools` 会替换全部默认工具，调用方需要知道这一语义；
10. 当前测试没有完整覆盖 Schema、权限、安全输入和 Agent 选工具能力。

## 写 notes 前的自测题

1. `cyberclaw_tool` 是 CyberClaw 自己实现的装饰器吗？
2. 普通 Python 函数经过装饰以后，多出了哪些与模型有关的信息？
3. 为什么测试使用 `calculator.invoke({...})`，而不是直接调用函数？
4. `bind_tools()` 和 `ToolNode` 分别保存或使用工具的哪一部分？
5. 为什么模型知道工具描述，却不能自己执行本地 Python？
6. 装饰器模式和继承 `CyberClawBaseTool` 分别适合什么场景？
7. `_arun()` 使用 `asyncio.to_thread()` 意味着什么？它是真正的异步 I/O 吗？
8. 只定义一个工具但不加入 `BUILTIN_TOOLS`，默认 Agent 能调用它吗？
9. 为什么 docstring 中写“删除前必须确认”仍然不是可靠的安全控制？
10. `calculator` 的无效输入测试通过，为什么不能证明它是安全的？
11. `get_system_model_info` 能否证明远端实际使用的就是该模型？
12. 显式传入 `create_agent_app(tools=[...])` 时，默认内置工具还在吗？
13. 当前 `save_user_profile` 的说明与实际工具集合有什么不一致？
14. 单元测试直接 `.invoke()` 与从终端对话测试分别验证什么？

## 本课完成标准

完成本课后，你应该能不用看文章讲清：

```text
函数名、docstring、类型标注
→ Tool 的名称、描述和参数 Schema
→ BUILTIN_TOOLS 注册
→ bind_tools 告诉模型
→ ToolNode 负责执行
```

还要能指出三个真实边界：

- 模型行为规则不等于代码安全控制；
- 工具单元测试不等于 Agent 端到端测试；
- LangChain Tool 抽象来自依赖库，不是 CyberClaw 自研协议。

达到这些标准后，再写：

```text
notes/02-tool-abstraction-and-builtins.md
```

下一课会沿着 `schedule_task` 创建出的 JSON 记录继续追踪：任务怎样被 Heartbeat 扫描、触发、续期，并重新注入 Agent。

# 第 5 课｜模型 Provider、配置与 OpenAI 兼容协议

> 主要源码：`cyberclaw/core/provider.py`、`entry/cli.py`  
> 辅助源码：`cyberclaw/core/config.py`、`entry/main.py`、`.env.example`

## 一、本课要解决的问题

CyberClaw 的 Agent 图并不直接依赖某一家模型厂商，而是依赖 LangChain 的统一聊天模型接口。

本课要看懂的是：

```text
.env 中的配置
        ↓
get_provider() 选择模型适配器
        ↓
ChatOpenAI / ChatAnthropic / ChatOllama
        ↓
bind_tools() 与 invoke()
        ↓
具体模型服务
```

学完后应当能够解释：

1. Provider、Model ID、Base URL 和 API Key 分别控制什么；
2. 为什么学校提供的 DeepSeek 网关应选择 `other`，而不是寻找一个叫 `deepseek` 的 Provider；
3. 配置向导如何验证并写入 `.env`；
4. 更换模型为什么不会自动删除原来的对话记忆；
5. 当前多模型支持有哪些真实边界。

## 二、阅读顺序

按下面顺序对照源码：

1. `.env.example`
2. `cyberclaw/core/provider.py` 的 `COMPATIBLE_BASE_URLS`
3. `cyberclaw/core/provider.py` 的 `get_provider()`
4. `entry/cli.py` 的 `config_wizard()`
5. `entry/cli.py` 的 `run_agent()`
6. `entry/main.py` 中创建 `llm` 的位置
7. `cyberclaw/core/agent.py` 中 `llm.bind_tools(tools)`

这个顺序是从配置数据出发，沿着对象创建过程一直走到 Agent 真正使用模型。

## 三、先分清四个概念

### 1. Provider：选择客户端适配方式

`DEFAULT_PROVIDER` 并不一定等于模型真正所属的厂商。

它首先决定 `get_provider()` 使用哪一种 LangChain 客户端，以及读取哪组环境变量：

| Provider | LangChain 客户端 | 主要密钥变量 |
|---|---|---|
| `openai` | `ChatOpenAI` | `OPENAI_API_KEY` |
| `aliyun` / `dashscope` | `ChatOpenAI` | `OPENAI_API_KEY` |
| `z.ai` | `ChatOpenAI` | `OPENAI_API_KEY` |
| `tencent` | `ChatOpenAI` | `OPENAI_API_KEY` |
| `other` | `ChatOpenAI` | `OPENAI_API_KEY` |
| `anthropic` | `ChatAnthropic` | `ANTHROPIC_API_KEY` |
| `ollama` | `ChatOllama` | 不要求云端密钥 |

所以 Provider 更接近“协议和客户端适配器选择”，而不是模型品牌标签。

### 2. Model ID：告诉服务端调用哪个模型

`DEFAULT_MODEL` 会传给模型客户端的 `model` 或 `model_name` 参数。

例如学校平台展示的：

```text
SDU-AI/DeepSeek-V4-Flash
```

就应当作为完整的 Model ID 使用。大小写、斜杠和前缀都可能是服务端路由的一部分，不能擅自改成：

```text
deepseek-v4-flash
```

### 3. Base URL：请求发到哪里

`OPENAI_API_BASE` 决定兼容 OpenAI 协议的请求被发送到哪个网关。

它解决的是网络路由问题：

```text
ChatOpenAI
   ↓ HTTP
学校网关 / 阿里云网关 / OpenAI 官方接口
```

Base URL 不是模型名称，也不是 API Key。

### 4. API Key：向目标网关证明身份

`OPENAI_API_KEY` 会被放进客户端请求的认证信息中。

同一套 OpenAI 兼容协议可以由不同网关提供，因此：

- OpenAI 官方 Key 只应交给 OpenAI 官方接口；
- 学校平台发放的 Key 应交给学校网关；
- 不要把真实 Key 写入 README、notes、Git 提交或截图。

## 四、`get_provider()` 是一个模型工厂

### 1. 统一输入

函数签名为：

```python
def get_provider(
    provider_name: str = "openai",
    model_name: str = "gpt-4o-mini",
    temperature: float = 0.0,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs: Any
) -> BaseChatModel:
```

它把不同厂商的创建过程封装成同一个入口，并承诺返回 `BaseChatModel`。

Agent 后续只需要使用统一接口：

```python
llm.bind_tools(...)
llm.invoke(...)
```

不必知道底层究竟是哪家服务。

### 2. OpenAI 兼容分支

下面这些 Provider 共用一个分支：

```python
["openai", "aliyun", "dashscope", "z.ai", "tencent", "other"]
```

它们都创建：

```python
ChatOpenAI(...)
```

这并不是说阿里云或学校模型变成了 OpenAI 模型，而是说它们公开了与 OpenAI 请求格式兼容的接口，因而可以复用同一个客户端。

### 3. 参数优先级

API Key 的优先级是：

```text
函数参数 api_key
    > 环境变量 OPENAI_API_KEY
```

Base URL 的优先级是：

```text
函数参数 base_url
    > 环境变量 OPENAI_API_BASE
    > COMPATIBLE_BASE_URLS 中的 Provider 默认地址
```

如果 Provider 是 `openai`，且没有显式 Base URL，也没有环境变量，字典中同样没有默认值。此时 `None` 交给 `ChatOpenAI`，由客户端采用官方默认地址。

`other` 没有内置默认地址，所以必须由使用者配置兼容网关地址。

### 4. Provider 名称会先规范为小写

```python
provider_name = provider_name.lower()
```

因此 `OPENAI` 与 `openai` 的分支选择相同。

但 Model ID 没有被转成小写，因为它必须按服务端要求原样传递。

### 5. 不支持的 Provider 会立即失败

如果名称不在已有分支中：

```python
raise ValueError(f"不支持的模型提供商: {provider_name}")
```

当前代码没有 `deepseek` 分支。因此把：

```text
DEFAULT_PROVIDER=deepseek
```

写进 `.env` 会直接报“不支持的模型提供商”，即使 Base URL 和 Key 都正确。

## 五、学校 DeepSeek 网关应该怎样理解

学校平台提供的是：

```text
网关地址：学校给出的 API 接口地址
模型 ID：SDU-AI/DeepSeek-V4-Flash
认证：学校发放的 API Key
协议：OpenAI compatible
```

在 CyberClaw 中，最能表达这个事实的配置是：

```dotenv
DEFAULT_PROVIDER=other
DEFAULT_MODEL=SDU-AI/DeepSeek-V4-Flash
OPENAI_API_KEY=你的学校平台密钥
OPENAI_API_BASE=学校提供的兼容接口地址
```

这里选择 `other` 的原因是：

1. 服务不是 OpenAI 官方服务；
2. 它又使用 OpenAI 兼容协议；
3. `other` 正好进入 `ChatOpenAI` 兼容分支；
4. `other` 不附带其他厂商的默认 Base URL，避免误发到错误网关。

选择 `openai` 并提供自定义 Base URL 在技术上也可能工作，但语义上不如 `other` 清楚。

### 工具调用还需要网关具备额外能力

普通对话成功，只能证明基本 Chat Completions 兼容。

CyberClaw 还会执行：

```python
llm_with_tools = llm.bind_tools(tools)
```

因此目标模型和网关还需要兼容工具定义、工具调用返回结构以及多轮 ToolMessage 流程。一个接口“OpenAI compatible”，不一定意味着所有高级能力都完全兼容。

## 六、配置是怎样被加载的

### 1. `load_dotenv()` 的作用

`provider.py` 和 `config.py` 在导入时都会调用：

```python
load_dotenv()
```

它会寻找 `.env` 并把其中的键加入当前 Python 进程的环境变量。

默认情况下，已有进程环境变量的优先级更高，`.env` 不会覆盖它们。也就是说，如果终端里已有：

```powershell
$env:OPENAI_API_BASE = "旧地址"
```

那么只修改 `.env` 后，当前进程仍可能继续使用终端里的旧值。

配置来源的实际关系是：

```text
当前进程环境变量
        ↑ 默认不被覆盖
.env 由 load_dotenv() 加载
```

### 2. 为什么 `.env` 必须使用 UTF-8

`python-dotenv` 会按文本编码读取 `.env`。如果文件被保存成 ANSI、GBK 或其他不兼容编码，又含有中文注释，就可能出现：

```text
UnicodeDecodeError
```

`PYTHONUTF8=1` 不能把一个已经是错误编码的文件自动转换为 UTF-8。正确做法是让 `.env` 本身以 UTF-8 保存。

### 3. `config.py` 读取的是工作区配置

`config.py` 关注：

```text
CYBERCLAW_WORKSPACE
```

它决定数据库、记忆、office、skills 和任务文件的根目录。

模型配置和工作区配置是两套不同问题：

```text
Provider / Model / Key / Base URL → 调哪个模型
CYBERCLAW_WORKSPACE               → 本地数据存在哪里
```

## 七、配置向导完整流程

`cyberclaw config` 对应 `config_wizard()`。

### 1. 收集 Provider

用户看到的是带说明的选项，例如：

```text
other (openai compatible)
```

代码通过：

```python
provider = provider_raw.split(" ")[0].strip()
```

最终得到：

```text
other
```

同时通过选项文字中是否包含 `openai`，计算：

```python
is_openai_compatible
```

### 2. 收集 Model、Key 和 Base URL

对于 `other`：

```text
env_key = OPENAI_API_KEY
Base URL = 用户输入的兼容地址
```

对于 `ollama`，向导不要求云端 Key。

### 3. 先临时写入当前进程

向导先执行类似：

```python
os.environ["OPENAI_API_KEY"] = api_key
os.environ["OPENAI_API_BASE"] = base_url
```

这一步只改变当前 CLI 进程，目的是让后面的探测调用立即使用新配置。

### 4. 创建模型并发送探测请求

```python
llm = get_provider(
    provider_name=provider,
    model_name=model_name
)
response = llm.invoke(
    [HumanMessage(content="回复我'收到'。")]
)
```

只有请求成功，向导才继续写 `.env`。

这能发现：

- Key 无效；
- Base URL 错误；
- Model ID 不存在；
- 网络不可达；
- 客户端与接口不兼容。

但它只验证了一次普通模型调用，没有完整验证工具调用和整个 Agent 图。

### 5. 清理旧 Base URL，再保存新配置

向导先删除三类 Base URL：

```text
OPENAI_API_BASE
ANTHROPIC_BASE_URL
OLLAMA_BASE_URL
```

再写入当前选择所需的 Key、Base URL、Provider 和 Model。

这样可以减少从一个协议切换到另一个协议时误用旧地址。

不过它没有删除所有旧 Provider 的密钥。例如从 Anthropic 切换到 OpenAI 兼容服务后，旧的 `ANTHROPIC_API_KEY` 仍可能留在 `.env`。它暂时不会被当前分支使用，但增加了密钥管理负担。

## 八、`cyberclaw run` 如何消费配置

`run_agent()` 先显式执行：

```python
load_dotenv(ENV_PATH)
```

然后读取：

```text
DEFAULT_PROVIDER
DEFAULT_MODEL
```

对于云端 Provider，还检查对应 Key 是否存在。

配置初步完整后，它才延迟导入：

```python
import entry.main as cyberclaw_main
```

`entry/main.py` 再次读取环境变量，并创建模型：

```python
llm = get_provider(
    provider_name=DEFAULT_PROVIDER,
    model_name=DEFAULT_MODEL
)
```

最后把 `llm` 交给 `create_agent()`，在其中通过：

```python
llm.bind_tools(tools)
```

生成真正参与图循环的模型对象。

## 九、直接修改 `.env` 后会发生什么

如果文件编码正确、变量名正确、当前终端没有同名旧环境变量，那么可以直接修改 `.env`，重新启动 `cyberclaw run`。

新启动的 Python 进程会重新加载配置并创建新的模型客户端。

需要注意：

1. 正在运行的进程不会因为文件改变而自动重建模型；
2. 修改后必须退出并重新启动；
3. 手工修改不会像配置向导一样先发探测请求；
4. 重复键、引号、空格或错误编码都可能造成解析问题；
5. 不应把 `.env` 提交到 Git。

## 十、更换模型后，之前的记忆为什么还在

模型配置保存在：

```text
项目根目录/.env
```

对话和用户数据保存在另外的位置：

```text
workspace/state.sqlite3
workspace/memory/user_profile.md
workspace/tasks.json
workspace/office/
```

换模型只会改变下一次启动时创建的 `llm`，不会自动删除这些文件。

因此：

```text
模型可以换
thread_id 不变
checkpoint 数据库不变
→ 历史消息通常仍可恢复
```

新的模型会继续读取旧模型留下的历史消息和摘要。不同模型对工具调用、角色消息和上下文长度的兼容程度不同，所以“数据还在”不等于“所有行为完全一致”。

## 十一、当前实现的工程边界

### 1. 多模型是“分支支持”，不是完全即插即用

每个分支还依赖相应安装包。

当前 `requirements.txt` 包含 `langchain-openai`，但没有完整声明：

```text
langchain-anthropic
langchain-community
```

因此 Anthropic 或 Ollama 分支可能在导入客户端时失败。代码里有分支，不等于安装环境已经具备全部依赖。

### 2. 兼容协议不保证工具调用兼容

配置向导只发普通消息。要证明模型适合 CyberClaw，还需要它支持工具绑定和工具调用消息格式。

### 3. 导入时加载环境带来隐式行为

`provider.py` 在模块导入阶段就执行 `load_dotenv()`。这很方便，但使配置加载时间和优先级不够显式，也给测试隔离带来困难。

更清晰的工程方式通常是：

```text
入口统一加载配置
→ 校验并构造 Settings 对象
→ 显式传给 provider factory
```

### 4. 全局环境变量容易残留

配置向导会临时修改当前进程的 `os.environ`。如果探测失败或在同一进程中多次配置，旧值和新值之间可能出现状态残留。

### 5. 错误信息存在表述问题

`_show_boot_error()` 中写着：

```text
检测到 API Key、模型或Baseurl
```

从调用条件看，真实含义应当是“未检测到”或“配置不完整”。学习源码时应以控制流为事实来源，而不是只相信提示文案。

## 十二、本课调用链总结

配置向导：

```text
cyberclaw config
→ Typer 分发到 config_wizard()
→ 收集 Provider / Model / Key / Base URL
→ 临时写入 os.environ
→ get_provider()
→ llm.invoke() 探测
→ 成功后 set_key() 写入 .env
```

正常启动：

```text
cyberclaw run
→ load_dotenv(.env)
→ 校验 Provider / Model / Key
→ entry.main
→ get_provider()
→ ChatModel
→ create_agent(llm)
→ llm.bind_tools(tools)
→ Agent 开始对话
```

学校模型配置：

```text
DEFAULT_PROVIDER=other
DEFAULT_MODEL=SDU-AI/DeepSeek-V4-Flash
OPENAI_API_BASE=学校兼容接口地址
OPENAI_API_KEY=学校发放的密钥
```

## 十三、学完本课应能回答

1. 为什么 Provider 不一定等于模型厂商名称？
2. `other` 为什么可以调用学校提供的 DeepSeek 模型？
3. API Key 和 Base URL 的函数参数、进程变量、`.env` 各有什么优先级？
4. 配置向导成功意味着什么，又没有证明什么？
5. 为什么修改 `.env` 后需要重启？
6. 为什么换模型不会自动清空历史对话？
7. 当前 Anthropic 和 Ollama 支持为什么可能在运行时失败？


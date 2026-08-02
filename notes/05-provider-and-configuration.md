# 05｜模型 Provider、配置与 OpenAI 兼容协议

> 对应源码：`cyberclaw/core/provider.py`、`entry/cli.py`  
> 辅助源码：`cyberclaw/core/config.py`、`entry/main.py`、`.env.example`

## 一、本课核心内容

### 1. 四个配置概念

```text
Provider → 选择哪一种客户端和协议适配分支
Model ID → 告诉服务端实际调用哪个模型
Base URL → 决定请求发往哪个网关
API Key  → 向该网关证明调用身份
```

Provider 不是简单的模型品牌。CyberClaw 中的 `openai`、`aliyun`、`z.ai`、`tencent` 和 `other` 都复用 `ChatOpenAI`，因为这些服务使用 OpenAI 兼容协议。

### 2. Provider 工厂

`get_provider()` 根据 `provider_name` 创建统一的 `BaseChatModel`：

```text
OpenAI 兼容分支 → ChatOpenAI
Anthropic       → ChatAnthropic
Ollama          → ChatOllama
```

Agent 只依赖统一接口：

```python
llm.bind_tools(tools)
llm.invoke(messages)
```

所以模型创建细节被隔离在 Provider 工厂中。

### 3. 学校 DeepSeek 的配置含义

学校平台提供的是一个 OpenAI 兼容网关，因此应配置：

```dotenv
DEFAULT_PROVIDER=other
DEFAULT_MODEL=SDU-AI/DeepSeek-V4-Flash
OPENAI_API_BASE=学校提供的接口地址
OPENAI_API_KEY=学校提供的密钥
```

`other` 表示“未知厂商但兼容 OpenAI 协议”。完整 Model ID 应按平台展示原样填写。

### 4. 参数优先级

Key：

```text
显式 api_key 参数 > 进程环境变量 OPENAI_API_KEY
```

Base URL：

```text
显式 base_url 参数
> 进程环境变量 OPENAI_API_BASE
> Provider 内置默认地址
```

`load_dotenv()` 默认不会覆盖进程中已经存在的同名变量。因此终端里的旧环境变量可能压过刚修改的 `.env`。

### 5. 配置向导

```text
收集配置
→ 临时写入 os.environ
→ get_provider() 创建客户端
→ invoke() 发送探测消息
→ 成功后写入 .env
```

探测能验证 Key、地址、Model ID 和基本网络调用，但不能完整证明工具调用兼容。

### 6. 直接修改 `.env`

可以直接修改，但应满足：

- 文件为 UTF-8；
- 变量名和 Model ID 正确；
- 当前终端没有同名旧变量覆盖它；
- 修改后完全退出并重新启动 CyberClaw；
- `.env` 不提交到 Git。

### 7. 模型配置与记忆相互独立

```text
.env                         → 模型配置
workspace/state.sqlite3      → 图状态与消息 checkpoint
workspace/memory/            → 用户画像
workspace/tasks.json         → 定时任务
workspace/office/            → 文件产物
```

更换模型只会使下一次启动创建新的模型客户端，不会自动删除工作区数据。只要数据库和 `thread_id` 不变，原来的历史通常仍会被读取。

### 8. 当前实现边界

- `deepseek` 不是受支持的 Provider 名称；
- OpenAI 兼容不必然包含完整工具调用兼容；
- Anthropic 和 Ollama 所需的 LangChain 包没有完整写进当前依赖；
- `load_dotenv()` 在导入阶段执行，配置生命周期不够显式；
- 配置向导会留下不再使用的旧 Provider 密钥；
- 向导只验证普通调用，没有验证完整 Agent 图。

## 二、自测题与参考答案

### 1. Provider、Model ID、Base URL 和 API Key 有什么区别？

**参考答案：**

Provider 决定 CyberClaw 选择哪个客户端适配分支；Model ID 决定网关内部实际调用哪个模型；Base URL 决定 HTTP 请求发往哪里；API Key 用于向该目标网关认证。四者分别解决客户端、模型路由、网络地址和身份认证问题。

### 2. 为什么学校的 DeepSeek 应选择 `other`？

**参考答案：**

学校网关不是 OpenAI 官方服务，但它提供 OpenAI 兼容接口。`other` 会进入 `ChatOpenAI` 兼容分支，又不会自动附加阿里云、腾讯等其他厂商的默认地址，因此语义最准确。

### 3. 为什么不能把 Provider 写成 `deepseek`？

**参考答案：**

`get_provider()` 没有 `deepseek` 分支。函数会把名称转成小写后逐个匹配，匹配不到便抛出“不支持的模型提供商”。DeepSeek 是此处的模型，而 Provider 应选择它所使用的协议适配器。

### 4. `SDU-AI/DeepSeek-V4-Flash` 为什么要完整填写？

**参考答案：**

这是学校平台用于模型路由的完整 ID。前缀、斜杠和大小写可能参与服务端匹配。CyberClaw 不会替用户转换 Model ID，因此应按平台展示原样传入。

### 5. `other` 最终创建了什么对象？

**参考答案：**

它与 `openai`、`aliyun`、`z.ai`、`tencent` 一样，最终创建 `langchain_openai.ChatOpenAI`，只是使用用户提供的 Key、Base URL 和 Model ID。

### 6. `.env` 和终端环境变量冲突时通常谁优先？

**参考答案：**

当前代码调用 `load_dotenv()` 时没有设置 `override=True`，所以进程中已经存在的同名环境变量通常优先，`.env` 不会覆盖它。

### 7. 配置向导为什么先调用模型，再写 `.env`？

**参考答案：**

它先把用户输入临时放入当前进程，创建客户端并发送探测消息。只有连接成功才持久化，避免明显错误的 Key、地址或模型名称直接成为默认配置。

### 8. 配置向导成功是否证明工具调用一定可用？

**参考答案：**

不能。向导只执行一次普通 `invoke()`。CyberClaw 实际运行还会调用 `bind_tools()`，要求模型和网关支持工具定义及工具调用消息格式，这部分没有被向导探测。

### 9. 为什么修改 `.env` 后要重启？

**参考答案：**

运行中的 Agent 已经根据旧配置创建了模型对象。项目没有监视 `.env` 并热重建客户端的机制，所以新配置只会在下一次进程启动和加载环境时生效。

### 10. 更换模型后，历史对话为什么还在？

**参考答案：**

模型配置位于 `.env`，历史状态位于 `workspace/state.sqlite3`，二者生命周期不同。重启后新的模型继续使用相同数据库和固定 `thread_id`，所以能取得旧 checkpoint。

### 11. `PYTHONUTF8=1` 能修复一个 GBK 编码的 `.env` 吗？

**参考答案：**

不能。它会影响 Python 的 UTF-8 模式，但不会把磁盘上已有的字节自动转码。应当用编辑器把 `.env` 真正另存为 UTF-8。

### 12. 为什么代码里有 Anthropic 和 Ollama 分支，仍可能启动失败？

**参考答案：**

分支会在运行时导入 `langchain_anthropic` 或 `langchain_community`，而当前依赖文件没有完整声明这些包。源码分支存在不等于安装环境已具备对应依赖。

## 三、面试追问与回答思路

### 1. 你会怎样重构当前配置系统？

**回答思路：**

在程序入口统一加载 `.env` 和系统环境，使用结构化 Settings 对象完成类型校验、必填校验和敏感字段隐藏，再显式传给 Provider 工厂。这样能减少模块导入副作用，使优先级清晰，也更容易做单元测试。

### 2. 怎样判断一个 OpenAI 兼容网关真的适合 Agent？

**回答思路：**

不能只测普通聊天，还应验证流式输出、工具定义、工具调用参数、ToolMessage 回传、多轮调用、错误码、超时、上下文长度和并发限制。将这些检查做成 Provider contract test。

### 3. 怎样安全管理 API Key？

**回答思路：**

本地开发至少使用被 Git 忽略的 `.env`，日志和错误输出做脱敏；生产环境使用操作系统凭据库或密钥管理服务，按 Provider 分开权限并支持轮换。不能把真实密钥写入源码、文档、测试夹具或提交历史。

### 4. 怎样实现运行时切换模型？

**回答思路：**

需要把模型客户端从启动期全局对象改成可管理资源：配置变更后校验新 Provider，构造新客户端，重新绑定工具，并在没有进行中请求时原子替换。还要记录每轮使用的模型，处理不同模型对历史消息的兼容差异。

### 5. 多 Provider 工厂怎样避免大量 `if/elif`？

**回答思路：**

可以建立 Provider registry，将名称映射到构造函数、必填配置、默认 Base URL 和能力声明。新增 Provider 时注册适配器，而不是修改一个不断增长的条件分支。

### 6. 简历中如何准确描述这一部分？

**回答思路：**

可以描述“梳理并重构多模型配置链路，支持 OpenAI 兼容网关的 Provider 注册、配置校验和能力探测”。不要仅因原项目已有几个条件分支，就宣称自己“实现了全平台大模型适配”。


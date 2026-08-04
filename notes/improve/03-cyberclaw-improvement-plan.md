# CyberClaw 具体改进与包装方案

> 本文是设计与实施计划，不代表功能已经完成。  
> 当前阶段只审计和规划，没有修改 CyberClaw 项目源码。  
> 参考来源：CoreCoder、learn-claude-code，以及对 CyberClaw 自身源码、测试和文档的审计。

## 1. 标记说明

为避免混淆“借鉴”和“个人发现”，本文使用以下标记：

| 标记 | 含义 |
|---|---|
| `[CC]` | 主要受 CoreCoder 的实现或测试思想启发 |
| `[LCC]` | 主要受 learn-claude-code 的阶段设计启发 |
| `[CCL]` | 来自对 CyberClaw 自身 bug、不合理设计或文档漂移的审计 |
| `[NEW]` | 为适配 CyberClaw 定位而提出的组合或重新设计 |

一个改进可能同时有多个来源。例如“分层上下文”同时吸收 `[CC]` 的三级压缩和 `[LCC]` 的工具结果外置，再结合 CyberClaw 的 LangGraph checkpoint 重新实现，因此最终代码和验证才是我们的个人贡献。

## 2. 项目重新定位

### 2.1 CyberClaw 当前的作者定位与实际定位

CyberClaw 原 README 将项目描述为“企业级透明可控智能体”，主要强调全行为审计、零信任执行、持续学习、心跳任务、Skill 生态和 MCP 集成。

但根据当前实际源码，它更准确的定位是：

> 一个基于 LangGraph 的本地终端 Agent 学习原型，包含 OpenAI-compatible 模型接入、基础工具、SQLite checkpoint、定时任务、Markdown Skill 和 JSONL 日志。

它已经能够在个人电脑上完成对话、工具调用、文件读写、计算、画像保存和定时任务等操作，但现阶段还不能把“企业级”“严格安全沙盒”“零信任”“原生 MCP”和“完整决策追踪”当成已经实现的事实。

因此，改造前的 CyberClaw 应被理解为：

```text
产品形态：本地终端个人 Agent 原型
技术基础：LangGraph + LangChain Tools + SQLite checkpoint
主要特点：工具、记忆、定时任务、Skill、JSONL 日志
主要不足：权限主要靠 Prompt、单会话、状态边界不清、MCP 缺失、可观测性不完整
```

### 2.2 两个参考项目的定位

CoreCoder 的定位是：

> 一个用于读懂并继续二次开发的最小 Coding Agent，即“编程 Agent 里的 nanoGPT”。

它是可以运行的教学地基，重点展示 Agent Loop、工具协议、上下文压缩、会话、并行与受限子 Agent，不以替代 Claude Code 或成为通用个人助手为目标。

learn-claude-code 的定位是：

> 一个从最小循环逐步构建 Claude Code-like Agent Harness 的课程项目。

它主要教授 Tool、Permission、Hook、Context、Memory、Task、MCP 等 Harness 机制，不是面向普通最终用户的完整产品，也不能直接作为生产架构照搬。

两个项目对 CyberClaw 的作用不同：

```text
CoreCoder
→ 提供小而清晰、可以测试的运行时不变量

learn-claude-code
→ 提供 Agent Harness 从简单循环到完整能力的演进地图

CyberClaw
→ 提供实际二次开发基础：LangGraph、异步终端、checkpoint、工具和本地工作区
```

### 2.3 “本地个人 Agent”“Agent Harness”和“Agent Workbench”

这三个词描述的是不同层面，并不冲突：

| 概念 | 含义 | 在本项目中的体现 |
|---|---|---|
| 本地 Agent | Agent Runtime 和工具主要运行在用户电脑上 | 访问本地工作区、执行本地工具、保存本地会话和日志 |
| 个人 Agent | 面向单个用户，不处理企业多租户和组织级权限 | 用户自己的配置、会话、记忆和工作区 |
| Agent Harness | 模型周围负责工具、上下文、权限、状态和生命周期的工程系统 | Runtime、Tool Registry、Policy、Context、Memory、MCP、Trace |
| Agent Workbench | 将 Harness 能力提供给用户操作、配置和观察的产品形态 | CLI，以及未来可能增加的会话、审批、监控和能力管理界面 |

“本地”不等于必须使用本地大模型。当前通过学校提供的 OpenAI-compatible API 调用 `.env` 中选择的远程模型，模型推理发生在远程服务，但 CyberClaw 的 Runtime、工具和数据仍主要位于个人电脑上，所以仍属于本地 Agent。

### 2.4 改进后的技术定位、产品定位与参考场景

建议将二次开发后的技术定位定义为：

> **一个策略可控、会话可恢复、能力可扩展、运行可观测的本地 Agent Harness。**

建议将产品定位定义为：

> **一个面向个人开发者的可控本地 Agent Workbench。**

为了避免“通用平台”缺少明确应用场景，第一版使用“本地开发工作区助手”作为参考应用：

```text
目标用户：个人开发者、Agent 学习者和需要调试 Agent 行为的高级用户

核心问题：
模型操作本地文件和外部工具时不透明、不易控制、难以恢复、难以排错

参考任务：
读取和分析本地项目
→ 提出工具调用
→ 高风险操作请求用户审批
→ 执行文件或受限命令
→ 必要时调用真实 MCP 工具
→ 保存会话并展示完整执行轨迹

核心价值：
所有能力统一注册
所有副作用统一审批
所有运行统一追踪
所有会话可以隔离和恢复
```

这不是要把 CyberClaw 变成另一个完整 Claude Code。开发者工作区助手只是验证 Harness 能力的参考应用；真正的个人技术贡献是 Tool Policy、Session Runtime、Context、MCP 和 Trace 等底层机制。

核心技术主线为：

```text
Session-aware Runtime
+ Policy-governed Tool System
+ Layered Context and Memory
+ Native MCP Integration
+ Structured Tracing and Evaluation
```

这个定位比“聊天助手增加更多工具”更有技术深度，也更容易在简历和面试中说明个人贡献。

暂时不把项目定位成：

- 企业级零信任平台；
- 绝对安全的操作系统沙盒；
- 高可用分布式调度系统；
- 完整 Claude Code 复刻；
- 多用户 SaaS；
- 大规模多 Agent 协作平台。

### 2.5 改造期间的连续可运行与配置兼容原则

后续改造必须遵守一条硬性约束：

> **任何一个阶段完成后，CyberClaw 都必须仍然可以在当前已经配置好的项目环境中启动、连接模型并完成基本对话与工具测试。**

当前可用基线包括：

- 项目本地 `.venv`，继续与 uv 工作流兼容，不修改用户目录中的 Python/Conda 环境；
- 当前项目 `.env` 中已经配置好的 Provider、模型、学校 API Base URL 和 API Key；
- 当前使用的 OpenAI-compatible 接入方式；
- 当前 `.env` 中已经配置并验证可用的模型；模型可以由用户按需切换，不能在重构代码中写死；
- 现有 `cyberclaw config`、`cyberclaw run` 和项目入口；
- 已经验证过的普通对话、时间、calculator、office 文件写入与读取流程。

具体兼容规则：

1. 不删除、不覆盖用户现有 `.env`，不在文档、提交、日志或测试输出中暴露 API Key；
2. 第一阶段保留现有 `DEFAULT_PROVIDER`、`DEFAULT_MODEL`、`OPENAI_API_KEY`、`OPENAI_API_BASE` 配置契约；
3. 如果以后引入新的 Settings 或配置文件，必须提供旧环境变量兼容适配和迁移说明，不能要求用户无提示重新配置；
4. 不依赖全局 Conda base，不修改用户目录 Python；所有依赖变化只作用于项目本地 `.venv`；
5. 每个改造拆成小步骤，完成一个步骤就运行离线测试和本地启动冒烟测试；涉及 Provider/Agent 主链时，再使用当前模型做一次最小真实调用；
6. 测试文件操作只能使用测试临时目录或 office 内专用测试目录，不能破坏用户已有文件、会话、画像和任务；
7. 任何破坏兼容性的配置变更都必须先提供迁移路径、回滚方案和兼容测试；
8. 如果某一步尚未通过基线测试，就不能继续叠加下一阶段功能，也不能把该阶段标记为完成。

这条原则不是要求永远保留所有旧的内部实现，而是保证重构期间始终存在一条可运行路径，并让外部配置迁移是显式、可测试、可回滚的。

## 3. 当前项目值得保留的基础

改进不等于全部推倒重写。以下结构可以保留并演进：

1. `[CCL]` LangGraph 的 `agent → tools → agent` 条件循环表达清楚；
2. `[CCL]` 内置工具和动态 Skill 最终进入统一 LangChain Tool 接口；
3. `[CCL]` AsyncSqliteSaver 已提供消息 checkpoint 基础；
4. `[CCL]` 用户输入和 heartbeat 通过同一任务队列串行交给 Agent，避免同时修改同一会话；
5. `[CCL]` CLI、监控、日志、任务和工作区已经具有基本模块边界；
6. `[CCL]` OpenAI-compatible Provider 能接入学校 API、DeepSeek 等兼容端点；
7. `[CCL]` 项目规模适合完成结构化重构，而不是只能做表面包装。

## 4. CyberClaw 自身问题清单

## 4.1 P0：安全与正确性

### P0-1 office 路径边界可被绕过 `[CCL][CC]`

位置：`cyberclaw/core/tools/sandbox_tools.py::_get_safe_path()`

当前使用字符串 `startswith()` 判断目标路径是否位于 office 根目录。它不能可靠阻止：

- 相同前缀的兄弟目录；
- symlink/Junction 指向根目录外；
- 不同大小写、分隔符和规范化路径边界。

改进：

```text
Path.resolve(strict=False)
→ 使用 Path.is_relative_to(root)
→ 明确是否拒绝 symlink/Junction
→ 对读、写、创建分别验证最终父目录
→ 增加 Windows 路径回归测试
```

来源：问题由 CyberClaw 审计发现；“输入规范化 + 最终落点校验”的纵深防御参考 `[CC]` Session 路径设计。

### P0-2 通用 Shell 不是安全沙盒 `[CCL]`（第一轮修复已完成）

位置：`cyberclaw/core/tools/sandbox_tools.py::execute_office_shell()`

原始实现存在以下问题：

- `subprocess.run(..., shell=True)`；
- 继承当前用户权限和包含 API Key 的进程环境；
- 只用正则过滤明显路径或命令；
- 没有网络隔离、资源限制、低权限账户和进程树清理；
- 可通过变量、脚本、编码、解释器、链接和子进程绕过字符串规则。

已完成的第一轮修复：

- 通用程序执行默认关闭，必须由用户显式启用；
- 通过配置白名单限定可启动程序；
- 使用 `shell=False` 和 argv 直接执行，禁止 PowerShell、CMD、Bash 等嵌套 Shell；
- 拒绝管道、重定向、命令连接、绝对路径、父目录跳转和解释器内联代码参数；
- 使用固定 office cwd 和最小化子进程环境，不继承模型 API Key；
- 保留超时，并限制返回给模型的 stdout/stderr 大小；
- 增加默认关闭、白名单、参数边界、环境脱敏和输出截断回归测试。

当前边界：配置白名单属于用户显式授权，不等于对被允许程序本身做了操作系统隔离。进程树清理、网络/资源限制和逐次人工审批将在后续 Tool Policy/Approval 阶段继续实现。

长期改进：需要更强隔离时，将执行器移到低权限 worker、容器或虚拟化沙盒。完成前只能称为“受限工作区执行器”，不能称为严格安全沙盒。

### P0-3 安全规则只存在于 Prompt/Docstring `[CCL][LCC]`

位置：`cyberclaw/core/agent.py`、`builtins.py`、`skill_loader.py`

当前系统提示词和工具说明包含大量“先说明、再执行”“不得越界”等要求，但工具执行前没有不可绕过的 Policy Engine，也没有人工审批状态。

改进为统一管线：

```text
ToolCall
→ 参数规范化
→ Hard Deny
→ Capability/Context Rule
→ User Approval（需要时）
→ Executor
→ Structured ToolResult
→ Audit Event
```

来源：执行管线主要参考 `[LCC]` Permission + Hooks；“不授予工具即没有能力”参考 `[CC]` 实例级工具集合。

### P0-4 calculator 使用 `eval()` `[CCL]`

位置：`cyberclaw/core/tools/builtins.py::calculator()`

即使移除 `__builtins__`，Python 对象模型仍使 `eval()` 不适合作为不可信表达式计算器。应改为 AST 白名单解析，只允许数字、括号和规定的算术运算符，并限制表达式长度、数字范围和计算复杂度。

### P0-5 Skill 可形成任意 Shell 执行通道 `[CCL][LCC]`（第一轮修复已完成）

位置：`cyberclaw/core/skill_loader.py`

原实现让 Skill 正文影响模型，并在 `run` 阶段执行模型提供的任意命令；help/run 两阶段只是建议，没有程序状态约束。

已完成的第一轮修复：

- 区分 instruction-only Skill 与 executable Skill；
- 未声明类型的第三方 Skill 默认只有说明能力；
- executable Skill 必须在 registry 中固定 `runtime` 和 `entrypoint`；
- 模型只能提交结构化 `arguments`，不能提供程序、命令字符串或入口路径；
- help→run 状态按 `thread_id` 隔离，并要求读完当前版本全部页面；
- 说明书或入口文件变化后，旧快照和旧 help 状态不能继续执行；
- 拒绝 Skill 路径逃逸、符号链接入口和工具名称冲突；
- Skill 文本明确标记为不可信内容，README 不再宣称直接兼容其他 Skill 生态。

剩余边界：统一 Tool Policy、逐次用户审批、风险分级和结构化审计将在后续 Tool Runtime 阶段实现；instruction 文本仍可能影响模型，因此不能把 Prompt 包装当成提示注入隔离。

### P0-6 日志可能泄露敏感数据 `[CCL]`

位置：`cyberclaw/core/logger.py` 及 `agent.py` 的日志调用

工具参数、模型回答、文件内容或命令可能包含 API Key、个人数据和源码。当前没有字段级脱敏、大小限制和保留策略。

改进：默认记录元数据而非完整正文；敏感字段统一 redaction；正文记录必须显式 opt-in；增加日志轮转、保留期和失败处理。

## 4.2 P1：运行时与状态可靠性

### P1-1 会话 ID 硬编码 `[CCL]`

位置：`entry/main.py`

`thread_id="local_geek_master"` 使所有启动都进入同一逻辑会话和日志文件，无法新建、命名、切换、重置和删除会话，也无法区分用户画像与 thread memory。

改进：新增 Session Manager 和 CLI 会话命令，明确：

```text
user_id → 用户画像
thread_id → 对话、摘要、checkpoint
run_id → 一次请求
tool_call_id → 一次工具调用
```

### P1-2 上下文压缩由固定回合数触发 `[CCL][CC][LCC]`

位置：`cyberclaw/core/context.py`、`agent.py`

当前以 40 个用户回合触发并只保留 10 个近期回合，未统计 system prompt、工具 schema、工具结果、模型窗口和输出预留。摘要长度只通过 Prompt 要求，没有程序强制；摘要失败也缺少可靠回退。

改进：建立 Context Pipeline：

```text
大 ToolResult 外置并返回引用 [LCC]
→ 旧 ToolResult 规则裁剪 [CC][LCC]
→ 保护 tool-call pair 的安全切分 [CC]
→ 结构化旧对话摘要 [CC][LCC]
→ prompt-too-long 紧急压缩 [LCC]
```

完整事件历史继续持久化，压缩只改变发给模型的 Context View。

### P1-3 用户画像全文件覆盖且缺少作用域 `[CCL][LCC]`

位置：`builtins.py::save_user_profile()`、`config.py`

当前画像全局共享，工具整文件覆盖；没有事实来源、版本、冲突、置信度、过期、删除和用户确认。改为结构化 Memory Store，并拆分 selection、candidate extraction、approval、consolidation。

### P1-4 Heartbeat 不是独立可靠调度器 `[CCL][LCC]`

位置：`cyberclaw/core/heartbeat.py`、`builtins.py`、`entry/main.py`

问题包括：

- 只在主进程运行期间工作；
- JSON 更新非原子且只有进程内锁；
- 到期任务在 Agent 消费前就从文件移除，崩溃可能丢失；
- 重复任务从旧目标时间推进，长时间停机后可能连续追赶；
- 缺少时区、幂等、领取/确认、重试和执行历史；
- 异常被宽泛吞掉；
- heartbeat 文本最终作为普通 HumanMessage 进入 Agent。

改进：用 SQLite Job Store 保存 `next_run_at/status/attempts/timezone`，调度器只生产类型化 `ScheduledRunRequested` 事件，Agent 成功处理后再 ack。

### P1-5 异步退出存在竞态 `[CCL]`

位置：`entry/main.py`

`/exit` 后 worker 可能先退出，而 `task_queue.join()` 仍等待未消费任务；heartbeat 也可能在关停过程中继续入队。取消任务后没有统一等待，`task_done()` 也需要放在 `finally` 中保证调用。

改进顺序：

```text
停止接收用户输入
→ 停止 heartbeat/其他生产者
→ 关闭新任务入队
→ 排空或显式取消队列项
→ 取消并 await 消费者
→ 关闭 checkpoint/provider/logger
```

### P1-6 Logger 生命周期不完整 `[CCL]`

位置：`cyberclaw/core/logger.py`

当前全局 singleton 在导入时启动 daemon thread，队列无界；重复 shutdown 可能等待一个已经结束的线程；写失败后事件直接丢失。应改为显式生命周期、幂等 close、有界队列、刷新策略和降级统计。

### P1-7 Skill 热更新与实际工具绑定不一致 `[CCL][LCC]`（第一轮修复已完成）

位置：`skill_loader.py`、`agent.py`

原实现问题包括：

- `_cache_size` 参数没有真正控制固定装饰器缓存；
- `reload_skills` 声称清缓存但未执行 cache clear；
- 工具闭包可能持有旧 mtime/旧正文；
- running graph 的 ToolNode 和 `bind_tools()` 在创建时固定，新 registry 不会自动更新；
- 完整说明被截断到 3000 字符。

已完成的第一轮修复：使用实例级有界 LRU 内容缓存，`cache_size` 会真正生效；刷新会同时清除内容缓存和 help 状态；每个工具闭包持有版本快照，源文件变化后旧快照拒绝执行；说明书支持分页读取；动态工具与内置工具执行名称冲突检测。

当前采用明确的“启动快照”语义：运行中 LangGraph 的 `ToolNode` 和 `bind_tools()` 保持原工具集合，刷新后需要重启或重建 Agent 图才能绑定新快照，不再宣称自动热更新。

### P1-8 Provider 缺少统一恢复与用量层 `[CCL][LCC][CC]`

位置：`cyberclaw/core/provider.py`

当前缺少分类重试、fallback、usage/cost、模型能力验证和清晰可选依赖。`langchain_anthropic`、`langchain_community` 的导入与 requirements 也不一致。

改进为 Model Gateway：

- Provider registry 与能力声明；
- 显式超时；
- 临时错误 jitter backoff；
- 不重试不可恢复 4xx；
- 可配置 fallback；
- Token、费用和延迟统计；
- prompt-too-long 通知 Context Manager；
- secrets 不进入日志。

### P1-9 配置存在导入副作用 `[CCL]`

位置：`config.py`、`provider.py`、`logger.py`

导入模块时会 `load_dotenv()`、创建目录、打印消息或启动线程，使测试、作为库导入和多实例配置困难。应建立显式 `Settings.load()` 与 `Runtime.start()/close()`，并将用户数据目录与包源码目录分开。

### P1-10 工具结果和错误没有统一契约 `[CCL][CC]`

不同工具返回普通字符串，调用方难以判断成功、可重试错误、策略拒绝、超时和用户取消。应引入 `ToolResult`/`ToolError`，同时保留 LangChain 所需文本视图。

## 4.3 P2：工程质量与可观测性

### P2-1 包管理仍是 legacy setup `[CCL]`

问题：

- 没有 `pyproject.toml` 和 uv lock；
- `setup.py` 的 `py_modules=["cli"]` 与真实包结构不符；
- requirements 只有宽泛下限；
- dev/provider extras 不完整；
- 没有 `python_requires`、lint/type/test 统一配置；
- 没有 Windows/Linux CI。

改进：使用 `pyproject.toml` 统一 metadata、entry points、runtime/dev/provider extras 和工具配置；用 uv 维护锁文件；保留 `cyberclaw` console script。

### P2-2 日志不是完整 Trace `[CCL][CC][LCC]`

当前事件缺少 `run_id/span_id/parent_span_id`、阶段耗时、状态、模型、usage、重试和审批信息。`llm_input` 只记录消息数，无法表达一次 run 的因果链。

改进：定义版本化 Agent Event：

```text
run.started / model.started / model.completed
tool.requested / tool.approved / tool.denied
tool.started / tool.completed / tool.failed
context.compacted / memory.updated
run.completed / run.failed / run.cancelled
```

### P2-3 Monitor 与 Logger 事件漂移 `[CCL]`

Monitor 不显示 `ai_message`，却保留从未产生的 `system_action` 分支；默认日志文件又固定到 `local_geek_master`。应让 Monitor 消费共享事件 schema，并支持按 session/run/filter 选择。

### P2-4 测试偏正常路径 `[CCL][CC]`

现有测试缺少：

- office 兄弟前缀、symlink/Junction 越界；
- Shell 环境变量泄露和间接绕过；
- 完整 Agent 多轮、中断、压缩和恢复协议；
- shutdown 队列竞态；
- logger 幂等关闭和脱敏；
- Skill 版本刷新；
- Provider 重试/fallback；
- 真实 MCP 生命周期；
- README 命令和文件存在性检查。

建议先建立 invariants 清单，再写 deterministic tests、安全回归、model eval 和 benchmark 四套证据。

## 5. 文档描述与实际实现不一致

以下问题必须在实现对应能力前降低或修正文档声明。

| 文档描述 | 实际实现 | 处理建议 |
|---|---|---|
| 已集成 MCP 服务/可调用 MCP | 仓库没有 MCP client/runtime、连接配置或协议依赖 | 暂改为“规划中”；真实实现后再写支持的 transport 和边界 |
| 严格安全沙盒、零信任、阻止未授权操作 | 已实现真实路径边界和默认关闭的程序白名单，但仍没有 OS 隔离、网络限制和逐次审批 | 使用“受限工作区与防误操作规则”，不宣称 OS 隔离 |
| 五类完整日志事件 | 实际主要产生 `llm_input/tool_call/tool_result/ai_message`；`system_action` 未产生 | 统一事件 schema、Logger 和 Monitor |
| 记录全部行为/完整决策轨迹 | `llm_input` 仅有消息数量，缺少模型调用、审批、用量和 span | 改为有限事件日志；完成 trace 后再升级声明 |
| Heartbeat 独立后台进程，主程序退出后仍工作 | 实际是在 `entry/main.py` 内创建的协程，没有独立服务入口 | 改为“随 CLI 生命周期运行的后台协程”，或真正拆独立 scheduler |
| 英文 README 每秒检查任务 | `main.py` 实际传入约 10 秒间隔 | 文档和默认配置统一 |
| `read_user_profile` 后再保存 | 没有这个工具，只有 Agent 节点直接读取 profile 文件 | 增加明确只读能力或删除错误说明 |
| Skill help 返回完整说明书 | 已改为 3000 字符分页，并要求 executable Skill 在当前会话读完全部页面 | 已修复，不再声称单次返回全文 |
| Skill 自动热更新 | 已实现版本化启动快照和正确缓存刷新；运行图需重启或重建 | 已修复描述，不宣称自动热更新 |
| 兼容 Claude Code/OpenClaw Skills | 当前只支持本项目 Markdown Skill 格式 | 已修复描述；其他生态需要适配器和兼容测试 |
| SQLite 保存完整短期记忆/完整历史 | Agent 会裁剪当前消息视图，应用也没有完整 transcript 浏览和恢复语义 | 区分 checkpoint、完整事件日志和模型 Context View |
| 双水位记忆 | 实际更接近“画像 + 对话摘要”两类记忆，不是两个数值水位 | 使用准确术语“用户画像与会话摘要” |
| 持续学习 | 实际由模型决定整文件覆盖用户画像，没有提取、冲突、整合和遗忘机制 | 改称“显式用户画像保存”；完成 Memory Pipeline 后再升级 |
| 测试文档引用 `tests/test_context.py` | README 已改为实际文件名 `test_context_advanced.py` | 已修复 |
| 示例从 `test_two_phase_skills.py` 导入 `run_tests` | README 已删除无效命令，并将该文件标为实时模型历史实验 | 已修复 |
| 存在 `tests/logs/test_two_phase_skills.md` | README 已删除不存在的报告引用和固定实验数字 | 已修复 |
| Skill Development 文档链接 | 对应文件缺失 | 补文档或删除链接 |
| 99.98% 提速、80% 内存节省等结果 | 缺少可复现 benchmark、原始数据和统计方法 | 删除或标记历史声明；重新测量后再填写 |
| 安全测试“破坏性执行率”结论 | 测试脚本依赖实时模型，表格与结论数字还不一致 | 建立固定用例、版本、模型、重复次数和原始结果 |
| README 列出预装 skills | `workspace/office` 被 gitignore，仓库没有这些可版本化内容 | 提供安装流程/示例 Skill，或从目录树删除 |

## 6. 目标架构

```text
CLI / Future TUI
        │
        ▼
Runtime Coordinator
├── Session Manager
├── Typed Request Queue
├── Budget / Cancellation
└── Lifecycle
        │
        ▼
LangGraph Agent
├── Prompt Builder
├── Context Manager
├── Memory Service
└── Model Gateway
        │
        ▼
Tool Runtime
├── Tool Registry
├── Tool Policy Engine
├── Approval Store
├── Hook Pipeline
└── Tool Executor
    ├── Built-in Tools
    ├── Skill Provider
    └── MCP Client Manager

State
├── Checkpoint / Session DB
├── Memory DB
├── Scheduler DB
├── Artifact / Tool Result Store
└── Event Store

Observability
├── Structured Logs
├── Traces
├── Metrics
└── Eval Reports
```

关键约束：

1. 所有工具来源必须经过同一个 Registry 和 Policy；
2. 完整状态与模型 Context View 分离；
3. Session、Run、ToolCall 和 Scheduled Job 使用不同 ID；
4. Runtime 负责生命周期，模块导入不能启动后台资源；
5. 安全声明只描述代码真正强制的边界；
6. 每个核心行为都有确定性测试或可复现实验。
7. 保留当前 `.env` 外部配置契约和项目本地 `.venv` 运行方式；
8. 每个里程碑合并前必须通过现有环境兼容回归，不允许长期处于“重构中、无法启动”的状态。

## 7. 分阶段实施路线

所有阶段共享同一个交付门禁：

```text
静态检查通过
→ 离线自动测试通过
→ 当前 .env 配置可被正确读取
→ cyberclaw 可以正常启动
→ 基本对话与低风险工具冒烟测试通过
→ 涉及模型主链时，当前学校 API + `.env` 所选模型的最小真实调用通过
→ 确认 API Key 未进入日志、文档和 Git diff
```

任何阶段没有通过这套门禁，都只能算进行中，不能继续叠加下一阶段改造。

## 阶段 0：建立可复现工程基线

来源：`[CCL][NEW]`

目标：先让后续修改可安装、可测试、可回滚，不改变产品行为。

未来涉及文件：

```text
新增 pyproject.toml
新增 uv.lock
新增/整理 tests/
新增 .github/workflows/ci.yml
重构 cyberclaw/core/config.py
更新 README.md / README_EN.md / CHANGELOG.md
逐步淘汰 setup.py / requirements.txt 的重复职责
```

完成标准：

- 干净 Windows/Linux 环境可用同一套 uv 流程安装；
- 当前本地 `.venv` 无需改用 Conda，仍可直接运行现有 CLI；
- 现有 `.env` 无需重新填写即可继续使用学校 API 和当前 DeepSeek 模型；
- 在不输出任何 secret 的前提下，记录当前 Python、依赖、入口、Provider 和功能冒烟基线；
- runtime/dev/provider 依赖边界清楚；
- CLI entry point 正常；
- CI 运行 lint、type check 和 tests；
- README 不再声明未实现的 MCP、严格沙盒和后台独立运行。

## 阶段 1：重构 Tool Runtime 与安全边界

来源：`[CC]` 实例级能力、结构化错误、路径纵深防御；`[LCC]` Permission/Hook/Registry；`[CCL]` 当前 sandbox、Skill、calculator 问题。

建议新增：

```text
cyberclaw/core/tools/contracts.py
cyberclaw/core/tools/registry.py
cyberclaw/core/tools/policy.py
cyberclaw/core/tools/executor.py
cyberclaw/core/tools/hooks.py
cyberclaw/core/approval.py
```

建议修改：

```text
cyberclaw/core/agent.py
cyberclaw/core/tools/base.py
cyberclaw/core/tools/builtins.py
cyberclaw/core/tools/sandbox_tools.py
cyberclaw/core/skill_loader.py
entry/main.py
```

主要交付：

1. `ToolSpec`：来源、版本、风险、能力、并发、超时；
2. `ToolResult`：成功、错误类型、可重试、正文和 metadata；
3. Tool Registry：内置/Skill/MCP 统一命名和冲突检测；
4. Policy：deny/rule/approval 的固定顺序；
5. Approval token 绑定规范化参数哈希；
6. 安全路径算法和链接策略；
7. AST calculator；
8. Shell 默认关闭或进入严格受限 executor；
9. Skill instruction 与 executable action 分离。

完成标准：

- 未授权高风险工具执行次数为 0；
- 修改审批参数后旧 approval 失效；
- 兄弟目录、`..`、symlink/Junction 用例不能越界；
- 所有 tool call 都产生结构化成功/失败/拒绝结果；
- 内置、Skill 和未来 MCP 不能绕过同一 Policy。

这是最适合作为第一个简历核心贡献的阶段。

## 阶段 2：多会话、类型化事件与可靠运行时

来源：`[CC]` Session/CLI 与协议不变量；`[LCC]` 类型化通知和单消费者；`[CCL]` 固定 thread、队列退出和日志问题。

建议新增：

```text
cyberclaw/core/session.py
cyberclaw/core/runtime.py
cyberclaw/core/events.py
cyberclaw/core/observability.py
```

建议修改：

```text
entry/main.py
entry/cli.py
cyberclaw/core/agent.py
cyberclaw/core/bus.py
cyberclaw/core/logger.py
cyberclaw/core/monitor.py
cyberclaw/core/config.py
```

主要交付：

- session create/list/resume/rename/delete；
- `user_id/thread_id/run_id/tool_call_id` 分层；
- UserRequest 与 ScheduledRequest 不再都伪装成普通字符串；
- 有界队列、取消、超时和固定 shutdown 顺序；
- 版本化事件 schema；
- 日志脱敏、轮转、幂等 close；
- Monitor 按 session/run 显示真实事件；
- 中断、压缩和恢复后的工具消息协议测试。

完成标准：

- 两个会话的历史、摘要和日志互不串扰；
- 重启后可选择恢复指定会话；
- `/exit` 在有 heartbeat/排队任务时仍可在限定时间内退出；
- 每个 run 可串联模型、审批、工具和结束状态；
- 敏感测试值不会出现在默认日志。

## 阶段 3：分层 Context 与可管理 Memory

来源：`[CC]` 三级压缩和 safe split；`[LCC]` ToolResult 外置、reactive compact、memory selection/extraction/consolidation；`[CCL]` 固定回合摘要和画像覆盖问题。

建议新增：

```text
cyberclaw/core/context_manager.py
cyberclaw/core/artifact_store.py
cyberclaw/core/memory/models.py
cyberclaw/core/memory/store.py
cyberclaw/core/memory/service.py
cyberclaw/core/prompt_builder.py
```

替换或迁移：

```text
cyberclaw/core/context.py
cyberclaw/core/config.py 中的 profile/summary 路径
cyberclaw/core/agent.py 中的大段硬编码 prompt
save_user_profile 工具
```

主要交付：

- 模型上下文窗口和输出预留配置；
- 大工具结果写入 artifact store；
- Token 预算触发的分层处理；
- tool pair 安全切分和协议验证；
- 结构化 summary：目标、完成项、决策、参数、待办、风险；
- `prompt-too-long` 紧急回退；
- Memory 候选、来源、作用域、版本、确认、删除和整合；
- 动态 Prompt Builder 只注入本轮相关记忆和真实工具目录。

完成标准：

- 长会话不会因固定回合阈值误判；
- 压缩前后不存在孤儿 tool message；
- 完整 transcript/事件仍可追溯；
- 两个 user/thread 的记忆隔离；
- 用户可以查看、纠正和删除长期记忆；
- 对长工具结果的 Token、延迟和任务成功率有对照数据。

## 阶段 4：真实 MCP Client Manager

来源：`[LCC]` 动态发现、命名空间和统一工具池；`[CCL]` README 已宣称但代码没有 MCP；`[NEW]` 使用真实协议和生命周期实现。

前提：阶段 1 的 Tool Registry/Policy 已完成，否则 MCP 会直接扩大攻击面。

建议新增：

```text
cyberclaw/mcp/config.py
cyberclaw/mcp/manager.py
cyberclaw/mcp/session.py
cyberclaw/mcp/adapter.py
cyberclaw/mcp/errors.py
```

主要交付：

- stdio 与选定的 HTTP transport；
- server 配置、信任和 secret 引用；
- initialize/list_tools/call_tool/close 生命周期；
- `mcp__server__tool` 命名和 collision handling；
- schema → LangChain Tool 适配；
- timeout、cancel、reconnect 和 output budget；
- MCP 工具经过同一 Policy/Approval/Trace；
- server 工具变化产生新的 Registry version。

完成标准：

- 至少接入两个真实 MCP server，并有可重复 demo；
- 某个 server 失败不会使整个 Agent 崩溃；
- 同名工具不会静默覆盖；
- 高风险 MCP 工具未经审批不可执行；
- 连接、调用、超时、重连和关闭均有测试/事件证据。

## 阶段 5：可靠调度、模型恢复与评测

来源：`[LCC]` Cron producer/consumer 和 Error Recovery；`[CC]` Provider 重试/用量；`[CCL]` Heartbeat、Provider 和指标问题。

建议新增或修改：

```text
cyberclaw/scheduler/models.py
cyberclaw/scheduler/store.py
cyberclaw/scheduler/service.py
cyberclaw/core/model_gateway.py
evals/
benchmarks/
```

主要交付：

- SQLite 调度状态、时区、领取、ack、retry/backoff、幂等；
- missed-run policy 和执行历史；
- Provider 分类重试、fallback、usage/cost；
- deterministic protocol tests；
- policy bypass/security regression；
- model task eval；
- Token、延迟和内存 benchmark 原始数据。

完成标准：

- 进程在领取后崩溃，任务能够按策略恢复且不静默丢失；
- 重复调度具有明确幂等结果；
- 429/临时错误和不可恢复错误走不同路径；
- README 中所有性能数字都能从仓库脚本和原始结果复现。

## 阶段 6：可选的 Coding Agent / 多 Agent 能力

来源：`[CC]` 受限子 Agent；`[LCC]` Task DAG、Teams、Worktree。

该阶段暂缓。只有项目明确转向代码任务，并且单 Agent Runtime、Policy、Session 和 Trace 已稳定，才考虑：

- 不可递归的探索子 Agent；
- 结构化 Task DAG；
- 后台任务查询与取消；
- Git worktree 隔离；
- 任务级预算和文件冲突合并。

不要为了简历关键词提前加入一个无法可靠取消、无法隔离副作用的多 Agent demo。

## 8. 改进点来源总表

| 改进点 | CoreCoder | learn-claude-code | CyberClaw 自身审计 |
|---|---:|---:|---:|
| 有界 run 预算 | ✓ |  | 当前缺少应用级预算 |
| Tool Registry/能力集合 | 实例级工具作用域 | 动态统一工具池 | ToolNode 创建时固定 |
| Policy/Approval | 通过不给工具限制能力 | 完整执行前管线 | 目前主要靠 Prompt |
| Hook/Event | CLI callbacks | 生命周期 Hook | Logger/Monitor 事件漂移 |
| 结构化 ToolResult | 参数错误分类 | 统一分发与通知 | 当前主要返回字符串 |
| 安全路径 | Session 纵深防御 |  | office `startswith()` 缺陷 |
| 安全编辑/Diff | 唯一匹配与 unified diff |  | 当前只有覆盖/追加 |
| 分层 Context | 三级压缩、safe split | 结果外置、microcompact、reactive compact | 固定回合摘要 |
| Memory Pipeline | 摘要降级 | 选择/提取/整合 | 画像整文件覆盖 |
| 子 Agent | 上下文隔离、禁递归 | 受限工具和任务协作 | 当前未实现，暂缓 |
| 异步任务 | 线程并行工具 | background + notification | heartbeat 队列有雏形但不可靠 |
| Scheduler |  | producer/queue/consumer | JSON heartbeat 生命周期问题 |
| Provider Recovery | retry/usage/cost | fallback/超限恢复 | 当前缺少统一层 |
| MCP |  | 动态发现/命名空间（教学 mock） | 文档声称、实现缺失 |
| Session/CLI | 安全保存与恢复 | 类型化任务通知 | 固定 `local_geek_master` |
| Tracing/Eval | 跨模块不变量测试 | Hook/集成数据流 | 日志字段、测试和指标不足 |

## 9. 推荐的第一版范围

为了形成一个完成度高、能讲清楚的简历项目，不建议同时实现全部阶段。第一版建议聚焦：

> **Policy-governed Local Agent Harness**

第一版包含：

1. 工程基线与真实文档；
2. Tool Registry + Tool Policy + Approval；
3. 安全路径、AST calculator、Shell 默认关闭/受限；
4. 多会话与 `run_id/tool_call_id`；
5. 类型化 Trace 和日志脱敏；
6. 一个真实 MCP server 接入作为统一 Policy 的证明。

第一版暂不包含：

- 多 Agent 团队；
- 自治领取；
- Git worktree；
- 分布式调度；
- Web UI；
- “企业级”“零信任”“完全安全”等无法证明的包装。

这样已经能形成一条完整面试故事：

```text
发现 Prompt 约束无法阻止副作用
→ 设计统一 Tool Contract/Registry/Policy
→ 让内置、Skill、MCP 走同一审批路径
→ 修复路径和 Shell 边界
→ 用 run trace 与安全回归证明不可绕过
```

## 10. 测试与证据计划

### 10.1 现有环境兼容回归

每次改进后优先验证原有可运行路径：

```text
项目本地 .venv 中 Python 可启动
→ 当前 .env 能读取 Provider/Model/Base URL
→ cyberclaw CLI 可启动和正常退出
→ 使用当前模型完成一次最小对话
→ calculator 完成确定性计算
→ 在专用临时目录完成 office 文件写入和读取
→ checkpoint/session 可以继续写入和恢复
```

测试约束：

- 自动测试默认使用 Fake Model/Mock Provider，不消耗真实 API 余额；
- 只有改动 Provider、Agent 主链、工具绑定或 Context 时，才运行最小真实 API 冒烟测试；
- 真实测试沿用现有配置，不在命令行、截图、日志或报告中输出 API Key；
- 不修改或删除现有 `.env`、用户画像、会话数据库、任务和 `hello.txt` 等用户数据；
- 文件类测试使用独立临时目录，并在成功或失败后安全清理；
- 每次测试记录 commit、测试范围和结果，失败时先恢复基线再继续开发。

### 10.2 确定性测试

- 每个 ToolCall 都有对应 ToolResult；
- 压缩、中断和恢复保持合法消息序列；
- Session/Run ID 隔离；
- Tool Registry 冲突与版本行为；
- Approval 参数哈希绑定；
- shutdown 不遗留未完成 queue item；
- Scheduler 领取和 ack 状态机。

### 10.3 安全回归

- `..`、相邻前缀、大小写、UNC、symlink/Junction；
- Shell 变量、脚本、解释器、子进程和环境泄露；
- Prompt/Skill/MCP 输出注入；
- 修改审批后的参数；
- 日志 secret redaction；
- 大工具输出导致的资源消耗。

### 10.4 模型评测

模型评测与程序安全测试分开。记录：

```text
模型/Provider/温度
任务集版本
每个任务重复次数
工具选择成功率
任务完成率
平均轮次和 Token
审批请求准确率
失败分类
```

### 10.5 性能基准

只测能够复现的指标：

- 冷启动/热启动时间；
- 一次 run 的模型与工具阶段延迟；
- Context 压缩前后输入 Token；
- 事件记录开销；
- MCP 首次连接、热调用和重连时间；
- 长会话内存占用。

不预先填写提升百分比，完成实现后用原始报告产生结论。

## 11. 包装与开源合规

项目来自 fork，正确的包装方式不是删除来源，而是让个人贡献边界清楚：

1. 保留原 MIT License、版权和 Git 历史；
2. README 标明 upstream 地址与基线 commit；
3. 新增 `ARCHITECTURE.md`、ADR 和迁移说明；
4. Changelog 区分 upstream 能力与本项目新增能力；
5. 对 CoreCoder 和 learn-claude-code 的设计借鉴保留链接和许可说明；
6. 不照搬代码后声称从零原创；
7. 简历只写自己完成并有测试/数据证明的阶段。

完成第一版后可使用如下事实型描述，再填入真实数据：

```text
基于开源 LangGraph Agent 原型二次开发本地 Agent Harness，
设计统一 Tool Registry 与 Policy Engine，使内置、Skill、MCP 工具
共享风险分级、参数哈希审批和结构化审计链路，并通过 N 类越权用例验证。
```

```text
重构会话与运行时状态，引入 thread/run/tool-call 分层 ID、
有界任务队列和类型化 Trace，实现多会话隔离、可恢复执行与敏感日志脱敏。
```

```text
实现真实 MCP Client Manager，支持动态工具发现、命名冲突处理、
超时/重连及统一权限审批，完成 N 个真实 MCP server 的可复现集成测试。
```

其中的 N 和任何性能数字只能使用最终仓库中能够复现的结果。

## 12. 下一步执行顺序

三份分析完成后，真正修改代码时建议严格按以下顺序：

```text
1. 建立 baseline tag，脱敏记录当前 .venv、.env 配置契约、启动和功能测试结果
2. 修正文档中的高风险错误声明
3. 建立 pyproject/uv/CI 工程底座
4. 写 Tool Runtime ADR 和不变量测试
5. 实现 Tool Contract/Registry
6. 实现 Policy/Approval/Hook
7. 修复路径、calculator、Shell 和 Skill 边界
8. 加入 Session/Run IDs 与类型化 Trace
9. 接入第一个真实 MCP server
10. 完成安全回归、演示、报告和 README 包装
```

在第 4 步开始写业务代码前，应先确定 Tool Contract 和审批状态机；这两个接口会影响后面 Skill、MCP、日志和测试，越晚修改成本越高。

上述每一步都采用“改动 → 离线测试 → 现有环境冒烟 → 检查 secret 与 Git diff → 再提交”的节奏。我们不会在最开始删除旧入口、重建 `.env` 或一次性替换整个运行时，而会先增加兼容层，再迁移调用方，最后才清理确认无用的旧实现。

## 13. 最终判断

CyberClaw 目前已经具备一个可运行 Agent 原型，但离“更加完善且有个人技术深度的简历项目”最大的距离，不是缺少更多工具，而是：

```text
安全约束没有程序强制
运行状态和会话边界不清楚
上下文与完整历史没有分离
外部工具没有真实标准协议接入
执行过程缺少可验证的因果证据
文档声明超过实际能力
```

CoreCoder 帮助我们补足小而可靠的运行时不变量；learn-claude-code 帮助我们建立可扩展 Harness 的能力地图；CyberClaw 的 LangGraph、异步队列、checkpoint 和终端原型则是实际改造基础。

后续不应把三个项目简单拼接，而应围绕“策略可控的本地 Agent Harness”这一条主线重新设计、实现和验证。只有完成端到端功能、失败场景、测试数据和文档后，相应内容才适合写入简历。

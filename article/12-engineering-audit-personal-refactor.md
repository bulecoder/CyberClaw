# 第 12 课｜工程审计与个人项目改造

> 覆盖范围：CyberClaw 全部核心模块、入口、测试、示例和文档  
> 本课目标：把“读懂一个 fork”转化为“完成有原创贡献、有工程证据的二次开发”

## 一、本课要解决的问题

学完整个仓库之后，最容易出现两个误区：

```text
误区一：换名字、换 Logo、改 README
       → 就把项目当成自己的原创

误区二：发现很多问题后全部重写
       → 失去对原架构取舍的理解，也难以完成
```

真正有价值的路径是：

```text
理解现有系统
→ 建立可运行基线
→ 做证据化工程审计
→ 选择一条清晰产品主线
→ 完成结构性改造
→ 用提交、文档和结果证明贡献
```

“自己的项目”不意味着隐藏 fork 来源，而意味着你能明确回答：

1. 原项目原来怎样工作；
2. 原设计有哪些优点和边界；
3. 你为什么选择某个问题；
4. 你设计并实现了什么；
5. 有哪些可验证结果；
6. 还有哪些限制没有解决。

## 二、先给项目重新定位

CyberClaw 当前更准确的定位是：

> 一个基于 LangGraph 的本地终端 Agent 学习原型，包含模型适配、工具循环、消息记忆、定时任务、文档驱动 Skill 和 JSONL 事件日志。

它并不是现成的：

- 企业级零信任平台；
- 操作系统安全沙盒；
- 高可用调度服务；
- 原生 MCP Host；
- 多用户 Agent SaaS；
- 完整可观测平台。

### 推荐的二次开发定位

建议把你的项目主线定义为：

> 面向个人开发者的可控、可观测本地 Agent Workbench。

核心价值不是“模型能聊天”，而是：

```text
多会话可恢复
工具执行可审批
本地工作区可限制
外部能力可标准化接入
每次运行可追踪
```

这个定位能够自然复用现有优点，又能让你的贡献集中在真实工程问题上。

## 三、先区分继承能力与个人贡献

### 原项目已有基础

- LangGraph Agent/Tool 条件循环；
- LangChain Tool 抽象；
- OpenAI 兼容 Provider 工厂；
- SQLite checkpoint；
- 用户画像和上下文摘要；
- 定时任务与心跳；
- office 文件/Shell 工具；
- Markdown Skill loader；
- JSONL 日志与终端 Monitor；
- Typer CLI。

### 可以成为你个人贡献的部分

只有亲自设计、实现并验证后，才能写为贡献。例如：

- 结构化 Settings 和 Provider registry；
- 多会话创建、切换、恢复与隔离；
- Token 预算上下文管理；
- 程序级工具审批和能力策略；
- 真实路径隔离与低权限执行器；
- 持久化调度器与时区处理；
- 原生 MCP Client Manager；
- 有界异步队列和优雅关闭；
- run/span 级可观测性和日志脱敏；
- `pyproject.toml`、uv lock 与 CI；
- 可复现 Agent eval。

“读懂原代码”是重要能力，但不能把作者已有实现直接改写成自己的成果描述。

## 四、工程审计结论

## 4.1 架构优点

### 1. Agent 图主链清晰

```text
agent
→ 有 tool_calls
→ tools
→ agent
→ 无 tool_calls
→ END
```

适合学习并扩展。

### 2. 工具接口统一

内置工具和动态 Skill 最终都实现 LangChain Tool 协议，可以进入同一个 `ToolNode`。

### 3. 状态与运行时初步分层

- LangGraph 管消息状态；
- SQLite 做 checkpoint；
- Markdown 做画像；
- JSON 做任务；
- office 放产物；
- JSONL 放事件。

虽然还不够规范，但数据职责已经有雏形。

### 4. 正式终端有并发协调意识

输入、心跳、Agent 和刷新被拆成协程，并用队列解耦。

### 5. 项目规模适合彻底学习

模块数量有限，每条主链都可以追踪到具体函数，适合从 fork 发展为工程作品。

## 4.2 P0：安全边界

### 1. office 不是 OS 沙盒

路径检查和正则黑名单不能改变进程权限。

主要问题：

- `startswith()` 路径前缀判断；
- symlink/Junction；
- 通用 `shell=True`；
- 脚本间接访问；
- 无网络、进程和资源隔离；
- 环境中可能含 API Key。

### 2. 两阶段执行没有程序强制

help 只是 Prompt 建议，run 不验证 help、审批或命令版本。

### 3. 记忆与 Skill 都可能提示注入

用户画像、摘要和 Skill 文档最终进入模型高影响上下文。

### 4. 日志可能泄露数据

工具参数与 AI 回答未脱敏。

这些问题应先于界面美化和功能扩展处理。

## 4.3 P1：正确性与可靠性

### 1. 单会话硬编码

```text
thread_id = local_geek_master
```

无法创建、命名、切换和删除独立会话。

### 2. 上下文压缩基于固定回合

无法准确控制 Token，摘要没有结构、质量校验和失败回退。

### 3. 用户画像全局共享并整文件覆盖

没有 user/thread 边界、并发版本和冲突处理。

### 4. 调度状态使用 JSON 文件

缺少：

- 事务；
- 并发控制；
- 原子更新；
- 时区；
- 宕机补偿；
- 幂等执行；
- 独立服务生命周期。

### 5. 异步关闭不完整

无界队列、取消后不等待、`task_done()` 非 finally、退出与心跳竞争。

### 6. Logger 生命周期不完整

无界队列、重复 shutdown 风险、无轮转和失败恢复。

## 4.4 P1：工程基础

- legacy `setup.py`；
- 依赖只有宽泛下限；
- 没有 `pyproject.toml` 和 `uv.lock`；
- 可选 Provider 依赖不完整；
- 没有 CI；
- 关键主链测试不足；
- README 与实现多处漂移；
- 配置加载和目录创建存在导入副作用。

## 4.5 P2：能力扩展

- 原生 MCP；
- 运行时 Tool registry；
- Skill 版本和签名；
- Web/TUI 会话管理；
- 模型路由与降级；
- 可复现 eval；
- 多实例调度和远程执行。

这些应当放在安全、状态和工程基础之后。

## 五、不要一次重写：采用分阶段路线

## 阶段 0：冻结学习基线

目标是保留一个能够随时对照的原始版本。

应形成：

```text
上游仓库和 commit 记录
当前 fork 基线 tag
本地环境说明
已知测试结果
课程源码覆盖表
已知问题清单
```

这样后续可以明确：

```text
什么是上游已有
什么是自己修改
哪个变化导致哪个结果
```

不要删除 Git 历史，也不要覆盖 MIT License。

## 阶段 1：工程底座标准化

目标是不改变产品功能，先让项目可重复开发。

### 设计内容

1. 用 `pyproject.toml` 统一项目元数据；
2. 明确 Python 3.11 范围；
3. 使用 uv 生成锁文件；
4. 拆分 runtime、dev 和 Provider extras；
5. 删除错误的 `py_modules=["cli"]`；
6. 统一格式化、lint、类型检查配置；
7. 建立 Windows/Linux CI；
8. 修正文档中的命令、阈值和文件路径；
9. 增加 Settings 对象，取消多模块隐式 `load_dotenv()`；
10. 把用户数据目录与包源码目录分开。

### 阶段完成标准

```text
干净环境可以用一条标准流程同步
CLI 三个入口可用
依赖和 Provider 缺失提示清晰
文档不再宣称未实现能力
```

这是后续所有改造的地基。

## 阶段 2：多会话与记忆重构

这是一条很适合作为第一项核心个人贡献的主线。

### 目标架构

```text
User
└── Profile
    ├── Thread A
    │   ├── Messages
    │   └── Summary
    └── Thread B
        ├── Messages
        └── Summary
```

### 需要改变

1. CLI 支持新建、列出、恢复、重命名、删除会话；
2. `thread_id` 不再硬编码；
3. profile 使用独立 `user_id` 作用域；
4. 消息和摘要继续按 `thread_id`；
5. 摘要按 Token 预算触发；
6. 摘要改为结构化字段：

```text
当前目标
已完成
关键决定
重要参数
待办
风险
```

7. 重要业务事实不依赖 LLM 摘要；
8. 提供明确的记忆查看、修正和删除语义。

### 为什么适合作为主线

它涉及：

- LangGraph checkpoint；
- 数据建模；
- CLI；
- 上下文工程；
- 隔离与隐私；
- 迁移兼容。

面试时能形成完整技术故事，而不是零散修 bug。

## 阶段 3：工具策略与安全执行

### 目标架构

```text
LLM 提出 ToolCall
        ↓
Tool Policy Engine
├── 允许
├── 拒绝
└── 需要用户审批
        ↓
受限 Tool Executor
        ↓
结构化 ToolResult
```

### 工具风险分级

```text
Read       → 只读、低风险
Write      → 修改工作区
Execute    → 启动进程
Network    → 外部通信
Credential → 使用密钥
Destructive→ 删除或不可逆操作
```

### 程序级审批

审批对象不能只是一段自然语言，应包含：

```text
tool name
规范化参数
风险等级
预计影响
计划 ID
过期时间
内容哈希
```

run 必须提交与已批准计划一致的数据，防止 help 后悄悄换命令。

### 执行器边界

优先取消通用 Shell，改为：

- 参数化工具；
- `shell=False` 的 argv；
- 命令 allowlist；
- 低权限子进程；
- 独立工作目录；
- 环境变量白名单；
- 网络策略；
- CPU、内存、时间、输出和进程数限制。

需要更强安全时，应把执行放进容器或专门 worker，而不是在主 Agent 进程中执行。

## 阶段 4：可靠调度与异步运行时

### 调度器改造

把任务从普通 JSON 迁移到 SQLite 表：

```text
task_id
thread_id
payload
schedule
timezone
next_run_at
status
attempts
last_error
created_at
updated_at
```

核心机制：

- 时区感知；
- 事务领取；
- 幂等 key；
- retry/backoff；
- missed-run policy；
- 执行历史；
- graceful shutdown；
- 与 Agent 主进程分离或明确同生命周期。

### 运行时改造

- 队列有界；
- 按 thread 串行，跨 thread 并行；
- 每个请求有 ID；
- 支持取消和超时；
- 使用 `ainvoke()` 的真实异步边界；
- 同步工具进入受控 executor；
- Task 统一由 TaskGroup 或等价生命周期管理；
- 先停生产者，再排空/取消消费者。

## 阶段 5：原生 MCP 接入

只有完成工具策略之后，才建议接入更多外部能力。

### 推荐组件

```text
MCPServerConfig
MCPClientManager
MCPServerSession
MCPToolAdapter
ToolRegistry
ToolPolicyEngine
```

### 数据流

```text
启动或连接 MCP servers
→ list_tools
→ schema 适配
→ 注册到 ToolRegistry
→ Agent 本轮选择候选工具
→ 策略检查和用户审批
→ call_tool
→ MCP result 转 ToolMessage
```

### 必须处理

- stdio/HTTP 连接生命周期；
- Server 信任与认证；
- tool schema 变化；
- 超时、取消和重连；
- 资源与 Prompt 能力边界；
- Server 输出大小；
- 日志脱敏；
- 运行中 registry 更新。

原生 MCP 的价值是标准协议互操作，不只是从 Shell 调一个叫 MCP 的命令。

## 阶段 6：可观测性与评测

### 结构化事件

每次用户请求至少有：

```text
thread_id
run_id
span_id
parent_span_id
event_type
start/end time
status
provider/model
token usage
latency
error category
```

### 三类信号

```text
Logs   → 具体事件与错误
Metrics→ 延迟、吞吐、错误率、队列深度
Traces → 一次请求中模型、工具、存储的因果链
```

### 隐私

默认不记录完整用户内容、API Key、文件正文和高风险参数。所有敏感字段都应有明确脱敏策略和保留期限。

### Eval

把评测分开：

```text
确定性测试 → 协议与状态正确性
安全评测   → 越界、注入、审批绕过
模型评测   → 工具选择和任务完成质量
性能基准   → 延迟、Token、内存和吞吐
```

结果必须保存原始数据、环境和统计方法。

## 六、推荐你真正选择的第一条主线

如果目标是尽快形成一个完整、有深度、可面试的项目，推荐顺序：

```text
第一主线：多会话 + 分层记忆
第二主线：工具审批 + 安全执行
第三主线：原生 MCP
```

原因：

### 多会话 + 分层记忆

- 与现有 LangGraph 主链最紧密；
- 容易做出可见产品差异；
- 能讲状态、数据库、Token 和隔离；
- 不必先处理外部服务生态。

### 工具审批 + 安全执行

- 技术深度高；
- 能纠正文档“零信任”与实现的差距；
- 但需要谨慎定义安全目标，不能轻易宣称绝对安全。

### 原生 MCP

- 能体现协议适配和动态工具注册；
- 但若没有前置权限策略，会扩大攻击面；
- 应放在第二阶段之后。

不要同时开三条主线。先完整交付一条，再扩展下一条。

## 七、目标架构草图

```text
CLI / TUI
   │
   ▼
Runtime Coordinator
├── Session Manager
├── Request Queue
└── Lifecycle / Cancellation
   │
   ▼
LangGraph Agent
├── Context Manager
├── Provider Registry
└── Tool Registry
       │
       ▼
Tool Policy Engine
├── Built-in Tools
├── Restricted Executor
└── MCP Client Manager

State Layer
├── Session / Checkpoint DB
├── Profile Store
├── Scheduler DB
└── Artifact Workspace

Observability
├── Structured Logs
├── Metrics
└── Traces
```

各层职责：

- Runtime 负责并发与生命周期；
- Agent 负责推理与状态图；
- Policy 负责是否允许执行；
- Executor/MCP 负责真实副作用；
- State 负责持久化；
- Observability 负责证据。

## 八、每次改造都要留下四类产物

### 1. 设计记录

用 ADR 或设计文档回答：

```text
问题是什么
约束是什么
有哪些方案
为什么选择当前方案
放弃了什么
怎样迁移和回滚
```

### 2. 小而清晰的提交

一个提交只解决一个逻辑问题。

提交历史应能看出：

```text
建立基线
补足保护
完成重构
迁移数据
更新文档
```

### 3. 可复现证据

包括：

- 干净环境安装结果；
- 自动验证；
- benchmark 原始数据；
- 架构图；
- 演示录屏或截图；
- 已知限制。

### 4. 用户视角说明

README 应先告诉用户：

```text
项目解决什么问题
最短启动路径
数据保存在哪里
哪些操作有风险
如何卸载和删除数据
```

不要先堆“企业级、零信任、无限扩展”等无法证明的形容词。

## 九、怎样形成可写进简历的成果

简历描述应使用：

```text
动作
+ 技术对象
+ 解决的问题
+ 可验证结果
```

### 不推荐

```text
开发了一个基于 LangGraph 的智能 Agent，
支持记忆、工具、MCP 和定时任务。
```

问题：

- 分不清哪些来自上游；
- “支持”没有边界；
- 没有个人决策；
- 没有证据。

### 推荐模板

在真实完成后再填数字：

```text
基于开源 CyberClaw 二次开发本地 Agent Workbench，
重构 thread/user 分层状态模型，实现会话创建、恢复与隔离，
将上下文裁剪由固定回合改为 Token 预算，并通过 [N] 个状态场景验证迁移兼容。
```

```text
设计 Tool Policy Engine，将文件、进程和网络能力分级，
实现计划哈希与用户审批，封堵 [N] 类路径/命令绕过，
高风险工具未经审批执行率由 [X] 降至 [Y]。
```

```text
实现原生 MCP Client Manager，统一管理 [N] 个 server session，
将 MCP tool schema 动态适配为 LangChain Tool，
支持超时、重连、权限审批和运行期工具更新。
```

```text
将 legacy setup.py 项目迁移到 pyproject.toml + uv lock，
拆分 Provider extras 并建立 Windows/Linux CI，
将全新环境部署步骤收敛为 [实际流程与结果]。
```

方括号只能填写亲自测得且能够复现的数据。

## 十、面试时怎样讲这个项目

推荐叙事顺序：

### 1. 背景

```text
我 fork 了一个可运行的 LangGraph 本地 Agent，
先用源码覆盖表完整学习全部模块。
```

### 2. 审计发现

选择两三个最重要且有代码证据的问题，例如：

```text
单 thread 硬编码
help/run 未强制
README 与真实 Heartbeat 生命周期不一致
```

### 3. 方案比较

说明为什么没有直接重写，以及比较过哪些方案。

### 4. 核心实现

沿一条数据流讲清：

```text
用户请求
→ 状态
→ 策略
→ 副作用
→ 持久化
→ 事件证据
```

### 5. 结果

展示：

- 具体行为变化；
- 失败场景；
- 数据；
- CI；
- commit；
- demo。

### 6. 限制

主动说明仍未解决的边界。清楚的限制比“企业级全支持”更可信。

## 十一、项目改名与品牌

可以在完成实质性改造后建立自己的名称和视觉识别，但应保留：

- 原 MIT License；
- 原版权；
- README 的 upstream 说明；
- fork/二次开发关系；
- 主要架构来源和个人变更列表。

一个合适的 README 表述：

```text
本项目基于 CyberClaw（MIT License）二次开发。
上游提供 LangGraph Agent、基础工具与终端原型；
本项目重点重构了多会话记忆、工具策略、原生 MCP
和可观测性。详细差异见 ARCHITECTURE.md / CHANGELOG。
```

项目归属的可信度来自实质贡献，不来自删除作者信息。

## 十二、最终完成标准

只有同时满足以下条件，才适合把它作为重点简历项目：

### 理解

- 能从 CLI 讲到 Provider、Agent、Tool、状态和输出；
- 能解释每个核心文件；
- 能区分真实实现与文档声明；
- 能说明所有持久化数据位置。

### 贡献

- 至少完成一条端到端改造主线；
- 有明确设计取舍；
- 改造不是只换名字和界面；
- 贡献与上游边界清楚。

### 证据

- 可复现安装；
- 自动化验证；
- 关键失败场景；
- 可追溯数据；
- 有意义的提交历史；
- 文档与实现一致。

### 诚实

- 保留 License；
- 标注二次开发；
- 不引用未复现的性能数字；
- 不宣称没有实现的 MCP、零信任或企业级能力。

## 十三、本课最终结论

你不需要把 CyberClaw 假装成“从零独立发明”的项目。

更有价值的成果是：

> 我完整读懂了一个真实 Agent 仓库，建立了源码、测试和文档覆盖账本，识别出状态、安全、异步和协议边界，并围绕一条明确主线完成了可验证的工程重构。

这能同时体现：

- 快速学习陌生系统；
- 源码追踪；
- 架构判断；
- 工程实现；
- 测试与评测意识；
- 开源合规；
- 技术表达。

## 十四、学完本课应能回答

1. 为什么 fork 项目仍然可以成为高质量个人项目？
2. 原项目能力和个人贡献怎样划界？
3. 为什么推荐先做工程底座，再做 MCP？
4. 当前最重要的 P0、P1、P2 分别是什么？
5. 多会话与分层记忆为什么适合作为第一主线？
6. 程序级工具审批需要保存哪些状态？
7. 调度器为什么不应长期依赖普通 JSON 文件？
8. 原生 MCP 接入为什么必须经过工具策略层？
9. 什么样的性能数字才能写进简历？
10. 怎样诚实说明开源来源而不削弱个人贡献？


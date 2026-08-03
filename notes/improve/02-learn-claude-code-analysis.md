# learn-claude-code 项目分析与 CyberClaw 借鉴方案

> 研究对象：`E:\graduate_student\projects\learn-claude-code`  
> 研究范围：主 README、当前 `s01`～`s20` 课程实现、辅助文档和测试  
> 本文目的：提炼 Agent Harness 从最小循环逐步演化的设计方法，并辨别教学实现与可用于 CyberClaw 的工程实现之间的边界

## 1. 项目定位

learn-claude-code 是一个从最小 Agent Loop 逐步搭建 Claude Code-like Agent Harness 的课程项目。它并不是 Claude Code 官方源码，也不能证明所有机制与 Claude Code 内部实现完全一致；更准确的理解是：

> 它用一系列可运行的教学阶段，解释一个现代 Agent Harness 可以怎样逐步加入工具、权限、Hook、上下文、记忆、子 Agent、任务协作和 MCP。

仓库同时保留新旧学习轨道。本文以 README 当前推荐的 `s01`～`s20` 主线为准，旧的 `agents/`、`docs/`、`web/` 只作为补充，不把两套实现混为一谈。

## 2. 二十个阶段解决了什么

| 阶段 | 主题 | 核心问题 |
|---|---|---|
| s01 | Agent Loop | 模型、工具结果和下一轮决策如何闭环 |
| s02 | Tool Use | 怎样通过 schema 与 handler map 扩展能力 |
| s03 | Permission | 工具执行前怎样做拒绝、规则和用户审批 |
| s04 | Hooks | 怎样把权限、日志等横切逻辑移出主循环 |
| s05 | Todo | 怎样让模型维护当前任务的短期执行计划 |
| s06 | Subagent | 怎样隔离复杂探索，限制递归和能力 |
| s07 | Skills | 怎样只暴露技能目录，按需加载完整内容 |
| s08 | Compact | 怎样按成本分层处理上下文增长 |
| s09 | Memory | 怎样选择、提取和整合长期记忆 |
| s10 | System Prompt | 怎样按运行环境动态组装系统提示词 |
| s11 | Error Recovery | 怎样处理限流、超限、续写和模型降级 |
| s12 | Task Graph | 怎样表达依赖、阻塞、领取和完成状态 |
| s13 | Background Tasks | 怎样让长工具后台执行并把结果重新注入模型 |
| s14 | Cron | 怎样将定时触发与 Agent 消费串联 |
| s15 | Teams | 怎样通过任务板和邮箱进行多 Agent 协作 |
| s16 | Protocols | 怎样给计划审批、关停等协作流程增加协议 |
| s17 | Autonomous Agents | 怎样让空闲 Agent 主动发现和领取任务 |
| s18 | Worktree | 怎样为并行代码任务隔离 Git 工作区 |
| s19 | MCP | 怎样把运行期发现的外部工具接入统一工具池 |
| s20 | Integration | 怎样把前面机制组织进一条完整运行链 |

这些阶段最重要的教学方式是“每一步只增加一个机制，并保持核心循环稳定”。它帮助我们区分：哪些是 Agent 的必要内核，哪些是围绕内核生长的 Harness 能力。

## 3. 核心设计思想

### 3.1 模型提供 Agency，Harness 提供边界

项目强调不要把模型的每一步都写成僵硬工作流。模型负责根据当前上下文决定下一步；Harness 负责提供：

```text
可用工具
可靠观察结果
权限边界
上下文与记忆
预算和恢复
可追踪的执行环境
```

这对 CyberClaw 很重要。我们不需要把 LangGraph 改造成包含所有业务步骤的固定 DAG；应该保持 `agent → tools → agent` 的自主循环，把确定性的安全、状态和生命周期约束放到循环外围。

### 3.2 扩展工具不应不断修改主循环

s02 使用工具 schema 列表和 handler map：模型看到统一定义，运行时按名称分发。后续内置工具、Skill 和 MCP 工具都可进入相同工具池。

CyberClaw 已利用 LangChain Tool 和 `ToolNode` 具备部分基础，但目前工具集合在图创建时固定。建议增加独立 Tool Registry：

```text
Built-in Tool Provider ┐
Skill Tool Provider    ├→ Tool Registry → Policy → Executor
MCP Tool Provider      ┘
```

Registry 负责名称、版本、来源、schema 和冲突；LangGraph 只消费某一时刻经过筛选的工具快照。

### 3.3 权限是执行管线，不是 Prompt 建议

s03 把工具执行前的判断拆成三层：

```text
Hard Deny
→ Contextual Rules
→ User Approval
→ Execute
```

该顺序非常适合 CyberClaw：

- 明确越界或禁止能力直接拒绝；
- 低风险、已配置规则允许的操作直接执行；
- 高风险或影响不明确的调用需要展示精确参数并请求确认。

审批必须绑定规范化后的工具名、参数、影响范围和内容哈希。不能只让模型先“说明计划”，随后允许它执行任意不同命令。

### 3.4 Hook 处理横切关注点

s04 用 Hook 把以下逻辑从主循环移出：

```text
UserPromptSubmit
PreToolUse
PostToolUse
Stop
```

Hook 可以承载权限、日志、参数规范化、结果裁剪、指标和审计。但正确的调用顺序必须由运行时固定：Hook 不能绕过 Hard Deny，也不能因为某个 Hook 返回 allow 就跳过核心策略。

CyberClaw 可设计小型、类型化 Hook Bus，而不是照搬任意插件系统。首批只需要：

- `before_model`：构建上下文、记录调用；
- `before_tool`：规范化参数、策略和审批；
- `after_tool`：裁剪结果、持久化产物、记录事件；
- `after_run`：用量、状态和清理。

### 3.5 Todo 与持久任务图不是同一种状态

s05 的 Todo 是模型当前回合的短期计划；s12 的 Task Graph 是可以分配、阻塞、领取和持久化的协作任务。项目把两者分开是正确的。

CyberClaw 目前的 scheduled task 也不是 Agent Todo。未来应区分：

| 状态 | 生命周期 | 用途 |
|---|---|---|
| Run Plan/Todo | 单次请求 | 告诉模型当前步骤与进度 |
| User Task | 跨请求 | 用户希望跟踪的待办 |
| Scheduled Job | 时间触发 | 到期后产生一次运行请求 |
| Agent Task Graph | 多 Agent 协作 | 依赖、分配和领取 |

不要继续把这些概念都塞进同一 JSON 文件或同一工具描述中。

### 3.6 Skill 采用“目录常驻，正文按需加载”

s07 只把 Skill 元数据目录放入系统上下文，模型需要时再按注册名加载完整说明。这样避免每次请求都携带全部 Skill 文档。

它还体现了一个安全细节：模型选择 registry 中的 Skill ID，而不是自由提供磁盘路径。

CyberClaw 已有两阶段 Skill，但存在正文只返回 3000 字符、help/run 不强制、缓存和热更新不一致等问题。建议重构为：

```text
Skill Catalog
→ load_skill(skill_id)
→ 返回完整、版本化、经过大小控制的 instruction
→ 若 Skill 声明可执行动作，再转换成受策略控制的 Tool
```

Skill instruction 与 executable command 应分开。不是每份 Markdown Skill 都应该变成任意 Shell 命令。

### 3.7 上下文采用便宜操作优先的多层管线

s08/s20 的上下文处理比 CyberClaw 当前固定回合摘要更完整：

1. 超大工具结果写入 `.task_outputs/tool-results`，上下文只保留预览和引用；
2. 裁剪较早消息的中间内容，同时保护工具调用配对；
3. 将更旧工具结果替换为轻量占位；
4. 必要时用 LLM 生成摘要并保存完整 transcript；
5. Provider 仍返回 prompt too long 时做响应式紧急压缩。

该设计与 CoreCoder 的分层压缩一致，但增加了“结果外置”和“完整记录与模型视图分离”。这是 CyberClaw 最值得采用的组合方案。

### 3.8 长期记忆拆成选择、提取和整合

s09 没有简单地把所有 memory 文件都塞进 prompt，而是拆成三个问题：

```text
Selection：本轮需要哪些旧记忆？
Extraction：本轮产生了什么值得长期保留的新事实？
Consolidation：记忆过多时如何去重、合并和淘汰？
```

CyberClaw 当前的用户画像是模型调用 `save_user_profile` 后整文件覆盖，缺少冲突、来源、置信度、作用域和删除机制。建议将记忆设计为结构化记录，并把模型生成的候选记忆与正式写入分开，允许用户查看、纠正和删除。

### 3.9 系统提示词应由稳定片段与真实运行状态组装

s10 将 system prompt 拆成固定行为规则和运行时信息，例如 workspace、启用工具和相关记忆，并为稳定结果计算 cache key。

CyberClaw 当前在 `agent.py` 中维护一段很大的硬编码 system prompt，其中混入了安全声明、Skill 说明和工具行为，容易与实现漂移。建议使用 Prompt Builder：

```text
Base Identity
+ Runtime Policy
+ Current Capability Catalog
+ Workspace Boundary
+ Selected Memory
+ Session Context
```

任何安全边界仍由代码强制，Prompt 只负责向模型解释边界和期望行为。

### 3.10 错误恢复必须按错误类型处理

s11 区分了：

- 429/529 等临时错误：带抖动退避；
- 主模型失败：按配置切换 fallback；
- 输出上限：提高上限或续写；
- 上下文过长：紧急压缩后重试；
- 不可恢复 4xx：直接报告，而不是盲目重试。

CyberClaw 当前 Provider 层缺少统一 retry、fallback、usage 和 cost 管理。建议把恢复策略放入 Model Gateway，而不是分散在 CLI 或 Agent 节点的宽泛 `except` 中。

### 3.11 后台任务的结果必须重新进入 Agent 因果链

s13 允许明确指定后台运行，也可根据慢任务规则建议后台化。完成结果通过 `<task_notification>` 重新进入模型上下文。

其关键不在于开线程，而在于：后台任务必须拥有 task ID、状态、结果存储和一次明确的“结果被主 Agent 接收”事件。

CyberClaw 的 heartbeat 已经通过队列将到期任务交给同一 Agent worker，这是一个正确方向；但当前任务在消费前就从 JSON 中移除，崩溃可能丢失，事件还伪装成普通 HumanMessage。应改为类型化队列事件和可持久化领取/确认状态。

### 3.12 调度采用“生产者入队，单消费者串行 Agent”

s14 的调度器只负责产生事件，不在调度线程直接并发调用 Agent；队列由单一消费者串行处理，从而保护会话状态。

CyberClaw 主程序也已经采用类似队列，应保留这一优点，并补上：

- 时区感知；
- 持久化状态；
- 原子领取；
- missed-run policy；
- 幂等键；
- graceful shutdown；
- 用户输入和系统任务的类型/优先级区别。

### 3.13 多 Agent、协议和 Worktree 是后期能力

s15～s18 展示了任务板、JSONL mailbox、计划审批、自动领取和 Git worktree。可以学习其中的概念边界：

```text
共享任务状态
+ 消息通道
+ 明确协议
+ 隔离工作区
```

但这些教学实现存在竞态、非原子领取和审批不强制等限制。CyberClaw 当前首先是本地个人 Agent，不应在基础权限、会话和日志都不稳时加入团队协作。多 Agent 只作为 P3 可选方向。

### 3.14 MCP 是运行期动态工具提供者

s19 将 MCP 工具命名为 `mcp__server__tool`，在连接后执行 `list_tools`，把 schema 与 handler 动态加入统一工具池，再经过同一权限管线。

这是 CyberClaw 应采用的核心结构：

```text
MCP Server Session
→ Tool Discovery
→ Name Normalization / Collision Check
→ Schema Adapter
→ Tool Registry
→ Policy / Approval
→ call_tool
→ ToolResult Adapter
```

但课程中的 MCP Client 是进程内 mock server，不是真实 stdio、SSE 或 Streamable HTTP 实现。CyberClaw 不能复制它后就声称“原生 MCP”；必须使用真实 SDK/协议实现连接生命周期、取消、超时、重连、认证和 server 信任。

### 3.15 集成不应等于堆成单文件

s20 很好地展示了统一数据流：

```text
prepare_context
→ build_user_content
→ call_llm
→ dispatch tools
→ process notifications
```

但该阶段是两千多行的教学整合文件，包含较多重复逻辑。CyberClaw 应借鉴数据流，不照搬单文件组织；继续保持 Runtime、Agent、Policy、State、MCP 和 Observability 的模块边界。

## 4. 参考项目自身的工程边界

这些限制决定了“学习思想、重新实现”比“复制功能”更重要。

### 4.1 MCP 只是教学 mock

课程通过本地 handler 模拟工具发现和调用，没有完整网络传输、session 生命周期、OAuth、server 能力协商和重连。

### 4.2 文件型多 Agent 状态存在竞态

JSON task board 和 JSONL mailbox 缺少完整文件锁与事务。读后删除邮箱文件可能造成消息竞争，多个 Agent claim 同一任务也可能同时成功。

### 4.3 计划审批不等于能力阻断

课程说明中已承认，等待 plan approval 时 Agent 仍可能执行工具。真正审批必须位于执行器前，且没有批准令牌就无法产生副作用。

### 4.4 后台线程不是可靠任务系统

daemon thread 没有进程重启恢复、可靠取消、持久结果和资源回收。CyberClaw 可先实现同进程受控后台任务，但不能称为高可用任务平台。

### 4.5 Shell 规则仍不是真正沙盒

字符串规则无法隔离文件系统、环境变量、网络、子进程和 OS 权限。CyberClaw 必须明确安全目标，必要时使用低权限 worker 或容器。

### 4.6 Memory 写入并非原子事务

课程的 consolidation 存在先删除旧文件再写新文件、宽泛吞异常的问题。我们应使用事务/临时文件替换，并保留来源与可回滚版本。

### 4.7 测试覆盖以教学冒烟为主

仓库覆盖了部分压缩配对、Todo 输入和后台管理，但没有为所有二十阶段建立生产级正确性测试。CyberClaw 需要自行定义验收标准，不能引用参考项目的功能存在作为可靠性证据。

## 5. 建议吸收到 CyberClaw 的内容

| 借鉴点 | 建议实现 | 优先级 |
|---|---|---|
| Tool Registry | 统一内置、Skill、MCP 工具来源，处理命名、版本、冲突和动态刷新 | P0 |
| Tool Policy Pipeline | Hard deny、规则、审批、执行、审计形成不可绕过管线 | P0 |
| Typed Hooks | 在固定顺序中注入日志、权限、裁剪和指标 | P0 |
| Dynamic Prompt Builder | 根据真实会话、工具和 workspace 组装 prompt | P1 |
| Layered Context | 大结果外置、旧结果微压缩、摘要和紧急回退 | P1 |
| Structured Memory | 选择、候选提取、确认、整合、删除 | P1 |
| Model Recovery | 分类重试、fallback、超限压缩、usage/cost | P1 |
| Durable Scheduler | 调度生产者与 Agent 消费者解耦，加入领取和确认 | P2 |
| Real MCP Manager | 真实协议连接、动态发现、策略、超时和重连 | P2 |
| Subagent/Task DAG/Worktree | 仅在项目明确转向 Coding Agent 时增加 | P3 |

## 6. 对 CyberClaw 的最重要启发

learn-claude-code 最值得我们吸收的不是二十项功能，而是三条演进规则。

### 6.1 保持主循环稳定

增加权限、日志、MCP 或记忆时，不应让 `agent.py` 继续变成巨型条件分支。新能力通过 Registry、Policy、Hook、Context Builder 和 Store 接入。

### 6.2 所有副作用走同一条窄管线

内置工具、Skill 和 MCP 不应拥有三套独立安全逻辑：

```text
ToolCall
→ Normalize
→ Resolve Capability
→ Policy
→ Approval
→ Execute
→ Normalize Result
→ Persist / Observe
```

### 6.3 完整状态与模型视图分离

Session、任务、原始工具输出和审计事件应可靠保存；模型每轮只获得经过预算、选择和脱敏的 Context View。这样既能压缩 Token，也不必破坏恢复和审计证据。

## 7. 最终结论

learn-claude-code 为 CyberClaw 提供了一张较完整的 Agent Harness 能力地图，但不是可以直接复制的生产架构。

最应该迁移的是：

```text
可扩展 Tool Registry
不可绕过的 Policy/Approval
类型化 Hook
分层上下文和结构化记忆
分类错误恢复
动态 MCP 工具接入
调度与 Agent 执行解耦
```

暂不应优先迁移的是：

```text
团队 Agent
自治领取
复杂任务图
Git worktree
大量后台 Agent
```

这些能力只有在 CyberClaw 的单 Agent 会话、工具安全、状态持久化和可观测性成熟后才有意义。实现时应保留参考项目的许可与来源说明，明确哪些是设计借鉴、哪些是结合 CyberClaw 重新设计和验证的个人贡献。

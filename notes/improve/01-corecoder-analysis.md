# CoreCoder 项目分析与 CyberClaw 借鉴方案

> 研究对象：`E:\graduate_student\projects\CoreCoder`  
> 研究范围：README、`article/`、`notes/`、核心源码、测试与项目配置  
> 本文目的：提炼可以迁移到 CyberClaw 的工程思想，不复制代码，也不把参考项目已有成果包装成个人原创
> 时点说明：第 1～7 节保留改造前的分析与建议；第 8 节是完成 CoreCoder 借鉴后的实际实现状态。

## 1. 项目定位

CoreCoder 是一个小型 Coding Agent。它用较少代码实现了一个可以读写代码、搜索项目、运行命令、维护上下文、创建子 Agent、保存会话的命令行 Agent。

它最有价值的地方不是功能数量，而是把 Agent Harness 的几个关键问题写得很清楚：

```text
模型只负责决策
→ Agent 循环负责调度
→ Tool 负责真实副作用
→ Context Manager 控制模型可见历史
→ Session 保存可恢复状态
→ CLI 负责交互与展示
```

CoreCoder 适合当作“小而完整的运行时参考”，但它仍是教学型实现，不是生产级安全沙盒、完整权限系统或分布式多 Agent 平台。

## 2. 整体结构

| 模块 | 主要职责 | 值得关注的设计 |
|---|---|---|
| `corecoder/agent.py` | Agent 主循环、工具调度、中断修复 | 有界循环、工具消息配对、实例级工具权限 |
| `corecoder/llm.py` | Provider 调用、流式响应、重试、用量统计 | 统一模型边界、工具调用参数拼接、错误分类 |
| `corecoder/context.py` | Token 估算和分层压缩 | 轻处理优先、保护 tool-call 协议不变量 |
| `corecoder/tools/` | 文件、搜索、Shell、子 Agent | 明确 schema、小工具、可测试错误语义 |
| `corecoder/session.py` | 会话保存、加载与列表 | Session ID 清洗和最终路径校验 |
| `corecoder/cli.py` | REPL、一次性模式、斜杠命令 | 内核事件与界面展示解耦 |
| `tests/` | 协议、安全边界和异常路径测试 | 测试跨模块不变量，而不只测正常结果 |

## 3. 最值得学习的设计

### 3.1 显式、有上限的 Agent 循环

`Agent.chat()` 明确表达了完整反馈循环：

```text
用户消息进入历史
→ 调用 LLM
→ 无 tool_calls：返回最终回答
→ 有 tool_calls：记录调用并执行工具
→ 记录每个 tool result
→ 再次调用 LLM
```

循环受 `max_rounds` 约束，避免模型因重复失败或错误规划无限调用工具。这个设计的价值在于，执行预算是程序约束，不依赖提示词要求模型“适时停止”。

CyberClaw 使用 LangGraph 表达了相同循环，图结构更直观；因此不需要放弃 LangGraph 重写成手工 `for` 循环。但应把 CoreCoder 中的显式预算、错误分类和协议检查补到图节点外围。

### 3.2 工具能力属于 Agent 实例

CoreCoder 用当前 Agent 自己的 `_tool_by_name` 查找工具。子 Agent 创建时直接删除 `agent` 工具，因此它在结构上无法继续创建孙 Agent。

这里体现了一个重要原则：

> 限制能力最可靠的方式，是不把能力交给执行主体，而不只是写一句“请勿使用”。

分析时的 CyberClaw 把内置工具和 Skill 工具整体绑定给模型，安全限制主要依赖 system prompt 和工具说明。后续应引入按会话、角色和任务构建的 Tool Registry，使“可见工具集合”本身成为权限边界。

### 3.3 工具参数错误与内部异常分开

CoreCoder 在执行前使用函数签名绑定参数：

```text
签名绑定失败
→ 模型给错了参数，可让模型修正

绑定成功后工具内部报错
→ 工具实现或外部环境失败
```

这比把所有异常统一转成一段字符串更利于重试、观测和策略判断。CyberClaw 后续应使用结构化 `ToolResult`，至少包含：

```text
status
content
error_type
retryable
metadata
```

### 3.4 工具消息协议是需要保护的不变量

每个 assistant `tool_call` 都必须有对应的 tool reply。CoreCoder 在 Ctrl+C 中断时为未完成调用补上 `[interrupted]`，避免残缺历史导致下一次 Provider 请求失败。

它的上下文压缩也用 `_safe_split()` 防止保留下来的历史从一条孤立 tool 消息开始。

这说明 Agent 测试不能只验证“最后回答对不对”，还要验证跨多条消息的协议不变量：

- tool call ID 唯一且能配对；
- 多工具结果不会配错调用；
- 压缩、中断和异常不会留下孤儿消息；
- 恢复会话后消息序列仍可被 Provider 接受。

CyberClaw 把部分协议处理交给 LangGraph/LangChain，但应用层仍应补充消息合法性测试，不能因为使用框架就假设所有边界自动正确。

### 3.5 分层上下文压缩

CoreCoder 采用由轻到重的三级处理：

```text
约 50%：截短过长的旧工具输出
约 70%：用 LLM 总结旧对话，保留近期原文
约 90%：紧急折叠，只保留更短尾部
```

这里最值得吸收的是策略顺序：

1. 先做确定性、低成本处理；
2. 再使用有费用且可能失真的 LLM 摘要；
3. 最后才用激进丢弃避免请求直接超限；
4. 每层处理后重新估算，空间足够就停止；
5. 摘要模型失败时必须有确定性降级方案。

分析时的 CyberClaw 只按固定用户回合数触发摘要，并保留固定数量近期回合。建议改为 Token 预算驱动，并把完整事件历史与“本轮发给模型的压缩视图”分开保存。

### 3.6 并行前先声明副作用

CoreCoder 会用线程池并行执行一次响应中的多个工具调用，并按原调用顺序收集结果。这对独立 I/O 有效，但当前实现没有判断两个写工具是否冲突。

该实现真正带来的启示不是“所有工具都并行”，而是：

> 引入并发以后，每个有可变状态或副作用的工具都必须重新接受并发正确性审查。

CyberClaw 未来若支持并行工具，Tool Metadata 至少应声明：

```text
只读/写入
风险等级
读资源集合
写资源集合
是否允许并行
超时
是否可取消
```

默认只并行明确标记为只读且互不依赖的工具；写工具应串行、加资源锁，或先生成补丁再统一应用。

### 3.7 子 Agent 首先是上下文隔离

CoreCoder 子 Agent 使用新的 `messages` 和更小轮次预算，只把最终结果返回主 Agent，并移除递归创建子 Agent 的能力。

这揭示了子 Agent 的首要价值：把搜索、阅读和失败尝试留在独立上下文中，保护主 Agent 的窗口；任务分解和并行只是其次。

但 CyberClaw 当前的核心定位不是 Coding Agent，因此不应为了“看起来高级”立即加入多 Agent。只有出现明确的重型、可隔离子任务时，才值得增加受限子 Agent，并同时设计：任务输入结构、工具集合、预算、取消、结果格式和文件系统隔离。

### 3.8 精确替换和可审阅 Diff

CoreCoder 的编辑工具要求待替换字符串只能匹配一次：

- 0 次匹配：告诉模型目标不存在；
- 多次匹配：要求提供更精确上下文；
- 1 次匹配：执行替换并生成 unified diff。

这比 CyberClaw 目前只有覆盖/追加的通用写文件工具更适合代码修改，因为它减少误覆盖，并能把变更交给用户审批和审计。

建议未来增加 `edit_file`/`apply_patch` 类受限工具，并把 diff 作为 ToolResult metadata、审批内容和追踪事件的一部分。

### 3.9 会话路径的纵深防御

CoreCoder 对 Session ID 做两层保护：

1. 清洗用户输入，只保留安全文件名；
2. `resolve()` 后验证最终文件仍位于 sessions 根目录。

这是“输入净化 + 输出落点校验”的纵深防御。CyberClaw 的 office 路径当前使用字符串 `startswith()` 判断，无法可靠处理相邻前缀目录和符号链接。后续路径控制应采用规范化绝对路径与 `Path.is_relative_to()`，并明确 symlink/Junction 策略。

### 3.10 内核事件与 CLI 展示解耦

CoreCoder 的内核通过 `on_token`、`on_tool` 通知外层，CLI 决定怎样显示。一次性模式还使用明确退出码区分成功、普通错误和 Ctrl+C。

CyberClaw 已经有事件日志和终端队列，但事件类型不完整，运行时、日志与 Monitor 之间存在漂移。可以进一步统一成类型化事件总线，让 CLI、Monitor、测试和未来 Web UI 消费同一份稳定事件协议。

### 3.11 测试“隐藏不变量”

CoreCoder 测试里较有价值的不是数量，而是测试对象：

- 中断后补齐 tool reply；
- 压缩不能产生孤儿 tool 消息；
- 工具权限是实例级；
- 错误参数和工具内部错误分开；
- Bash cwd 不在线程间串数据；
- Session 路径穿越和损坏 JSON；
- Provider 的重试和兼容性回退。

这些测试把跨模块设计约束固定下来。CyberClaw 后续也应先列系统不变量，再围绕它们设计测试，而不是只写几个创建对象和正常调用的单元测试。

## 4. 建议吸收到 CyberClaw 的内容

| 借鉴点 | CyberClaw 分析时情况 | 建议的重新实现方式 | 优先级 |
|---|---|---|---|
| 有界执行预算 | LangGraph 循环无明确应用级预算 | 为每次 run 增加轮次、Token、时间和工具次数预算 | P0 |
| 实例级工具权限 | 默认整组工具绑定给模型 | 建立会话级 Tool Registry 与 Capability Set | P0 |
| 结构化工具结果 | 异常多以普通文本处理 | 统一 `ToolResult` 和错误分类 | P0 |
| 协议不变量测试 | 依赖框架，深层异常路径不足 | 增加中断、压缩、多工具和恢复测试 | P0 |
| 路径纵深防御 | `startswith()` 路径判断 | `resolve()`、`is_relative_to()`、链接策略 | P0 |
| 分层上下文 | 固定 40 回合压缩并保留 10 回合 | Token 预算、工具结果裁剪/外置、摘要和紧急降级 | P1 |
| 可审阅编辑 | 只有写入/追加 | 唯一匹配编辑、diff、审批、版本检查 | P1 |
| 受控并行 | 当前主链基本串行 | 只读工具并行，写资源冲突检测 | P2 |
| 子 Agent 隔离 | 尚未实现 | 仅在有明确场景时做受限、不可递归子 Agent | P3 |

## 5. 不应直接照搬的部分

### 5.1 Shell 黑名单不是安全沙盒

CoreCoder 自己也明确把 Bash 规则视为防误操作保护，而不是真正隔离。CyberClaw 当前也存在相同问题，不能用另一套正则替换当前正则后继续宣称“严格沙盒”。

### 5.2 无条件并行所有工具

CoreCoder 一次收到多个 tool call 就全部进入线程池，可能造成写冲突。CyberClaw 应先建立工具副作用元数据和资源冲突策略，再考虑并行。

### 5.3 粗略 Token 估算直接作为完整预算

字符数除以常数适合触发启发式压缩，但没有完整计入 system prompt、工具 schema、输出预留和 Provider 协议开销。CyberClaw 可以先用近似值，但必须保留安全余量，并记录 Provider 返回的真实 usage。

### 5.4 同步子 Agent 与共享工具实例

CoreCoder 子 Agent 共享 LLM、文件系统和多数工具实例，并非安全或状态隔离。若 CyberClaw 以后加入子 Agent，需要独立运行状态、预算和取消机制；涉及写操作时还要考虑独立 worktree、容器或受限工作区。

### 5.5 仅保存 messages 的 Session

CoreCoder 的 Session 适合续聊，但不能恢复待执行工具、预算、cwd、审批和副作用状态。CyberClaw 已使用 LangGraph SQLite checkpoint，应该在其上设计明确的 Session Manager，而不是退回简单 JSON 会话文件。

## 6. 对 CyberClaw 改造的具体影响

CoreCoder 最适合影响 CyberClaw 的四个底层设计：

### 6.1 Runtime Invariants

明确并测试以下不变量：

```text
每个 ToolCall 最终都有 ToolResult
同一 run 不超过预算
未授权工具永远不能进入执行器
压缩前后消息协议合法
会话和文件路径不能越过所属根目录
退出后不再接收新任务且所有后台任务有明确结局
```

### 6.2 Tool Contract

工具不再只有 name、description、args_schema 和一段字符串结果，而应包含能力、风险、并发和审计信息。

### 6.3 Context View

数据库保存完整事件，Context Manager 只负责构建模型可见视图。裁剪不再等于删除历史。

### 6.4 Test Strategy

先写安全、协议和生命周期失败场景，再实现功能；尤其要把“平时看不见、特定边界才触发”的不变量变成自动化测试。

## 7. 最终结论

CoreCoder 给 CyberClaw 的最大价值不是增加更多功能，而是教会我们用较少、清晰、可测试的机制守住 Agent 运行时边界。

建议吸收的核心可以概括为：

```text
能力通过工具集合授予
副作用通过结构化契约管理
上下文通过分层策略控制
跨消息协议通过不变量测试保护
会话和文件路径使用纵深防御
界面通过事件消费运行时状态
```

后续实现时应保留 CoreCoder 的 MIT 许可和来源说明；借鉴设计思想后重新结合 LangGraph、CyberClaw 的异步终端和产品定位实现，不能把参考代码或上游成果描述成自己的原创。

## 8. 实际借鉴结果（已完成）

截至 2026-08-08，CoreCoder 借鉴阶段已经完成。CyberClaw 没有照搬 CoreCoder 的手写 Agent 循环，而是在保留 LangGraph 主结构的前提下，用 7 个独立提交重新实现了适合本项目的运行时边界。

| 提交 | 实际落地内容 | 主要代码位置 |
|---|---|---|
| `606d0bb` | 引入实例级 `ToolSpec` 与 `ToolRegistry`，统一工具来源、风险、只读、并发和超时元数据，并拒绝重名工具 | `cyberclaw/core/tools/contracts.py`、`registry.py` |
| `e75214b` | 建立统一工具执行入口，将成功、参数错误、超时、拒绝、中断和内部异常规范化为 `ToolResult`，再转换为 LangChain `ToolMessage` | `cyberclaw/core/tools/executor.py` |
| `aa41a3d` | 为每个新用户任务增加模型调用、工具调用和 LangGraph 递归上限，超限后由程序停止运行 | `cyberclaw/core/runtime.py`、`agent.py` |
| `49c1374` | 取消任务后查找没有结果的 tool call，并幂等补写 `INTERRUPTED` ToolResult，保护消息协议 | `cyberclaw/core/agent.py`、`entry/main.py` |
| `f7266f1` | 只并行同一模型响应中连续且显式声明 `concurrent_safe` 的工具；未知、写入及未授权并发的工具形成串行屏障 | `cyberclaw/core/tools/executor.py` |
| `6dfc7a9` | 引入近似 Token 预算、旧工具结果裁剪、完整回合收缩和单轮溢出保护，构建发送给模型的分层 Context View | `cyberclaw/core/context.py`、`agent.py` |
| `05f49d9` | 统一 Agent、摘要与配置探测的模型调用边界；分类瞬时/永久错误，执行有限指数退避，设置请求超时并记录 attempts/usage | `cyberclaw/core/provider.py`、`agent.py` |

最终兼容验证结果为：

```text
143 passed, 1 skipped, 46 subtests passed
cyberclaw --help 正常
现有 .env + 学校 OpenAI-compatible API 真实 Agent 调用正常
Provider attempts 与 usage 可被读取
```

### 8.1 已吸收的核心思想

```text
工具能力先注册再执行
→ 工具结果使用结构化契约
→ 每次 run 受程序预算约束
→ 中断后仍保持 tool-call 配对
→ 只有显式安全的工具才能并行
→ 上下文按预算分层收缩
→ 所有模型调用经过统一 Provider 边界
```

### 8.2 明确没有照搬或尚未实现的内容

- 没有加入 CoreCoder 的 Coding Agent 文件编辑、精确替换和 unified diff；
- 没有加入子 Agent、多 Agent 或递归任务分解；
- 没有把 SQLite checkpoint 退化成只保存 messages 的 JSON Session；
- Tool Registry 已完成，但完整 Policy Engine、逐次用户审批和 Hook Pipeline 尚未实现；
- Context 已有分层可见视图，但完整 transcript/artifact store、结构化 Memory Pipeline 尚未实现；
- Provider 已有分类重试、超时和 Token usage，但没有 fallback、价格表和费用估算。

因此，“CoreCoder 借鉴完成”指计划中的 7 个底层运行时改造已经实现并验证，不表示 CyberClaw 的全部目标架构已经完成。下一阶段应进入 learn-claude-code 借鉴，重点补充 CoreCoder 没有覆盖完整的 Permission、Hook、Session/Event、Memory 和 MCP 等 Harness 能力。

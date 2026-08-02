# 用 CyberClaw 学懂一个 LangGraph Agent，再把它改成自己的项目

这套学习材料服务于两个目标：

第一，沿着 CyberClaw 的真实运行链路，理解一个终端 Agent 如何连接模型、状态图、工具、记忆、定时任务与技能系统。第二，在理解现有实现和边界之后，完成有证据、有测试、能写进简历的工程化改造。

它不是 README 的重复说明，也不是逐行翻译代码。每一课只聚焦一个子系统，并回答四个问题：

1. 这个子系统解决什么问题？
2. 数据怎样在代码中流动？
3. 当前实现为什么这样设计？
4. 它距离更可靠的工程实现还差什么？

## 开始学习前的基线

当前项目已经完成以下验证：

- 使用 uv 创建了项目级 `.venv`，Python 版本为 3.11；
- CyberClaw CLI 可以正常启动；
- OpenAI 兼容模型可以完成普通对话；
- `get_current_time`、`calculator`、`write_office_file`、`read_office_file` 工具调用成功；
- `workspace/state.sqlite3` 可以在程序重启后恢复历史对话；
- 现有测试在 Windows 上为 52 项通过、1 项因临时文件句柄语义失败。

这意味着我们不是在纸上分析一个无法运行的仓库，而是在解释一套已经亲手验证过的系统。

## article 和 notes 如何配合

每一课分成两个阶段。

`article/` 由课程材料组成，负责提供：

- 问题背景和设计动机；
- 对应源码与测试入口；
- 关键调用链；
- 当前实现的工程取舍；
- 真实能力边界与进一步问题。

`notes/` 是每课的精炼复盘稿。后续课程已经预先生成，但学习时仍应先脱离参考答案独立复述，再用 notes 校正并补充自己的理解。笔记不能只是 article 的缩写，而要按照下面的结构重新组织：

```text
# 课次｜标题

> 对应源码
> 辅助源码
> 对应测试

## 一、本课核心内容
用自己的语言解释数据、流程、约束和边界

## 二、自测题与参考答案
先脱离文章回答，再对照代码修正

## 三、面试追问与回答思路
从当前实现推导工程问题和改进方案
```

如果一段内容只能从文章里复述，却不能在源码中指出位置、不能用运行现象证明，就还没有真正掌握。

## 每一课的固定学习流程

1. **读 article**：先建立完整调用链，不急着记细节。
2. **对照源码**：从入口开始追踪，不按文件名随机阅读。
3. **关闭资料复述**：独立画出流程并解释关键约束。
4. **先答 notes 问题**：不要先看参考答案。
5. **对照 notes 校正**：补充遗漏、边界和面试表达。
6. **按需验证**：只有仍不确定时，再单独进行调试、运行或测试。
7. **复盘缺口**：把仍不能解释的问题留到下一课或改造阶段。

每课完成的标准不是“看完了”，而是同时满足：

- 能从用户输入讲到最终输出；
- 能说出核心状态保存在哪里；
- 能指出至少一个现有实现的优点；
- 能指出至少一个真实边界或风险；
- 能回答本课自测题；
- 能在不看 article 的情况下读懂并修正本课 notes。

## 全量覆盖承诺

如果目标只是“理解主要架构”，八个主题足够；如果目标是“所有代码全部看完”，还必须增加覆盖账本。

本系列采用下面的全量标准：

- `cyberclaw/`、`entry/`、`examples/`、`tests/` 中每个 Python 文件都必须归入具体课次；
- 每个核心函数、类、全局对象和重要副作用都必须被解释；
- 每个测试文件都要说明它证明了什么、没有证明什么；
- `setup.py`、`requirements.txt`、`.env.example`、README、CHANGELOG 和关键 docs 都要核对；
- 文档宣称但代码没有实现的能力也必须记录，不能把 README 当成事实；
- 图片等二进制素材只检查用途和引用关系，不逐字节分析；
- 自动生成内容、`.venv`、运行时数据库、日志和缓存不按源码逐行学习，但要理解其格式与生命周期。

完整映射记录在 [`00-source-coverage.md`](00-source-coverage.md)。每完成一课，都要更新覆盖状态。只有所有条目完成，才能称为“看完了整个项目”。

## 课程路线

### 第 1 课：Agent 不是 while 循环，而是一张状态图

对应源码：

- `cyberclaw/core/agent.py`
- `cyberclaw/core/context.py`
- `tests/test_agent.py`

从一次 `read_office_file` 调用出发，理解 `StateGraph`、`AgentState`、`ToolNode`、`tools_condition`、消息 reducer 和图循环。

### 第 2 课：工具抽象与基础内置工具

对应源码：

- `cyberclaw/core/tools/base.py`
- `cyberclaw/core/tools/builtins.py`
- `tests/test_builtins.py`

理解 LangChain Tool 的定义、同步与异步执行接口、装饰器、工具注册，以及时间、计算器、用户画像和任务 CRUD。

### 第 3 课：定时任务状态机与心跳调度

对应源码：

- `cyberclaw/core/tools/builtins.py`
- `cyberclaw/core/heartbeat.py`
- `cyberclaw/core/bus.py`
- `entry/main.py`
- `tests/test_heartbeat.py`

追踪任务从创建、JSON 持久化、到期扫描、重复续期到重新注入 Agent 队列的完整生命周期，并解释 Windows 临时文件测试为什么失败。

### 第 4 课：office 文件工具与“沙盒”的真实边界

对应源码：

- `cyberclaw/core/config.py`
- `cyberclaw/core/tools/sandbox_tools.py`
- `cyberclaw/core/agent.py`

理解路径规范化、读写截断、Shell cwd、命令黑名单与超时。重点区分“提示词约束”“应用层路径检查”和“操作系统级隔离”。

### 第 5 课：模型 Provider、配置与 OpenAI 兼容协议

对应源码：

- `cyberclaw/core/provider.py`
- `cyberclaw/core/config.py`
- `entry/cli.py`
- `.env.example`

理解 DeepSeek、学校模型网关、OpenAI、Anthropic 与 Ollama 如何被适配成统一的 ChatModel，以及 `.env`、Provider 名称、模型 ID 和 Base URL 各自控制什么。

### 第 6 课：短期状态、长期画像与上下文压缩

对应源码：

- `cyberclaw/core/context.py`
- `cyberclaw/core/agent.py`
- `cyberclaw/core/tools/builtins.py`
- `entry/main.py`
- `tests/test_context_advanced.py`

区分 SQLite checkpoint、消息历史、摘要、用户画像和 office 文件。理解 `RemoveMessage`、按用户轮次裁剪，以及为什么更换模型后历史仍然存在。

### 第 7 课：异步终端与运行时协调

对应源码：

- `entry/main.py`
- `cyberclaw/core/bus.py`

理解 `asyncio` 如何同时维护用户输入、Agent worker、终端刷新和心跳协程；分析同步 LLM 调用如何进入异步运行时。

### 第 8 课：Skill 懒加载、两阶段执行与 MCP 真相

对应源码：

- `cyberclaw/core/skill_loader.py`
- `tests/test_lazy_loader.py`
- `tests/test_two_phase_skills.py`
- `docs/LAZY_LOADING_GUIDE.md`

理解为什么启动时只读取 metadata、为什么第一次调用先返回帮助、缓存如何失效，以及 Skill 最终怎样借助 Shell 工具执行。同时核对 README 的 MCP 声明：核心仓库没有 MCP 协议实现，只有通过外部 Skill/CLI 间接扩展的可能性。

### 第 9 课：CLI、配置向导与打包入口

对应源码：

- `setup.py`
- `entry/cli.py`
- `entry/__init__.py`
- `cyberclaw/__init__.py`
- `cyberclaw/core/__init__.py`
- `requirements.txt`
- `.env.example`

理解 `cyberclaw.exe` 如何生成、Typer 如何分发命令、配置向导如何写 `.env`，以及当前依赖声明和开发安装说明的缺口。

### 第 10 课：审计日志、后台线程与监控终端

对应源码：

- `cyberclaw/core/logger.py`
- `entry/monitor.py`

理解单例 logger、无界队列、守护线程、退出刷新、JSONL 事件和 tail monitor，并核对“透明”究竟能观察到哪些信息。

### 第 11 课：测试、示例、文档与实现一致性

对应内容：

- `tests/` 全部测试；
- `examples/` 全部示例；
- `docs/` 三篇懒加载文档和对比页面；
- `README.md`、`CHANGELOG.md`、`LICENSE`；
- README 引用的图片和项目结构。

这一课建立代码、测试和文档三方对照表，专门找“文档说有、代码没有”“测试通过但功能仍有边界”的地方。

### 第 12 课：工程审计与个人项目改造

覆盖内容：

- 依赖与打包；
- Windows 编码；
- 多会话管理；
- 异步模型调用；
- 工具审批；
- 沙盒强化；
- 调度服务；
- 测试与 CI；
- 可观测性与评测。

这一课不再只是阅读。我们会从现有缺口中选择一条主线，写设计、补测试、实现功能，并形成能够诚实写入简历的成果。

## 全部课程文件

课程文件已经一次性准备完成；“材料已生成”不代表“课程已掌握”，真实学习进度以 [`00-source-coverage.md`](00-source-coverage.md) 为准。

| 课次 | 主题 | Article | Notes | 当前学习状态 |
|---:|---|---|---|---|
| 01 | Agent 状态图 | [阅读](01-agent-graph.md) | [复盘](../notes/01-agent-graph.md) | 已掌握 |
| 02 | 工具抽象与内置工具 | [阅读](02-tool-abstraction-and-builtins.md) | [复盘](../notes/02-tool-abstraction-and-builtins.md) | 已掌握 |
| 03 | 定时任务与 Heartbeat | [阅读](03-scheduled-task-heartbeat.md) | [复盘](../notes/03-scheduled-task-heartbeat.md) | 已掌握 |
| 04 | office 与沙盒边界 | [阅读](04-office-sandbox-boundaries.md) | [复盘](../notes/04-office-sandbox-boundaries.md) | 已掌握 |
| 05 | Provider 与配置 | [阅读](05-provider-and-configuration.md) | [复盘](../notes/05-provider-and-configuration.md) | 已掌握 |
| 06 | 记忆与上下文压缩 | [阅读](06-memory-and-context-compression.md) | [复盘](../notes/06-memory-and-context-compression.md) | 已掌握 |
| 07 | 异步终端运行时 | [阅读](07-async-terminal-runtime.md) | [复盘](../notes/07-async-terminal-runtime.md) | 已掌握 |
| 08 | Skill 与 MCP | [阅读](08-skill-lazy-loading-and-mcp.md) | [复盘](../notes/08-skill-lazy-loading-and-mcp.md) | 已掌握 |
| 09 | CLI、环境与打包 | [阅读](09-cli-packaging-environment.md) | [复盘](../notes/09-cli-packaging-environment.md) | 概览完成 |
| 10 | 日志与 Monitor | [阅读](10-audit-logging-monitor.md) | [复盘](../notes/10-audit-logging-monitor.md) | 概览完成 |
| 11 | 测试与文档一致性 | [阅读](11-tests-examples-doc-consistency.md) | [复盘](../notes/11-tests-examples-doc-consistency.md) | 概览完成 |
| 12 | 工程审计与个人改造 | [阅读](12-engineering-audit-personal-refactor.md) | [复盘](../notes/12-engineering-audit-personal-refactor.md) | 概览完成 |

## 推荐顺序

前七课建议按顺序完成。它们构成 CyberClaw 的主链路与运行时：

```text
模型配置
→ Agent 状态图
→ 工具执行
→ 消息与记忆
→ 异步运行时
```

第 8 至第 11 课覆盖扩展、入口、可观测性和仓库一致性。第 12 课必须放在全量阅读之后，否则很容易把“重写”误认为“理解”。

当前已经完成第 1～4 课。下一步从第 5 课开始，先阅读 Provider 与配置 article，再沿 `.env → get_provider() → ChatModel → bind_tools()` 对照源码。

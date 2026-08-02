# CyberClaw 源码覆盖账本

这个文件回答一个简单但严格的问题：仓库中的每一部分，在哪一课被完整学习？

状态约定：

```text
未开始：尚未进入对应课程
进行中：article 已读，正在对照代码或写 notes
已掌握：完成源码复述、自测、必要验证和 notes 审核
```

只有“已掌握”才算完成，不以 article 是否存在为准。

第 9～12 课已完成概览式学习，但没有逐文件完成源码复述、自测和 notes 审核，因此课程索引记为“概览完成”，本账本仍如实保留“未开始”或“进行中”，不等同于“已掌握”。

## 核心源码

| 文件 | 课次 | 状态 | 必须掌握的内容 |
|---|---:|---|---|
| `cyberclaw/core/agent.py` | 01、04、06 | 已掌握 | 状态图、Sandbox Prompt、摘要压缩、长期画像与最终模型上下文 |
| `cyberclaw/core/context.py` | 01、06 | 已掌握 | AgentState、add_messages、完整用户回合分组与上下文裁剪 |
| `cyberclaw/core/tools/base.py` | 02 | 已掌握 | BaseTool、同步/异步入口、装饰器 |
| `cyberclaw/core/tools/builtins.py` | 02、03、06 | 已掌握 | 工具接口与注册、任务 CRUD、用户画像整文件覆盖语义 |
| `cyberclaw/core/heartbeat.py` | 03 | 已掌握 | 到期判断、重复续期、锁、队列注入 |
| `cyberclaw/core/bus.py` | 03、07 | 已掌握 | 用户输入与 Heartbeat 共用的 `asyncio.Queue`、生产者/单消费者关系及未使用的 `emit_task()` |
| `cyberclaw/core/tools/sandbox_tools.py` | 04 | 已掌握 | 路径校验、文件读写、Shell、平台差异、安全边界 |
| `cyberclaw/core/provider.py` | 05 | 已掌握 | Provider 工厂、协议适配、参数优先级、Base URL、API Key 与可选依赖边界 |
| `cyberclaw/core/config.py` | 04、05、06 | 已掌握 | `.env` 加载、工作区派生路径、数据库/画像/任务/office 的保存位置与导入副作用 |
| `cyberclaw/core/skill_loader.py` | 08 | 已掌握 | metadata 扫描、闭包延迟加载、LRU、帮助与执行、热更新边界、两阶段工具与 MCP 区别 |
| `cyberclaw/core/logger.py` | 10 | 未开始 | 单例、队列、后台线程、关闭语义 |

## 入口与运行时

| 文件 | 课次 | 状态 | 必须掌握的内容 |
|---|---:|---|---|
| `entry/main.py` | 03、05、06、07 | 已掌握 | Heartbeat、Provider/Model、SQLite、PromptSession、队列 worker、spinner、节点事件流与退出竞态 |
| `entry/cli.py` | 05、09 | 进行中 | 第 05 课配置向导、环境变量与 `run` 配置链已掌握；Typer 打包入口和 monitor 待第 09 课 |
| `entry/monitor.py` | 10 | 未开始 | tail、事件解析、Rich 渲染 |
| `entry/__init__.py` | 09 | 进行中 | 包结构中的作用 |
| `cyberclaw/__init__.py` | 09 | 进行中 | 包结构中的作用 |
| `cyberclaw/core/__init__.py` | 09 | 进行中 | 包结构中的作用 |

## 测试

| 文件 | 主要课次 | 状态 |
|---|---:|---|
| `tests/test_agent.py` | 01 | 已掌握 |
| `tests/test_builtins.py` | 02、03、06、11 | 进行中 |
| `tests/test_sandbox_tools.py` | 04、11 | 未开始 |
| `tests/test_config_and_skill_loader.py` | 05、08 | 未开始 |
| `tests/test_context_advanced.py` | 06、11 | 未开始 |
| `tests/test_heartbeat.py` | 03 | 已掌握 |
| `tests/test_lazy_loader.py` | 08 | 未开始 |
| `tests/test_two_phase_skills.py` | 08、11 | 未开始 |

第 11 课会再次横向检查全部测试，包括测试发现机制、平台差异、mock 边界和未覆盖路径。

## 示例、打包与文档

| 文件或目录 | 课次 | 状态 |
|---|---:|---|
| `examples/basic_usage.py` | 07、11 | 进行中（第 07 课同步入口与多轮状态边界已掌握；文档一致性待第 11 课） |
| `examples/benchmark_lazy_loading.py` | 08、11 | 进行中 |
| `setup.py` | 09 | 进行中 | 包发现、依赖声明与命令行入口 |
| `requirements.txt` | 09 | 进行中 | 直接依赖与版本范围 |
| `.env.example` | 05、09 | 进行中（第 05 课模型字段已掌握；打包与分发边界待第 09 课） |
| `.gitignore` | 09、11 | 进行中 | 本地环境、密钥和运行产物的忽略规则 |
| `.vscode/settings.json` | 09、11 | 进行中 | 编辑器环境管理配置 |
| `article/`、`notes/` | 学习辅助 | 第 1～12 课材料已生成；不计入源码掌握状态 |
| `README.md` | 11 | 未开始 |
| `CHANGELOG.md` | 11 | 未开始 |
| `LICENSE` | 11 | 未开始 |
| `docs/LAZY_LOADING_GUIDE.md` | 08、11 | 进行中 |
| `docs/LAZY_LOADING_QUICKSTART.md` | 08、11 | 进行中 |
| `docs/LAZY_LOADING_SUMMARY.md` | 08、11 | 进行中 |
| `docs/two_phase_comparison.html` | 08、11 | 未开始 |
| `docs/*.png` | 11 | 未开始 |

## 运行时产物

这些内容不按源码逐行学习，但必须理解生命周期：

| 路径 | 相关课次 | 作用 |
|---|---:|---|
| `.env` | 05、09 | 本地模型配置与密钥 |
| `.venv/` | 09 | 项目依赖环境 |
| `workspace/state.sqlite3` | 06 | LangGraph checkpoint |
| `workspace/memory/` | 06 | 长期用户画像 |
| `workspace/office/` | 04、08 | 文件工位与 Skills |
| `workspace/tasks.json` | 03 | 定时任务 |
| `logs/*.jsonl` | 10 | 审计事件 |

## MCP 覆盖说明

README 多处宣称支持 MCP，并推荐：

```text
mcporter
mcp-builder
```

但当前仓库没有：

- MCP Python 依赖；
- MCP client 或 server；
- stdio、SSE 或 Streamable HTTP transport；
- tool/resource/prompt discovery；
- MCP session 生命周期；
- MCP 相关测试。

因此本项目核心当前实现的是 Skill 加载，不是 MCP。外部 Skill 可以调用一个独立的 MCP CLI，从而形成间接集成，但协议能力属于外部程序，不属于 CyberClaw 核心。

第 8 课会详细比较：

```text
Tool：模型可调用的结构化函数
Skill：带说明文档和脚本的本地能力包
MCP：客户端与外部服务交换工具、资源和提示的协议
```

“实现原生 MCP client”可以成为第 12 课的个人改造候选方向。

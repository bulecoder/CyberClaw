# 第 11 课｜测试、示例、文档与实现一致性

> 测试范围：`tests/` 中全部 Python 文件  
> 示例范围：`examples/basic_usage.py`、`examples/benchmark_lazy_loading.py`  
> 文档范围：`README.md`、`CHANGELOG.md`、`docs/`、`LICENSE`

## 一、本课要解决的问题

读懂源码之后，还不能直接相信：

```text
测试全部通过
README 写着支持
示例能运行
CHANGELOG 给出性能数字
```

这四类材料的证据强度不同。

本课采用三方对照：

```text
代码：实际实现了什么
测试：哪些行为被自动验证
文档：项目对外宣称什么
```

真正可靠的结论必须回答：

1. 声明能否在源码中定位；
2. 测试是否执行了真实目标逻辑；
3. 断言是否足以证明该声明；
4. 数据是否由可复现方法产生；
5. 文件名、命令和路径是否真实存在；
6. 平台差异是否被考虑；
7. 文档是否把未来规划写成当前能力。

这是从“会读代码”走向“会做工程审计”的一课。

## 二、怎样给证据分级

可以把仓库材料分为五类：

### 1. 单元测试

直接调用一个函数或类，隔离外部依赖，验证明确输入输出。

适合证明：

- 路径函数对某个输入的行为；
- reducer 辅助函数的分组结果；
- CRUD 对临时文件的修改。

### 2. 集成测试

让多个真实组件共同运行，例如：

```text
Agent 图
+ fake model
+ ToolNode
+ checkpointer
```

适合证明组件之间的协议和状态流。

当前仓库这类覆盖较少。

### 3. 端到端测试

从 CLI 或用户入口经过模型、图、工具和持久化取得最终结果。

真实云模型会带来成本和不确定性，通常应使用可脚本化 fake model 建立稳定主测试，再把真实模型验证作为单独 smoke/eval。

### 4. 评测与 benchmark

回答的是质量和性能问题：

- 工具选择成功率；
- 冷启动时间；
- 内存占用；
- Token 成本。

它们需要固定环境、重复运行、统计方法和原始结果，不能等同于普通 pass/fail 测试。

### 5. 示例和说明文档

它们帮助用户理解用法，但通常不自动执行，也不保证与当前代码同步。

## 三、当前测试基线

此前在当前 Windows 学习环境中已经得到：

```text
pytest 收集 53 项
52 项通过
1 项失败
```

失败来自 Windows 对仍打开的 `NamedTemporaryFile` 的删除语义。

这个结果只能说明：

> 在当时的代码、依赖、系统和测试输入下，52 个断言路径成立，另有一个平台相关测试夹具问题。

它不能自动推出：

- 所有功能都正确；
- Agent 主链全部被覆盖；
- 沙盒是安全的；
- 文档中的性能数字成立；
- 所有操作系统都通过；
- 云端模型行为稳定。

## 四、逐个审计测试文件

## 4.1 `tests/test_agent.py`

### 实际测试内容

它包含：

- `AgentState` 可用普通字典方式初始化；
- 不传工具时可以创建并编译 Agent；
- 传入自定义 LangChain tool 时可以创建 Agent；
- 传入 `MemorySaver` checkpointer 时可以创建 Agent。

模型通过 Mock 替代，`bind_tools()` 也返回 Mock。

### 它证明了什么

- `create_agent_app()` 的基本组装代码没有立即抛错；
- 自定义工具参数和 checkpointer 参数能走到图编译；
- `AgentState` 字段形状符合预期。

### 它没有证明什么

测试从未真正调用：

```text
app.invoke()
app.stream()
app.astream()
```

因此没有验证：

- `agent → tools → agent` 循环；
- 工具结果是否回到模型；
- `tools_condition` 分支；
- 摘要生成；
- `RemoveMessage`；
- 用户画像；
- SQLite 重启恢复；
- 审计事件；
- 真实模型工具调用。

### Mock target 还有隔离风险

`agent.py` 使用：

```python
from .provider import get_provider
```

测试装饰器 patch 的是：

```text
cyberclaw.core.provider.get_provider
```

如果 `cyberclaw.core.agent` 已经导入，它内部保存的是自己的函数引用，patch 原模块不一定能替换它。

更可靠的目标应是使用方名称：

```text
cyberclaw.core.agent.get_provider
cyberclaw.core.agent.load_dynamic_skills
```

当前测试结果可能受模块导入顺序影响。

## 4.2 `tests/test_builtins.py`

### 实际覆盖

- 当前时间工具返回一段字符串；
- 计算器处理若干正常和非法表达式；
- 用户画像整文件写入；
- 定时任务创建、列出、修改和删除；
- 模型信息工具读取环境变量。

### 有价值的部分

任务 CRUD 大多使用临时 JSON 文件，并检查文件内容或后续列表结果。这比只断言函数“不报错”更有意义。

### 时间测试断言过弱

工具输出使用英文冒号：

```text
当前本地系统时间是:
```

测试替换的却是中文冒号：

```text
当前本地系统时间是：
```

替换失败后，`strptime()` 很可能报错。异常分支只断言：

```python
len(time_str) > 0
```

所以即使日期格式错误，该测试仍可能通过。

### 计算器测试的边界

它证明列出的非法字符串被拒绝，但没有系统覆盖：

- 超大幂运算造成资源耗尽；
- 很深的括号；
- 超长表达式；
- 浮点特殊边界；
- 解析器的完整允许语法。

“这些恶意样例失败”不等于“计算器对所有攻击安全”。

### 日期构造存在月末风险

测试使用：

```python
future_time.replace(day=future_time.day + 1)
```

在月末可能生成不存在的日期而失败。

测试应使用：

```python
future_time + timedelta(days=1)
```

时间相关测试还应冻结时钟，避免运行日期影响结果。

## 4.3 `tests/test_heartbeat.py`

这是测试名与实际证明范围偏差最大的一组。

### 大多数测试只操作夹具

许多用例执行：

```text
把任务字典写入临时文件
→ 再读出来
→ 验证原数据仍在
```

它们没有实际调用 `pacemaker_loop()` 完成一个扫描周期。

例如“到期任务会被触发”的测试只验证到期任务已写入 JSON，并没有验证：

- 调用了 `task_queue.put()`；
- 单次任务被移除；
- 重复任务被续期；
- 文件被原子写回；
- 提醒文本正确。

### 缺失文件与空文件测试也没有执行目标代码

异步内部只有：

```python
pass
```

因此“不抛异常”实际上是测试自身没有做任何事。

### 重复次数逻辑是复制实现

测试在测试文件中重新写一遍：

```python
if repeat_count > 1:
    repeat_count -= 1
```

这只能证明测试里的 Python 逻辑，不证明生产代码使用了同样规则。

### 队列测试是占位符

```python
self.assertTrue(True)
```

它无论生产实现怎样都会通过。

### Windows 失败的原因

`setUp()` 创建并保持一个打开的：

```python
NamedTemporaryFile(delete=False)
```

某个用例在关闭前直接：

```python
os.unlink(self.temp_file.name)
```

Windows 通常不允许删除仍被打开且未共享删除权限的文件，因此出现 `PermissionError`。

这首先是测试夹具生命周期问题，不代表 heartbeat 业务逻辑错误。

### 这一文件应怎样修复

把单次扫描逻辑从无限循环中提取为：

```python
async def process_due_tasks(
    now,
    tasks,
    task_queue
) -> updated_tasks:
```

然后用固定时钟、临时路径和 `AsyncMock` 真正断言队列调用和更新结果。

## 4.4 `tests/test_context_advanced.py`

这是当前较有价值的一组纯函数测试。

### 实际覆盖

- 阈值以下全部保留；
- 超过阈值后保留最后若干回合；
- 有无 SystemMessage；
- 空消息；
- ToolMessage 与同一用户回合一起保留；
- `AgentState` 字段形状。

### 它证明了什么

对于构造的消息序列，`trim_context_messages()` 能以 HumanMessage 为回合边界，并保持一个回合内 AI/Tool 消息的整体性。

### 仍缺少什么

- 总回合数恰好等于 trigger 的边界；
- `keep_turns=0`；
- `keep_turns > total_turns`；
- 第一个 HumanMessage 前存在孤立 AI/Tool 消息；
- 多条 SystemMessage；
- 缺失 message ID；
- 实际摘要调用；
- `RemoveMessage` 经 reducer 后的结果；
- checkpoint 写入与恢复；
- 摘要长度和失败回退。

### 注释与断言也有偏差

某个 ToolMessage 用例的注释说 discarded 包含系统消息，实际系统消息被放在 kept 中。断言数字是对的，注释说明不准确。

这提醒我们：测试注释同样不是事实，必须和输入、生产代码、断言一起核对。

## 4.5 `tests/test_config_and_skill_loader.py`

### 实际覆盖

- 若干配置常量是字符串；
- Skill loader 函数可以导入；
- Skill 目录不存在或为空时返回空列表。

### 它没有证明什么

- `CYBERCLAW_WORKSPACE` 覆盖是否正确；
- 目录导入副作用；
- 所有派生路径是否位于 workspace；
- metadata 解析；
- 重名处理；
- UTF-8 错误；
- mtime 缓存；
- help/run；
- Agent 工具重新绑定。

### 全局 patch 较宽

测试 patch：

```text
os.path.exists
os.listdir
```

而不是 loader 模块中的使用位置。

过宽 patch 可能影响同一调用链中的其他库代码；再叠加全局 `_lazy_loader` 缓存，测试顺序可能影响结果。

## 4.6 `tests/test_sandbox_tools.py`

### 实际覆盖

- 一个正常相对路径；
- 一个 `../../` 越界路径；
- 文件列出、读取、写入的普通分支；
- 无效写入 mode；
- Mock 的安全 Shell 命令；
- 五个明显危险命令被黑名单拒绝。

### 它证明了什么

普通 happy path 和列出的几种显式字符串会得到预期结果。

### 它没有证明安全隔离

没有覆盖：

- `office_backup` 一类字符串前缀兄弟目录；
- symlink 与 Windows Junction；
- 大小写和不同盘符；
- UNC 路径；
-检查与使用之间的 TOCTOU；
- 黑名单的大小写、引号、转义和命令组合绕过；
- 脚本文件间接访问外部路径；
- 环境变量展开；
- 网络访问；
- 子进程、CPU、内存和输出限制；
- timeout 后的进程树清理。

安全测试必须从攻击面和不变量出发，不能只列几个危险字符串。

## 4.7 `tests/test_lazy_loader.py`

### 实际覆盖

它在临时 workspace 创建 Skill，重载 config 和 loader，验证：

- 能发现 5 个 Skill；
- 能创建 5 个工具；
- help 能返回文档内容；
-第二次 help 的测量时间不大于第一次；
- 强制重扫能看到第 6 个 Skill；
- 可以调用清理缓存。

### 有价值的部分

它真实创建文件并经过 loader，覆盖程度高于只 patch 文件系统。

### 主要边界

1. 只有一个顶层 `test_lazy_loading()`，内部很多步骤不是相互隔离的用例；
2. 使用 `tool.func()` 绕过 `StructuredTool.invoke()` 和 Pydantic schema；
3. “第二次必须更快或相等”受计时精度、OS 文件缓存和调度噪声影响，容易偶发失败；
4. 清缓存后只测耗时，没有断言文件确实重新打开；
5. 新增 Skill 后只验证 loader 数量，没有验证运行中 Agent 的 `ToolNode` 与模型绑定；
6. 没有测试修改已有文件后的旧 wrapper 行为；
7. 没有测试重复名称和 3000 字符截断。

它证明 loader 可以重扫，不证明 Agent 零停机热更新。

## 4.8 `tests/test_two_phase_skills.py`

这个文件更准确地说是“真实模型评测脚本”，不是稳定自动测试。

### 为什么 pytest 不会把它作为测试执行

主要入口叫：

```python
run_experiment()
```

没有以 `test_` 开头的测试函数或类方法。

因此常规 pytest 收集不会运行 40 组云模型评测。

### 它实际做什么

包含 20 个场景，每个场景有：

```text
一个名称诱导的错误工具
一个真正适合的工具
```

共构造 40 个工具，并比较：

```text
单阶段：只看短描述直接执行
双阶段：先 help 读 manual，再决定 run
```

### 为什么结果不稳定

- 使用真实 Provider 和模型；
- 工具顺序会 `random.shuffle()`；
- 没有固定随机种子；
- 模型输出具有随机性和服务端变化；
- 网络、限流和余额会影响；
- 没有固定模型版本与采样参数记录；
- 单次样本量有限；
-异常场景 `continue` 后仍用 20 作为报告分母。

### “强制两阶段”仍然只在 Prompt 中

双阶段 tool 的 `run` 分支没有验证先前是否 help。

模型若直接 `run`，runner 仍会执行。所谓强制来自 System Prompt，不是执行状态机。

### README 命令是错误的

README 提供：

```python
from tests.test_two_phase_skills import run_tests
run_tests()
```

文件中没有 `run_tests()`，只有：

```python
run_experiment()
```

### 宣称的报告文件不存在

README 引用：

```text
tests/logs/test_two_phase_skills.md
```

当前仓库没有这个报告。

因此 50%、90%、事故率降低 80% 等数字没有随仓库提供可追溯原始结果。

## 五、示例文件审计

## 5.1 `examples/basic_usage.py`

### 它展示了什么

- 直接调用 `create_agent_app()`；
- 使用同步 `app.stream()`；
- 查看 `stream_mode="updates"` 的节点事件；
- 打印工具调用与工具结果。

### 它与正式入口的差别

没有：

- checkpointer；
- 固定 thread；
- 异步终端；
- heartbeat；
- queue；
- monitor。

模型和 Provider 还被硬编码为：

```text
aliyun / glm-5
```

不会跟随 `.env` 中的 `DEFAULT_PROVIDER` 和 `DEFAULT_MODEL`。

### 多轮状态存在逻辑缺口

示例本地维护：

```python
state = {"messages": []}
```

每轮只追加新的 HumanMessage，没有把图产生的 AIMessage 和 ToolMessage 合并回本地 state。

所以后续轮次传给图的历史只有用户输入，不是完整对话。

它适合展示单轮节点流，不应作为多轮 Agent 使用范例。

## 5.2 `examples/benchmark_lazy_loading.py`

### 脚本真实测量

- 不同 Skill 数量下的扫描耗时；
- 第一项 Skill 的第一次 help；
- 同一项 Skill 的第二次 help。

### 脚本没有真实测量

- 预加载实现的启动时间；
- 进程 RSS 或 Python heap 内存；
- 100 个以上扩展极限；
- 运行中 Agent 热更新；
- 模型工具 schema 的 Token 成本；
- 工具选择质量。

结尾对照表中的：

```text
~2000ms
~250KB
99.5%
80%
无限制
```

是直接写在 `print()` 中的假设值，不是该脚本测出的对照数据。

因此它是一个 loader timing demo，不是足以支撑全部宣传数字的严谨 benchmark。

## 六、文档文件审计

## 6.1 三篇懒加载 Markdown

文件：

```text
LAZY_LOADING_GUIDE.md
LAZY_LOADING_QUICKSTART.md
LAZY_LOADING_SUMMARY.md
```

它们正确描述了部分机制：

- 前 50 行 metadata；
- help 时加载全文；
- LRU maxsize 50；
- metadata 缓存 60 秒；
- mtime 参与缓存键。

但存在过度结论：

- “零延迟启动”；
- “修改后自动生效”；
- “零停机热更新”；
- “无限数量”；
- 固定 99.98% 与 80%；
- “完整测试覆盖”。

`LAZY_LOADING_SUMMARY.md` 还链接：

```text
SKILL_DEVELOPMENT.md
AGENT_ARCHITECTURE.md
```

这两个文件当前并不存在。

## 6.2 `two_phase_comparison.html`

这是一个静态展示页，把：

```text
成功率提升 40%
错误执行率降低 80%
时间开销 23.5%
```

写入页面。

HTML 本身不运行评测，也不读取结果文件。它只能展示数字，不能作为数字来源。

## 6.3 README 图片

README 引用的本地图片：

```text
cyber_logo.png
config.png
welcome.png
chat.png
monitor.png
architect.png
memory.png
context_cut.png
turn_memory.png
```

当前都能在 `docs/` 找到。

这部分文件引用是完整的。

## 七、README 与实现的主要不一致

| README 声明 | 代码证据 | 审计结论 |
|---|---|---|
| 企业级、零信任执行 | help/run 未强制，Shell 不是 OS 沙盒 | 当前是学习型原型，措辞过度 |
| P0 事故率降低 80% | 真实模型评测不稳定，报告文件缺失 | 不能作为可复现结论 |
| Heartbeat 是后台独立进程 | `pacemaker_loop` 是主进程中的 Task | 不一致 |
| 每秒检查任务 | 正式入口传 `check_interval=10` | 实际约 10 秒一次 |
| 主程序不运行也可执行 | 没有独立 heartbeat CLI/服务入口 | 当前无法自动做到 |
| MCP 服务集成 | 核心无 MCP client/server/transport | 仅可能通过外部 Skill/CLI 间接访问 |
| 自动摘要每 20 轮 | `agent.py` 显式传 40 | 实际为 40/10 |
| Windows 完整支持 PowerShell + CMD | `shell=True` 使用平台默认 shell，没有 PowerShell 适配层 | 不能称完整双 Shell 支持 |
| 测试文件 `test_context.py` | 实际为 `test_context_advanced.py` | 文件名错误 |
| `run_tests()` 可运行两阶段测试 | 文件只有 `run_experiment()` | 命令错误 |
| 有 `tests/logs` 评测报告 | 目录和报告不存在 | 引用失效 |
| `.env` 位于 office | 实际主配置在项目根目录 | 结构图内部自相矛盾 |
| 全部测试通过 | 当前 Windows 基线有 1 个夹具失败 | 平台结论不准确 |
| 5 类企业审计日志 | 生产代码主要写 4 类事件，Monitor 还漏掉 ai_message | 能力与完整性不足 |

### 中英文双份 README 的维护成本

README 前后大体重复中文和英文内容。

当代码变化时，必须同步修改两套：

- 数字；
- 命令；
- 文件名；
- 架构说明；
- 能力边界。

当前多处偏差已经说明重复文档容易漂移。更好的方式是拆分语言文件，并建立链接检查和关键片段自动生成。

## 八、CHANGELOG 的证据边界

CHANGELOG 声明：

```text
99.98% 启动提升
80% 内存降低
无限 Skill
自动热更新
零停机
所有测试通过
```

源码能支持“存在懒加载和缓存机制”，却不能支持所有定量或生产级结论。

CHANGELOG 应记录实际变更，不应把：

- 没有对照测量的数字；
- 未完成的热绑定；
- 未验证的平台能力；
- 理论上无限的扩展性

写成已完成事实。

## 九、License 与二次开发归属

项目使用 MIT License：

```text
Copyright (c) 2026 THOR
```

MIT 允许：

- 使用；
- 复制；
- 修改；
- 合并；
- 发布；
- 再许可；
- 销售。

条件是：

> 在软件的所有副本或实质部分中保留原版权声明和许可声明。

所以可以 fork、学习和二次开发，但不应：

- 删除原作者的版权与 MIT License；
- 把原作者已有代码和设计全部声称为自己原创；
- 在简历中模糊开源来源。

正确做法是明确：

```text
基于开源 CyberClaw 的二次开发
原项目提供了哪些基础
自己重新设计并实现了哪些部分
用什么测试和数据证明改造效果
```

这不会削弱项目价值，反而能体现代码审计、架构理解和工程改造能力。

## 十、怎样建立文档—代码—测试追踪表

建议每项能力都维护：

| 能力 | 实现入口 | 自动测试 | 文档 | 状态 |
|---|---|---|---|---|
| Agent 工具循环 | `agent.py` | 待补集成测试 | README | 部分验证 |
| Heartbeat 到期触发 | `heartbeat.py` | 当前未真实覆盖 | README | 未验证 |
| Skill help/run | `skill_loader.py` | loader 部分覆盖 | docs | 已实现但未强制 |
| 原生 MCP | 无 | 无 | README | 未实现 |
| 上下文 40/10 | `agent.py` | trim 纯函数覆盖 | README 写 20/10 | 文档错误 |
| JSONL 日志 | `logger.py` | 无 | README | 基础实现 |

状态至少区分：

```text
未实现
已实现未测试
部分测试
自动验证
人工验证
有可复现 benchmark
```

不要只使用“支持/不支持”两个模糊标签。

## 十一、测试改进优先级

### P0：安全不变量

- office 真实路径不能逃逸；
- symlink/Junction 不能越界；
- Shell 不允许通用绕过；
- 高风险工具必须有程序级审批；
- 日志不能泄露密钥。

### P1：主链正确性

- fake chat model 驱动 `agent → tool → agent`；
- checkpoint 按 thread 恢复；
- 40 回合压缩、摘要与删除；
- heartbeat 到期、重复、队列和原子写；
- CLI 配置优先级；
- Logger 正常与重复关闭。

### P2：平台与韧性

- Windows/Linux 临时文件；
- 月末、时区和夏令时；
-取消、超时和退出竞态；
- 日志轮转和磁盘失败；
- Skill 文件更新和重名。

### P3：评测与性能

- 固定数据集；
- 固定模型版本和采样参数；
- 多次重复；
- 置信区间；
- 冷热缓存分离；
- 真实内存测量；
- 原始结果文件和运行元数据。

## 十二、本课最终结论

CyberClaw 是一个适合学习的 Agent 原型，主链真实可运行，也包含状态图、工具、记忆、调度、Skill 和日志等丰富部件。

但仓库当前存在三个明显层次：

```text
真实可运行的核心机制
> 自动测试证明的范围
> 文档和宣传声明的范围
```

学习这个项目最重要的能力，不是背下 README，而是能说：

```text
这条结论来自哪段代码
这个测试真实执行了什么
这个数字怎样复现
这项能力还缺哪个边界
```

## 十三、学完本课应能回答

1. 测试通过为什么不等于功能没有边界？
2. 哪个测试文件最没有真正覆盖其命名目标？
3. `test_agent.py` 为什么没有证明 Agent 工具循环？
4. `test_context_advanced.py` 的证据为什么相对更强？
5. 两阶段评测为什么不属于稳定 pytest？
6. benchmark 中哪些数字是测量值，哪些是硬编码对照？
7. README 关于 Heartbeat、摘要和 MCP 的主要偏差是什么？
8. `reload_skills()` 为什么不能支持“零停机热更新”的结论？
9. MIT License 允许你做什么，又要求保留什么？
10. 怎样把一条产品声明变成可验证工程证据？


# 第 8 课｜Skill 懒加载、两阶段执行与 MCP 真相

> 主要源码：`cyberclaw/core/skill_loader.py`  
> 辅助源码：`cyberclaw/core/agent.py`、`cyberclaw/core/config.py`、`cyberclaw/core/tools/sandbox_tools.py`  
> 相关材料：`docs/LAZY_LOADING_GUIDE.md`、`docs/LAZY_LOADING_QUICKSTART.md`、`docs/LAZY_LOADING_SUMMARY.md`

## 一、本课要解决的问题

CyberClaw 的内置工具在 Python 源码中定义，而 Skill 采用另一种扩展方式：

```text
一个技能目录
+ 一份 SKILL.md 或 README.md
+ 文档中说明的命令
```

系统启动时扫描技能元数据，把每个 Skill 包装成一个 LangChain `StructuredTool`。模型先读取帮助，再根据文档拼出命令，最终仍由 office Shell 工具执行。

完整链路：

```text
workspace/office/skills/<skill>/
        ↓ 扫描元数据
StructuredTool 占位工具
        ↓ bind_tools
模型看到 name / description / schema
        ↓ mode="help"
读取完整技能文档
        ↓ mode="run"
替换 {baseDir}
        ↓
execute_office_shell
```

本课要分清四件事：

1. “只加载元数据”究竟节省了什么；
2. `help → run` 两阶段协议是否真的被强制；
3. mtime 和 LRU 缓存实际怎样工作；
4. 当前 Skill 系统为什么不等于原生 MCP 集成。

## 二、阅读顺序

按下面顺序对照：

1. `config.py` 中 `SKILLS_DIR`
2. `skill_loader.py` 的 `DynamicSkillInput`
3. `LazySkillLoader.__init__()`
4. `_scan_skills()`
5. `_extract_metadata()`
6. `_load_skill_content()`
7. `_create_lazy_tool()`
8. `get_all_tools()` 与全局 loader
9. `agent.py` 中 `load_dynamic_skills()` 和 `bind_tools()`
10. `reload_skills()`、`clear_skill_cache()`
11. 三篇懒加载文档

## 三、Skill 文件放在哪里

路径来自：

```python
SKILLS_DIR = os.path.join(
    OFFICE_DIR,
    "skills"
)
```

默认结构：

```text
workspace/
└── office/
    └── skills/
        ├── deploy_website/
        │   └── SKILL.md
        └── data_report/
            └── README.md
```

每个一级子目录被当作一个 Skill 候选。

加载器优先寻找：

```text
SKILL.md
```

不存在时回退到：

```text
README.md
```

两者都没有，该目录会被跳过。

## 四、Skill 与内置 Python Tool 的区别

### 内置工具

内置工具有真实 Python 函数：

```python
@cyberclaw_tool
def calculator(expression: str) -> str:
    ...
```

名称、参数、执行逻辑都由 Python 实现。

### 动态 Skill

Skill 的核心不是一个被 Python 导入的插件类，而是一份操作说明：

```text
name
description
如何拼接命令
依赖哪些脚本或外部 CLI
```

所有 Skill 在 Python 侧最终共享同一个 `lazy_runner()` 逻辑：

```text
help → 返回文档
run  → 执行模型提供的 command
```

因此它更接近“文档驱动的 Shell 工具包装器”，而不是具有独立 Python 生命周期的插件协议。

## 五、`DynamicSkillInput` 定义统一参数

所有动态 Skill 共用：

```python
class DynamicSkillInput(BaseModel):
    mode: str
    command: Optional[str] = ""
```

模型看到的参数结构固定为：

```json
{
  "mode": "help 或 run",
  "command": "run 模式使用的完整命令"
}
```

### `mode` 没有使用 `Literal`

类型只是：

```python
str
```

所以 Pydantic schema 本身不会拒绝：

```text
mode="anything"
```

真正的校验发生在 `lazy_runner()` 内部：

```text
help → 读取说明
run  → 执行命令
其他 → 返回错误字符串
```

如果使用：

```python
Literal["help", "run"]
```

模型 schema 和输入校验会更明确。

## 六、启动阶段只扫描元数据

### 1. 60 秒元数据缓存

loader 保存：

```text
_skill_registry
_last_scan_time
_scan_interval = 60
```

当以下条件同时成立：

```text
不是强制扫描
已有 registry
距离上次扫描不足 60 秒
```

`_scan_skills()` 直接返回旧 registry，不访问目录。

### 2. 扫描每个一级子目录

代码使用：

```python
for item in os.listdir(SKILLS_DIR):
```

没有显式排序，因此不同文件系统环境中工具顺序不应被当作稳定 API。

### 3. 只读前 50 行

`_extract_metadata()` 最多读取前 50 行，再使用正则查找：

```text
^name:\s*(.+)$
^description:\s*(.+)$
```

注意，它不是完整 YAML front matter 解析器。

它只认单行、行首字段：

```markdown
name: report_builder
description: 创建分析报告
```

多行 YAML、缩进字段或复杂转义不会按真正 YAML 规则解析。

### 4. 缺省值

没有 `name` 时，使用目录名。

没有 `description` 时，生成：

```text
提供 <raw_name> 相关功能
```

如果描述被一对单引号或双引号包裹，代码会移除最外层引号。

### 5. 工具名清洗

```python
re.sub(
    r"[^a-zA-Z0-9_-]",
    "_",
    raw_name
)
```

空格、中文和特殊字符会变成下划线。

这可能产生碰撞：

```text
data report
data@report
→ data_report
```

当前代码没有主动检测重复工具名。

## 七、“懒加载”具体懒在哪里

启动扫描会读：

```text
每个文档前 50 行
```

不会立刻把所有 Skill 文档完整内容放进工具描述。

模型启动时只看到：

```text
工具名
简短 description
统一参数 schema
两阶段使用提示
```

这主要减少：

- 启动期读取全部文档的 I/O；
- 大量说明文本进入模型工具描述的上下文成本；
- Python 进程同时持有全部完整文档内容。

但它并非“零读取”或“零上下文”：

- 每个 Skill 的前 50 行仍会被读取；
- 每个 Skill 仍会创建 `StructuredTool`；
- 所有工具的名称、描述和 schema 仍会通过 `bind_tools()` 交给模型。

Skill 数量极大时，工具列表自身仍会占用模型上下文，并增加工具选择难度。

## 八、完整内容缓存怎样工作

### 1. LRU 缓存键

```python
@lru_cache(maxsize=50)
def _load_skill_content(
    md_path: str,
    mtime: float
) -> str:
```

缓存键包含：

```text
文件路径 + 扫描时记录的修改时间
```

由于这是实例方法，Python 的实际缓存键还会包含 `self`；对于当前全局单例 loader，可以把有业务意义的变化理解为“文件路径 + 扫描时 mtime”。同一 loader 中相同路径且相同 mtime 的第二次调用会复用缓存内容。

### 2. `cache_size` 参数没有真正生效

构造函数接收：

```python
LazySkillLoader(cache_size=50)
```

并保存到：

```python
self._cache_size
```

但装饰器写死：

```python
@lru_cache(maxsize=50)
```

`self._cache_size` 没有参与缓存创建。因此即使实例化时传入 10，真实上限仍是 50。

### 3. help 会先读取全文，再截断返回

```python
skill_content = self._load_skill_content(...)
return skill_content[:3000]
```

文件会被完整读入并缓存，但只把前 3000 个 Python 字符返回给模型。

因此：

- 文档 3000 字符以后的关键说明不可见；
- 内存占用仍是完整文件大小；
- “只加载所需片段”并不准确。

### 4. mtime 失效依赖重新扫描

新的 mtime 只有 `_scan_skills()` 重新访问文件后才会记录到新的 `skill_info`。

已有工具闭包捕获的是创建当时的：

```text
md_path
mtime
folder
```

如果文件已经被 help 加载并缓存，之后只修改文档而不创建新的工具对象，旧工具仍会用旧 mtime 查询缓存，可能继续返回旧内容。

## 九、每个 Skill 怎样变成 `StructuredTool`

`_create_lazy_tool()` 在内部定义：

```python
def lazy_runner(mode: str, command: str = "") -> str:
    ...
```

再调用：

```python
StructuredTool.from_function(
    func=lazy_runner,
    name=skill_info["name"],
    description=mini_description,
    args_schema=DynamicSkillInput
)
```

这个闭包保存了当前 Skill 的：

```text
目录
文档路径
扫描时 mtime
原始名称
```

所以所有工具执行代码相同，但每个闭包关联的文档和目录不同。

## 十、两阶段执行的真实逻辑

### 阶段一：`mode="help"`

执行：

```text
读取并缓存完整文档
→ 返回前 3000 字符
→ 提示模型根据说明再次用 run 调用
```

### 阶段二：`mode="run"`

如果 `command` 为空，返回错误。

否则：

```python
actual_cmd = command.replace(
    "{baseDir}",
    f"skills/{folder}"
)
```

再调用：

```python
execute_office_shell.invoke(
    {"command": actual_cmd}
)
```

Shell 的 cwd 是 office，因此：

```text
{baseDir}
→ skills/<skill-folder>
```

指向当前 Skill 目录。

### `help → run` 并未被强制

描述中写着“第一次使用请务必先 help”，但 loader 没有保存：

```text
这个 thread 是否看过 help
这个调用者是否获得执行许可
help 对应哪个文档版本
```

模型可以第一次就调用：

```json
{
  "mode": "run",
  "command": "..."
}
```

只要命令非空，代码就会执行。

所以当前两阶段机制是提示词层协议，不是程序状态机或安全审批。

### `command` 不必包含 `{baseDir}`

代码只做字符串替换，没有要求占位符必须出现。模型可以传入任何通过 Shell 工具检查的命令。

这使 Skill 更像一个获得通用 Shell 入口的说明书，而不是被限制到预定义脚本的安全能力。

## 十一、Skill 怎样进入 Agent

`create_agent_app()` 中：

```python
dynamic_tools = load_dynamic_skills()
actual_tools = BUILTIN_TOOLS + dynamic_tools
```

随后：

```python
tool_node = ToolNode(actual_tools)
llm_with_tools = llm.bind_tools(actual_tools)
```

动态 Skill 同时进入：

1. 模型可见的工具 schema；
2. LangGraph 的实际工具执行节点。

### 工具集合在 Agent 创建时固定

`load_dynamic_skills()` 只在 `create_agent_app()` 中调用一次。

已编译的 Agent：

- `ToolNode` 保存当时的工具集合；
- `llm_with_tools` 绑定当时的工具 schema；
- 每个 Skill wrapper 捕获当时的 metadata。

因此新增 Skill 后，仅仅在别处调用：

```python
reload_skills()
```

得到一份新的工具列表，并不会自动替换已经运行的 `ToolNode` 和模型绑定。

要让运行中的 Agent 真正看到新工具，需要显式重建或热替换图中的工具注册。

## 十二、缓存与“热更新”的真实边界

### 1. `reload_skills()` 没有清理内容缓存

函数说明写着“强制重新扫描技能目录并清除缓存”，实际只调用：

```python
get_all_tools(force_rescan=True)
```

它会重新扫描 metadata，但没有执行：

```python
_load_skill_content.cache_clear()
```

只有 `clear_skill_cache()` 才真正清理内容 LRU 缓存。

新 mtime 通常会形成一个新缓存键，所以新 wrapper 能读新内容；旧缓存条目仍会保留，直到被 LRU 淘汰或手动清除。

### 2. 运行中 Agent 不会自动采用返回的新 wrappers

重新扫描返回新工具对象，但没有注册回已编译 Agent。

所以“修改后自动重新加载、无需重启 Agent”的文档说法超出了当前实现。

更准确的描述是：

> loader 能重新扫描并创建反映新文件状态的工具对象；调用方仍必须把这些新对象重新绑定到运行中的 Agent，或重建 Agent。

### 3. 删除 Skill 也不会自动从旧 Agent 消失

旧 `ToolNode` 和模型绑定仍持有旧 wrapper。文件被删除后，help 可能读取失败，但模型仍可能看到那个工具名。

## 十三、Skill 的安全边界

`run` 最终进入 `execute_office_shell`。

因此 Skill 的执行权限由第 4 课分析过的 Shell 工具决定：

```text
office cwd
+ 正则黑名单
+ timeout
```

它不是进程级、容器级或虚拟机级隔离。

此外，Skill 文档本身也应视为不可信输入：

- 可能包含恶意命令；
- 可能诱导模型读取或泄露敏感信息；
- 可能要求安装外部程序；
- 可能调用网络服务；
- 可能被修改后投毒。

安全的技能系统需要来源验证、能力声明、用户审批、命令白名单、隔离执行和审计，而不能只要求模型“先读说明书”。

## 十四、为什么当前 Skill 不是 MCP

### 1. MCP 解决的是协议互操作

原生 MCP 集成通常至少包含：

```text
客户端或服务端实现
传输方式与连接生命周期
能力协商
工具/资源/提示的发现
结构化 schema
远程调用与结果协议
错误、认证和取消处理
```

### 2. 当前仓库实际拥有的能力

CyberClaw 核心实现是：

```text
扫描本地 Markdown
→ 包装为 LangChain StructuredTool
→ 运行本地 Shell 命令
```

仓库中没有核心 MCP：

- client session；
- server；
- stdio、SSE 或 Streamable HTTP transport；
- `list_tools` / `call_tool` 协议调用；
- MCP 资源和 Prompt 发现；
- MCP 生命周期与认证管理。

### 3. “Skill 中调用 MCP CLI”仍不等于核心原生支持

理论上，一份 Skill 可以让 Shell 调用某个外部 MCP 命令行客户端。

这只是：

```text
CyberClaw → Shell → 外部 CLI → MCP 服务
```

MCP 协议由外部 CLI 实现，不由 CyberClaw 核心实现。

所以更准确的表述是：

> 当前项目拥有文档驱动的本地 Skill 扩展机制，并可能借助外部命令间接访问 MCP；核心仓库尚未实现原生 MCP 集成。

## 十五、要实现原生 MCP 需要增加什么

一种合理的接入链路：

```text
MCP 配置文件
→ MCP Client Manager
→ 建立并维护多个 server session
→ list_tools()
→ 把 MCP tool schema 适配成 LangChain Tool
→ Agent bind_tools()
→ call_tool()
→ 把 MCP result 转成 ToolMessage
→ 处理超时、取消、认证和断线重连
```

还需明确：

- 哪些 Server 被信任；
- Server 能访问哪些本地资源；
- 工具调用是否需要用户审批；
- 输出怎样脱敏与审计；
- 配置和凭据怎样保存；
- 新增或断开 Server 时怎样更新已运行 Agent 的工具集合。

## 十六、文档性能结论应怎样看

懒加载文档给出启动时间、内存和百分比改善。

源码能够证明的机制是：

```text
只扫描前 50 行
完整内容按需读取
最多缓存 50 个内容键
```

但固定百分比需要可复现基准支持。

示例 benchmark 确实测量：

- metadata 扫描时间；
-一个 Skill 的第一次 help；
- 同一个 Skill 的第二次 help。

但“传统预加载约 2000 ms”“内存约 250 KB”“扩展性无限”等对照值是代码中直接打印的假设值，不是该脚本实际测量的对照组。

所以机制可以学习，性能结论必须重新测量后才能写入简历。

## 十七、本课完整调用链

启动：

```text
create_agent_app()
→ load_dynamic_skills()
→ _scan_skills()
→ 每个目录读取文档前 50 行
→ _extract_metadata()
→ _create_lazy_tool()
→ StructuredTool 列表
→ 与 BUILTIN_TOOLS 合并
→ ToolNode + llm.bind_tools()
```

使用：

```text
模型选择某个 Skill
→ mode="help"
→ _load_skill_content(path, scanned_mtime)
→ 返回前 3000 字符
→ 模型再次调用 mode="run"
→ command 中替换 {baseDir}
→ execute_office_shell.invoke()
→ Shell 结果作为 ToolMessage 回到 Agent
```

## 十八、学完本课应能回答

1. Skill 与内置 Python Tool 有什么本质区别？
2. 启动时具体读取了 Skill 的哪些内容？
3. mtime 为什么是 LRU 缓存键的一部分？
4. `cache_size` 参数为什么没有真正控制缓存？
5. 两阶段执行为什么只是建议而不是强制？
6. `reload_skills()` 为什么不代表运行中 Agent 已热更新？
7. Skill 为什么仍继承 Shell 工具的安全风险？
8. 当前项目为什么不能称为原生 MCP 集成？
9. 真正接入 MCP 还需要哪些组件？

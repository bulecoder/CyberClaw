# 08｜Skill 懒加载、两阶段执行与 MCP 真相

> 对应源码：`cyberclaw/core/skill_loader.py`  
> 辅助源码：`cyberclaw/core/agent.py`、`cyberclaw/core/config.py`、`cyberclaw/core/tools/sandbox_tools.py`

## 一、本课核心内容

### 1. Skill 的真实形式

每个 Skill 是：

```text
workspace/office/skills/<目录>/
└── SKILL.md 或 README.md
```

Python 不会导入一个独立插件类，而是把文档元数据包装成统一的 `StructuredTool`。执行时所有 Skill 都共用 `lazy_runner()`，最终调用 office Shell。

### 2. 元数据扫描

loader 遍历每个一级子目录，优先找 `SKILL.md`，否则找 `README.md`，最多读取前 50 行并用正则提取：

```text
name
description
```

这不是完整 YAML 解析。缺失名称时使用目录名；非法工具名字符被替换为下划线，可能产生重名。

### 3. 懒加载节省的内容

启动时不读取所有文档全文，也不把全文放进工具描述。系统仍会：

- 读取每个文档前 50 行；
- 为每个 Skill 创建工具对象；
- 把全部工具名、短描述和 schema 交给模型。

因此 Skill 很多时，工具 schema 的上下文成本仍然存在。

### 4. 内容缓存

完整文档通过：

```python
@lru_cache(maxsize=50)
_load_skill_content(md_path, mtime)
```

缓存。作为实例方法，实际键还包含 `self`；对当前全局单例 loader 来说，主要由路径和扫描时 mtime 区分内容版本。

构造器的 `cache_size` 被保存但没有用于装饰器，真实上限始终写死为 50。

help 会读取并缓存全文，但只把前 3000 字符返回给模型。

### 5. 两阶段协议

```text
mode="help" → 读取文档并返回说明
mode="run"  → 替换 {baseDir} 后调用 execute_office_shell
```

代码没有记录某个会话是否已经 help，也没有审批状态。第一次直接 run 仍会执行，只要 command 非空。因此这是提示词建议，不是强制状态机。

### 6. 工具注册时间

动态 Skill 只在 `create_agent_app()` 时加载，并同时传给：

```text
ToolNode
llm.bind_tools()
```

已运行 Agent 的工具集合由创建时的 wrappers 固定。

### 7. 热更新边界

`reload_skills()` 只强制重新扫描并返回新 wrappers，没有：

- 清理内容 LRU；
- 替换旧 Agent 的 `ToolNode`；
- 重新执行 `llm.bind_tools()`。

所以 loader 能创建反映新文件状态的新工具列表，但运行中的 Agent 不会自动采用它。

### 8. 安全边界

run 最终还是通用 Shell 命令，并继承 office Shell 的黑名单和弱隔离边界。Skill 文档也可能被投毒。两阶段说明不能替代来源验证、用户审批和隔离执行。

### 9. MCP 真相

当前核心是：

```text
本地 Markdown
→ LangChain Tool
→ 本地 Shell
```

仓库没有 MCP client/server、transport、session、能力发现、`list_tools()` 或 `call_tool()` 等协议实现。

Skill 可以调用外部 MCP CLI，但那是外部程序实现协议，只能称为间接扩展，不是 CyberClaw 原生 MCP。

## 二、自测题与参考答案

### 1. 动态 Skill 和内置工具有什么区别？

**参考答案：**

内置工具的参数和执行逻辑由独立 Python 函数实现；动态 Skill 的独特内容主要在 Markdown 说明中，Python 侧共享同一个 help/run runner，并最终把模型拼出的命令交给 Shell。

### 2. loader 怎样发现一个 Skill？

**参考答案：**

它遍历 `SKILLS_DIR` 的一级子目录，优先寻找 `SKILL.md`，否则寻找 `README.md`。两者都不存在就跳过该目录。

### 3. 为什么说 metadata 解析不是真正的 YAML？

**参考答案：**

代码只读取前 50 行，用正则匹配行首的单行 `name:` 和 `description:`。它不处理完整 YAML front matter、多行字段、嵌套结构和标准转义。

### 4. 工具名清洗有什么风险？

**参考答案：**

所有非字母、数字、下划线和连字符都变成下划线。不同原始名称可能清洗成同一个工具名，而当前代码没有检测冲突。

### 5. 懒加载是否完全不读取未使用 Skill？

**参考答案：**

不是。启动时仍读取每个 Skill 文档前 50 行并创建 wrapper，只是不读取全文，也不把全文都交给模型。

### 6. 完整文档的缓存键是什么？

**参考答案：**

是 Markdown 路径和扫描时记录的 mtime。路径相同但 mtime 改变会形成不同缓存键，前提是 loader 已重新扫描并创建使用新 mtime 的 wrapper。

### 7. `cache_size` 为什么目前无效？

**参考答案：**

构造函数把参数保存为 `_cache_size`，但 `lru_cache` 的 `maxsize` 在装饰器中写死为 50，未读取实例字段。

### 8. help 是否只把前 3000 字符读入内存？

**参考答案：**

不是。函数先 `f.read()` 读取并缓存全文，然后在返回字符串时使用 `skill_content[:3000]` 截断。

### 9. 为什么两阶段执行不是强制的？

**参考答案：**

runner 没有记录 help 状态。`mode="run"` 只检查 command 是否非空，模型无需先成功调用 help，也无需经过用户审批。

### 10. `{baseDir}` 有什么作用？

**参考答案：**

run 会把它替换成相对于 office cwd 的 `skills/<folder>`，方便命令定位当前技能目录。代码不要求 command 必须包含该占位符。

### 11. `reload_skills()` 为什么不能让当前 Agent 自动获得新 Skill？

**参考答案：**

它只返回新工具对象；已编译 Agent 的 `ToolNode` 和 `llm_with_tools` 仍持有创建时的旧工具集合，没有被重新绑定。

### 12. `reload_skills()` 是否真的清除了内容缓存？

**参考答案：**

没有。函数只调用 `get_all_tools(force_rescan=True)`。真正的 `cache_clear()` 在 `clear_skill_cache()` 中。

### 13. 当前 Skill 为什么不是安全插件沙盒？

**参考答案：**

它最终执行模型提供的 Shell 命令，仍拥有 CyberClaw 进程的系统权限，只受 office cwd、黑名单和超时等应用层限制。

### 14. 为什么当前仓库不是原生 MCP？

**参考答案：**

没有 MCP 协议客户端或服务端、连接 transport、session、能力发现和结构化远程调用。它实现的是本地 Markdown 驱动的 LangChain 工具。

### 15. 原生 MCP 接入至少需要哪些组件？

**参考答案：**

需要 MCP Client Manager、server 配置与连接生命周期、工具发现、MCP schema 到 LangChain schema 的适配、`call_tool` 结果转换、认证、超时、取消、断线重连和安全审批。

## 三、面试追问与回答思路

### 1. 你会怎样实现真正的两阶段审批？

**回答思路：**

为每次调用生成不可伪造的计划 ID，help 阶段返回结构化命令、权限和风险；用户审批后签发短期 token；run 必须提交相同计划 ID、文档版本和审批 token，且执行参数不得超出批准内容。

### 2. 怎样实现运行时 Skill 热更新？

**回答思路：**

使用文件监听或版本轮询，构造新的不可变工具 registry，验证重名和 schema 后，原子替换路由；模型绑定也要重建。进行中请求继续使用旧版本，新请求使用新版本，并保留回滚能力。

### 3. 怎样避免把全部 Skill schema 都交给模型？

**回答思路：**

先对用户意图做检索或路由，只选少量候选 Skill 绑定到本轮模型；也可采用目录工具先搜索能力，再动态绑定。需要衡量召回率、Token 成本和工具选择准确率。

### 4. 怎样提高 Skill 安全性？

**回答思路：**

采用签名和来源信任、声明式能力、参数 schema、用户审批、命令白名单、低权限容器、只读挂载、网络策略、资源限制和完整审计，避免让文档自由生成通用 Shell。

### 5. 怎样为 CyberClaw 增加原生 MCP？

**回答思路：**

实现统一 MCP session manager，启动或连接配置的 servers，发现工具并适配为 LangChain Tools；调用时维护 MCP 生命周期、认证和结果类型；对工具变化重建绑定，并把权限审批与审计置于协议调用之前。

### 6. 简历中怎样描述性能改进？

**回答思路：**

只能写亲自设计、测量和复现的数据。应说明技能规模、文档大小、机器环境、冷启动/热启动方法和对照组，不能直接引用示例脚本中硬编码的 99.5% 或 80%。

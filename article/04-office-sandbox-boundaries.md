# 第 4 课｜office 文件工具与“沙盒”的真实边界

CyberClaw 把模型能够操作的文件区域称为：

```text
office 工位
```

你之前创建的：

```text
hello.txt
```

实际保存在：

```text
workspace/office/hello.txt
```

这套能力由四个 Tool 提供：

```text
list_office_files
read_office_file
write_office_file
execute_office_shell
```

本课要回答：

> CyberClaw 如何把文件操作限制在 office 中？这些限制在哪些情况下有效，又为什么还不能称为真正的操作系统沙盒？

本课阅读：

- `cyberclaw/core/config.py` 中的工作区路径；
- `cyberclaw/core/tools/sandbox_tools.py`；
- `cyberclaw/core/tools/builtins.py` 中的导入与注册；
- `cyberclaw/core/agent.py` 中的 Sandbox Prompt。

## 先明确什么才是真正的“沙盒”

安全领域中的沙盒，通常意味着被执行代码即使主动尝试越权，也会受到强制隔离，例如：

- 使用受限操作系统账户；
- 使用容器、虚拟机或独立进程；
- 文件系统只挂载允许目录；
- 禁止或限制网络；
- 限制 CPU、内存、进程数和执行时间；
- 不向子进程传递宿主机密钥；
- 使用操作系统权限阻止访问其他路径。

CyberClaw 当前没有建立这些操作系统级隔离。它主要依靠：

```text
系统提示词
+ Python 路径检查
+ Shell 命令正则黑名单
+ subprocess 的 cwd 和 timeout
```

因此更准确的说法是：

> 当前实现是一个带应用层限制的工作目录，不是能够抵抗恶意代码的强隔离沙盒。

这不代表这些限制没有价值。它们能阻止一部分模型误操作，但不能作为最后的安全边界。

## 第一部分：office 路径从哪里来

打开：

```text
cyberclaw/core/config.py
```

项目先计算仓库根目录：

```python
CORE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(CORE_DIR)
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)
```

然后确定工作区：

```python
WORKSPACE_DIR = os.getenv(
    "CYBERCLAW_WORKSPACE",
    os.path.join(PROJECT_ROOT, "workspace")
)
```

如果没有配置 `CYBERCLAW_WORKSPACE`，默认路径是：

```text
CyberClaw/workspace
```

office 路径则是：

```python
OFFICE_DIR = os.path.join(WORKSPACE_DIR, "office")
```

在你的项目中通常对应：

```text
E:\graduate_student\projects\CyberClaw\workspace\office
```

### 导入配置模块会产生副作用

`config.py` 底部执行：

```python
for d in [
    WORKSPACE_DIR,
    MEMORY_DIR,
    PERSONAS_DIR,
    SCRIPTS_DIR,
    OFFICE_DIR,
    SKILLS_DIR
]:
    os.makedirs(d, exist_ok=True)
```

因此第一次导入配置模块时，就会自动创建这些目录，并打印工作区路径。

这解释了为什么启动程序时，即使还没有调用文件工具，也会出现：

```text
[Config] Workspace 路径已就绪
```

需要注意：

```text
创建一个名为 office 的目录
≠
操作系统自动限制程序只能访问该目录
```

Python 进程仍然继承当前 Windows 用户的全部文件权限。真正的限制需要后续代码主动检查。

## 第二部分：文件工具的统一路径入口

打开：

```text
cyberclaw/core/tools/sandbox_tools.py
```

三个文件工具首先调用：

```python
_get_safe_path(relative_path)
```

它的目标是把模型传入的相对路径转换成 office 内的绝对路径。

### 当前转换过程

第一步，把 office 变成绝对路径：

```python
base_dir = os.path.abspath(OFFICE_DIR)
```

第二步，把用户路径拼到 office 后面并规范化：

```python
target_path = os.path.abspath(
    os.path.join(base_dir, relative_path)
)
```

例如：

```text
relative_path = docs/readme.md

target_path =
E:\...\workspace\office\docs\readme.md
```

`abspath()` 还会折叠：

```text
.
..
```

第三步，通过字符串前缀判断是否仍在 office：

```python
if not target_path.startswith(base_dir):
    raise PermissionError(...)
```

普通的目录穿越输入，例如：

```text
../../outside.txt
```

规范化后通常不再以 office 路径开头，因此会被拒绝。

### `startswith()` 不是可靠的目录包含判断

假设：

```text
base_dir
= E:\project\workspace\office

target_path
= E:\project\workspace\office_backup\note.txt
```

从目录结构看，`office_backup` 是 office 的兄弟目录，不在 office 内。

但字符串判断：

```python
target_path.startswith(base_dir)
```

仍然为真，因为：

```text
E:\project\workspace\office_backup
```

确实以字符串：

```text
E:\project\workspace\office
```

开头。

所以类似：

```text
..\office_backup\note.txt
```

可能绕过当前文件工具的前缀检查。

正确方向是使用路径层面的共同祖先判断，而不是字符串前缀：

```python
base = os.path.normcase(os.path.realpath(OFFICE_DIR))
target = os.path.normcase(
    os.path.realpath(os.path.join(base, relative_path))
)

if os.path.commonpath([base, target]) != base:
    raise PermissionError(...)
```

如果目标位于不同盘符，`commonpath()` 可能抛出异常，也应按越界拒绝。

### 符号链接和 Windows Junction

`abspath()` 只处理路径字符串，不解析路径中的符号链接或 Windows Junction。

例如：

```text
office/external_link
→ 实际指向 office 外部目录
```

访问：

```text
external_link/file.txt
```

字符串路径仍位于 office 下，但操作系统实际打开的文件可能在外部。

`realpath()` 可以改善这个问题，但如果攻击者能在检查后、文件打开前替换链接，还存在 TOCTOU：

```text
检查时安全
→ 链接被替换
→ 打开时已经指向外部
```

强安全场景需要使用操作系统文件描述符、禁止跟随链接或直接用容器挂载边界，而不只是检查字符串。

## 第三部分：`list_office_files`

工具定义：

```python
@cyberclaw_tool
def list_office_files(sub_dir: str = "") -> str:
```

`sub_dir` 为空时列出 office 根目录，否则列出指定子目录。

完整过程：

```text
sub_dir
→ _get_safe_path()
→ 检查路径是否存在
→ os.listdir()
→ 判断每一项是文件还是目录
→ 格式化为字符串
```

核心代码：

```python
items = os.listdir(target_dir)
```

随后：

```python
item_type = "📁" if os.path.isdir(item_path) else "📄"
```

返回结果类似：

```text
📄 hello.txt
📁 skills
```

### 当前行为边界

1. 没有排序，显示顺序取决于文件系统；
2. 只返回当前层，不递归遍历；
3. 没有区分普通文件、链接和其他特殊文件；
4. `os.path.isdir()` 默认会跟随链接；
5. 路径存在但不是目录时，`os.listdir()` 抛出的异常会转成字符串；
6. 所有异常都作为普通 Tool 文本返回，没有结构化错误码。

## 第四部分：`read_office_file`

工具接收 office 下的相对路径：

```python
@cyberclaw_tool
def read_office_file(filepath: str) -> str:
```

过程是：

```text
filepath
→ _get_safe_path()
→ 检查存在
→ 按 UTF-8 打开
→ 读取全部文本
→ 超过 10000 字符时截断返回
```

核心代码：

```python
with open(target_path, "r", encoding="utf-8") as f:
    content = f.read()
```

返回前：

```python
if len(content) > 10000:
    return content[:10000] + "...[内容过长]..."
```

### 截断保护解决了什么

它主要防止把特别长的文件完整放进 `ToolMessage`，从而减少：

- 上下文长度；
- 模型 Token 消耗；
- 终端输出长度；
- 大文件内容对后续推理的干扰。

### 它没有限制实际读取内存

代码先执行：

```python
content = f.read()
```

然后才截断。因此一个几 GB 的文件仍可能先被完整读入 Python 内存。

更合理的实现是：

```python
content = f.read(10001)
```

只读取上限再判断是否截断，或者先检查文件大小。

### 10000 字符不等于 10000 Token

`len(content)` 统计 Python 字符数量。实际模型 Token 数量受到语言和内容影响：

- 英文单词可能由一个或多个 Token 组成；
- 中文字符与 Token 并非固定一一对应；
- 代码、数字和特殊符号的切分也不同。

因此字符上限只能粗略控制上下文，不能精确控制 Token。

### 文本和编码边界

工具固定使用：

```python
encoding="utf-8"
```

所以：

- UTF-8 文本可以读取；
- GBK 等编码可能解码失败；
- 二进制文件不适合该工具；
- 解码异常会以错误字符串返回。

### 文件内容也是不可信输入

文件内容会以 `ToolMessage` 回到模型。一个文件可以包含伪装成指令的文本，例如要求模型忽略原任务。

因此读取成功不代表内容可信。Agent 应将文件内容视为数据，而不是新的高优先级指令。

这属于 Tool output prompt injection：攻击内容来自工具结果，而不一定来自用户当前输入。

## 第五部分：`write_office_file`

工具签名：

```python
def write_office_file(
    filepath: str,
    content: str,
    mode: str = "w"
) -> str:
```

支持：

```text
w → 新建或覆盖
a → 追加
```

首先进行路径检查，然后校验：

```python
if mode not in ["w", "a"]:
    return "mode 参数必须是 w 或 a"
```

目标位于子目录时：

```python
os.makedirs(
    os.path.dirname(target_path),
    exist_ok=True
)
```

因此写入：

```text
docs/readme.md
```

会自动创建 `docs`。

### 覆盖模式

```python
open(target_path, "w", encoding="utf-8")
```

文件存在时会先截断旧内容。写入中途如果进程崩溃，可能留下空文件或部分内容。

当前实现没有：

- 原子临时文件替换；
- 旧版本备份；
- 文件哈希校验；
- 乐观并发控制；
- 写入前确认；
- 内容大小限制。

### 追加模式

当前逻辑：

```python
if mode == "a" and not content.startswith("\n"):
    f.write("\n" + content)
```

它检查的是新内容是否以换行开头，没有检查旧文件是否以换行结尾。

所以：

- 新建空文件时也可能先写入一个空行；
- 旧文件已经以换行结尾时可能多出额外空行；
- 旧文件没有换行时，自动补换行才真正防止粘连。

更准确的实现需要检查目标文件末尾，而不是只检查新内容开头。

返回值中的：

```python
len(content)
```

不包含自动添加的换行，因此也不完全等于实际写入字符数。

### 写入脚本会扩大执行风险

路径工具只能限制脚本文件被写到哪里，却不能限制脚本运行后访问哪里。

例如，一个脚本文件本身位于 office 内，但脚本代码可以：

- 读取 office 外的路径；
- 访问网络；
- 读取环境变量；
- 启动新进程；
- 修改当前用户有权限的其他文件。

docstring 和系统提示词禁止模型编写越界脚本，但文件写入函数本身没有分析或阻止这类代码。

因此：

```text
脚本文件位于 office
≠
脚本执行能力被限制在 office
```

### office 内还包含 Skill

`SKILLS_DIR` 位于：

```text
workspace/office/skills
```

所以 `write_office_file` 也能够修改 Skill 的说明和脚本。这样的修改可能影响未来 Agent 的工具说明和行为，形成持久化的提示词或代码供应链风险。

Skill 的加载机制将在第 8 课详细学习。

## 第六部分：`execute_office_shell`

这是本课风险最高的工具：

```python
@cyberclaw_tool
def execute_office_shell(command: str) -> str:
```

它接收模型生成的一整段 Shell 命令，并执行：

```python
subprocess.run(
    command,
    shell=True,
    cwd=OFFICE_DIR,
    capture_output=True,
    encoding="utf-8",
    errors="replace",
    timeout=60
)
```

理解它必须拆成几个参数。

### `shell=True`

命令不是作为一个固定程序和参数列表执行，而是先交给系统 Shell 解释。

通常：

```text
Windows → cmd.exe
Linux/macOS → /bin/sh 或系统对应 Shell
```

因此命令可以使用：

- 管道；
- 重定向；
- 命令连接；
- 环境变量展开；
- Shell 内置命令；
- 启动脚本和其他解释器。

这提供了灵活性，也显著扩大了命令注入与越权面。

### `cwd=OFFICE_DIR`

它只设置子进程启动时的当前工作目录：

```text
进程从 office 开始执行
```

它不表示：

```text
进程被操作系统锁在 office 中
```

子进程仍继承当前用户权限，可以主动访问其他路径、环境变量、网络和系统资源。

`cwd` 是默认位置，不是安全边界。

### 每次执行都是独立进程

每次调用都会重新执行一次 `subprocess.run()`。

所以第一次调用：

```text
cd skills
```

不会让下一次 Tool 调用继续停留在 `skills` 中。下一次仍从 `OFFICE_DIR` 启动。

如果需要在一次调用中进入子目录并执行命令，只能在同一条命令中完成。

### `capture_output=True`

标准输出和标准错误会被捕获：

```python
result.stdout
result.stderr
```

但标准输入没有显式设置为：

```python
subprocess.DEVNULL
```

所以“完全非交互式”主要是 docstring 中的约定，并非严格由参数保证。等待输入的命令仍可能挂起，最终依赖 timeout。

### `timeout=60`

命令超过 60 秒会抛出：

```python
subprocess.TimeoutExpired
```

然后返回超时错误文本。

这个限制可以减少无限等待，但仍有边界：

- 不能限制 60 秒内的 CPU 和内存消耗；
- 不限制子进程数量；
- 不限制网络流量；
- Shell 派生的后代进程不一定都能被可靠终止；
- 命令可能在超时前已经产生不可逆副作用。

超时是运行时间限制，不是事务回滚。

## 第七部分：Shell 正则黑名单

执行前，代码遍历五个正则：

```python
dangerous_patterns = [
    r"\.\.",
    r"(?:^|\s|[<>|&;])/",
    r"(?:^|\s|[<>|&;])~",
    r"(?:^|\s|[<>|&;])\\",
    r"(?i)(?:^|\s|[<>|&;])[a-z]:",
]
```

它们分别尝试拦截：

```text
..
Unix 绝对路径
~
Windows 根路径
Windows 盘符路径
```

### 黑名单能够阻止什么

它能拦截一部分常见、直接的越界表达：

- 明文 `..`；
- 未加特殊包装的绝对路径；
- 直接写出的 Windows 盘符；
- 部分重定向后的绝对路径。

这对降低模型无意误操作有帮助。

### 为什么黑名单无法覆盖 Shell 语法

Shell 命令不是简单路径字符串。路径可以通过多种形式产生：

- 引号改变字符前后关系；
- 环境变量在 Shell 中展开；
- 脚本在运行时自行构造路径；
- 链接或 Junction 指向外部；
- 子命令生成另一段命令；
- 解释器读取 office 内脚本后执行任意逻辑。

正则只检查原始命令文本，无法看到 Shell 展开后的真实行为。

例如，正则要求绝对路径前面是：

```text
命令开头、空白或部分操作符
```

路径放在引号中时，斜杠或盘符前面可能是引号字符，从而不匹配当前表达式。

更关键的是：

```text
在 office 内运行一个脚本
```

命令文本本身不含外部路径，但脚本内部仍可访问外部。

### 黑名单还会产生误报

第一条：

```python
r"\.\."
```

会拒绝任何连续两个点，即使它们只是普通文本、文件名或输出内容的一部分。

黑名单通常同时存在：

```text
漏报
→ 危险行为没有匹配

误报
→ 安全命令被拒绝
```

对于高权限 Shell，允许列表通常比禁止列表更可靠。

## 第八部分：子进程继承了什么

`subprocess.run()` 没有传入自定义：

```python
env
```

所以子进程默认继承 CyberClaw 的环境变量。

其中可能包含：

- 模型 API Key；
- Provider Base URL；
- 代理配置；
- 用户目录；
- PATH；
- 其他本地凭据。

这意味着 Shell 或脚本理论上可以读取这些环境变量。即使文件路径受限，密钥也可能通过进程环境泄露。

更安全的做法是构造最小环境：

```python
safe_env = {
    "PATH": restricted_path,
    "TEMP": isolated_temp
}
```

只传递工具真正需要的变量，绝不默认继承模型密钥。

子进程还继承当前 Windows 用户能够执行和访问的权限。当前代码没有：

- 降权用户；
- Job Object；
- 容器；
- 网络隔离；
- 只读文件系统；
- 系统调用过滤。

这正是应用层限制与 OS 沙盒的根本区别。

## 第九部分：Shell 输出处理

工具返回：

```text
当前系统
执行命令
退出码
STDOUT
STDERR
```

标准输出和错误各自最多返回最后 2000 个字符：

```python
stdout[-2000:]
stderr[-2000:]
```

保留末尾的好处是通常能看到最终错误和总结。

但与文件读取工具类似，截断发生在命令完成之后：

```python
capture_output=True
```

已经把完整输出缓存在内存中。最终只返回 2000 字符，并不能阻止子进程生成大量输出导致内存压力。

### 编码问题

代码固定按 UTF-8 解码，并使用：

```python
errors="replace"
```

不能按 UTF-8 解码的字节会被替换字符代替，而不是让工具崩溃。

Windows 原生命令可能使用当前代码页，而不一定输出 UTF-8，因此中文结果可能出现乱码或替换字符。

### 交互提示判断只是启发式

代码尝试在：

```text
stderr 中出现 prompt
stdout 中出现 y/n
```

时建议加入 `-y`。

它不能覆盖所有交互式程序，而且不同命令的免确认参数并不统一。真正非交互执行应关闭 stdin，并为允许的具体命令定义明确参数。

### 输出同样是不可信输入

Shell 输出会作为 Tool 结果返回模型。外部程序可以输出伪装成指令的文本，因此 Agent 不应把 STDOUT 或 STDERR 当成可信控制信息。

## 第十部分：四层安全控制

CyberClaw 当前与 office 有关的限制可以分成四层。

### 第一层：模型提示词

`agent.py` 的系统提示词要求：

```text
禁止越权访问 office 外部
禁止使用解释器单行命令绕过
禁止编写越界脚本
发现越权要求时拒绝
```

这是行为引导，可以减少正常模型的误操作。

它不能防止：

- 模型判断错误；
- Prompt injection；
- Tool 被代码直接调用；
- 工具说明与实现不一致；
- 已写入脚本的运行时行为。

### 第二层：文件路径检查和命令正则

这是应用层强制代码，比提示词更可靠。

但当前实现存在：

- `startswith()` 前缀问题；
- 链接与 Junction 问题；
- Shell 展开与脚本绕过；
- 黑名单误报和漏报。

### 第三层：`cwd`、输出截断和 timeout

这些是执行约束：

- 从 office 启动；
- 最长运行 60 秒；
- 返回内容截断。

它们改善可用性和部分资源风险，但不构成文件、网络或权限隔离。

### 第四层：操作系统隔离

当前缺失：

- 独立低权限账户；
- 文件系统挂载边界；
- 容器或虚拟机；
- 网络策略；
- 密钥隔离；
- CPU、内存和进程限制。

所以当前信任边界是：

```text
默认模型大体遵守提示词
+ 应用层拦截明显路径
+ CyberClaw 以当前用户权限执行
```

它适合个人受控学习环境，不适合直接执行不可信用户或不可信 Skill 提供的任意命令。

## 第十一部分：工具如何进入 Agent

`builtins.py` 导入：

```python
from .sandbox_tools import (
    list_office_files,
    read_office_file,
    write_office_file,
    execute_office_shell
)
```

随后将四个对象加入：

```python
BUILTIN_TOOLS
```

因此它们与计算器、时间和任务工具走相同链路：

```text
@cyberclaw_tool
→ Tool 对象
→ BUILTIN_TOOLS
→ actual_tools
→ bind_tools
→ 模型生成 tool_call
→ ToolNode 执行
```

路径检查和 Shell 正则都位于具体 Tool 内，而不在 `ToolNode` 或统一中间件中。

这意味着：

- 直接调用 Tool 仍会经过函数内部路径检查；
- 但没有统一审批所有高风险工具；
- 新增文件或 Shell 工具时，开发者必须主动重复安全逻辑；
- 工具风险等级没有进入统一注册信息。

## 第十二部分：怎样改造成更可靠的 office 执行层

### 1. 修复路径包含判断

至少应使用：

```text
realpath
+ normcase
+ commonpath
```

并明确拒绝链接、Junction、不同盘符和特殊文件。

### 2. 文件读写使用资源上限

读取时：

- 限制实际读取字节；
- 限制文件类型；
- 检查文件大小；
- 明确编码策略；
- 将内容标记为不可信数据。

写入时：

- 限制内容大小；
- 使用临时文件加原子替换；
- 保存版本或备份；
- 对覆盖操作进行确认；
- 避免无条件修改 Skill。

### 3. 避免任意 `shell=True`

优先提供确定性工具：

```text
rename_file(source, target)
delete_file(path)
run_python_file(path)
list_directory(path)
```

参数经过结构化 Schema 校验，比让模型自由拼接 Shell 字符串更容易控制。

如果必须执行命令，应：

- 使用允许的程序列表；
- 使用参数数组并设置 `shell=False`；
- 禁止命令连接和重定向；
- 对每个程序定义参数规则；
- 关闭 stdin；
- 使用最小环境变量；
- 记录并审批高风险操作。

### 4. 把执行放进真正隔离环境

高风险脚本应在独立执行器中运行：

```text
受限用户 / 容器 / 虚拟机
→ 只挂载 office
→ 默认无网络
→ 不注入 API Key
→ 限制 CPU、内存、进程数和时间
→ 执行完销毁
```

这样即使脚本主动越权，操作系统也会拒绝，而不是依赖模型自律。

### 5. 增加统一风险策略

工具注册信息可以包含：

```text
risk_level
required_permission
requires_confirmation
timeout
allowed_paths
network_policy
```

在 `agent` 与 `tools` 之间加入策略节点：

```text
低风险读取
→ 直接执行

覆盖、删除、Shell
→ 展示具体参数
→ 等待用户确认
→ 执行并审计
```

审批必须绑定工具名称和完整参数，避免批准内容与最终执行内容不一致。

## 本课源码阅读顺序

按照安全边界从外到内阅读：

1. `config.py` 第 6～22 行：工作区和 office 怎样产生；
2. `sandbox_tools.py` 第 8～24 行：操作系统识别与路径转换；
3. `sandbox_tools.py` 第 26～50 行：目录列表；
4. `sandbox_tools.py` 第 52～70 行：文本读取和截断；
5. `sandbox_tools.py` 第 72～109 行：覆盖、追加和目录创建；
6. `sandbox_tools.py` 第 112～172 行：Shell 黑名单、子进程和输出；
7. `builtins.py` 第 8～13、275～287 行：工具导入和注册；
8. `agent.py` 第 99～112 行：模型层 Sandbox Prompt。

阅读每一层时都问：

```text
这是给模型看的规则，还是代码强制规则？
这是路径规范化，还是操作系统权限？
它限制的是返回内容，还是实际资源消耗？
直接调用 Tool 能否绕过这一层？
子进程继承了哪些宿主机能力？
```

## 本课完成标准

完成后应能不看文章讲清：

```text
OFFICE_DIR
→ _get_safe_path
→ list/read/write
→ Shell 正则检查
→ subprocess(cwd=office)
→ ToolMessage 返回模型
```

还应能准确区分：

```text
提示词约束
→ 模型行为指导

应用层路径与命令检查
→ 部分强制拦截

cwd 和 timeout
→ 执行参数，不是系统隔离

OS 沙盒
→ 当前项目没有实现
```

最重要的结论是：

> office 限制可以降低模型无意越界的概率，但任意 Shell 和脚本仍以当前用户权限运行，因此不能把它当成抵抗恶意输入的安全沙盒。

下一课将学习模型 Provider 和配置系统，理解不同模型服务如何被适配为 LangChain ChatModel，以及学校 OpenAI 兼容网关的 Provider、Model、Base URL 和 API Key 分别控制什么。

# 第 9 课｜CLI、配置向导、Python 环境与打包入口

> 主要源码：`setup.py`、`entry/cli.py`  
> 辅助文件：`requirements.txt`、`.env.example`、`.gitignore`、`.vscode/settings.json`  
> 包结构：`entry/__init__.py`、`cyberclaw/__init__.py`、`cyberclaw/core/__init__.py`

## 一、本课要解决的问题

在终端输入：

```text
cyberclaw config
cyberclaw run
cyberclaw monitor
```

看起来像在运行一个独立程序，实际链路是：

```text
uv 创建项目级 Python 环境
→ 安装 CyberClaw 包
→ 安装器读取 setup.py
→ 生成 console_scripts 启动器
→ Windows 中出现 cyberclaw.exe
→ 启动器导入 entry.cli:main
→ Typer 分发 config / run / monitor
```

本课要看懂：

1. `.venv` 怎样提供隔离；
2. 为什么 `.venv` 仍会引用某个基础 Python；
3. `cyberclaw.exe` 是怎样生成的；
4. Typer 怎样把命令分发给 Python 函数；
5. 配置向导、运行入口与监控入口怎样延迟导入；
6. 当前 `setup.py`、`requirements.txt` 与 uv 锁定有什么关系；
7. 当前打包结构还缺少哪些工程能力。

## 二、阅读顺序

建议按安装到执行的顺序：

1. `.gitignore` 中的 `.venv/`
2. `.vscode/settings.json`
3. `requirements.txt`
4. `setup.py`
5. 三个空的 `__init__.py`
6. `entry/cli.py` 顶部导入和路径处理
7. `Typer()` 与三个 `@app.command`
8. `main()` 和 `app()`
9. `.env.example`

## 三、uv 与 `.venv` 分别做什么

### 1. uv 是环境和依赖管理工具

uv 可以负责：

- 查找或下载合适的 Python 解释器；
- 创建虚拟环境；
- 安装项目及依赖；
- 解析依赖版本；
- 在使用 `pyproject.toml` 时生成和同步锁文件。

uv 本身不是虚拟环境。真正隔离项目包的是项目目录中的：

```text
.venv/
```

### 2. `.venv` 的隔离内容

Windows 虚拟环境通常包含：

```text
.venv/
├── pyvenv.cfg
├── Scripts/
│   ├── python.exe
│   ├── activate
│   └── cyberclaw.exe
└── Lib/
    └── site-packages/
```

项目依赖被安装到：

```text
.venv/Lib/site-packages
```

命令启动器被安装到：

```text
.venv/Scripts
```

它们不进入 Conda base 的 `site-packages`。

### 3. 为什么 `pyvenv.cfg` 会指向用户目录 Python

虚拟环境必须基于一个真实 Python 解释器创建。

如果 uv 使用自己管理的 Python 3.11，`pyvenv.cfg` 中的 `home` 可能指向：

```text
用户目录中的 uv Python 安装或缓存
```

这表示：

```text
该解释器是创建虚拟环境的基础运行时
```

不表示 CyberClaw 的依赖被装进那个基础 Python，也不表示运行项目会修改它的全局包。

隔离关系更准确地表示为：

```text
uv 管理的基础 Python 3.11
          ↓ 提供解释器二进制和标准库
项目 .venv
          ↓ 拥有自己的 site-packages 和 Scripts
CyberClaw 依赖
```

虚拟环境不是完整复制一套完全无关的 Python，而是在复用基础解释器的前提下隔离第三方包和命令入口。

### 4. 激活环境只是在当前终端调整路径

执行激活脚本后，主要变化是：

```text
PATH 前面加入 .venv/Scripts
提示符显示 (.venv)
VIRTUAL_ENV 指向当前环境
```

因此输入：

```text
python
cyberclaw
```

时会优先找到 `.venv/Scripts` 下的程序。

激活并不是环境存在的前提，也不是创建环境。即使不激活，也可以显式执行：

```text
.\.venv\Scripts\python.exe
.\.venv\Scripts\cyberclaw.exe
```

### 5. Conda base 与 uv 环境可以共存

VS Code 终端自动显示 `(base)`，是 Conda shell 初始化行为。

当激活 `.venv` 或直接使用 `.venv\Scripts\python.exe` 后，项目解释器和依赖来自 `.venv`。为了减少歧义，可以先 `conda deactivate`，再激活项目环境。

重点不是提示符文字，而是确认实际路径：

```text
python.exe 来自哪里
cyberclaw.exe 来自哪里
site-packages 来自哪里
```

## 四、VS Code 配置说明

当前 `.vscode/settings.json` 指定：

```json
{
  "python-envs.defaultEnvManager": "ms-python.python:venv",
  "python-envs.defaultPackageManager": "ms-python.python:pip"
}
```

它影响 VS Code Python 扩展的默认环境管理和包管理偏好，但不会把已有 `.venv` 改造成 Conda 环境，也不会决定 `cyberclaw.exe` 的运行逻辑。

对于当前项目，最关键的是 VS Code 选择：

```text
E:\graduate_student\projects\CyberClaw\.venv\Scripts\python.exe
```

解释器选择和终端是否自动激活 Conda 是两个相关但不完全相同的设置。

## 五、`setup.py` 描述了什么

核心配置：

```python
setup(
    name="cyberclaw",
    version="1.0.0",
    packages=find_packages(),
    py_modules=["cli"],
    install_requires=parse_requirements(
        "requirements.txt"
    ),
    entry_points={
        "console_scripts": [
            "cyberclaw=entry.cli:main",
        ],
    },
)
```

### 1. distribution name

```text
name="cyberclaw"
```

这是安装发行包的名称。

它不必与每个 Python import 路径完全相同，但当前项目也确实有：

```python
import cyberclaw
```

### 2. version

```text
1.0.0
```

这是包元数据版本。当前版本只写在 `setup.py`，没有在 `cyberclaw/__init__.py` 中暴露。

### 3. `find_packages()`

它查找含 `__init__.py` 的包目录，例如：

```text
cyberclaw
cyberclaw.core
cyberclaw.core.tools
entry
```

几个空的 `__init__.py` 没有业务逻辑，但它们使这些目录成为传统 Python package，并可被打包工具发现。

### 4. `py_modules=["cli"]` 存在问题

`py_modules` 用于声明顶层单文件模块，例如项目根目录存在：

```text
cli.py
```

当前仓库没有这个顶层文件，真正文件是：

```text
entry/cli.py
```

它已经由 `find_packages()` 发现的 `entry` 包包含。因此 `py_modules=["cli"]` 是不准确且多余的声明，应当删除或改正包结构。

## 六、依赖怎样从 `requirements.txt` 进入安装

`parse_requirements()`：

```python
return [
    line.strip()
    for line in f
    if line.strip()
    and not line.startswith("#")
]
```

它跳过空行和以 `#` 开头的整行注释，把其余每一行直接交给 `install_requires`。

### 当前声明的特点

依赖大多使用最低版本：

```text
langgraph>=0.1.0
langchain-core>=0.2.0
pydantic>=2.0.0
```

这表示安装器可以选择任何更高且满足约束的版本。

优点是约束宽，缺点是：

- 今天和未来安装出的版本可能不同；
- 上游大版本变更可能破坏 API；
- 很难复现作者当时验证的组合；
- `requirements.txt` 既承担运行依赖，又没有开发依赖分组。

### 声明并不完整

`provider.py` 的可选分支还导入：

```text
langchain_anthropic
langchain_community
```

当前依赖没有完整列出它们。

安装了 `anthropic` SDK，不等于安装了 `langchain-anthropic` 适配包。

所以 requirements 能支持当前 OpenAI 兼容主路径，但不能证明所有 Provider 分支开箱即用。

## 七、`cyberclaw.exe` 是怎样出现的

关键入口：

```python
"cyberclaw=entry.cli:main"
```

含义：

```text
命令名 cyberclaw
→ 导入 entry.cli
→ 调用其中 main()
```

安装器处理 `console_scripts` 时会在当前环境的 Scripts 目录生成平台启动器。

Windows 中通常是：

```text
.venv/Scripts/cyberclaw.exe
```

它不是作者手工用 C/C++ 写的完整可执行程序，也不是把整个 Agent 打包成一个单文件二进制。

它是一个小型启动包装器，核心工作相当于：

```python
from entry.cli import main
main()
```

### editable install 的含义

本地学习常使用 editable 安装：

```text
项目源码仍位于当前仓库
安装环境记录到源码的可编辑引用
console script 仍生成在 .venv/Scripts
```

修改仓库中的 Python 源码后，通常不需要重新复制整个包，启动器会导入当前工作区代码。

如果修改了入口元数据、依赖或包结构，仍可能需要重新安装或同步环境。

## 八、Typer 怎样分发子命令

创建应用：

```python
app = typer.Typer(...)
```

注册三个命令：

```python
@app.command("config")
def config_wizard():
    ...

@app.command("run")
def run_agent():
    ...

@app.command("monitor")
def run_monitor():
    ...
```

入口：

```python
def main():
    app()
```

执行：

```text
cyberclaw config
```

时，console script 调用 `main()`，Typer 读取命令行参数并分发给 `config_wizard()`。

## 九、CLI 导入时的路径副作用

`entry/cli.py` 在模块顶层执行：

```python
ENTRY_DIR = ...
PROJECT_ROOT = ...
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)
```

### 这样做的目的

无论用户从哪个目录输入 `cyberclaw`，都把当前工作目录切到仓库根目录，使：

- `.env` 路径稳定；
- 相对 `logs/` 路径稳定；
- 本地源码更容易导入。

### 代价

仅仅导入 `entry.cli` 就会改变整个进程的 cwd。

如果 CyberClaw 被另一个 Python 程序当库导入，这个全局副作用可能影响宿主程序的所有相对路径。

更稳健的方式是全部使用显式绝对路径，而不是在模块导入时修改进程工作目录。

## 十、为什么 `run` 使用延迟导入

`run_agent()` 校验配置成功后才执行：

```python
import entry.main as cyberclaw_main
cyberclaw_main.main()
```

这样：

- 运行 `config` 时不必启动正式 Agent；
- 配置不完整时不会过早创建模型和数据库；
- 命令的重依赖只在真正使用时导入。

`monitor` 同样只在该子命令中导入监控模块。

## 十一、PowerShell 与 CMD 环境变量语法

PowerShell：

```powershell
$env:PYTHONUTF8 = "1"
```

CMD：

```bat
set PYTHONUTF8=1
```

这两种语法不能混用。

`PYTHONUTF8` 的有效开关值应为：

```text
0
1
```

如果误输入：

```text
1t
```

Python 在解释器预初始化阶段就会报：

```text
invalid PYTHONUTF8 environment variable value
```

这时 CyberClaw 代码甚至还没有开始导入。

环境变量只对当前终端进程及其子进程生效，关闭终端后通常消失，除非写入系统持久环境配置。

## 十二、`.gitignore` 表达了数据边界

当前忽略：

```text
.venv/
.env
logs/
*.jsonl
workspace/state.sqlite3
workspace/tasks.json
workspace/memory/*.md
workspace/office
*.egg-info/
```

它避免提交：

- 本机虚拟环境；
- API Key；
- 对话和画像；
- 用户工作文件；
- 运行日志；
- 安装元数据。

这说明 Git 仓库只保存源码和公共说明，运行时状态留在本机。

也意味着：

```text
workspace/office/skills
```

当前同样被整个 `workspace/office` 规则忽略。用户自己创建的 Skill 不会自动进入版本控制。如果以后要把 Skill 作为项目功能发布，需要调整目录设计或 Git 规则，并继续隔离其运行产物和秘密。

## 十三、依赖锁定是什么

### 1. 依赖声明

`requirements.txt` 中：

```text
langgraph>=0.1.0
```

表达“允许哪些版本”。

### 2. 锁文件

锁文件记录解析后实际使用的精确版本和依赖树。

概念上：

```text
依赖声明 → 允许范围
锁文件   → 一次确定的解析结果
```

锁定的目的不只是让别人得到完全相同环境，也包括：

- 自己以后重装时可复现；
- 出现升级问题时知道之前工作版本；
- 审查依赖变化；
- CI 与本机使用一致组合。

### 3. 当前项目状态

仓库没有：

```text
pyproject.toml
uv.lock
```

所以虽然环境由 uv 创建，项目元数据仍是传统 `setup.py + requirements.txt`，没有 uv 项目锁。

对于只在当前已经可运行环境中学习，立刻锁定不是运行前提。但在准备工程改造、重建环境或把项目写进简历前，迁移到 `pyproject.toml` 并生成 `uv.lock` 会更规范。

## 十四、当前打包方式的主要缺口

### 1. 使用传统 `setup.py`

现代 Python 项目通常用 `pyproject.toml` 声明：

- build system；
- project metadata；
- dependencies；
- scripts；
- optional dependency groups；
- tool settings。

### 2. 包元数据过少

当前没有在 `setup()` 中声明：

- Python 版本范围；
- 项目描述；
- README；
- License 元数据；
- 作者和项目链接；
- classifiers；
- package data。

### 3. 没有运行依赖与开发依赖分组

测试、格式化、类型检查等工具没有形成可安装的 dev group。

### 4. 没有锁文件

最低版本约束可能解析出未来不兼容组合。

### 5. 可选 Provider 依赖不完整

应当选择：

- 全部作为基础依赖安装；或
- 设计 `anthropic`、`ollama` 等 optional extras；
- 在缺依赖时给出清晰安装提示。

### 6. CLI 与仓库根目录耦合

入口通过 `os.chdir(PROJECT_ROOT)` 假定包和可写工作区的布局。真正安装到任意环境后，源码目录未必适合当数据目录，也未必可写。

发布型应用应明确：

```text
包资源位置
用户配置目录
用户数据目录
缓存与日志目录
```

## 十五、本课完整链路

环境创建与安装：

```text
uv 选择基础 Python
→ 创建项目 .venv
→ 安装 requirements / 当前项目
→ setuptools 读取 setup.py
→ find_packages() 收集 Python 包
→ 生成 cyberclaw console script
→ .venv/Scripts/cyberclaw.exe
```

命令执行：

```text
cyberclaw.exe
→ import entry.cli
→ 模块切换 cwd 到 PROJECT_ROOT
→ main()
→ Typer app()
→ 分发 config / run / monitor
```

正式运行：

```text
run_agent()
→ 加载并校验 .env
→ 延迟导入 entry.main
→ asyncio.run(async_main())
```

## 十六、学完本课应能回答

1. uv、基础 Python 和 `.venv` 分别扮演什么角色？
2. 为什么 `.venv` 指向用户目录解释器仍然是隔离环境？
3. 激活虚拟环境实际改变了什么？
4. `cyberclaw.exe` 是作者写的完整 exe 吗？
5. `console_scripts` 怎样定位 `entry.cli:main`？
6. `find_packages()` 与 `__init__.py` 有什么关系？
7. `py_modules=["cli"]` 为什么不准确？
8. 依赖范围和锁文件有什么区别？
9. `os.chdir(PROJECT_ROOT)` 解决了什么，又带来什么副作用？
10. 当前项目为什么还不能称为规范的 uv 项目？


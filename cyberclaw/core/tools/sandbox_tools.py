import os
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath
import platform
import shlex
import shutil
import stat
import subprocess
import tempfile

from .base import cyberclaw_tool
from ..config import OFFICE_DIR

SYS_OS = platform.system()
_MAX_OFFICE_READ_CHARS = 10_000
_MAX_SHELL_COMMAND_CHARS = 2_000
_MAX_SHELL_ARGUMENTS = 64
_MAX_SHELL_OUTPUT_BYTES = 16_384
_SHELL_TIMEOUT_SECONDS = 60
_SHELL_ENABLED_ENV = "CYBERCLAW_ENABLE_SHELL"
_SHELL_ALLOWLIST_ENV = "CYBERCLAW_SHELL_ALLOWED_COMMANDS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FORBIDDEN_COMMAND_INTERPRETERS = frozenset({
    "bash",
    "cmd",
    "command",
    "cscript",
    "fish",
    "powershell",
    "pwsh",
    "sh",
    "wscript",
    "wsl",
    "zsh",
})
_INLINE_CODE_FLAGS = {
    "node": frozenset({"-e", "--eval", "-p", "--print"}),
    "python": frozenset({"-c", "-m"}),
    "py": frozenset({"-c", "-m"}),
}


def _atomic_write_text(target_path: str, content: str) -> None:
    """Write text to a sibling temp file and atomically replace the target."""
    parent_dir = os.path.dirname(target_path)
    existing_mode = None
    if os.path.isfile(target_path):
        existing_mode = stat.S_IMODE(os.stat(target_path).st_mode)

    file_descriptor, temp_path = tempfile.mkstemp(
        dir=parent_dir,
        prefix=f".{os.path.basename(target_path)}.",
        suffix=".tmp",
    )
    descriptor_open = True
    try:
        temp_file = os.fdopen(
            file_descriptor, "w", encoding="utf-8", newline=""
        )
        descriptor_open = False
        with temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        os.replace(temp_path, target_path)
    except Exception:
        if descriptor_open:
            os.close(file_descriptor)
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise


def _append_text(target_path: str, content: str) -> None:
    """Append text with exactly one separator newline when it is needed."""
    if not content:
        with open(target_path, "a", encoding="utf-8", newline="") as target_file:
            target_file.flush()
            os.fsync(target_file.fileno())
        return

    needs_separator = False
    if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
        with open(target_path, "rb") as existing_file:
            existing_file.seek(-1, os.SEEK_END)
            last_byte = existing_file.read(1)
        needs_separator = (
            last_byte not in (b"\n", b"\r")
            and not content.startswith(("\n", "\r"))
        )

    payload = f"\n{content}" if needs_separator else content
    with open(target_path, "a", encoding="utf-8", newline="") as target_file:
        target_file.write(payload)
        target_file.flush()
        os.fsync(target_file.fileno())


def _normalize_executable_name(executable: str) -> str:
    """Normalize a bare executable name for allowlist comparisons."""
    normalized = executable.casefold()
    if normalized.endswith(".exe"):
        normalized = normalized[:-4]
    return normalized


def _shell_execution_enabled() -> bool:
    return os.getenv(_SHELL_ENABLED_ENV, "").strip().casefold() in _TRUE_VALUES


def _get_allowed_shell_commands() -> set[str]:
    raw_allowlist = os.getenv(_SHELL_ALLOWLIST_ENV, "")
    return {
        _normalize_executable_name(item.strip())
        for item in raw_allowlist.split(",")
        if item.strip()
    }


def _strip_windows_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _reject_unsafe_argument_path(argument: str) -> None:
    """Reject obvious path escapes before an approved executable is started."""
    value = (
        argument.split("=", 1)[1]
        if argument.startswith("-") and "=" in argument
        else argument
    )
    normalized = value.replace("\\", "/")

    if ".." in normalized:
        raise PermissionError("命令参数禁止使用 '..' 跳出 office 工位")
    if normalized.startswith(("/", "~")) or PureWindowsPath(value).is_absolute():
        raise PermissionError("命令参数禁止使用绝对路径或用户目录路径")


def _parse_restricted_command(command: str) -> list[str]:
    """Parse a command string without invoking a system shell."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("命令不能为空")
    if len(command) > _MAX_SHELL_COMMAND_CHARS:
        raise ValueError(f"命令长度不能超过 {_MAX_SHELL_COMMAND_CHARS} 个字符")
    control_characters = ("\x00", "\r", "\n", "|", "&", ";", "<", ">")
    if any(character in command for character in control_characters):
        raise PermissionError("禁止使用管道、重定向、命令连接符或多行命令")

    try:
        argv = shlex.split(command, posix=SYS_OS != "Windows")
    except ValueError as exc:
        raise ValueError("命令中的引号不完整") from exc

    if SYS_OS == "Windows":
        argv = [_strip_windows_quotes(value) for value in argv]
    return argv


def _validate_restricted_argv(argv: Sequence[str]) -> tuple[list[str], str]:
    """Validate argv from either the generic tool or a fixed Skill entrypoint."""
    if not argv:
        raise ValueError("命令不能为空")
    if len(argv) > _MAX_SHELL_ARGUMENTS:
        raise ValueError(f"命令参数不能超过 {_MAX_SHELL_ARGUMENTS - 1} 个")
    if any(not isinstance(argument, str) for argument in argv):
        raise ValueError("程序名称和参数必须全部是字符串")

    normalized_argv = list(argv)

    executable = normalized_argv[0]
    if (
        Path(executable).name != executable
        or PureWindowsPath(executable).name != executable
        or "/" in executable
        or "\\" in executable
    ):
        raise PermissionError("只能通过白名单中的程序名称执行，不能提供程序路径")

    normalized_executable = _normalize_executable_name(executable)
    if normalized_executable in _FORBIDDEN_COMMAND_INTERPRETERS:
        raise PermissionError("禁止启动命令解释器或再次嵌套 Shell")

    allowed_commands = _get_allowed_shell_commands()
    if not allowed_commands:
        raise PermissionError(
            f"Shell 白名单为空，请先配置 {_SHELL_ALLOWLIST_ENV}"
        )
    if normalized_executable not in allowed_commands:
        raise PermissionError(
            f"程序 '{executable}' 不在 {_SHELL_ALLOWLIST_ENV} 白名单中"
        )

    flag_group = (
        "python"
        if normalized_executable.startswith("python")
        else normalized_executable
    )
    forbidden_flags = _INLINE_CODE_FLAGS.get(flag_group, frozenset())
    if any(
        argument.casefold() in forbidden_flags
        for argument in normalized_argv[1:]
    ):
        raise PermissionError("禁止使用解释器的内联代码或模块执行参数")

    for argument in normalized_argv[1:]:
        _reject_unsafe_argument_path(argument)

    return normalized_argv, normalized_executable


def _build_restricted_subprocess_env(office_dir: str) -> dict[str, str]:
    """Build a minimal child environment without model/provider credentials."""
    safe_names = (
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "WINDIR",
    )
    child_env = {
        name: os.environ[name]
        for name in safe_names
        if os.environ.get(name)
    }

    temp_dir = os.path.join(office_dir, ".tmp")
    os.makedirs(temp_dir, exist_ok=True)
    child_env.update({
        "HOME": office_dir,
        "USERPROFILE": office_dir,
        "TEMP": temp_dir,
        "TMP": temp_dir,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    return child_env


def _read_bounded_process_output(stream) -> tuple[str, bool]:
    """Read only the tail of a temporary output stream into memory."""
    stream.flush()
    stream.seek(0, os.SEEK_END)
    total_bytes = stream.tell()
    stream.seek(max(0, total_bytes - _MAX_SHELL_OUTPUT_BYTES))
    output = stream.read(_MAX_SHELL_OUTPUT_BYTES).decode("utf-8", errors="replace")
    return output.strip(), total_bytes > _MAX_SHELL_OUTPUT_BYTES


def _execute_restricted_argv(argv: Sequence[str]) -> str:
    """Execute validated argv inside office with a minimal child environment."""
    if not _shell_execution_enabled():
        return (
            "❌ 程序执行默认关闭。若确有需要，请由用户显式配置 "
            f"{_SHELL_ENABLED_ENV}=true，并在 {_SHELL_ALLOWLIST_ENV} 中列出允许的程序。"
        )

    try:
        safe_argv, normalized_executable = _validate_restricted_argv(argv)
        office_dir = str(Path(OFFICE_DIR).resolve(strict=False))
        child_env = _build_restricted_subprocess_env(office_dir)
        executable_path = shutil.which(safe_argv[0], path=child_env.get("PATH"))
        if executable_path is None:
            return f"❌ 执行异常：找不到白名单程序 '{safe_argv[0]}'。"

        with tempfile.TemporaryFile(mode="w+b") as stdout_file, \
             tempfile.TemporaryFile(mode="w+b") as stderr_file:
            result = subprocess.run(
                [executable_path, *safe_argv[1:]],
                shell=False,
                cwd=office_dir,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=_SHELL_TIMEOUT_SECONDS,
                check=False,
            )
            stdout, stdout_truncated = _read_bounded_process_output(stdout_file)
            stderr, stderr_truncated = _read_bounded_process_output(stderr_file)

        output = f" ● 当前系统: {SYS_OS}\n"
        output += (
            f" ● 执行程序: `{normalized_executable}`"
            f"（{len(safe_argv) - 1} 个参数）\n"
        )
        output += f" ● 退出码 (Exit Code): {result.returncode}\n"

        if stdout:
            marker = "...[较早输出已截断]...\n" if stdout_truncated else ""
            output += f"\n[STDOUT]\n{marker}{stdout}"
        if stderr:
            marker = "...[较早错误输出已截断]...\n" if stderr_truncated else ""
            output += f"\n[STDERR]\n{marker}{stderr}"

        if not stdout and not stderr:
            if result.returncode == 0:
                output += "\n(静默执行完毕：无终端输出)"
            else:
                output += "\n(异常退出：Exit Code 非 0，无错误日志输出)"

        return output

    except (PermissionError, ValueError) as exc:
        return f"❌ 权限拒绝：{exc}"
    except subprocess.TimeoutExpired:
        return f"❌ 执行超时：程序运行超过 {_SHELL_TIMEOUT_SECONDS} 秒，已终止。"
    except Exception as exc:
        return f"❌ 执行异常：{exc}"


def execute_office_program(executable: str, arguments: Sequence[str]) -> str:
    """Run a fixed executable with structured arguments for an approved Skill."""
    return _execute_restricted_argv([executable, *arguments])


def _get_safe_path(relative_path: str) -> str:
    """
    将相对于 office 的路径转换为规范绝对路径，并验证最终落点。

    resolve(strict=False) 会解析已经存在的符号链接或 Junction，
    relative_to() 则按真实路径组成部分判断包含关系，避免字符串前缀误判。
    """
    if not isinstance(relative_path, str):
        raise TypeError("路径必须是字符串")

    base_dir = Path(OFFICE_DIR).resolve(strict=False)
    requested_path = Path(relative_path)
    if requested_path.is_absolute():
        raise PermissionError(
            f"越权拦截：禁止使用绝对路径 '{relative_path}'！你只能在 office 工位内活动。"
        )

    target_path = (base_dir / requested_path).resolve(strict=False)
    try:
        target_path.relative_to(base_dir)
    except ValueError as exc:
        raise PermissionError(
            f"越权拦截：你试图访问 office 外的路径 '{relative_path}'！"
        ) from exc

    return str(target_path)


@cyberclaw_tool
def list_office_files(sub_dir: str = "") -> str:
    """
    查看你的 office 工位里有哪些文件和文件夹。
    如果 sub_dir 为空，则查看工位根目录。
    """
    try:
        target_dir = _get_safe_path(sub_dir)
        if not os.path.exists(target_dir):
            return f"目录不存在：{sub_dir}"
        
        if not os.path.isdir(target_dir):
            return f"路径不是目录：{sub_dir}"

        items = sorted(os.listdir(target_dir), key=str.casefold)
        if not items:
            return f"[{sub_dir if sub_dir else 'office 根目录'}] 是空的。"
        
        # 格式化输出，标注是文件还是文件夹
        result = []
        for item in items:
            item_path = os.path.join(target_dir, item)
            item_type = "📁" if os.path.isdir(item_path) else "📄"
            result.append(f"{item_type} {item}")
            
        return "\n".join(result)
    except Exception as e:
        return str(e)


@cyberclaw_tool
def read_office_file(filepath: str) -> str:
    """
    读取 office 工位里指定文件的内容。
    filepath 参数应该是相对于 office 的路径，例如 "test.py" 或 "skills/my_skill.py"。
    """
    try:
        target_path = _get_safe_path(filepath)
        if not os.path.exists(target_path):
            return f"文件不存在：{filepath}"
        if not os.path.isfile(target_path):
            return f"路径不是普通文件：{filepath}"
        
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read(_MAX_OFFICE_READ_CHARS + 1)
            if len(content) > _MAX_OFFICE_READ_CHARS:
                return (
                    content[:_MAX_OFFICE_READ_CHARS]
                    + "\n\n...[内容过长，已被安全截断]..."
                )
            return content
    except Exception as e:
        return str(e)


@cyberclaw_tool
def write_office_file(filepath: str, content: str, mode: str = "w") -> str:
    """
    在 office 工位里操作文件内容。
    
    参数说明:
    - filepath: 相对路径，例如 "spider.py" 或 "docs/readme.md"。
    - content: 要写入的具体文本或代码内容。
    - mode: 写入模式。
        - "w" (默认): 【覆盖/新建】模式。通过同目录临时文件原子替换目标，失败时尽量保留旧文件。
        - "a": 【追加】模式。保留原内容，并只在新旧内容之间确有需要时补一个换行。
        
    ⚠️ 智能体操作规范：
    1. "w" 会替换整个文件，只能在已经拥有完整目标内容时使用。
    2. 本工具不支持删除和重命名，不要为了绕过限制而编写或执行脚本。
    3. 禁止尝试访问 office 工位之外的路径。
    """
    try:
        target_path = _get_safe_path(filepath)
        
        # 严格校验传入的 mode
        if mode not in ["w", "a"]:
             return "❌ 错误：mode 参数必须是 'w' (覆盖) 或 'a' (追加)。"
        
        # 如果模型想在子目录里写文件，确保子目录存在
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        
        if mode == "w":
            _atomic_write_text(target_path, content)
        else:
            _append_text(target_path, content)
                
        action = "覆盖/新建" if mode == "w" else "追加"
        return f" ● 成功以 {action} 模式写入文件：{filepath} (共 {len(content)} 字符)"
    except Exception as e:
        return str(e)
    

@cyberclaw_tool
def execute_office_shell(command: str) -> str:
    """
    在 office 工位中执行显式启用并列入白名单的单个程序。

    默认关闭。启用后也不会经过 PowerShell、cmd 或 Bash，不支持管道、
    重定向、命令连接、绝对路径和父目录跳转。子进程只获得最小化环境，
    不继承模型 API Key。该能力是受限执行器，不是操作系统级安全沙盒。
    """
    try:
        return _execute_restricted_argv(_parse_restricted_command(command))
    except (PermissionError, ValueError) as exc:
        return f"❌ 权限拒绝：{exc}"
    except Exception as exc:
        return f"❌ 执行异常：{exc}"

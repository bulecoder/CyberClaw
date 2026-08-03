import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import tempfile

from .base import cyberclaw_tool
from ..config import OFFICE_DIR

SYS_OS = platform.system()
_MAX_OFFICE_READ_CHARS = 10_000


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
    在 office 工位中执行 Shell 命令。
    
    ⚠️ 【极其重要的环境限制】：
    1. 💻 跨平台注意：当前宿主机可能是 Windows、Linux 或 Mac。请根据你得到的环境反馈，使用对应的原生 Shell 命令（例如 Win 用 dir/del，Linux 用 ls/rm）。如果命令报错，请自行调整重试！
    2. 这是一个非交互式终端！所有命令必须携带免确认参数（如 -y, --quiet）。
    3. 禁止使用 cd 命令跳出当前目录，你的活动范围仅限 office。
    4. [无状态警告] 每次执行都是独立的终端进程！需要进入子目录请使用“命令链”或相对路径。
    5. 禁止一切形式跳出office工位!!! 例如运行跳出或查看office路径的任何脚本以及其他高危操作。
    """
    try:
        dangerous_patterns = [
            r"\.\.",                        # 杀招1：拦截所有相对路径越权 (如 ../)
            r"(?:^|\s|[<>|&;])/",           # 杀招2：Unix 拦截绝对路径 (连 cat </etc/passwd 这种黑客写法也防了)
            r"(?:^|\s|[<>|&;])~",           # 杀招3：Unix 拦截用户主目录 (防 ~/.ssh/)
            r"(?:^|\s|[<>|&;])\\",          # 杀招4：Win 拦截根目录 (防 dir \)
            r"(?i)(?:^|\s|[<>|&;])[a-z]:",  # 杀招5：Win 拦截直接跳盘符及绝对路径 (防 D:, type C:\...)
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, command):
                return f"❌ 权限拒绝：检测到危险的目录跳转指令。你被禁止离开 office 工位！"

        result = subprocess.run(    # 没有显式传入 env，所以默认继承 CyberClaw 的环境变量，继承当前 windows 用户的系统权限和网络能力
            command,
            shell=True, # 命令会交给系统shell解释，因此支持管道、重定向、命令连接、环境变量展开和脚本执行，功能灵活但风险最高
            cwd=OFFICE_DIR, # 子进程从 office 开始，默认工作目录是office
            capture_output=True,    # 完整输出先被捕获到内存
            encoding='utf-8',
            errors='replace',
            timeout=60  # 限制等待时间60s
        )
        
        output = f" ● 当前系统: {SYS_OS}\n"
        output += f" ● 执行命令: `{command}`\n"
        output += f" ● 退出码 (Exit Code): {result.returncode}\n"
        
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        
        if result.returncode != 0 and ("prompt" in stderr.lower() or "y/n" in stdout.lower()):
            output += "\n💡 系统提示：命令可能由于交互式等待而失败。请重试并添加 -y 参数！"
        
        if stdout:
            output += f"\n[STDOUT]\n{stdout[-2000:] if len(stdout) > 2000 else stdout}"
        if stderr:
            output += f"\n[STDERR]\n{stderr[-2000:] if len(stderr) > 2000 else stderr}"
            
        if not stdout and not stderr:
            if result.returncode == 0:
                output += "\n(静默执行完毕：无终端输出)"
            else:
                output += "\n(异常退出：Exit Code 非 0，无错误日志输出)"
            
        return output
        
    except subprocess.TimeoutExpired:
        return "❌ 严重错误：命令执行超时（60s）被熔断！请检查是否有阻塞式交互。"
    except Exception as e:
        return f"❌ 执行异常：{str(e)}"

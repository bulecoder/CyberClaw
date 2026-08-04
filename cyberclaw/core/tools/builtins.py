import ast
from datetime import datetime
import math
import operator
import os
import json
import uuid
import threading

from .base import cyberclaw_tool
from ..config import MEMORY_DIR, TASKS_FILE
from .sandbox_tools import (
    list_office_files,
    read_office_file,
    write_office_file,
    execute_office_shell
)


tasks_lock = threading.Lock()
PROFILE_PATH = os.path.join(MEMORY_DIR, "user_profile.md")


_CALCULATOR_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CALCULATOR_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MAX_EXPRESSION_LENGTH = 200
_MAX_AST_NODES = 64
_MAX_POWER_EXPONENT = 1000
_MAX_INTEGER_BITS = 4096


class CalculatorExpressionError(ValueError):
    """Raised when a calculator expression is outside the safe subset."""


def _validate_calculator_number(value: object) -> int | float:
    """Validate literals and intermediate results produced by the calculator."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculatorExpressionError("只允许整数和浮点数")
    if isinstance(value, float) and not math.isfinite(value):
        raise CalculatorExpressionError("计算结果必须是有限数值")
    if isinstance(value, int) and value.bit_length() > _MAX_INTEGER_BITS:
        raise CalculatorExpressionError("计算结果过大")
    return value


def _evaluate_calculator_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant):
        return _validate_calculator_number(node.value)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _CALCULATOR_UNARY_OPERATORS:
        operand = _evaluate_calculator_node(node.operand)
        return _validate_calculator_number(
            _CALCULATOR_UNARY_OPERATORS[type(node.op)](operand)
        )

    if isinstance(node, ast.BinOp) and type(node.op) in _CALCULATOR_BINARY_OPERATORS:
        left = _evaluate_calculator_node(node.left)
        right = _evaluate_calculator_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POWER_EXPONENT:
            raise CalculatorExpressionError(
                f"幂指数绝对值不能超过 {_MAX_POWER_EXPONENT}"
            )
        result = _CALCULATOR_BINARY_OPERATORS[type(node.op)](left, right)
        return _validate_calculator_number(result)

    raise CalculatorExpressionError("表达式包含不支持的语法")


def _safe_calculate(expression: str) -> int | float:
    """Evaluate a small arithmetic-only expression without executing Python code."""
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorExpressionError("表达式不能为空")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise CalculatorExpressionError(
            f"表达式长度不能超过 {_MAX_EXPRESSION_LENGTH} 个字符"
        )

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorExpressionError("表达式语法错误") from exc

    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise CalculatorExpressionError("表达式过于复杂")

    return _evaluate_calculator_node(tree.body)


@cyberclaw_tool
def get_system_model_info() -> str:
    """
    获取当前 CyberClaw 正在运行的底层大模型（LLM）型号和提供商信息。
    当用户询问“你是基于什么模型”、“你的底层大模型是什么”、“你是GPT还是GLM”、“现在用的什么模型”等身份问题时，调用此工具。
    """
    provider = os.getenv("DEFAULT_PROVIDER", "unknown")
    model = os.getenv("DEFAULT_MODEL", "unknown")
    
    if provider == "unknown" or model == "unknown":
        return "无法获取当前的系统模型配置，可能是环境变量未正确加载。"
        
    return f"当前使用的模型提供商(Provider)是: {provider}，具体型号(Model)是: {model}。"


@cyberclaw_tool
def save_user_profile(new_content: str) -> str:
    """
    更新用户的全局显性记忆档案。
    当你发现用户的偏好发生改变，或者有新的重要事实需要记录时：
    1. 当前画像已经由 Agent 注入上下文，请先阅读其中的完整内容。
    2. 将新信息融入画像，并删去冲突或过时的旧信息。
    3. 将修改后的完整 Markdown 文本作为 new_content 参数传入此工具。
    注意：此操作将完全覆盖旧文件！请确保传入的是完整的最新档案。
    """
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)    # 整文件覆盖

    return "记忆档案已成功覆写更新。新的人设画像已生效。"


@cyberclaw_tool
def get_current_time() -> str:
    """
    获取当前的系统时间和日期。
    当用户询问“现在几点”、“今天星期几”、“今天几号”等与当前时间相关的问题时，调用此工具。
    """
    now = datetime.now()
    return f"当前本地系统时间是: {now.strftime('%Y-%m-%d %H:%M:%S')}"


@cyberclaw_tool
def calculator(expression: str) -> str:
    """
    一个简单的数学计算器。
    用于计算只包含数字、括号和基础算术运算符的表达式，
    例如: '3 * 5'、'100 / 4' 或 '(2 + 3) ** 2'。
    不支持变量、函数调用、属性访问、容器或任何 Python 代码。
    """
    try:
        result = _safe_calculate(expression)
        return f"表达式 '{expression}' 的计算结果是: {result}"
    except Exception as e:
        return f"计算出错，请检查表达式格式。错误信息: {str(e)}"


@cyberclaw_tool
def schedule_task(target_time: str, description: str, repeat: str = None, repeat_count: int = None) -> str:
    """
    为一个未来的任务设定闹钟或提醒。
    参数 target_time 必须是严格的格式："YYYY-MM-DD HH:MM:SS"（请先调用 get_current_time 获取当前时间，并在其基础上推算）。
    参数 description 是需要执行的动作或要说的话。
    
    【高级循环功能】：
    - repeat (可选): 设置重复频率。可选值为 "hourly", "daily", "weekly"。如果不重复请留空。
    - repeat_count (可选): 结合 repeat 使用，表示一共需要触发几次。
    
    【案例教学】：
    1. 用户说："以后每天8点提醒我喝牛奶" -> repeat="daily", repeat_count=None (无限循环)
    2. 用户说："接下来的3天，每天提醒我吃药" -> repeat="daily", repeat_count=3 (有限循环)
    3. 用户说："明早8点叫我起床" -> repeat=None, repeat_count=None (单次任务)

    【时间歧义严格确认协议 (AM/PM Ambiguity CRITICAL)】：
    当用户说出的时间存在 12 小时制的模糊性时（例如：只说了“7点”，没明确说早上还是晚上）：
    1. 你必须向用户提问确认是上午还是下午。
    2. 【死命令】：在用户明确回复“上午”或“下午”（或改为24小时制）之前，本工具处于【绝对锁定状态】！
    3. 就算用户发省略号（如“。。”）、发脾气、或者说无关内容，你也【绝对禁止】为了讨好用户而自行猜测时间！
    4. 严禁出现“抱歉多问了”、“默认早上”这种妥协行为。
    5. 如果用户不明确回答，你必须坚定地回复：“抱歉，没有明确上下午，我无权为您设置闹钟。请明确告知时间段。”并立即中止工具调用。
    """
    try:
        target_dt = datetime.strptime(target_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "设定失败：时间格式错误，必须严格遵循 'YYYY-MM-DD HH:MM:SS' 格式。"
    
    now = datetime.now()
    if target_dt <= now:
        return (
            "设定失败：target_time 必须晚于当前时间。"
            f" 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}，"
            f" 你传入的是：{target_time}"
        )

    with tasks_lock:    # 进入共享锁
        tasks = []
        if os.path.exists(TASKS_FILE):
            try:
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        tasks = json.loads(content)
            except Exception as e:
                return f"设定失败：读取任务队列异常 {str(e)}"

        new_task = {
            "id": str(uuid.uuid4())[:8],
            "target_time": target_time,
            "description": description,
            "repeat": repeat,
            "repeat_count": repeat_count
        }
        tasks.append(new_task)

        try:
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"设定失败：写入任务队列异常 {str(e)}"

    msg = f" 任务已成功加入队列。首发时间：{target_time} | 任务：{description}"
    if repeat:
        msg += f" | 循环模式：{repeat} (共 {repeat_count if repeat_count else '无限'} 次)"
    return msg


@cyberclaw_tool
def list_scheduled_tasks() -> str:
    """
    查看当前所有待处理的定时任务列表。
    当用户询问“我都有哪些任务”、“查一下闹钟”、“刚才定了什么”时调用此工具。
    """
    with tasks_lock:
        if not os.path.exists(TASKS_FILE):
            return "当前没有任何定时任务。"
        
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return "任务列表为空。"
                tasks = json.loads(content)
            
            if not tasks:
                return "当前没有任何定时任务。"
            
            tasks.sort(key=lambda x: x['target_time'])
            
            res = " 当前待执行任务列表：\n"
            for t in tasks:
                res += f"- [ID: {t['id']}] 时间: {t['target_time']} | 任务: {t['description']}\n"
            return res
        except Exception as e:
            return f"查询失败：{str(e)}"
    

@cyberclaw_tool
def delete_scheduled_task(task_id: str) -> str:
    """
    根据任务 ID 取消或删除一个定时任务。
    
    【强制性风险控制协议 (CRITICAL)】：
    删除操作具有不可逆性。
    1. 只要匹配到符合描述的任务数量 > 1。
    2. 无论用户语气多么确定，只要他没提供具体的任务 ID。
    
    【你必须执行的动作】：
    【禁止】在单次回复中针对同一个模糊描述发起多个删除工具调用。
    你必须先列出所有匹配的任务（1. 2. 3.），并询问用户：
    “发现了多个符合条件的提醒（列出列表），为了安全起见，请问是要全部删除，还是只删除其中几个？”
    必须要用户明确给出编号或者说确定全部删除，才能调用此工具！！
    严禁自作主张执行批量删除。
    """

    with tasks_lock:
        if not os.path.exists(TASKS_FILE):
            return "删除失败：任务列表文件不存在。"

        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                tasks = json.loads(content) if content else []
            
            new_tasks = [t for t in tasks if t['id'] != task_id]
            
            if len(new_tasks) == len(tasks):
                return f"删除失败：未找到 ID 为 {task_id} 的任务。"
            
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(new_tasks, f, ensure_ascii=False, indent=2)
            
            return f" 任务 [ID: {task_id}] 已成功取消。"
        except Exception as e:
            return f"操作异常：{str(e)}"
    

@cyberclaw_tool
def modify_scheduled_task(task_id: str, new_time: str = None, new_description: str = None) -> str:
    """
    修改现有定时任务的时间或内容。
    
    【强制性风险控制协议 (CRITICAL)】：
    1. 只要用户通过“模糊描述”（如：那个5天的任务、洗澡的任务）来要求修改，而没有直接提供 ID。
    2. 无论用户的话语看起来是单数还是复数（如：“把5天的任务全改了”）。
    3. 只要系统中匹配到的任务数量 > 1。
    
    【你必须执行的动作】：
    禁止直接调用本工具！你必须向用户展示匹配到的所有任务列表，并强制询问：
    “我发现有 [N] 个任务符合描述（列出列表），请问你是要【全部修改】，还是修改其中【某几个】？（请告诉我编号或确认全部）”
    
    必须在用户回复“全部”或者指定了具体编号后，你才能继续操作！修改任务并非小事,这是为了安全！！
    """

    with tasks_lock:
        if not os.path.exists(TASKS_FILE):
            return "修改失败：任务列表为空。"

        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                tasks = json.loads(content) if content else []
            
            found = False
            for t in tasks:
                if t['id'] == task_id:
                    if new_time:
                        parsed_new_time = datetime.strptime(new_time, "%Y-%m-%d %H:%M:%S")
                        now = datetime.now()
                        if parsed_new_time <= now:
                            return (
                                "修改失败：new_time 必须晚于当前时间。"
                                f" 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}，"
                                f" 你传入的是：{new_time}"
                            )
                        t['target_time'] = new_time
                    if new_description:
                        t['description'] = new_description
                    found = True
                    break
            
            if not found:
                return f"修改失败：未找到 ID 为 {task_id} 的任务。"
            
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
                
            return f" 任务 [ID: {task_id}] 已成功更新。"
        except ValueError:
            return "修改失败：时间格式错误。"
        except Exception as e:
            return f"操作异常：{str(e)}"


BUILTIN_TOOLS = [
    get_current_time,
    calculator,
    save_user_profile,
    list_office_files,
    read_office_file,
    write_office_file,
    execute_office_shell,
    get_system_model_info,
    schedule_task,
    list_scheduled_tasks,
    delete_scheduled_task,
    modify_scheduled_task
]


# Metadata is deliberately conservative. Future policy code may deny or require
# approval based on these fields, so unknown or state-changing tools must not be
# labelled as read-only/concurrent-safe by default.
BUILTIN_TOOL_PROFILES = {
    "get_current_time": {
        "risk": "low",
        "read_only": True,
        "concurrent_safe": True,
    },
    "calculator": {
        "risk": "low",
        "read_only": True,
        "concurrent_safe": True,
    },
    "get_system_model_info": {
        "risk": "low",
        "read_only": True,
        "concurrent_safe": True,
    },
    "list_office_files": {"risk": "low", "read_only": True},
    "read_office_file": {"risk": "low", "read_only": True},
    "list_scheduled_tasks": {"risk": "low", "read_only": True},
    "save_user_profile": {"risk": "medium"},
    "write_office_file": {"risk": "medium"},
    "schedule_task": {"risk": "medium"},
    "delete_scheduled_task": {"risk": "medium"},
    "modify_scheduled_task": {"risk": "medium"},
    "execute_office_shell": {"risk": "high"},
}

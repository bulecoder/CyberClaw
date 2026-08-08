import os
import time
import asyncio
import random
from contextlib import AsyncExitStack
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.styles import Style
from prompt_toolkit.application import get_app

from cyberclaw.core.agent import (
    backfill_interrupted_tool_calls,
    create_agent_app,
)
from cyberclaw.core.config import DB_PATH, ensure_workspace
from cyberclaw.core.environment import load_project_env
from cyberclaw.core.heartbeat import pacemaker_loop
from cyberclaw.core.logger import audit_logger
from cyberclaw.core.mcp import (
    MCPClientManager,
    MCPConfigurationError,
    MCPManagerError,
    load_mcp_server_configs,
)
from cyberclaw.core.runtime import AgentRunLimits, STOP_TASK, shutdown_task_queue
from cyberclaw.core.tools import ApprovalStore, ToolPolicyEngine, ToolRisk

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def type_line(text: str, delay: float = 0.008):
    for ch in text:
        print(ch, end='', flush=True)
        time.sleep(delay)
    print()

def print_banner():
    clear_screen()

    CYAN = '\033[38;5;51m'
    PURPLE = '\033[38;5;141m'
    SILVER = '\033[38;5;250m'
    DIM = '\033[2m'
    BOLD = '\033[1m'
    RESET = '\033[0m'
    WHITE = '\033[37m'

    logo = f"""{CYAN}{BOLD}
 ██████╗██╗   ██╗██████╗ ███████╗██████╗
██╔════╝╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗
██║      ╚████╔╝ ██████╔╝█████╗  ██████╔╝
██║       ╚██╔╝  ██╔══██╗██╔══╝  ██╔══██╗
╚██████╗   ██║   ██████╔╝███████╗██║  ██║
 ╚═════╝   ╚═╝   ╚═════╝ ╚══════╝╚═╝  ╚═╝

 ██████╗██╗      █████╗ ██╗    ██╗
██╔════╝██║     ██╔══██╗██║    ██║
██║     ██║     ███████║██║ █╗ ██║
██║     ██║     ██╔══██║██║███╗██║
╚██████╗███████╗██║  ██║╚███╔███╔╝
 ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝
{RESET}"""

    sub_title = f"{WHITE}{BOLD} 👾 Welcome to the {PURPLE}{BOLD}CyberClaw{RESET}{WHITE}{BOLD} !  {RESET}"

    quotes = [
        "It works on my machine.",
        "It compiles! Ship it.",
        "Git commit, push, pray.",
        "There's no place like 127.0.0.1.",
        "sudo make me a sandwich.",
        "Works fine in dev.",
        "May the source be with you.",
        "Ctrl+C, Ctrl+V, Deploy.",
        "Hello, World."
    ]
    quote = random.choice(quotes)
    meta = f" {SILVER}✦{RESET} {CYAN}{quote}{RESET}"

    tip = (
        f"{PURPLE} ✦ {RESET}"
        f"{SILVER}{PURPLE}{BOLD}CyberClaw{RESET} 已完成启动。输入命令开始，输入 {PURPLE}/exit{RESET}{SILVER} 退出。{RESET}\n"
    )

    print(logo)
    print(sub_title)
    print() 
    time.sleep(0.12)
    print(meta)
    print() 
    type_line(tip, delay=0.004)


def cprint(text="", end="\n"):
    print_formatted_text(ANSI(str(text)), end=end)


async def async_main():
    print_banner()
    ensure_workspace()
    current_provider = os.getenv("DEFAULT_PROVIDER", "aliyun")
    current_model = os.getenv("DEFAULT_MODEL", "glm-5")
    run_limits = AgentRunLimits.from_env()
    task_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=100)
    approval_store = ApprovalStore()
    tool_policy = ToolPolicyEngine(
        approval_risks={ToolRisk.MEDIUM, ToolRisk.HIGH}
    )
    thread_id = "local_geek_master"

    try:
        mcp_configs = load_mcp_server_configs()
    except MCPConfigurationError as exc:
        mcp_configs = ()
        cprint(f"  \033[33m[ MCP 配置未加载：{exc} ]\033[0m")
    mcp_manager = MCPClientManager(mcp_configs)
    try:
        mcp_specs = mcp_manager.start()
    except MCPManagerError as exc:
        mcp_specs = ()
        mcp_manager.close()
        cprint(f"  \033[33m[ MCP 管理器未启动：{exc} ]\033[0m")
    for server_name, error_type in mcp_manager.errors.items():
        cprint(
            f"  \033[33m[ MCP server '{server_name}' 启动失败："
            f"{error_type} ]\033[0m"
        )
    if mcp_specs:
        cprint(f"  \033[36m[ 已加载 {len(mcp_specs)} 个 MCP 工具 ]\033[0m")

    async with AsyncExitStack() as stack:
        stack.push_async_callback(asyncio.to_thread, mcp_manager.close)
        memory = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(DB_PATH)
        )
        app = create_agent_app(
            provider_name=current_provider,
            model_name=current_model,
            checkpointer=memory,
            run_limits=run_limits,
            tool_policy=tool_policy,
            approval_store=approval_store,
            additional_tool_specs=mcp_specs,
        )
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": run_limits.recursion_limit,
        }

        class SpinnerState:
            action_words = [
                "Thinking...", "Working...", "Beep boop...", "Eating bugs...",
                "Charging battery...", "Brewing coffee...", "Blinking lights...",
                "Polishing pixels...", "Scanning matrix...", "Warming up circuits...",
                "Syncing data...", "Pinging server...",
            ]
            current_words = []
            is_spinning = False
            start_time = 0
            frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
            is_tool_calling = False
            tool_msg = ""

        spinner = SpinnerState()

        def get_bottom_toolbar():
            if not spinner.is_spinning:
                return ANSI("")

            elapsed = time.time() - spinner.start_time
            if spinner.is_tool_calling:
                display_msg = spinner.tool_msg
            else:
                idx_word = int(elapsed) % len(spinner.current_words)
                display_msg = f"👾 {spinner.current_words[idx_word]}"

            idx_frame = int(elapsed * 12) % len(spinner.frames)
            frame = spinner.frames[idx_frame]
            return ANSI(f"  \033[38;5;51m{frame}\033[0m \033[38;5;250m{display_msg}\033[0m \033[38;5;141m[{elapsed:.1f}s]\033[0m")

        prompt_message = ANSI("  \033[38;5;51m❯\033[0m ")
        placeholder_text = ANSI("\033[3m\033[38;5;242minput...\033[0m")

        async def agent_worker():
            while True:
                item = await task_queue.get()
                try:
                    if item is STOP_TASK:
                        return

                    spinner.current_words = spinner.action_words.copy()
                    random.shuffle(spinner.current_words)
                    spinner.start_time = time.time()
                    spinner.is_spinning = True
                    spinner.is_tool_calling = False

                    inputs = {"messages": [HumanMessage(content=str(item))]}
                    try:
                        async for event in app.astream(inputs, config=config, stream_mode="updates"):
                            for node_name, node_data in event.items():
                                if node_name == "agent":
                                    last_msg = node_data["messages"][-1]
                                    if getattr(last_msg, "tool_calls", None):
                                        for tool_call in last_msg.tool_calls:
                                            spinner.is_tool_calling = True
                                            spinner.tool_msg = f"唤醒内置工具 : {tool_call['name']}..."
                                            cprint(f"  ●\033[38;5;51m Tool Call: \033[0m{tool_call['name']}")
                                            cprint()
                                    elif last_msg.content:
                                        spinner.is_spinning = False
                                        lines = last_msg.content.strip().split('\n')
                                        formatted_out = f"  \033[38;5;141m❯\033[0m \033[38;5;250m{lines[0]}"
                                        for line in lines[1:]:
                                            formatted_out += f"\n    {line}"
                                        cprint(formatted_out + "\033[0m")
                                else:
                                    spinner.is_tool_calling = False
                                    for tool_message in node_data.get("messages", []):
                                        artifact = getattr(tool_message, "artifact", None)
                                        result = (
                                            artifact.get("cyberclaw_tool_result", {})
                                            if isinstance(artifact, dict)
                                            else {}
                                        )
                                        approval = result.get("metadata", {}).get(
                                            "approval", {}
                                        )
                                        request_id = approval.get("request_id")
                                        if request_id:
                                            cprint(
                                                "  \033[33m● 需要用户审批\033[0m\n"
                                                f"    工具：{approval['tool_name']}\n"
                                                f"    参数：{approval['arguments']}\n"
                                                f"    批准一次：/approve {request_id}\n"
                                                f"    拒绝：/deny {request_id}"
                                            )
                    except asyncio.CancelledError:
                        try:
                            repaired_calls = await asyncio.wait_for(
                                backfill_interrupted_tool_calls(app, config),
                                timeout=2.0,
                            )
                        except Exception:
                            repaired_calls = 0
                            cprint(
                                "  \033[33m[ ⚠️ 当前运行已取消，但未能确认"
                                " Checkpoint 中的工具消息是否完整。 ]\033[0m"
                            )
                        if repaired_calls:
                            cprint(
                                "  \033[33m[ ⚠️ 当前运行已取消，"
                                f"已为 {repaired_calls} 个未完成工具调用补写中断结果。 ]"
                                "\033[0m"
                            )
                        raise
                    except Exception as exc:
                        cprint(f"  \033[31m[ ⚠️ 引擎异常 : {exc} ]\033[0m")
                    cprint()
                finally:
                    spinner.is_spinning = False
                    task_queue.task_done()

        async def user_input_loop():
            custom_style = Style.from_dict({
                'bottom-toolbar': 'bg:default fg:default noreverse',
            })
            session = PromptSession(
                bottom_toolbar=get_bottom_toolbar,
                style=custom_style,
                erase_when_done=True,
                reserve_space_for_menu=0,
            )

            async def redraw_timer():
                while True:
                    if spinner.is_spinning:
                        try:
                            get_app().invalidate()
                        except Exception:
                            pass
                    await asyncio.sleep(0.08)

            redraw_task = asyncio.create_task(redraw_timer(), name="cyberclaw-redraw")
            try:
                while True:
                    try:
                        user_input = await session.prompt_async(
                            prompt_message,
                            placeholder=placeholder_text,
                        )
                    except (KeyboardInterrupt, EOFError):
                        cprint("\n  \033[38;5;141m✦ 正在安全停止，CyberClaw 进入休眠。\033[0m")
                        return

                    user_input = user_input.strip()
                    if not user_input:
                        continue

                    padded_bubble = f"  ❯ {user_input}    "
                    cprint(f"\033[48;2;38;38;38m\033[38;5;255m{padded_bubble}\033[0m\n")
                    if user_input.lower() in ["/exit", "/quit"]:
                        cprint("  \033[38;5;141m✦ 正在安全停止，CyberClaw 进入休眠。\033[0m")
                        return
                    command, _, request_id = user_input.partition(" ")
                    if command.lower() == "/approve":
                        grant = approval_store.approve(
                            request_id.strip(),
                            thread_id=thread_id,
                        )
                        if grant is None:
                            cprint("  \033[33m[ 审批编号无效、已处理或已过期。]\033[0m\n")
                            continue
                        cprint("  \033[32m[ 已批准一次，将重试完全相同的工具调用。]\033[0m\n")
                        await task_queue.put(
                            "用户已在终端批准上一个工具调用。"
                            "请使用完全相同的工具名称和参数重新发起；"
                            "不要修改参数或改为其他高风险操作。"
                        )
                        continue
                    if command.lower() == "/deny":
                        denied = approval_store.deny(
                            request_id.strip(),
                            thread_id=thread_id,
                        )
                        message = (
                            "已拒绝该工具调用。"
                            if denied
                            else "审批编号无效、已处理或已过期。"
                        )
                        cprint(f"  \033[33m[ {message}]\033[0m\n")
                        continue
                    await task_queue.put(user_input)
            finally:
                redraw_task.cancel()
                await asyncio.gather(redraw_task, return_exceptions=True)

        with patch_stdout():
            worker = asyncio.create_task(agent_worker(), name="cyberclaw-agent")
            heartbeat_worker = asyncio.create_task(
                pacemaker_loop(task_queue=task_queue, check_interval=10),
                name="cyberclaw-heartbeat",
            )
            try:
                await user_input_loop()
            finally:
                clean_shutdown = await shutdown_task_queue(
                    task_queue,
                    consumer=worker,
                    producers=(heartbeat_worker,),
                )
                if not clean_shutdown:
                    cprint("  \033[33m[ ⚠️ 后台任务未能在限定时间内正常结束，已强制清理。]\033[0m")

def main():
    try:
        load_project_env()
        asyncio.run(async_main())
    finally:
        if not audit_logger.close():
            cprint("  \033[33m[ ⚠️ 审计日志未能在限定时间内完全刷新。]\033[0m")

if __name__ == "__main__":
    main()

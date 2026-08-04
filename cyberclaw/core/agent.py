import os
from collections.abc import Sequence

from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import ANSI

from . import provider, skill_loader
from .config import MEMORY_DIR
from .context import AgentState, trim_context_messages
from .logger import audit_logger
from .tools import ToolRegistry, ToolRisk, ToolSource
from .tools import builtins as builtin_tools


def build_tool_registry(
    tools: Sequence[BaseTool] | None = None,
    tool_registry: ToolRegistry | None = None,
) -> ToolRegistry:
    """Build an immutable tool capability snapshot for one Agent graph."""
    if tools is not None and tool_registry is not None:
        raise ValueError("tools 与 tool_registry 不能同时传入")

    if tool_registry is not None:
        return tool_registry.snapshot()

    registry = ToolRegistry()
    if tools is not None:
        for tool in tools:
            registry.register_tool(tool, source=ToolSource.CUSTOM)
        return registry.freeze()

    for tool in builtin_tools.BUILTIN_TOOLS:
        profile = builtin_tools.BUILTIN_TOOL_PROFILES.get(tool.name, {})
        registry.register_tool(
            tool,
            source=ToolSource.BUILTIN,
            risk=ToolRisk(profile.get("risk", ToolRisk.MEDIUM)),
            read_only=bool(profile.get("read_only", False)),
            concurrent_safe=bool(profile.get("concurrent_safe", False)),
            timeout_seconds=profile.get("timeout_seconds"),
        )

    dynamic_tools = skill_loader.load_dynamic_skills(
        reserved_names=set(registry.names)
    )
    for tool in dynamic_tools:
        registry.register_tool(
            tool,
            source=ToolSource.SKILL,
            risk=ToolRisk.HIGH,
        )
    return registry.freeze()


def create_agent_app(
    provider_name: str = "openai",
    model_name: str = "gpt-4o-mini",
    tools: Sequence[BaseTool] | None = None,
    tool_registry: ToolRegistry | None = None,
    checkpointer = None
):
    registry = build_tool_registry(tools=tools, tool_registry=tool_registry)
    actual_tools = list(registry.tools)

    tool_node = ToolNode(actual_tools)  # 创建执行器（工具交给 ToolNode）

    llm = provider.get_provider(
        provider_name=provider_name,
        model_name=model_name,
    )
    llm_with_tools = llm.bind_tools(actual_tools)   # 把工具描述交给模型，bind_tools不会执行工具，而是把工具名称、描述和参数schema告诉模型

    def agent_node(state: AgentState, config: RunnableConfig) -> dict:
        """
        核心大脑：读取状态托盘里的历史消息，决定是直接回答，还是调用工具。
        """
        thread_id = config.get("configurable", {}).get("thread_id", "system_default")   # 读取会话ID

        raw_messages = state["messages"]

        if raw_messages:    # 从后往前找连续的 ToolMessage，然后写入审计日志
            recent_tool_msgs = []
            for msg in reversed(raw_messages):
                if msg.type == "tool":
                    recent_tool_msgs.append(msg)
                else:
                    break
            for msg in reversed(recent_tool_msgs):
                audit_logger.log_event(
                    thread_id=thread_id,
                    event="tool_result",
                    tool=msg.name,
                    result_chars=len(str(msg.content)),
                )

        current_summary = state.get("summary", "")
        final_msgs, discarded_msgs = trim_context_messages(raw_messages, trigger_turns=40, keep_turns=10)   # 上下文裁剪
        state_updates = {}

        if discarded_msgs:  # 如果发生了上下文压缩，将舍弃掉的旧对话提取摘要，融合到上下文摘要里面
            import sys
            print_formatted_text(ANSI("\033[K \033[38;5;141m ● 正在更新上下文记忆... \033[0m"))
            discarded_text = "\n".join([f"{m.type}: {m.content}" for m in discarded_msgs if m.content])
        
            summary_prompt = (
                    f"你是一个负责维护 AI 工作台上下文的后台模块。\n\n"
                    f"【现有的交接文档】\n{current_summary if current_summary else '暂无记录'}\n\n"
                    f"【刚刚过去的旧对话】\n{discarded_text}\n\n"
                    f"任务：请仔细阅读旧对话，提取出当前的对话语境和任务进度。\n"
                    f"动作：将新进展与【现有的交接文档】进行无缝融合，输出一份最新的上下文摘要。\n"
                    f"严格警告：只记录'我们在聊什么'、'解决了什么问题'、'得出了什么结论'等。绝对不要记录用户的静态偏好(如姓名、职业、爱好等)，这部分由其他模块负责！\n"
                    f"要求：客观、精简，不要输出任何解释性废话，直接返回最新的记忆文本，总字数不要超过150字"
                )
        
            # 这里可以用便宜模型
            new_summary_response = llm.invoke([HumanMessage(content=summary_prompt)], config={"callbacks":[]})  # 这里使用的是llm而不是llm_with_tools，摘要调用没有绑定工具，不会进入工具循环
            active_summary = new_summary_response.content

            # 更新摘要
            state_updates["summary"] = active_summary

            # 从状态机中删除信息
            delete_cmds = [RemoveMessage(id=m.id) for m in discarded_msgs if m.id]  # 这里不是直接操作列表，而是返回一组状态更新命令，之后add_messages reducer处理
            state_updates["messages"] = delete_cmds
        else:
            active_summary = current_summary

        # 读取用户画像，用户画像没有缓存在 AgentState中，每次思考前都会重新读取文件
        profile_path = os.path.join(MEMORY_DIR, "user_profile.md")
        profile_content = "暂无记录"
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
                if content:
                    profile_content = content

        sys_prompt = (
            "你是 CyberClaw，一个聪明、高效、说话自然的 AI 助手。\n\n"
            "【对话核心原则】\n"
            "1. 像人类一样自然对话。\n"
            "2. 【双脑协同】：在回答时，你必须综合考量下方的【用户长期画像】（对方的习惯与底线）与【近期对话上下文】（目前的任务进度）。\n"
            "3. 【记忆进化】：当你敏锐地捕捉到用户提及了新的长期偏好、个人信息，或要求你“记住某事”时，必须主动调用 'save_user_profile' 工具更新画像。\n"
            "4. 保持简练，直接回应用户【最新】的一句话。并且要很自然地，像一个非常了解用户的好朋友一样，禁止说'根据你的用户画像'类似的机器人回答\n"
            "【工作区安全规则】\n"
            "1. 文件工具只允许访问 office 工位，不得尝试读取或写入其外部路径。\n"
            "2. 程序执行能力默认关闭；即使用户显式启用，也只能调用配置白名单中的单个程序，不支持管道、重定向、命令连接或嵌套 Shell。\n"
            "3. 不得使用 Python、Node.js 等解释器的内联代码参数绕过限制，也不得运行意图访问 office 外部资源的脚本。\n"
            "4. 工具返回权限拒绝时必须停止该操作，不得通过其他工具或编码方式绕过。\n"
            "5. 当前边界属于受限工作区和防误操作规则，不是操作系统级隔离沙盒；不要向用户声称已经实现绝对安全。"
        )

        sys_prompt += (
            f"\n\n=============================\n"
            f"【用户长期画像 (静态偏好)】\n"
            f"{profile_content}\n"
            f"=============================\n"
        )

        if active_summary:
            sys_prompt += f"\n\n[近期对话上下文]\n{active_summary}\n\n(注：这是系统自动生成的近期沟通摘要，请结合它来理解用户的最新问题)"

        msgs_for_llm = [SystemMessage(content=sys_prompt)] + \
        [m for m in final_msgs if not isinstance(m, SystemMessage)] # 从 final_msgs里面保留所有不是 SystemMessage 的消息，保证最终只有新创建的系统提示词，避免旧的系统消息重复出现s

        for m in msgs_for_llm:  # 清理消息文本中可能导致 UTF-8编码失败的非法字符
            if isinstance(m.content, str):
                m.content = m.content.encode('utf-8', 'ignore').decode('utf-8')

        # 记录即将发送给发模型的消息 (监控Token)
        audit_logger.log_event(
            thread_id=thread_id,
            event="llm_input",
            message_count=len(msgs_for_llm)
        )

        response = llm_with_tools.invoke(msgs_for_llm)  # 对话时调用的是绑定（bind）后的模型

        # 解析大模型的回答并记录到日志
        if response.tool_calls:
            for tool_call in response.tool_calls:
                audit_logger.log_event(
                    thread_id=thread_id,
                    event="tool_call",
                    tool=tool_call["name"],
                    args=tool_call["args"]
                )
        elif response.content:
            audit_logger.log_event(
                thread_id=thread_id,
                event="ai_message",
                content_chars=len(str(response.content)),
            )

        if "messages" not in state_updates:
            state_updates["messages"] = []
        state_updates["messages"].append(response)  # 状态更新，本轮新 AIMessage也会追加进去

        return state_updates    # 返回状态更新

    workflow = StateGraph(AgentState)   # 创建状态图，状态是 AgentState

    # 注册两个节点
    workflow.add_node("agent", agent_node)  # 把消息发给模型，得到AIMessage
    workflow.add_node("tools", tool_node)   # 根据AIMessage的 tool_calls 执行本地工具


    workflow.add_edge(START, "agent")   # 定义入口

    # 每次 agent 思考完，检查它有没有发出工具调用指令。
    # tools_condition 会自动判断：有指令 -> 走向 "tools" 节点；没指令 -> 走向 END。
    workflow.add_conditional_edges("agent", tools_condition)    # 定义条件分支

    workflow.add_edge("tools", "agent")     # tools 后必须回到 agent，工具结果必须回到模型

    app = workflow.compile(checkpointer=checkpointer)   # 编译图

    return app

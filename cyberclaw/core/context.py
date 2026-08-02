from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    # 存储对话历史。    BaseMessage类型的列表，使用add_messages函数合并旧消息和新消息
    messages: Annotated[list[BaseMessage], add_messages]    # langgraph 将 add_messages 称为消息字段的 reducer(归并函数)，新消息通常会追加，如果新消息和旧消息具有相同的ID，则会更新对应的旧消息，不会重复追加
    
    # 摘要压缩
    summary: str

def trim_context_messages(messages: list[BaseMessage], trigger_turns: int = 8, keep_turns: int = 4) -> tuple[list[BaseMessage], list[BaseMessage]]:
    # 按照完整用户回合来裁剪上下文：即 一个会从从HumanMessage开始，直到下一个HumanMessage结束，会把AIMessage、tool_calls、ToolMessage一并保留
    first_system = next((m for m in messages if isinstance(m, SystemMessage)), None)    # 找到第一条 SystemMessage
    non_system_msgs = [m for m in messages if not isinstance(m, SystemMessage)]     # 排除所有系统消息

    if not non_system_msgs:
        return ([first_system] if first_system else []), []
    
    turns: list[list[BaseMessage]] = []
    current_turn: list[BaseMessage] = []

    # 遍历非系统信息，按回合进行分组
    for msg in non_system_msgs:     # 按 HumanMessage 分组，遇到新用户消息，就把上一个回合保存，再开始新回合
        if isinstance(msg, HumanMessage):
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        else:
            if current_turn:
                current_turn.append(msg)
    
    # 保存最后一个回合
    if current_turn:
        turns.append(current_turn)

    total_turns = len(turns)

    if total_turns < trigger_turns:     # 判断是否达到阈值，没有达到阈值直接拼接返回
        final_messages = ([first_system] if first_system else []) + non_system_msgs
        return final_messages, []
    
    recent_turns = turns[-keep_turns:]
    discarded_turns = turns[:-keep_turns]

    final_messages: list[BaseMessage] = []
    if first_system:
        final_messages.append(first_system)
    for turn in recent_turns:
        final_messages.extend(turn)

    discarded_messages: list[BaseMessage] = []
    for turn in discarded_turns:
        discarded_messages.extend(turn)

    return final_messages, discarded_messages

    

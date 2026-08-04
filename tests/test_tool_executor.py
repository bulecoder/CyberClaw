import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from cyberclaw.core.tools import (
    ToolExecutorNode,
    ToolProtocolError,
    ToolRegistry,
    ToolResultStatus,
    ToolSource,
)
from cyberclaw.core.runtime import AgentRunLimits


@tool
def echo_tool(value: str) -> str:
    """Return a value."""
    return f"echo:{value}"


@tool
def internal_type_error(value: str) -> str:
    """Raise TypeError from inside a valid tool call."""
    raise TypeError(f"internal:{value}")


@tool
def permission_tool(path: str) -> str:
    """Reject an operation with PermissionError."""
    raise PermissionError(f"禁止访问 {path}")


@tool
def timeout_tool() -> str:
    """Report a timeout raised by the tool implementation."""
    raise TimeoutError("too slow")


_execution_order: list[str] = []


@tool
def ordered_tool(value: str) -> str:
    """Record serial execution order."""
    _execution_order.append(value)
    return value


@tool
def config_tool(value: str, config: RunnableConfig) -> str:
    """Read the thread id injected through RunnableConfig."""
    thread_id = config.get("configurable", {}).get("thread_id", "missing")
    return f"{value}:{thread_id}"


def _registry(*registered_tools) -> ToolRegistry:
    registry = ToolRegistry()
    for registered_tool in registered_tools:
        registry.register_tool(
            registered_tool,
            source=ToolSource.CUSTOM,
        )
    return registry.freeze()


def _call(name: str, args: object, call_id: str = "call-1") -> dict:
    return {
        "name": name,
        "args": args,
        "id": call_id,
        "type": "tool_call",
    }


def _config(thread_id: str = "thread-a") -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


class TestToolExecutorResultClassification(unittest.TestCase):
    def test_success_returns_structured_artifact_and_readable_content(self):
        executor = ToolExecutorNode(_registry(echo_tool))

        result = executor.execute_call(
            _call("echo_tool", {"value": "hello"}),
            _config(),
        )
        message = result.to_tool_message()

        self.assertEqual(result.status, ToolResultStatus.SUCCESS)
        self.assertTrue(result.succeeded)
        self.assertEqual(message.content, "echo:hello")
        self.assertEqual(message.tool_call_id, "call-1")
        self.assertEqual(message.status, "success")
        structured = message.artifact["cyberclaw_tool_result"]
        self.assertEqual(structured["status"], "success")
        self.assertEqual(structured["metadata"]["source"], "custom")
        self.assertIn("duration_ms", structured["metadata"])

    def test_unknown_tool_is_not_dispatched(self):
        executor = ToolExecutorNode(_registry(echo_tool))

        result = executor.execute_call(
            _call("missing_tool", {}),
            _config(),
        )

        self.assertEqual(result.status, ToolResultStatus.TOOL_NOT_FOUND)
        self.assertEqual(result.error_type, "ToolNotFound")
        self.assertIn("echo_tool", str(result.content))

    def test_invalid_argument_shape_is_rejected_before_invocation(self):
        executor = ToolExecutorNode(_registry(echo_tool))

        result = executor.execute_call(
            _call("echo_tool", "not-an-object"),
            _config(),
        )

        self.assertEqual(result.status, ToolResultStatus.INVALID_ARGUMENTS)
        self.assertEqual(result.error_type, "InvalidArgumentShape")

    def test_schema_error_does_not_echo_raw_input(self):
        executor = ToolExecutorNode(_registry(echo_tool))
        secret_value = "should-not-be-echoed"

        result = executor.execute_call(
            _call("echo_tool", {"wrong": secret_value}),
            _config(),
        )

        self.assertEqual(result.status, ToolResultStatus.INVALID_ARGUMENTS)
        self.assertNotIn(secret_value, str(result.content))
        self.assertIn("value", str(result.content))

    def test_internal_type_error_is_not_misclassified_as_bad_arguments(self):
        executor = ToolExecutorNode(_registry(internal_type_error))

        result = executor.execute_call(
            _call("internal_type_error", {"value": "valid"}),
            _config(),
        )

        self.assertEqual(result.status, ToolResultStatus.EXECUTION_ERROR)
        self.assertEqual(result.error_type, "TypeError")
        self.assertNotIn("internal:valid", str(result.content))

    def test_permission_error_has_a_distinct_status(self):
        executor = ToolExecutorNode(_registry(permission_tool))

        result = executor.execute_call(
            _call("permission_tool", {"path": "../outside"}),
            _config(),
        )

        self.assertEqual(result.status, ToolResultStatus.PERMISSION_DENIED)
        self.assertEqual(result.error_type, "PermissionError")
        self.assertEqual(result.to_tool_message().status, "error")

    def test_timeout_error_has_a_distinct_status(self):
        executor = ToolExecutorNode(_registry(timeout_tool))

        result = executor.execute_call(
            _call("timeout_tool", {}),
            _config(),
        )

        self.assertEqual(result.status, ToolResultStatus.TIMEOUT)
        self.assertEqual(result.error_type, "TimeoutError")

    def test_runnable_config_is_forwarded_to_structured_tool(self):
        executor = ToolExecutorNode(_registry(config_tool))

        result = executor.execute_call(
            _call("config_tool", {"value": "ok"}),
            _config("session-42"),
        )

        self.assertEqual(result.status, ToolResultStatus.SUCCESS)
        self.assertEqual(result.content, "ok:session-42")


class TestToolExecutorNodeProtocol(unittest.TestCase):
    def test_tool_budget_executes_only_the_allowed_calls_and_pairs_the_rest(self):
        _execution_order.clear()
        executor = ToolExecutorNode(
            _registry(ordered_tool),
            max_tool_calls=1,
        )
        ai_message = AIMessage(
            content="",
            tool_calls=[
                _call("ordered_tool", {"value": "executed"}, "call-1"),
                _call("ordered_tool", {"value": "blocked"}, "call-2"),
            ],
        )

        update = executor(
            {"messages": [HumanMessage(content="run"), ai_message]},
            _config(),
        )

        self.assertEqual(_execution_order, ["executed"])
        self.assertEqual(
            [message.tool_call_id for message in update["messages"]],
            ["call-1", "call-2"],
        )
        self.assertEqual(
            [
                message.artifact["cyberclaw_tool_result"]["status"]
                for message in update["messages"]
            ],
            ["success", "budget_exceeded"],
        )

    def test_node_pairs_every_call_in_original_serial_order(self):
        _execution_order.clear()
        executor = ToolExecutorNode(_registry(ordered_tool))
        ai_message = AIMessage(
            content="",
            tool_calls=[
                _call("ordered_tool", {"value": "first"}, "call-1"),
                _call("ordered_tool", {"value": "second"}, "call-2"),
            ],
        )

        update = executor({"messages": [ai_message]}, _config())

        self.assertEqual(_execution_order, ["first", "second"])
        self.assertEqual(
            [message.tool_call_id for message in update["messages"]],
            ["call-1", "call-2"],
        )

    def test_node_pairs_success_and_failures_without_dropping_calls(self):
        executor = ToolExecutorNode(_registry(echo_tool))
        ai_message = AIMessage(
            content="",
            tool_calls=[
                _call("echo_tool", {"value": "ok"}, "call-success"),
                _call("missing_tool", {}, "call-missing"),
                _call("echo_tool", {"wrong": "x"}, "call-invalid"),
            ],
        )

        update = executor({"messages": [ai_message]}, _config())

        self.assertEqual(
            [message.tool_call_id for message in update["messages"]],
            ["call-success", "call-missing", "call-invalid"],
        )
        self.assertEqual(
            [message.status for message in update["messages"]],
            ["success", "error", "error"],
        )
        self.assertEqual(
            [
                message.artifact["cyberclaw_tool_result"]["status"]
                for message in update["messages"]
            ],
            ["success", "tool_not_found", "invalid_arguments"],
        )

    def test_node_rejects_state_without_terminal_ai_tool_call(self):
        executor = ToolExecutorNode(_registry(echo_tool))

        with self.assertRaises(ToolProtocolError):
            executor({"messages": []}, _config())

        with self.assertRaises(ToolProtocolError):
            executor(
                {"messages": [AIMessage(content="done")]},
                _config(),
            )


class TestToolExecutorGraphIntegration(unittest.TestCase):
    @patch("cyberclaw.core.agent.audit_logger.log_event", return_value=True)
    @patch("cyberclaw.core.agent.provider.get_provider")
    def test_model_budget_resets_for_each_new_user_turn(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from langgraph.checkpoint.memory import MemorySaver

        from cyberclaw.core.agent import create_agent_app

        bound_model = Mock()
        bound_model.invoke.side_effect = [
            AIMessage(content="first answer"),
            AIMessage(content="second answer"),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(
            tools=[echo_tool],
            checkpointer=MemorySaver(),
            run_limits=AgentRunLimits(
                max_model_calls=1,
                max_tool_calls=1,
                recursion_limit=4,
            ),
        )
        config = _config("persistent-budget-thread")

        first = app.invoke(
            {"messages": [HumanMessage(content="first")], "summary": ""},
            config=config,
        )
        second = app.invoke(
            {"messages": [HumanMessage(content="second")], "summary": ""},
            config=config,
        )

        self.assertEqual(first["messages"][-1].content, "first answer")
        self.assertEqual(second["messages"][-1].content, "second answer")
        self.assertEqual(bound_model.invoke.call_count, 2)

    @patch("cyberclaw.core.agent.audit_logger.log_event", return_value=True)
    @patch("cyberclaw.core.agent.provider.get_provider")
    def test_model_call_budget_stops_an_infinite_tool_loop(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from cyberclaw.core.agent import create_agent_app

        bound_model = Mock()
        bound_model.invoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[_call("echo_tool", {"value": "one"}, "call-1")],
            ),
            AIMessage(
                content="",
                tool_calls=[_call("echo_tool", {"value": "two"}, "call-2")],
            ),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(
            tools=[echo_tool],
            run_limits=AgentRunLimits(
                max_model_calls=2,
                max_tool_calls=10,
                recursion_limit=6,
            ),
        )
        result = app.invoke(
            {
                "messages": [HumanMessage(content="keep using tools")],
                "summary": "",
            },
            config=_config("budget-thread"),
        )

        self.assertEqual(bound_model.invoke.call_count, 2)
        self.assertIn("达到模型调用上限", result["messages"][-1].content)
        self.assertFalse(result["messages"][-1].tool_calls)

    @patch("cyberclaw.core.agent.audit_logger.log_event", return_value=True)
    @patch("cyberclaw.core.agent.provider.get_provider")
    def test_graph_preserves_protocol_when_tool_budget_is_exhausted(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from cyberclaw.core.agent import create_agent_app

        _execution_order.clear()
        bound_model = Mock()
        bound_model.invoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    _call("ordered_tool", {"value": "one"}, "call-1"),
                    _call("ordered_tool", {"value": "two"}, "call-2"),
                ],
            ),
            AIMessage(content="done"),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(
            tools=[ordered_tool],
            run_limits=AgentRunLimits(
                max_model_calls=3,
                max_tool_calls=1,
                recursion_limit=8,
            ),
        )
        result = app.invoke(
            {
                "messages": [HumanMessage(content="run both")],
                "summary": "",
            },
            config=_config("tool-budget-thread"),
        )

        tool_messages = [
            message
            for message in result["messages"]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(_execution_order, ["one"])
        self.assertEqual(
            [message.tool_call_id for message in tool_messages],
            ["call-1", "call-2"],
        )
        self.assertEqual(
            tool_messages[1].artifact["cyberclaw_tool_result"]["status"],
            "budget_exceeded",
        )

    @patch("cyberclaw.core.agent.audit_logger.log_event", return_value=True)
    @patch("cyberclaw.core.agent.provider.get_provider")
    def test_agent_graph_executes_and_pairs_tool_result(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from cyberclaw.core.agent import create_agent_app

        bound_model = Mock()
        bound_model.invoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    _call("echo_tool", {"value": "graph"}, "graph-call")
                ],
            ),
            AIMessage(content="done"),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(tools=[echo_tool])
        result = app.invoke(
            {
                "messages": [HumanMessage(content="run the tool")],
                "summary": "",
            },
            config=_config("graph-thread"),
        )

        tool_messages = [
            message
            for message in result["messages"]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].tool_call_id, "graph-call")
        self.assertEqual(tool_messages[0].content, "echo:graph")
        self.assertEqual(
            tool_messages[0].artifact["cyberclaw_tool_result"]["status"],
            "success",
        )
        self.assertEqual(result["messages"][-1].content, "done")


class TestToolExecutorAsyncGraphIntegration(unittest.IsolatedAsyncioTestCase):
    @patch("cyberclaw.core.agent.audit_logger.log_event", return_value=True)
    @patch("cyberclaw.core.agent.provider.get_provider")
    async def test_astream_supports_the_sync_serial_executor_node(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from cyberclaw.core.agent import create_agent_app

        bound_model = Mock()
        bound_model.invoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    _call("echo_tool", {"value": "stream"}, "stream-call")
                ],
            ),
            AIMessage(content="done"),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(tools=[echo_tool])
        events = [
            event
            async for event in app.astream(
                {
                    "messages": [HumanMessage(content="stream the tool")],
                    "summary": "",
                },
                config=_config("stream-thread"),
                stream_mode="updates",
            )
        ]

        tool_messages = [
            message
            for event in events
            for message in event.get("tools", {}).get("messages", [])
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].tool_call_id, "stream-call")
        self.assertEqual(tool_messages[0].content, "echo:stream")


if __name__ == "__main__":
    unittest.main()

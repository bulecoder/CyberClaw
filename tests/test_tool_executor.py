import threading
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from cyberclaw.core.tools import (
    ApprovalStore,
    ToolExecutorNode,
    ToolPolicyEngine,
    ToolProtocolError,
    ToolRegistry,
    ToolResultStatus,
    ToolSource,
    build_interrupted_tool_messages,
    find_pending_tool_calls,
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
_phase_trace: list[str] = []
_phase_barriers: dict[str, threading.Barrier] = {}
_phase_lock = threading.Lock()


@tool
def ordered_tool(value: str) -> str:
    """Record serial execution order."""
    _execution_order.append(value)
    return value


@tool
def concurrent_phase_tool(phase: str, value: str) -> str:
    """Synchronize with another call in the same safe execution group."""
    _phase_barriers[phase].wait(timeout=1)
    with _phase_lock:
        _phase_trace.append(f"{phase}:{value}")
    return value


@tool
def serial_barrier_tool(value: str) -> str:
    """Record a serial barrier between safe execution groups."""
    with _phase_lock:
        _phase_trace.append(f"serial:{value}")
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

    def test_policy_deny_blocks_tool_before_invocation(self):
        _execution_order.clear()
        executor = ToolExecutorNode(
            _registry(ordered_tool),
            policy=ToolPolicyEngine(denied_tools={"ordered_tool"}),
        )

        result = executor.execute_call(
            _call("ordered_tool", {"value": "blocked"}),
            _config(),
        )

        self.assertEqual(_execution_order, [])
        self.assertEqual(result.status, ToolResultStatus.PERMISSION_DENIED)
        self.assertEqual(result.error_type, "ToolPolicyDenied")
        self.assertEqual(result.metadata["policy"]["behavior"], "deny")
        self.assertIn("invocation_fingerprint", result.metadata)

    def test_policy_ask_fails_closed_without_an_approval(self):
        executor = ToolExecutorNode(
            _registry(echo_tool),
            policy=ToolPolicyEngine(approval_tools={"echo_tool"}),
        )

        result = executor.execute_call(
            _call("echo_tool", {"value": "confirm"}),
            _config(),
        )

        self.assertEqual(result.status, ToolResultStatus.PERMISSION_DENIED)
        self.assertEqual(result.error_type, "ToolApprovalRequired")
        self.assertEqual(result.metadata["policy"]["behavior"], "ask")

    def test_approved_invocation_executes_once_and_changed_args_do_not(self):
        _execution_order.clear()
        store = ApprovalStore()
        executor = ToolExecutorNode(
            _registry(ordered_tool),
            policy=ToolPolicyEngine(approval_tools={"ordered_tool"}),
            approval_store=store,
        )
        call = _call("ordered_tool", {"value": "approved"})

        pending = executor.execute_call(call, _config())
        request_id = pending.metadata["approval"]["request_id"]
        store.approve(request_id, thread_id="thread-a")
        approved = executor.execute_call(call, _config())
        reused = executor.execute_call(call, _config())
        changed = executor.execute_call(
            _call("ordered_tool", {"value": "changed"}),
            _config(),
        )

        self.assertEqual(_execution_order, ["approved"])
        self.assertEqual(approved.status, ToolResultStatus.SUCCESS)
        self.assertTrue(approved.metadata["approval"]["consumed"])
        self.assertEqual(reused.error_type, "ToolApprovalRequired")
        self.assertEqual(changed.error_type, "ToolApprovalRequired")
        self.assertNotEqual(
            reused.metadata["approval"]["request_id"],
            changed.metadata["approval"]["request_id"],
        )


class TestToolExecutorNodeProtocol(unittest.TestCase):
    def test_only_consecutive_concurrent_safe_calls_run_in_parallel(self):
        _phase_trace.clear()
        _phase_barriers.clear()
        _phase_barriers.update({
            "before": threading.Barrier(2),
            "after": threading.Barrier(2),
        })
        registry = ToolRegistry()
        registry.register_tool(
            concurrent_phase_tool,
            source=ToolSource.CUSTOM,
            concurrent_safe=True,
        )
        registry.register_tool(
            serial_barrier_tool,
            source=ToolSource.CUSTOM,
            concurrent_safe=False,
        )
        executor = ToolExecutorNode(registry.freeze())
        ai_message = AIMessage(
            content="",
            tool_calls=[
                _call(
                    "concurrent_phase_tool",
                    {"phase": "before", "value": "a"},
                    "call-1",
                ),
                _call(
                    "concurrent_phase_tool",
                    {"phase": "before", "value": "b"},
                    "call-2",
                ),
                _call(
                    "serial_barrier_tool",
                    {"value": "barrier"},
                    "call-3",
                ),
                _call(
                    "concurrent_phase_tool",
                    {"phase": "after", "value": "c"},
                    "call-4",
                ),
                _call(
                    "concurrent_phase_tool",
                    {"phase": "after", "value": "d"},
                    "call-5",
                ),
            ],
        )

        update = executor(
            {"messages": [HumanMessage(content="run phases"), ai_message]},
            _config(),
        )

        self.assertEqual(set(_phase_trace[:2]), {"before:a", "before:b"})
        self.assertEqual(_phase_trace[2], "serial:barrier")
        self.assertEqual(set(_phase_trace[3:]), {"after:c", "after:d"})
        self.assertEqual(
            [message.tool_call_id for message in update["messages"]],
            ["call-1", "call-2", "call-3", "call-4", "call-5"],
        )
        self.assertTrue(
            all(message.status == "success" for message in update["messages"])
        )

    def test_interruption_backfill_only_pairs_missing_terminal_calls(self):
        ai_message = AIMessage(
            content="",
            tool_calls=[
                _call("echo_tool", {"value": "done"}, "call-1"),
                _call("echo_tool", {"value": "pending"}, "call-2"),
            ],
        )
        existing_result = ToolMessage(
            content="echo:done",
            name="echo_tool",
            tool_call_id="call-1",
        )
        messages = [
            HumanMessage(content="run both"),
            ai_message,
            existing_result,
        ]

        pending = find_pending_tool_calls(messages)
        placeholders = build_interrupted_tool_messages(messages)

        self.assertEqual([call["id"] for call in pending], ["call-2"])
        self.assertEqual(len(placeholders), 1)
        self.assertEqual(placeholders[0].tool_call_id, "call-2")
        self.assertEqual(placeholders[0].status, "error")
        self.assertEqual(
            placeholders[0].artifact["cyberclaw_tool_result"]["status"],
            "interrupted",
        )

        repaired_messages = messages + placeholders
        self.assertEqual(find_pending_tool_calls(repaired_messages), [])
        self.assertEqual(build_interrupted_tool_messages(repaired_messages), [])

    def test_interruption_backfill_ignores_completed_or_nonterminal_calls(self):
        completed_ai = AIMessage(
            content="",
            tool_calls=[_call("echo_tool", {"value": "ok"}, "call-1")],
        )
        completed = [
            HumanMessage(content="run"),
            completed_ai,
            ToolMessage(
                content="echo:ok",
                name="echo_tool",
                tool_call_id="call-1",
            ),
        ]
        old_pending_followed_by_new_input = [
            HumanMessage(content="old"),
            completed_ai,
            HumanMessage(content="new"),
        ]

        self.assertEqual(build_interrupted_tool_messages(completed), [])
        self.assertEqual(
            build_interrupted_tool_messages(old_pending_followed_by_new_input),
            [],
        )

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
    def test_agent_graph_consumes_approved_call_on_the_next_turn(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from langgraph.checkpoint.memory import MemorySaver

        from cyberclaw.core.agent import create_agent_app

        _execution_order.clear()
        store = ApprovalStore()
        call = _call(
            "ordered_tool",
            {"value": "approved-once"},
            "approval-call",
        )
        bound_model = Mock()
        bound_model.invoke.side_effect = [
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="waiting for approval"),
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="completed"),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(
            tools=[ordered_tool],
            checkpointer=MemorySaver(),
            tool_policy=ToolPolicyEngine(approval_tools={"ordered_tool"}),
            approval_store=store,
        )
        config = _config("approval-graph-thread")
        first = app.invoke(
            {
                "messages": [HumanMessage(content="run protected tool")],
                "summary": "",
            },
            config=config,
        )
        pending = next(
            message
            for message in first["messages"]
            if isinstance(message, ToolMessage)
        )
        request_id = pending.artifact["cyberclaw_tool_result"]["metadata"][
            "approval"
        ]["request_id"]
        store.approve(request_id, thread_id="approval-graph-thread")

        second = app.invoke(
            {
                "messages": [HumanMessage(content="approved; retry exactly")],
                "summary": "",
            },
            config=config,
        )

        self.assertEqual(_execution_order, ["approved-once"])
        self.assertEqual(second["messages"][-1].content, "completed")
        successful_tool_message = [
            message
            for message in second["messages"]
            if isinstance(message, ToolMessage) and message.status == "success"
        ][-1]
        approval_metadata = successful_tool_message.artifact[
            "cyberclaw_tool_result"
        ]["metadata"]["approval"]
        self.assertTrue(approval_metadata["consumed"])
        self.assertNotIn("token", approval_metadata)

    @patch("cyberclaw.core.agent.audit_logger.log_event", return_value=True)
    @patch("cyberclaw.core.agent.provider.get_provider")
    def test_agent_graph_cannot_bypass_tool_policy(
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
                    _call(
                        "ordered_tool",
                        {"value": "must-not-run"},
                        "denied-call",
                    )
                ],
            ),
            AIMessage(content="denied safely"),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(
            tools=[ordered_tool],
            tool_policy=ToolPolicyEngine(denied_tools={"ordered_tool"}),
        )
        result = app.invoke(
            {
                "messages": [HumanMessage(content="run blocked tool")],
                "summary": "",
            },
            config=_config("policy-thread"),
        )

        tool_messages = [
            message
            for message in result["messages"]
            if isinstance(message, ToolMessage)
        ]
        self.assertEqual(_execution_order, [])
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(
            tool_messages[0].artifact["cyberclaw_tool_result"]["status"],
            "permission_denied",
        )

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
    async def test_cancelled_checkpoint_can_be_backfilled_idempotently(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from langgraph.checkpoint.memory import MemorySaver

        from cyberclaw.core.agent import (
            backfill_interrupted_tool_calls,
            create_agent_app,
        )

        bound_model = Mock()
        bound_model.invoke.side_effect = [
            AIMessage(
                content="",
                tool_calls=[
                    _call("echo_tool", {"value": "pending"}, "pending-call")
                ],
            ),
            AIMessage(content="recovered next turn"),
        ]
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model

        app = create_agent_app(
            tools=[echo_tool],
            checkpointer=MemorySaver(),
        )
        config = _config("interrupted-checkpoint")
        stream = app.astream(
            {
                "messages": [HumanMessage(content="start")],
                "summary": "",
            },
            config=config,
            stream_mode="updates",
        )

        first_event = await anext(stream)
        self.assertIn("agent", first_event)
        await stream.aclose()

        repaired = await backfill_interrupted_tool_calls(app, config)
        repaired_again = await backfill_interrupted_tool_calls(app, config)
        snapshot = await app.aget_state(config)
        tool_messages = [
            message
            for message in snapshot.values["messages"]
            if isinstance(message, ToolMessage)
        ]

        self.assertEqual(repaired, 1)
        self.assertEqual(repaired_again, 0)
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0].tool_call_id, "pending-call")
        self.assertEqual(
            tool_messages[0].artifact["cyberclaw_tool_result"]["status"],
            "interrupted",
        )

        resumed = await app.ainvoke(
            {
                "messages": [HumanMessage(content="new user turn")],
                "summary": "",
            },
            config=config,
        )
        self.assertEqual(resumed["messages"][-1].content, "recovered next turn")

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

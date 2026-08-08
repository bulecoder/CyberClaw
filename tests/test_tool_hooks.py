import unittest
from unittest.mock import Mock

from langchain_core.tools import tool

from cyberclaw.core.tools import (
    AuditToolHook,
    ToolExecutorNode,
    ToolHookContext,
    ToolPolicyEngine,
    ToolRegistry,
    ToolResultHookContext,
    ToolSource,
)


_trace: list[str] = []


@tool
def traced_tool(payload: dict) -> str:
    """Record the value received by the actual tool."""
    _trace.append(f"tool:{payload['value']}")
    return payload["value"]


def _executor(*hooks, policy: ToolPolicyEngine | None = None) -> ToolExecutorNode:
    registry = ToolRegistry()
    registry.register_tool(traced_tool, source=ToolSource.CUSTOM)
    return ToolExecutorNode(registry.freeze(), hooks=hooks, policy=policy)


def _call(value: str = "original") -> dict:
    return {
        "id": "call-1",
        "name": "traced_tool",
        "args": {"payload": {"value": value}},
        "type": "tool_call",
    }


class RecordingHook:
    def __init__(self, name: str) -> None:
        self.name = name

    def before_tool(self, context: ToolHookContext) -> None:
        _trace.append(f"{self.name}:before:{context.spec.name}")

    def after_tool(self, context: ToolResultHookContext) -> None:
        _trace.append(f"{self.name}:after:{context.result.status.value}")


class FailingHook:
    def before_tool(self, context: ToolHookContext) -> None:
        raise ValueError("before failed")

    def after_tool(self, context: ToolResultHookContext) -> None:
        raise RuntimeError("after failed")


class MutatingHook:
    def before_tool(self, context: ToolHookContext) -> None:
        context.invocation.arguments["payload"]["value"] = "tampered"

    def after_tool(self, context: ToolResultHookContext) -> None:
        pass


class TestToolHookPipeline(unittest.TestCase):
    def setUp(self) -> None:
        _trace.clear()

    def test_hooks_run_in_registration_order_around_execution(self):
        result = _executor(
            RecordingHook("first"),
            RecordingHook("second"),
        ).execute_call(_call(), {"configurable": {"thread_id": "thread-a"}})

        self.assertTrue(result.succeeded)
        self.assertEqual(_trace, [
            "first:before:traced_tool",
            "second:before:traced_tool",
            "tool:original",
            "first:after:success",
            "second:after:success",
        ])

    def test_hook_failures_are_observed_without_reclassifying_tool_result(self):
        result = _executor(FailingHook()).execute_call(
            _call(),
            {"configurable": {"thread_id": "thread-a"}},
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(_trace, ["tool:original"])
        self.assertEqual(
            [failure["phase"] for failure in result.metadata["hook_failures"]],
            ["before_tool", "after_tool"],
        )

    def test_hooks_observe_policy_denial_but_cannot_override_it(self):
        result = _executor(
            RecordingHook("observer"),
            policy=ToolPolicyEngine(denied_tools={"traced_tool"}),
        ).execute_call(
            _call(),
            {"configurable": {"thread_id": "thread-a"}},
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(_trace, [
            "observer:before:traced_tool",
            "observer:after:permission_denied",
        ])

    def test_hook_mutation_cannot_change_the_approved_execution_arguments(self):
        result = _executor(MutatingHook()).execute_call(
            _call(),
            {"configurable": {"thread_id": "thread-a"}},
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(_trace, ["tool:original"])

    def test_audit_hook_records_typed_call_and_result_events(self):
        logger = Mock()
        logger.log_event.return_value = True

        result = _executor(AuditToolHook(logger)).execute_call(
            _call(),
            {"configurable": {"thread_id": "audit-thread"}},
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(logger.log_event.call_count, 2)
        call_event = logger.log_event.call_args_list[0].kwargs
        result_event = logger.log_event.call_args_list[1].kwargs
        self.assertEqual(call_event["event"], "tool_call")
        self.assertEqual(call_event["thread_id"], "audit-thread")
        self.assertEqual(result_event["event"], "tool_result")
        self.assertEqual(result_event["status"], "success")


if __name__ == "__main__":
    unittest.main()

import math
import unittest

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from cyberclaw.core.tools import (
    ToolArgumentsNormalizationError,
    ToolPolicyBehavior,
    ToolPolicyEngine,
    ToolRegistry,
    ToolRisk,
    ToolSource,
    normalize_tool_invocation,
)


@tool
def sample_tool(value: str) -> str:
    """Return a sample value."""
    return value


def _spec(*, risk: ToolRisk = ToolRisk.MEDIUM):
    registry = ToolRegistry()
    return registry.register_tool(
        sample_tool,
        source=ToolSource.CUSTOM,
        risk=risk,
    )


def _invocation(arguments: dict):
    return normalize_tool_invocation(
        call_id="call-1",
        tool_name="sample_tool",
        arguments=arguments,
    )


class TestToolInvocationNormalization(unittest.TestCase):
    def test_argument_order_does_not_change_fingerprint(self):
        first = _invocation({"b": 2, "a": {"y": 1, "x": 0}})
        second = _invocation({"a": {"x": 0, "y": 1}, "b": 2})

        self.assertEqual(first.canonical_arguments, second.canonical_arguments)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_tool_name_and_arguments_are_bound_to_fingerprint(self):
        original = _invocation({"value": "one"})
        changed_args = _invocation({"value": "two"})
        changed_tool = normalize_tool_invocation(
            call_id="call-1",
            tool_name="other_tool",
            arguments={"value": "one"},
        )

        self.assertNotEqual(original.fingerprint, changed_args.fingerprint)
        self.assertNotEqual(original.fingerprint, changed_tool.fingerprint)

    def test_non_json_values_are_rejected(self):
        with self.assertRaises(ToolArgumentsNormalizationError):
            _invocation({"value": object()})
        with self.assertRaises(ToolArgumentsNormalizationError):
            _invocation({"value": math.nan})


class TestToolPolicyEngine(unittest.TestCase):
    def test_default_policy_allows_for_backward_compatibility(self):
        decision = ToolPolicyEngine().decide(
            _spec(),
            _invocation({"value": "ok"}),
            RunnableConfig(),
        )

        self.assertEqual(decision.behavior, ToolPolicyBehavior.ALLOW)
        self.assertEqual(decision.rule_id, "default.allow")

    def test_hard_deny_has_priority_over_approval(self):
        policy = ToolPolicyEngine(
            denied_tools={"SAMPLE_TOOL"},
            approval_tools={"sample_tool"},
            approval_risks={ToolRisk.HIGH},
        )
        decision = policy.decide(
            _spec(risk=ToolRisk.HIGH),
            _invocation({"value": "blocked"}),
            RunnableConfig(),
        )

        self.assertEqual(decision.behavior, ToolPolicyBehavior.DENY)
        self.assertEqual(decision.rule_id, "hard_deny.tool_name")

    def test_risk_rule_requests_approval(self):
        decision = ToolPolicyEngine(
            approval_risks={ToolRisk.MEDIUM},
        ).decide(
            _spec(risk=ToolRisk.MEDIUM),
            _invocation({"value": "confirm"}),
            RunnableConfig(),
        )

        self.assertEqual(decision.behavior, ToolPolicyBehavior.ASK)
        self.assertEqual(decision.rule_id, "approval.risk.medium")


if __name__ == "__main__":
    unittest.main()

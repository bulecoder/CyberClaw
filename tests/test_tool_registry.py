import unittest
from unittest.mock import patch

from langchain_core.tools import tool

from cyberclaw.core.tools import (
    ToolRegistrationError,
    ToolRegistry,
    ToolRisk,
    ToolSource,
    ToolSpec,
)


@tool
def first_tool(value: str) -> str:
    """Return the input value."""
    return value


@tool("FIRST_TOOL")
def duplicate_tool(value: str) -> str:
    """Use a case-insensitive duplicate name."""
    return value


@tool
def second_tool(value: str) -> str:
    """Return another input value."""
    return value


class TestToolSpec(unittest.TestCase):
    def test_spec_exposes_immutable_capability_metadata(self):
        spec = ToolSpec.from_tool(
            first_tool,
            source=ToolSource.BUILTIN,
            risk=ToolRisk.LOW,
            read_only=True,
            concurrent_safe=True,
            timeout_seconds=2.5,
            metadata={"owner": "core"},
        )

        self.assertEqual(spec.name, "first_tool")
        self.assertEqual(spec.source, ToolSource.BUILTIN)
        self.assertEqual(spec.risk, ToolRisk.LOW)
        self.assertTrue(spec.read_only)
        self.assertTrue(spec.concurrent_safe)
        self.assertEqual(spec.timeout_seconds, 2.5)
        with self.assertRaises(TypeError):
            spec.metadata["owner"] = "changed"

    def test_spec_rejects_non_positive_timeout(self):
        with self.assertRaises(ValueError):
            ToolSpec.from_tool(
                first_tool,
                source=ToolSource.CUSTOM,
                timeout_seconds=0,
            )


class TestToolRegistry(unittest.TestCase):
    def test_registry_rejects_case_insensitive_name_collision(self):
        registry = ToolRegistry()
        registry.register_tool(first_tool, source=ToolSource.BUILTIN)

        with self.assertRaises(ToolRegistrationError):
            registry.register_tool(duplicate_tool, source=ToolSource.SKILL)

    def test_frozen_snapshot_is_independent_from_builder(self):
        registry = ToolRegistry()
        registry.register_tool(first_tool, source=ToolSource.BUILTIN)
        snapshot = registry.snapshot()

        registry.register_tool(second_tool, source=ToolSource.CUSTOM)

        self.assertEqual(snapshot.names, frozenset({"first_tool"}))
        self.assertEqual(
            registry.names,
            frozenset({"first_tool", "second_tool"}),
        )
        with self.assertRaises(ToolRegistrationError):
            snapshot.register_tool(second_tool, source=ToolSource.CUSTOM)

    def test_lookup_is_case_insensitive(self):
        registry = ToolRegistry()
        registered = registry.register_tool(
            first_tool,
            source=ToolSource.BUILTIN,
        )

        self.assertIs(registry.get("FIRST_TOOL"), registered)
        self.assertIsNone(registry.get("missing"))


class TestAgentToolRegistryAssembly(unittest.TestCase):
    @patch("cyberclaw.core.agent.skill_loader.load_dynamic_skills")
    @patch("cyberclaw.core.agent.builtin_tools.BUILTIN_TOOL_PROFILES", {
        "first_tool": {
            "risk": "low",
            "read_only": True,
            "concurrent_safe": True,
        }
    })
    @patch("cyberclaw.core.agent.builtin_tools.BUILTIN_TOOLS", [first_tool])
    def test_default_registry_combines_builtin_and_skill_snapshots(
        self,
        mock_load_skills,
    ):
        from cyberclaw.core.agent import build_tool_registry

        mock_load_skills.return_value = [second_tool]

        registry = build_tool_registry()

        self.assertTrue(registry.frozen)
        self.assertEqual(
            registry.names,
            frozenset({"first_tool", "second_tool"}),
        )
        self.assertEqual(registry.get("first_tool").source, ToolSource.BUILTIN)
        self.assertEqual(registry.get("first_tool").risk, ToolRisk.LOW)
        self.assertTrue(registry.get("first_tool").read_only)
        self.assertEqual(registry.get("second_tool").source, ToolSource.SKILL)
        self.assertEqual(registry.get("second_tool").risk, ToolRisk.HIGH)
        mock_load_skills.assert_called_once_with(
            reserved_names={"first_tool"}
        )

    @patch("cyberclaw.core.agent.skill_loader.load_dynamic_skills")
    def test_custom_tools_replace_default_sources(self, mock_load_skills):
        from cyberclaw.core.agent import build_tool_registry

        registry = build_tool_registry(tools=[first_tool])

        self.assertEqual(registry.names, frozenset({"first_tool"}))
        self.assertEqual(registry.get("first_tool").source, ToolSource.CUSTOM)
        mock_load_skills.assert_not_called()

    def test_explicit_registry_is_copied_before_agent_use(self):
        from cyberclaw.core.agent import build_tool_registry

        builder = ToolRegistry()
        builder.register_tool(first_tool, source=ToolSource.CUSTOM)

        snapshot = build_tool_registry(tool_registry=builder)
        builder.register_tool(second_tool, source=ToolSource.CUSTOM)

        self.assertTrue(snapshot.frozen)
        self.assertEqual(snapshot.names, frozenset({"first_tool"}))

    def test_tools_and_registry_are_mutually_exclusive(self):
        from cyberclaw.core.agent import build_tool_registry

        registry = ToolRegistry().freeze()
        with self.assertRaises(ValueError):
            build_tool_registry(tools=[first_tool], tool_registry=registry)


if __name__ == "__main__":
    unittest.main()

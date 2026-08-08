import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cyberclaw.core.mcp import (
    MCPClientManager,
    MCPConfigurationError,
    MCPServerConfig,
    _tool_name,
    load_mcp_server_configs,
)
from cyberclaw.core.tools import (
    ApprovalStore,
    ToolExecutorNode,
    ToolPolicyEngine,
    ToolResultStatus,
    ToolRisk,
    ToolSource,
    ToolRegistry,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SERVER = PROJECT_ROOT / "examples" / "mcp_demo_server.py"
RUN_CONFIG = {"configurable": {"thread_id": "mcp-test"}}


class TestMCPConfiguration(unittest.TestCase):
    def test_missing_config_disables_mcp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.json"

            self.assertEqual(load_mcp_server_configs(missing), ())

    def test_config_requires_explicit_trust(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mcp.json"
            path.write_text(
                json.dumps({
                    "servers": {
                        "demo": {"command": "python", "args": []}
                    }
                }),
                encoding="utf-8",
            )

            with self.assertRaises(MCPConfigurationError):
                load_mcp_server_configs(path)

    def test_environment_references_are_resolved_without_changing_os_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mcp.json"
            path.write_text(
                json.dumps({
                    "servers": {
                        "demo": {
                            "trusted": True,
                            "command": "python",
                            "env": {"TOKEN": "${MCP_TEST_TOKEN}"},
                        }
                    }
                }),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"MCP_TEST_TOKEN": "secret"}):
                configs = load_mcp_server_configs(path)

            self.assertEqual(dict(configs[0].env), {"TOKEN": "secret"})
            self.assertNotIn("TOKEN", os.environ)

    def test_long_tool_names_are_stable_and_bounded(self):
        first = _tool_name("demo", "x" * 100)
        second = _tool_name("demo", "x" * 100)

        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 64)


class TestMCPStdioIntegration(unittest.TestCase):
    def test_real_server_discovery_policy_execution_and_shutdown(self):
        configs = (
            MCPServerConfig(
                name="broken",
                command="cyberclaw-command-that-does-not-exist",
                timeout_seconds=5,
            ),
            MCPServerConfig(
                name="demo",
                command=sys.executable,
                args=(str(DEMO_SERVER),),
                cwd=PROJECT_ROOT,
                timeout_seconds=10,
            ),
        )
        manager = MCPClientManager(configs)

        try:
            specs = manager.start()
            registry = ToolRegistry(specs, frozen=True)
            store = ApprovalStore()
            executor = ToolExecutorNode(
                registry,
                policy=ToolPolicyEngine(
                    approval_risks={ToolRisk.MEDIUM, ToolRisk.HIGH}
                ),
                approval_store=store,
            )

            self.assertIn("broken", manager.errors)
            self.assertEqual(
                registry.names,
                frozenset({"mcp__demo__echo", "mcp__demo__remember_demo"}),
            )
            echo_spec = registry.get("mcp__demo__echo")
            remember_spec = registry.get("mcp__demo__remember_demo")
            self.assertEqual(echo_spec.source, ToolSource.MCP)
            self.assertEqual(echo_spec.risk, ToolRisk.LOW)
            self.assertTrue(echo_spec.read_only)
            self.assertEqual(remember_spec.risk, ToolRisk.HIGH)

            echo = executor.execute_call(
                {
                    "id": "echo-1",
                    "name": "mcp__demo__echo",
                    "args": {"message": "hello from MCP"},
                },
                RUN_CONFIG,
            )
            self.assertEqual(echo.status, ToolResultStatus.SUCCESS)
            self.assertEqual(echo.content, "hello from MCP")
            self.assertEqual(echo.metadata["source"], "mcp")

            change_call = {
                "id": "change-1",
                "name": "mcp__demo__remember_demo",
                "args": {"note": "approved once"},
            }
            pending = executor.execute_call(change_call, RUN_CONFIG)
            self.assertEqual(pending.error_type, "ToolApprovalRequired")
            request_id = pending.metadata["approval"]["request_id"]
            store.approve(request_id, thread_id="mcp-test")

            approved = executor.execute_call(change_call, RUN_CONFIG)
            self.assertEqual(approved.status, ToolResultStatus.SUCCESS)
            self.assertEqual(approved.content, "remembered: approved once")
        finally:
            self.assertTrue(manager.close())

        self.assertFalse(manager.running)
        self.assertTrue(manager.close())


if __name__ == "__main__":
    unittest.main()

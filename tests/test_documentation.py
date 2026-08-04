import unittest
from pathlib import Path

from cyberclaw.core.tools.builtins import save_user_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
README = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")


class TestDocumentationContract(unittest.TestCase):
    def test_readme_code_fences_are_balanced(self):
        self.assertEqual(README.count("```") % 2, 0)

    def test_readme_does_not_claim_unimplemented_capabilities(self):
        forbidden_claims = (
            "企业级透明可控智能体",
            "enterprise-grade transparent and controllable agent",
            "MCP 服务集成",
            "MCP service integration",
            "双水位记忆",
            "dual-watermark memory",
            "存储完整对话历史",
            "stores complete conversation history",
        )
        for claim in forbidden_claims:
            with self.subTest(claim=claim):
                self.assertNotIn(claim, README)

    def test_documented_core_files_exist(self):
        documented_files = (
            "cyberclaw/core/agent.py",
            "cyberclaw/core/config.py",
            "cyberclaw/core/environment.py",
            "cyberclaw/core/logger.py",
            "cyberclaw/core/provider.py",
            "cyberclaw/core/runtime.py",
            "cyberclaw/core/skill_loader.py",
        )
        for relative_path in documented_files:
            with self.subTest(path=relative_path):
                self.assertIn(f"`{relative_path}`", README)
                self.assertTrue((PROJECT_ROOT / relative_path).is_file())

    def test_packaging_declares_an_isolated_build_backend(self):
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[build-system]", pyproject)
        self.assertIn('build-backend = "setuptools.build_meta"', pyproject)

    def test_profile_tool_does_not_reference_missing_read_tool(self):
        self.assertNotIn("read_user_profile", save_user_profile.description)


if __name__ == "__main__":
    unittest.main()

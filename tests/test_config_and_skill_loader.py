import unittest
import os
import sys
import tempfile
import importlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestConfig(unittest.TestCase):

    def test_config_import(self):
        """测试配置模块导入"""
        from cyberclaw.core.config import WORKSPACE_DIR, MEMORY_DIR, PERSONAS_DIR, SCRIPTS_DIR, OFFICE_DIR, SKILLS_DIR, DB_PATH, TASKS_FILE

        # 验证配置项存在
        self.assertIsInstance(WORKSPACE_DIR, str)
        self.assertIsInstance(MEMORY_DIR, str)
        self.assertIsInstance(PERSONAS_DIR, str)
        self.assertIsInstance(SCRIPTS_DIR, str)
        self.assertIsInstance(OFFICE_DIR, str)
        self.assertIsInstance(SKILLS_DIR, str)
        self.assertIsInstance(DB_PATH, str)
        self.assertIsInstance(TASKS_FILE, str)

    def test_config_import_has_no_directory_creation_side_effect(self):
        import cyberclaw.core.config as config

        with patch("pathlib.Path.mkdir") as mkdir:
            importlib.reload(config)

        mkdir.assert_not_called()

    def test_workspace_creation_is_explicit(self):
        from cyberclaw.core.config import ensure_workspace

        with tempfile.TemporaryDirectory() as temp_dir:
            directories = [
                Path(temp_dir) / "workspace",
                Path(temp_dir) / "workspace" / "office" / "skills",
            ]
            ensure_workspace(directories)

            self.assertTrue(all(path.is_dir() for path in directories))

    def test_load_project_env_accepts_utf8_bom(self):
        from cyberclaw.core.environment import load_project_env

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("CYBERCLAW_TEST_VALUE=测试\n", encoding="utf-8-sig")
            with patch.dict(os.environ, {}, clear=True):
                self.assertTrue(load_project_env(env_path, override=True))
                self.assertEqual(os.environ["CYBERCLAW_TEST_VALUE"], "测试")

    def test_load_project_env_reports_invalid_encoding(self):
        from cyberclaw.core.environment import ConfigurationError, load_project_env

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_bytes(b"OPENAI_API_KEY=\xb9\n")

            with self.assertRaisesRegex(ConfigurationError, "UTF-8"):
                load_project_env(env_path)


class TestSkillLoader(unittest.TestCase):

    def test_skill_loader_import(self):
        """测试技能加载器模块导入"""
        try:
            from cyberclaw.core.skill_loader import load_dynamic_skills
            # 确保函数存在
            self.assertTrue(callable(load_dynamic_skills))
        except ImportError as e:
            # 如果导入失败，可能是因为依赖问题，但仍需确认模块结构
            self.fail(f"无法导入技能加载器: {e}")

    def test_load_dynamic_skills_no_directory(self):
        """测试技能加载器 - 不存在的目录"""
        from cyberclaw.core.skill_loader import LazySkillLoader

        with tempfile.TemporaryDirectory() as temp_dir:
            office_dir = Path(temp_dir) / "office"
            office_dir.mkdir()
            loader = LazySkillLoader(
                skills_dir=office_dir / "missing",
                office_dir=office_dir,
            )
            self.assertEqual(loader.get_all_tools(), [])

    def test_load_dynamic_skills_empty_directory(self):
        """测试技能加载器 - 空目录"""
        from cyberclaw.core.skill_loader import LazySkillLoader

        with tempfile.TemporaryDirectory() as temp_dir:
            office_dir = Path(temp_dir) / "office"
            skills_dir = office_dir / "skills"
            skills_dir.mkdir(parents=True)
            loader = LazySkillLoader(
                skills_dir=skills_dir,
                office_dir=office_dir,
            )
            self.assertEqual(loader.get_all_tools(), [])


if __name__ == '__main__':
    unittest.main()

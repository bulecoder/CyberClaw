import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cyberclaw.core.skill_loader import LazySkillLoader


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


class TestLazySkillLoader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.office_dir = Path(self.temp_dir.name) / "office"
        self.skills_dir = self.office_dir / "skills"
        self.skills_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_skill(
        self,
        folder: str,
        manifest: str,
        entrypoint: tuple[str, str] | None = None,
    ) -> Path:
        skill_dir = self.skills_dir / folder
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(manifest, encoding="utf-8")
        if entrypoint:
            relative_path, content = entrypoint
            entrypoint_path = skill_dir / relative_path
            entrypoint_path.parent.mkdir(parents=True, exist_ok=True)
            entrypoint_path.write_text(content, encoding="utf-8")
        return skill_dir

    def _loader(self, cache_size: int = 50) -> LazySkillLoader:
        return LazySkillLoader(
            cache_size=cache_size,
            skills_dir=self.skills_dir,
            office_dir=self.office_dir,
        )

    def test_instruction_skill_is_lazy_and_cannot_run(self):
        self._write_skill(
            "guide",
            "name: Guide\ndescription: 学习说明\n\n这是说明正文。",
        )
        loader = self._loader()

        tools = loader.get_all_tools()

        self.assertEqual([tool.name for tool in tools], ["Guide"])
        self.assertEqual(loader.content_cache_entries, 0)
        help_result = tools[0].invoke(
            {"mode": "help", "page": 1}, config=_config("thread-a")
        )
        run_result = tools[0].invoke(
            {"mode": "run", "arguments": []}, config=_config("thread-a")
        )
        self.assertIn("这是说明正文", help_result)
        self.assertIn("instruction 类型", run_result)
        self.assertEqual(loader.content_cache_entries, 1)

    @patch(
        "cyberclaw.core.skill_loader.execute_office_program",
        return_value="executed",
    )
    def test_executable_skill_fixes_runtime_entrypoint_and_arguments(
        self, mock_execute
    ):
        self._write_skill(
            "safe_runner",
            (
                "name: Safe Runner\n"
                "description: 执行本地测试脚本\n"
                "type: executable\n"
                "runtime: python\n"
                "entrypoint: run.py\n\n"
                "只允许使用固定入口。"
            ),
            entrypoint=("run.py", "print('ok')\n"),
        )
        tool = self._loader().get_all_tools()[0]

        denied_before_help = tool.invoke(
            {"mode": "run", "arguments": ["--name", "test"]},
            config=_config("thread-a"),
        )
        tool.invoke({"mode": "help", "page": 1}, config=_config("thread-a"))
        denied_other_thread = tool.invoke(
            {"mode": "run", "arguments": []},
            config=_config("thread-b"),
        )
        result = tool.invoke(
            {"mode": "run", "arguments": ["--name", "test"]},
            config=_config("thread-a"),
        )

        self.assertIn("必须先", denied_before_help)
        self.assertIn("必须先", denied_other_thread)
        self.assertEqual(result, "executed")
        mock_execute.assert_called_once_with(
            "python",
            ["skills/safe_runner/run.py", "--name", "test"],
        )

    @patch(
        "cyberclaw.core.skill_loader.execute_office_program",
        return_value="executed",
    )
    def test_all_help_pages_are_required_before_run(self, mock_execute):
        self._write_skill(
            "long_manual",
            (
                "name: Long Manual\n"
                "description: 长说明书\n"
                "type: executable\n"
                "runtime: python\n"
                "entrypoint: run.py\n\n"
                + "x" * 3_500
            ),
            entrypoint=("run.py", "print('ok')\n"),
        )
        tool = self._loader().get_all_tools()[0]
        config = _config("thread-a")

        first_page = tool.invoke({"mode": "help", "page": 1}, config=config)
        denied = tool.invoke({"mode": "run", "arguments": []}, config=config)
        second_page = tool.invoke({"mode": "help", "page": 2}, config=config)
        result = tool.invoke({"mode": "run", "arguments": []}, config=config)

        self.assertIn("1/2", first_page)
        self.assertIn("尚未读完", denied)
        self.assertIn("2/2", second_page)
        self.assertEqual(result, "executed")
        mock_execute.assert_called_once()

    @patch("cyberclaw.core.skill_loader.execute_office_program")
    def test_changed_entrypoint_invalidates_help_approval(self, mock_execute):
        skill_dir = self._write_skill(
            "mutable",
            (
                "name: Mutable\n"
                "description: 可变入口测试\n"
                "type: executable\n"
                "runtime: python\n"
                "entrypoint: run.py\n"
            ),
            entrypoint=("run.py", "print('old')\n"),
        )
        tool = self._loader().get_all_tools()[0]
        config = _config("thread-a")
        tool.invoke({"mode": "help", "page": 1}, config=config)

        (skill_dir / "run.py").write_text(
            "print('new and different')\n", encoding="utf-8"
        )
        result = tool.invoke({"mode": "run", "arguments": []}, config=config)

        self.assertIn("入口文件已变化", result)
        mock_execute.assert_not_called()

    def test_configured_lru_cache_size_is_honored(self):
        self._write_skill("one", "name: One\ndescription: one\n")
        self._write_skill("two", "name: Two\ndescription: two\n")
        loader = self._loader(cache_size=1)
        tools = loader.get_all_tools()

        for tool in tools:
            tool.invoke({"mode": "help", "page": 1}, config=_config("thread-a"))

        self.assertEqual(loader.content_cache_entries, 1)

    def test_reload_clears_cache_and_old_snapshot_detects_change(self):
        skill_dir = self._write_skill(
            "reloadable",
            "name: Reloadable\ndescription: old description\nold body",
        )
        loader = self._loader()
        old_tool = loader.get_all_tools()[0]
        old_tool.invoke({"mode": "help", "page": 1}, config=_config("thread-a"))

        (skill_dir / "SKILL.md").write_text(
            "name: Reloadable\ndescription: new description\nnew body is longer",
            encoding="utf-8",
        )
        new_tool = loader.reload_tools()[0]
        stale_result = old_tool.invoke(
            {"mode": "help", "page": 1}, config=_config("thread-a")
        )

        self.assertIn("new description", new_tool.description)
        self.assertIn("已变化", stale_result)
        self.assertEqual(loader.content_cache_entries, 0)

    def test_duplicate_and_reserved_tool_names_are_rejected(self):
        self._write_skill("first", "name: Duplicate Skill\ndescription: first")
        self._write_skill("second", "name: Duplicate@Skill\ndescription: second")
        self._write_skill("reserved", "name: Calculator\ndescription: conflict")
        self._write_skill("safe", "name: Safe Guide\ndescription: safe")

        tools = self._loader().get_all_tools(reserved_names={"calculator"})

        self.assertEqual([tool.name for tool in tools], ["Safe_Guide"])

    def test_executable_skill_cannot_escape_with_entrypoint(self):
        self._write_skill(
            "escape",
            (
                "name: Escape\n"
                "description: invalid\n"
                "type: executable\n"
                "runtime: python\n"
                "entrypoint: ../outside.py\n"
            ),
        )
        (self.skills_dir / "outside.py").write_text("print('bad')", encoding="utf-8")

        tools = self._loader().get_all_tools()

        self.assertEqual(tools, [])

    def test_missing_skills_directory_returns_empty_list(self):
        missing_dir = self.office_dir / "missing"
        loader = LazySkillLoader(
            skills_dir=missing_dir,
            office_dir=self.office_dir,
        )

        self.assertEqual(loader.get_all_tools(), [])


if __name__ == "__main__":
    unittest.main()

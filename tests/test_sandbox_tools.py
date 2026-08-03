import unittest
from unittest.mock import patch, mock_open
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cyberclaw.core.tools.sandbox_tools import (
    _MAX_OFFICE_READ_CHARS,
    list_office_files,
    read_office_file,
    write_office_file,
    execute_office_shell,
    _get_safe_path
)
from cyberclaw.core.config import OFFICE_DIR


class TestSandboxTools(unittest.TestCase):

    def test_get_safe_path_normal(self):
        """测试正常路径连接"""
        # _get_safe_path 是内部函数，不受装饰器影响，可以直接调用
        # 注意：OFFICE_DIR 是模块级常量，patch 需要在导入前或使用正确的路径
        original_office_dir = OFFICE_DIR
        try:
            # 使用实际 OFFICE_DIR 测试
            result = _get_safe_path('subdir/file.txt')
            expected = os.path.abspath(os.path.join(OFFICE_DIR, 'subdir/file.txt'))
            self.assertEqual(result, expected)
        finally:
            pass

    def test_get_safe_path_traversal_attempt(self):
        """测试路径遍历攻击"""
        with self.assertRaises(PermissionError):
            _get_safe_path('../../forbidden/file.txt')

    def test_get_safe_path_rejects_sibling_with_same_prefix(self):
        """兄弟目录即使共享 office 前缀也不能通过包含检查。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            office_dir = os.path.join(temp_dir, "office")
            sibling_dir = os.path.join(temp_dir, "office_backup")
            os.makedirs(office_dir)
            os.makedirs(sibling_dir)

            with patch(
                'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
            ):
                with self.assertRaises(PermissionError):
                    _get_safe_path('../office_backup/secret.txt')

    def test_get_safe_path_rejects_absolute_path(self):
        """模型不能使用绝对路径绕过 office 根目录。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            office_dir = os.path.join(temp_dir, "office")
            os.makedirs(office_dir)

            with patch(
                'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
            ):
                with self.assertRaises(PermissionError):
                    _get_safe_path(os.path.abspath(temp_dir))

    def test_get_safe_path_rejects_symlink_escape(self):
        """office 内指向外部目录的链接不能形成路径逃逸。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            office_dir = os.path.join(temp_dir, "office")
            outside_dir = os.path.join(temp_dir, "outside")
            link_path = os.path.join(office_dir, "outside_link")
            os.makedirs(office_dir)
            os.makedirs(outside_dir)

            try:
                os.symlink(outside_dir, link_path, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前系统不允许创建测试符号链接: {exc}")

            with patch(
                'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
            ):
                with self.assertRaises(PermissionError):
                    _get_safe_path('outside_link/secret.txt')

    @patch('cyberclaw.core.tools.sandbox_tools.os.path.exists', return_value=True)
    @patch('cyberclaw.core.tools.sandbox_tools.os.listdir', return_value=['file1.txt', 'subdir'])
    @patch(
        'cyberclaw.core.tools.sandbox_tools.os.path.isdir',
        side_effect=lambda x: not x.endswith('file1.txt'),
    )
    def test_list_office_files(self, mock_isdir, mock_listdir, mock_exists):
        """测试列出办公文件功能"""
        # 工具需要通过 .invoke() 调用
        result = list_office_files.invoke({"sub_dir": ""})

        # 验证函数调用了正确的路径检查
        mock_exists.assert_called_once()
        mock_listdir.assert_called_once()

        # 检查返回结果包含预期元素
        self.assertIn("📄 file1.txt", result)
        self.assertIn("📁 subdir", result)

    @patch('cyberclaw.core.tools.sandbox_tools.os.path.exists', return_value=False)
    def test_list_office_files_nonexistent_dir(self, mock_exists):
        """测试列出不存在目录的文件"""
        result = list_office_files.invoke({"sub_dir": "nonexistent"})
        self.assertIn("目录不存在", result)

    @patch('cyberclaw.core.tools.sandbox_tools.os.path.isfile', return_value=True)
    @patch('cyberclaw.core.tools.sandbox_tools.os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="file content")
    def test_read_office_file_success(self, mock_file, mock_exists, mock_isfile):
        """测试成功读取办公文件"""
        result = read_office_file.invoke({"filepath": "test.txt"})
        self.assertEqual(result, "file content")
        mock_file.assert_called_once()
        mock_file().read.assert_called_once_with(_MAX_OFFICE_READ_CHARS + 1)

    @patch('cyberclaw.core.tools.sandbox_tools.os.path.isfile', return_value=True)
    @patch('cyberclaw.core.tools.sandbox_tools.os.path.exists', return_value=True)
    @patch(
        'builtins.open',
        new_callable=mock_open,
        read_data="x" * (_MAX_OFFICE_READ_CHARS + 1),
    )
    def test_read_office_file_reads_only_bounded_preview(
        self, mock_file, mock_exists, mock_isfile
    ):
        """读取大文件时只请求返回上限附近的内容。"""
        result = read_office_file.invoke({"filepath": "large.log"})

        self.assertIn("内容过长，已被安全截断", result)
        self.assertEqual(result.count("x"), _MAX_OFFICE_READ_CHARS)
        mock_file().read.assert_called_once_with(_MAX_OFFICE_READ_CHARS + 1)

    @patch('cyberclaw.core.tools.sandbox_tools.os.path.exists', return_value=False)
    def test_read_office_file_nonexistent(self, mock_exists):
        """测试读取不存在的办公文件"""
        result = read_office_file.invoke({"filepath": "nonexistent.txt"})
        self.assertIn("文件不存在", result)

    def test_write_office_file_success(self):
        """覆盖模式通过真实临时目录写入完整内容。"""
        with tempfile.TemporaryDirectory() as office_dir:
            with patch(
                'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
            ):
                result = write_office_file.invoke({
                    "filepath": "docs/test.txt",
                    "content": "test content",
                    "mode": "w",
                })

            target_path = os.path.join(office_dir, "docs", "test.txt")
            self.assertIn("成功以 覆盖/新建 模式写入文件", result)
            with open(target_path, "r", encoding="utf-8") as target_file:
                self.assertEqual(target_file.read(), "test content")

    def test_write_office_file_preserves_old_file_when_replace_fails(self):
        """原子替换失败时保留旧内容并清理临时文件。"""
        with tempfile.TemporaryDirectory() as office_dir:
            target_path = os.path.join(office_dir, "test.txt")
            with open(target_path, "w", encoding="utf-8") as target_file:
                target_file.write("old content")

            with patch(
                'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
            ), patch(
                'cyberclaw.core.tools.sandbox_tools.os.replace',
                side_effect=OSError("replace failed"),
            ):
                result = write_office_file.invoke({
                    "filepath": "test.txt",
                    "content": "new content",
                    "mode": "w",
                })

            self.assertIn("replace failed", result)
            with open(target_path, "r", encoding="utf-8") as target_file:
                self.assertEqual(target_file.read(), "old content")
            self.assertEqual(os.listdir(office_dir), ["test.txt"])

    def test_write_office_file_append_newline_semantics(self):
        """追加模式不产生开头空行或重复空行。"""
        with tempfile.TemporaryDirectory() as office_dir:
            target_path = os.path.join(office_dir, "test.txt")
            tool_path = 'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR'

            with patch(tool_path, office_dir):
                write_office_file.invoke({
                    "filepath": "test.txt", "content": "first", "mode": "a"
                })
                write_office_file.invoke({
                    "filepath": "test.txt", "content": "second", "mode": "a"
                })
                write_office_file.invoke({
                    "filepath": "test.txt", "content": "\nthird", "mode": "a"
                })

            with open(target_path, "r", encoding="utf-8", newline="") as target_file:
                self.assertEqual(target_file.read(), "first\nsecond\nthird")

            with open(target_path, "w", encoding="utf-8", newline="") as target_file:
                target_file.write("line\n")
            with patch(tool_path, office_dir):
                write_office_file.invoke({
                    "filepath": "test.txt", "content": "next", "mode": "a"
                })
            with open(target_path, "r", encoding="utf-8", newline="") as target_file:
                self.assertEqual(target_file.read(), "line\nnext")

    def test_write_office_file_empty_append_creates_empty_file(self):
        """向不存在的文件追加空内容时，成功结果与文件状态保持一致。"""
        with tempfile.TemporaryDirectory() as office_dir:
            with patch(
                'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
            ):
                result = write_office_file.invoke({
                    "filepath": "empty.txt", "content": "", "mode": "a"
                })

            target_path = os.path.join(office_dir, "empty.txt")
            self.assertIn("成功以 追加 模式写入文件", result)
            self.assertTrue(os.path.isfile(target_path))
            self.assertEqual(os.path.getsize(target_path), 0)

    def test_write_office_file_invalid_mode(self):
        """测试写入办公文件 - 无效模式"""
        result = write_office_file.invoke({"filepath": "test.txt", "content": "test content", "mode": "x"})
        self.assertIn("❌ 错误：mode 参数必须是", result)

    @patch('cyberclaw.core.tools.sandbox_tools.shutil.which', return_value='mock-ls')
    @patch('cyberclaw.core.tools.sandbox_tools.subprocess.run')
    def test_execute_office_shell_uses_argv_and_sanitized_environment(
        self, mock_subprocess, mock_which
    ):
        """显式批准的程序使用 argv 执行，且子进程拿不到 API Key。"""
        def fake_run(*args, **kwargs):
            kwargs["stdout"].write(b"command output")
            return SimpleNamespace(returncode=0)

        mock_subprocess.side_effect = fake_run
        with tempfile.TemporaryDirectory() as office_dir, patch(
            'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
        ), patch.dict(os.environ, {
            "CYBERCLAW_ENABLE_SHELL": "true",
            "CYBERCLAW_SHELL_ALLOWED_COMMANDS": "ls",
            "OPENAI_API_KEY": "should-not-leak",
            "ANTHROPIC_API_KEY": "should-not-leak",
        }, clear=False):
            result = execute_office_shell.invoke({"command": "ls -l"})

        self.assertIn("ls", result)
        self.assertIn("command output", result)
        call_args, call_kwargs = mock_subprocess.call_args
        self.assertEqual(call_args[0], ['mock-ls', '-l'])
        self.assertFalse(call_kwargs["shell"])
        self.assertEqual(call_kwargs["cwd"], os.path.abspath(office_dir))
        self.assertNotIn("OPENAI_API_KEY", call_kwargs["env"])
        self.assertNotIn("ANTHROPIC_API_KEY", call_kwargs["env"])
        self.assertEqual(call_kwargs["env"]["HOME"], os.path.abspath(office_dir))

    @patch('cyberclaw.core.tools.sandbox_tools.subprocess.run')
    def test_execute_office_shell_is_disabled_by_default(self, mock_subprocess):
        """没有用户显式授权时，通用程序执行保持关闭。"""
        with patch.dict(os.environ, {"CYBERCLAW_ENABLE_SHELL": ""}, clear=False):
            result = execute_office_shell.invoke({"command": "ls"})

        self.assertIn("Shell 执行默认关闭", result)
        mock_subprocess.assert_not_called()

    @patch('cyberclaw.core.tools.sandbox_tools.subprocess.run')
    def test_execute_office_shell_rejects_program_outside_allowlist(
        self, mock_subprocess
    ):
        """启用执行器不等于允许模型选择任意程序。"""
        with patch.dict(os.environ, {
            "CYBERCLAW_ENABLE_SHELL": "true",
            "CYBERCLAW_SHELL_ALLOWED_COMMANDS": "python",
        }, clear=False):
            result = execute_office_shell.invoke({"command": "git status"})

        self.assertIn("不在 CYBERCLAW_SHELL_ALLOWED_COMMANDS 白名单", result)
        mock_subprocess.assert_not_called()

    def test_execute_office_shell_dangerous_commands(self):
        """命令连接、路径逃逸和绝对路径参数都会在执行前被拦截。"""
        dangerous_commands = [
            "ls && whoami",
            "ls | more",
            "ls ../",
            "ls /etc/passwd",
            "ls C:\\windows\\system32",
            "ls ~",
        ]

        with patch.dict(os.environ, {
            "CYBERCLAW_ENABLE_SHELL": "true",
            "CYBERCLAW_SHELL_ALLOWED_COMMANDS": "ls",
        }, clear=False):
            for cmd in dangerous_commands:
                with self.subTest(cmd=cmd):
                    result = execute_office_shell.invoke({"command": cmd})
                    self.assertIn("❌ 权限拒绝", result)

    @patch('cyberclaw.core.tools.sandbox_tools.subprocess.run')
    def test_execute_office_shell_rejects_nested_shell_and_inline_code(
        self, mock_subprocess
    ):
        """白名单也不能重新引入 Shell 或解释器内联代码通道。"""
        commands = (
            ("cmd /c dir", "cmd,python"),
            ("powershell -Command Get-ChildItem", "powershell,python"),
            ("python -c print(1)", "python"),
            ("python -m http.server", "python"),
        )

        for command, allowlist in commands:
            with self.subTest(command=command), patch.dict(os.environ, {
                "CYBERCLAW_ENABLE_SHELL": "true",
                "CYBERCLAW_SHELL_ALLOWED_COMMANDS": allowlist,
            }, clear=False):
                result = execute_office_shell.invoke({"command": command})
                self.assertIn("❌ 权限拒绝", result)

        mock_subprocess.assert_not_called()

    @patch('cyberclaw.core.tools.sandbox_tools.shutil.which', return_value='mock-ls')
    @patch('cyberclaw.core.tools.sandbox_tools.subprocess.run')
    def test_execute_office_shell_bounds_returned_output(
        self, mock_subprocess, mock_which
    ):
        """大量子进程输出只返回固定大小的尾部，避免撑爆模型上下文。"""
        def fake_run(*args, **kwargs):
            kwargs["stdout"].write(b"x" * 20_000)
            return SimpleNamespace(returncode=0)

        mock_subprocess.side_effect = fake_run
        with tempfile.TemporaryDirectory() as office_dir, patch(
            'cyberclaw.core.tools.sandbox_tools.OFFICE_DIR', office_dir
        ), patch.dict(os.environ, {
            "CYBERCLAW_ENABLE_SHELL": "true",
            "CYBERCLAW_SHELL_ALLOWED_COMMANDS": "ls",
        }, clear=False):
            result = execute_office_shell.invoke({"command": "ls"})

        self.assertIn("较早输出已截断", result)
        self.assertLess(len(result), 17_000)


if __name__ == '__main__':
    unittest.main()

import unittest
import os
import sys
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cyberclaw.core.context import AgentState
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


class TestAgent(unittest.TestCase):

    @patch('cyberclaw.core.agent.audit_logger.log_event', return_value=True)
    @patch('cyberclaw.core.provider.get_provider')
    def test_context_overflow_stops_before_calling_model(
        self,
        mock_get_provider,
        _mock_log_event,
    ):
        from cyberclaw.core.agent import create_agent_app
        from cyberclaw.core.context import ContextPolicy

        bound_model = Mock()
        model = Mock()
        model.bind_tools.return_value = bound_model
        mock_get_provider.return_value = model
        app = create_agent_app(
            tools=[],
            context_policy=ContextPolicy(
                max_tokens=500,
                tool_output_chars=120,
                emergency_tool_output_chars=60,
            ),
        )

        result = app.invoke({
            "messages": [HumanMessage(content="x" * 5_000)],
            "summary": "",
        })

        bound_model.invoke.assert_not_called()
        self.assertIn("超过安全窗口", result["messages"][-1].content)

    def test_agent_state_initialization(self):
        """测试 AgentState 的初始化"""
        from cyberclaw.core.context import AgentState

        initial_state = AgentState(
            messages=[],
            summary=""
        )

        self.assertEqual(initial_state["messages"], [])
        self.assertEqual(initial_state["summary"], "")

    @patch('cyberclaw.core.provider.get_provider')
    @patch('cyberclaw.core.skill_loader.load_dynamic_skills')
    @patch('cyberclaw.core.tools.builtins.BUILTIN_TOOLS', [])
    def test_create_agent_app_basic(self, mock_load_skills, mock_get_provider):
        """测试创建基础代理应用（带 Mock）"""
        from cyberclaw.core.agent import create_agent_app

        # Mock provider 返回值
        mock_provider = Mock()
        mock_provider.bind_tools.return_value = Mock()
        mock_get_provider.return_value = mock_provider

        # Mock 动态技能加载
        mock_load_skills.return_value = []

        try:
            app = create_agent_app(provider_name="openai", model_name="gpt-4o-mini")
            self.assertIsNotNone(app)
        except Exception as e:
            # 即使出现其他错误也记录
            print(f"Unexpected error: {e}")
            raise

    @patch('cyberclaw.core.provider.get_provider')
    @patch('cyberclaw.core.skill_loader.load_dynamic_skills')
    @patch('cyberclaw.core.tools.builtins.BUILTIN_TOOLS', [])
    def test_create_agent_app_with_custom_tools(self, mock_load_skills, mock_get_provider):
        """测试创建带有自定义工具的代理应用（带 Mock）"""
        from cyberclaw.core.agent import create_agent_app
        from langchain_core.tools import tool

        # Mock provider 返回值
        mock_provider = Mock()
        mock_provider.bind_tools.return_value = Mock()
        mock_get_provider.return_value = mock_provider

        # Mock 动态技能加载
        mock_load_skills.return_value = []

        # 创建一个真正的 mock 工具（使用@tool 装饰器）
        @tool
        def mock_tool(test_param: str) -> str:
            """A mock tool for testing"""
            return f"mock result: {test_param}"

        try:
            app = create_agent_app(
                provider_name="openai",
                model_name="gpt-4o-mini",
                tools=[mock_tool]
            )
            self.assertIsNotNone(app)
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise

    @patch('cyberclaw.core.provider.get_provider')
    @patch('cyberclaw.core.skill_loader.load_dynamic_skills')
    @patch('cyberclaw.core.tools.builtins.BUILTIN_TOOLS', [])
    def test_create_agent_app_with_checkpointer(self, mock_load_skills, mock_get_provider):
        """测试创建带有检查点的代理应用（带 Mock）"""
        from cyberclaw.core.agent import create_agent_app
        from langgraph.checkpoint.memory import MemorySaver

        # Mock provider 返回值
        mock_provider = Mock()
        mock_provider.bind_tools.return_value = Mock()
        mock_get_provider.return_value = mock_provider

        # Mock 动态技能加载
        mock_load_skills.return_value = []

        memory_saver = MemorySaver()
        try:
            app = create_agent_app(
                provider_name="openai",
                model_name="gpt-4o-mini",
                checkpointer=memory_saver
            )
            self.assertIsNotNone(app)
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise


if __name__ == '__main__':
    unittest.main()

import os
import unittest
from unittest.mock import patch

from cyberclaw.core.provider import ProviderConfigurationError, get_provider


class TestProviderConfiguration(unittest.TestCase):
    @patch("langchain_openai.ChatOpenAI")
    def test_other_provider_accepts_school_compatible_endpoint(self, chat_openai):
        get_provider(
            provider_name=" OTHER ",
            model_name="SDU-AI/DeepSeek-V4-Flash",
            api_key="test-key",
            base_url="https://xplt.sdu.edu.cn:4000",
        )

        chat_openai.assert_called_once_with(
            model="SDU-AI/DeepSeek-V4-Flash",
            temperature=0.0,
            api_key="test-key",
            base_url="https://xplt.sdu.edu.cn:4000",
        )

    @patch("langchain_openai.ChatOpenAI")
    def test_explicit_settings_override_environment(self, chat_openai):
        environment = {
            "OPENAI_API_KEY": "old-key",
            "OPENAI_API_BASE": "https://old.example.com/v1",
        }
        with patch.dict(os.environ, environment, clear=True):
            get_provider(
                provider_name="other",
                model_name="new-model",
                api_key="new-key",
                base_url="https://new.example.com/v1",
            )

        self.assertEqual(chat_openai.call_args.kwargs["api_key"], "new-key")
        self.assertEqual(
            chat_openai.call_args.kwargs["base_url"],
            "https://new.example.com/v1",
        )

    @patch("langchain_openai.ChatOpenAI")
    def test_official_compatible_provider_uses_known_fallback(self, chat_openai):
        with patch.dict(os.environ, {}, clear=True):
            get_provider("aliyun", "qwen", api_key="test-key")

        self.assertEqual(
            chat_openai.call_args.kwargs["base_url"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_other_provider_requires_base_url(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "OPENAI_API_BASE"):
                get_provider("other", "model")

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaisesRegex(ProviderConfigurationError, "完整"):
            get_provider(
                "other",
                "model",
                api_key="test-key",
                base_url="xplt.sdu.edu.cn:4000",
            )

    def test_missing_model_is_rejected(self):
        with self.assertRaisesRegex(ProviderConfigurationError, "模型名称"):
            get_provider("openai", "   ", api_key="test-key")


if __name__ == "__main__":
    unittest.main()

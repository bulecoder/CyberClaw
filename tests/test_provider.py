import os
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from cyberclaw.core.provider import (
    ProviderConfigurationError,
    ProviderErrorKind,
    ProviderInvocationError,
    ProviderRetryPolicy,
    classify_provider_error,
    extract_model_usage,
    get_invocation_attempts,
    get_provider,
    invoke_model,
)


class StatusError(Exception):
    def __init__(self, status_code: int, message: str = "provider error"):
        self.status_code = status_code
        super().__init__(message)


class TestProviderInvocation(unittest.TestCase):
    @patch("cyberclaw.core.provider.time.sleep")
    def test_retries_transient_errors_with_bounded_exponential_backoff(
        self,
        mock_sleep,
    ):
        response = AIMessage(content="ok")
        model = Mock()
        model.invoke.side_effect = [
            TimeoutError("first"),
            ConnectionError("second"),
            response,
        ]
        policy = ProviderRetryPolicy(
            max_attempts=3,
            request_timeout_seconds=10,
            initial_backoff_seconds=0.25,
            max_backoff_seconds=1,
        )

        result = invoke_model(model, ["message"], policy=policy)

        self.assertIs(result, response)
        self.assertEqual(model.invoke.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in mock_sleep.call_args_list],
            [0.25, 0.5],
        )
        self.assertEqual(get_invocation_attempts(result), 3)

    @patch("cyberclaw.core.provider.time.sleep")
    def test_permanent_4xx_error_is_not_retried_or_exposed(self, mock_sleep):
        model = Mock()
        model.invoke.side_effect = StatusError(
            400,
            "request included secret sk-should-not-leak",
        )

        with self.assertRaises(ProviderInvocationError) as caught:
            invoke_model(model, [], policy=ProviderRetryPolicy())

        self.assertEqual(caught.exception.info.kind, ProviderErrorKind.BAD_REQUEST)
        self.assertEqual(caught.exception.attempts, 1)
        self.assertNotIn("should-not-leak", str(caught.exception))
        self.assertEqual(model.invoke.call_count, 1)
        mock_sleep.assert_not_called()

    def test_retryable_error_is_classified_after_attempts_are_exhausted(self):
        model = Mock()
        model.invoke.side_effect = StatusError(503)
        policy = ProviderRetryPolicy(
            max_attempts=2,
            initial_backoff_seconds=0,
        )

        with self.assertRaises(ProviderInvocationError) as caught:
            invoke_model(model, [], policy=policy)

        self.assertEqual(caught.exception.info.kind, ProviderErrorKind.SERVER)
        self.assertTrue(caught.exception.info.retryable)
        self.assertEqual(caught.exception.attempts, 2)

    def test_extracts_standard_and_compatible_usage_metadata(self):
        standard = AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 12,
                "output_tokens": 3,
                "total_tokens": 15,
            },
        )
        compatible = AIMessage(
            content="ok",
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                }
            },
        )

        self.assertEqual(
            extract_model_usage(standard).as_dict(),
            {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        )
        self.assertEqual(
            extract_model_usage(compatible).as_dict(),
            {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
        )
        self.assertIsNone(extract_model_usage(AIMessage(content="no usage")))

    def test_classifies_authentication_and_rate_limit_errors(self):
        authentication = classify_provider_error(StatusError(401))
        rate_limit = classify_provider_error(StatusError(429))

        self.assertEqual(authentication.kind, ProviderErrorKind.AUTHENTICATION)
        self.assertFalse(authentication.retryable)
        self.assertEqual(rate_limit.kind, ProviderErrorKind.RATE_LIMIT)
        self.assertTrue(rate_limit.retryable)

        request_timeout = classify_provider_error(StatusError(408))
        self.assertEqual(request_timeout.kind, ProviderErrorKind.TIMEOUT)
        self.assertTrue(request_timeout.retryable)

    def test_policy_loads_environment_and_rejects_invalid_values(self):
        with patch.dict(
            os.environ,
            {
                "CYBERCLAW_PROVIDER_MAX_ATTEMPTS": "4",
                "CYBERCLAW_PROVIDER_TIMEOUT_SECONDS": "45.5",
            },
            clear=True,
        ):
            policy = ProviderRetryPolicy.from_env()
        self.assertEqual(policy.max_attempts, 4)
        self.assertEqual(policy.request_timeout_seconds, 45.5)

        with patch.dict(
            os.environ,
            {"CYBERCLAW_PROVIDER_MAX_ATTEMPTS": "invalid"},
            clear=True,
        ):
            with self.assertRaises(ProviderConfigurationError):
                ProviderRetryPolicy.from_env()


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
            max_retries=0,
            timeout=60.0,
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
    def test_custom_policy_sets_client_timeout_and_disables_sdk_retry(
        self,
        chat_openai,
    ):
        get_provider(
            provider_name="openai",
            model_name="test-model",
            api_key="test-key",
            retry_policy=ProviderRetryPolicy(
                max_attempts=5,
                request_timeout_seconds=12.5,
            ),
            max_retries=99,
        )

        self.assertEqual(chat_openai.call_args.kwargs["timeout"], 12.5)
        self.assertEqual(chat_openai.call_args.kwargs["max_retries"], 0)

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

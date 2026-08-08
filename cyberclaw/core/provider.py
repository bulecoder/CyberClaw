import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlparse

from langchain_core.language_models.chat_models import BaseChatModel


class ProviderConfigurationError(ValueError):
    """Raised when provider settings are missing or invalid."""


class ProviderErrorKind(str, Enum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER = "server"
    AUTHENTICATION = "authentication"
    BAD_REQUEST = "bad_request"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderErrorInfo:
    kind: ProviderErrorKind
    retryable: bool
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderRetryPolicy:
    """One bounded retry policy shared by every CyberClaw model call."""

    max_attempts: int = 3
    request_timeout_seconds: float = 60.0
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 4.0

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ProviderConfigurationError("max_attempts 必须大于 0")
        if self.request_timeout_seconds <= 0:
            raise ProviderConfigurationError("request_timeout_seconds 必须大于 0")
        if self.initial_backoff_seconds < 0:
            raise ProviderConfigurationError("initial_backoff_seconds 不能小于 0")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ProviderConfigurationError(
                "max_backoff_seconds 不能小于 initial_backoff_seconds"
            )

    @classmethod
    def from_env(cls) -> "ProviderRetryPolicy":
        return cls(
            max_attempts=_read_int_env(
                "CYBERCLAW_PROVIDER_MAX_ATTEMPTS",
                3,
            ),
            request_timeout_seconds=_read_float_env(
                "CYBERCLAW_PROVIDER_TIMEOUT_SECONDS",
                60.0,
            ),
        )

    def backoff_seconds(self, failed_attempt: int) -> float:
        return min(
            self.initial_backoff_seconds * (2 ** max(0, failed_attempt - 1)),
            self.max_backoff_seconds,
        )


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


class ProviderInvocationError(RuntimeError):
    """Safe, classified failure raised after retry handling finishes."""

    def __init__(
        self,
        info: ProviderErrorInfo,
        attempts: int,
    ) -> None:
        self.info = info
        self.attempts = attempts
        super().__init__(
            f"模型调用失败（{info.kind.value}，已尝试 {attempts} 次）"
        )


def _read_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value.strip())
    except (AttributeError, ValueError) as exc:
        raise ProviderConfigurationError(f"{name} 必须是整数") from exc


def _read_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value.strip())
    except (AttributeError, ValueError) as exc:
        raise ProviderConfigurationError(f"{name} 必须是数字") from exc


def _exception_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def classify_provider_error(exc: Exception) -> ProviderErrorInfo:
    """Classify without exposing the provider's raw error body."""

    status_code = _exception_status_code(exc)
    class_name = type(exc).__name__.casefold()
    if status_code == 429 or "ratelimit" in class_name:
        return ProviderErrorInfo(ProviderErrorKind.RATE_LIMIT, True, status_code)
    if (
        status_code == 408
        or isinstance(exc, TimeoutError)
        or "timeout" in class_name
    ):
        return ProviderErrorInfo(ProviderErrorKind.TIMEOUT, True, status_code)
    if isinstance(exc, ConnectionError) or any(
        marker in class_name for marker in ("connection", "connecterror", "network")
    ):
        return ProviderErrorInfo(ProviderErrorKind.CONNECTION, True, status_code)
    if status_code is not None and status_code >= 500:
        return ProviderErrorInfo(ProviderErrorKind.SERVER, True, status_code)
    if status_code in {401, 403} or any(
        marker in class_name for marker in ("authentication", "permissiondenied")
    ):
        return ProviderErrorInfo(
            ProviderErrorKind.AUTHENTICATION,
            False,
            status_code,
        )
    if status_code is not None and 400 <= status_code < 500:
        return ProviderErrorInfo(ProviderErrorKind.BAD_REQUEST, False, status_code)
    if any(marker in class_name for marker in ("badrequest", "invalidrequest")):
        return ProviderErrorInfo(ProviderErrorKind.BAD_REQUEST, False, status_code)
    return ProviderErrorInfo(ProviderErrorKind.UNKNOWN, False, status_code)


def invoke_model(
    model: Any,
    messages: Any,
    *,
    config: Any = None,
    policy: ProviderRetryPolicy | None = None,
) -> Any:
    """Invoke a model with one explicit retry layer and safe final errors."""

    active_policy = policy or ProviderRetryPolicy.from_env()
    for attempt in range(1, active_policy.max_attempts + 1):
        try:
            if config is None:
                response = model.invoke(messages)
            else:
                response = model.invoke(messages, config=config)
        except Exception as exc:
            error_info = classify_provider_error(exc)
            if not error_info.retryable or attempt >= active_policy.max_attempts:
                raise ProviderInvocationError(error_info, attempt) from exc
            time.sleep(active_policy.backoff_seconds(attempt))
            continue

        response_metadata = getattr(response, "response_metadata", None)
        if isinstance(response_metadata, dict):
            response.response_metadata = {
                **response_metadata,
                "cyberclaw_provider_attempts": attempt,
            }
        return response

    raise RuntimeError("Provider 重试循环异常结束")


def get_invocation_attempts(response: Any) -> int:
    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, Mapping):
        attempts = metadata.get("cyberclaw_provider_attempts", 1)
        if isinstance(attempts, int) and attempts > 0:
            return attempts
    return 1


def _non_negative_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def extract_model_usage(response: Any) -> ModelUsage | None:
    """Normalize LangChain and OpenAI-compatible usage metadata."""

    usage = getattr(response, "usage_metadata", None)
    usage_keys = {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
    if isinstance(usage, Mapping) and usage_keys.intersection(usage):
        input_tokens = _non_negative_int(
            usage.get("input_tokens", usage.get("prompt_tokens"))
        )
        output_tokens = _non_negative_int(
            usage.get("output_tokens", usage.get("completion_tokens"))
        )
        total_tokens = _non_negative_int(usage.get("total_tokens"))
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or input_tokens + output_tokens,
        )

    response_metadata = getattr(response, "response_metadata", None)
    if not isinstance(response_metadata, Mapping):
        return None
    token_usage = response_metadata.get("token_usage")
    if (
        not isinstance(token_usage, Mapping)
        or not usage_keys.intersection(token_usage)
    ):
        return None
    input_tokens = _non_negative_int(token_usage.get("prompt_tokens"))
    output_tokens = _non_negative_int(token_usage.get("completion_tokens"))
    total_tokens = _non_negative_int(token_usage.get("total_tokens"))
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens or input_tokens + output_tokens,
    )

# 各大厂商官方的 OpenAI 兼容接口地址 (当用户未配置 BASE_URL 时作为兜底)
COMPATIBLE_BASE_URLS = {
    "aliyun": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "z.ai": "https://open.bigmodel.cn/api/paas/v4",
    "tencent": "https://api.hunyuan.cloud.tencent.com/v1"
}

OPENAI_COMPATIBLE_PROVIDERS = {
    "openai",
    "aliyun",
    "dashscope",
    "z.ai",
    "tencent",
    "other",
}


def _validate_base_url(value: str | None, provider_name: str) -> str | None:
    if not value:
        return None
    base_url = value.strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderConfigurationError(
            f"{provider_name} 的 Base URL 必须是完整的 http:// 或 https:// 地址"
        )
    return base_url

def get_provider(
    provider_name: str = "openai",
    model_name: str = "gpt-4o-mini",
    temperature: float = 0.0,
    base_url: str | None = None,  # 允许外部传入
    api_key: str | None = None,   # 允许外部传入
    retry_policy: ProviderRetryPolicy | None = None,
    **kwargs: Any
) -> BaseChatModel:
    """Create a chat model from explicit arguments or existing environment variables."""
    active_policy = retry_policy or ProviderRetryPolicy.from_env()
    provider_name = provider_name.strip().lower()
    model_name = model_name.strip()
    if not provider_name:
        raise ProviderConfigurationError("Provider 不能为空")
    if not model_name:
        raise ProviderConfigurationError("模型名称不能为空")

    if provider_name in OPENAI_COMPATIBLE_PROVIDERS:
        from langchain_openai import ChatOpenAI

        current_api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not current_api_key:
            raise ProviderConfigurationError(
                "未找到 OPENAI_API_KEY；请先运行 cyberclaw config 或检查 .env"
            )

        final_base_url = base_url or os.environ.get("OPENAI_API_BASE")
        if not final_base_url:
            final_base_url = COMPATIBLE_BASE_URLS.get(provider_name)
        final_base_url = _validate_base_url(final_base_url, provider_name)
        if provider_name == "other" and not final_base_url:
            raise ProviderConfigurationError(
                "other Provider 必须配置 OPENAI_API_BASE 或显式传入 base_url"
            )

        kwargs["max_retries"] = 0
        kwargs.setdefault("timeout", active_policy.request_timeout_seconds)
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            api_key=current_api_key,
            base_url=final_base_url,
            **kwargs,
        )

    if provider_name == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ProviderConfigurationError(
                "Anthropic Provider 需要额外安装 langchain-anthropic"
            ) from exc

        current_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not current_api_key:
            raise ProviderConfigurationError("未找到 ANTHROPIC_API_KEY")

        final_base_url = _validate_base_url(
            base_url or os.environ.get("ANTHROPIC_BASE_URL"),
            provider_name,
        )

        kwargs["max_retries"] = 0
        kwargs.setdefault("timeout", active_policy.request_timeout_seconds)
        return ChatAnthropic(
            model_name=model_name,
            temperature=temperature,
            api_key=current_api_key,
            base_url=final_base_url,
            **kwargs,
        )

    if provider_name == "ollama":
        try:
            from langchain_community.chat_models import ChatOllama
        except ImportError as exc:
            raise ProviderConfigurationError(
                "Ollama Provider 需要额外安装 langchain-community"
            ) from exc

        final_base_url = _validate_base_url(
            base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            provider_name,
        )
        return ChatOllama(
            model=model_name,
            temperature=temperature,
            base_url=final_base_url,
            **kwargs,
        )

    raise ProviderConfigurationError(f"不支持的模型提供商: {provider_name}")


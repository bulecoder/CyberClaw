import os
from typing import Any
from urllib.parse import urlparse

from langchain_core.language_models.chat_models import BaseChatModel


class ProviderConfigurationError(ValueError):
    """Raised when provider settings are missing or invalid."""

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
    **kwargs: Any
) -> BaseChatModel:
    """Create a chat model from explicit arguments or existing environment variables."""
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


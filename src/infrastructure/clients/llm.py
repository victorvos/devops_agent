"""LLM client factory.

Returns a LangChain-compatible chat model based on the configured provider.

Primary target: Azure AI Foundry (GPT 5.x models deployed via the
AI Foundry model catalog). Falls back to Azure OpenAI, OpenAI direct,
or Anthropic.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from src.core.config import LLMProvider, Settings


def get_chat_model(settings: Settings) -> BaseChatModel:
    """Instantiate the configured chat model.

    Azure AI Foundry models expose an OpenAI-compatible endpoint, so we
    use AzureChatOpenAI with the Foundry endpoint URL and model name.
    """

    match settings.llm_provider:
        case LLMProvider.AZURE_AI_FOUNDRY:
            from langchain_openai import AzureChatOpenAI

            # Azure AI Foundry exposes an OpenAI-compatible inference API.
            # The endpoint is the Foundry project endpoint and the
            # deployment name maps to the model name in the catalog.
            return AzureChatOpenAI(
                azure_endpoint=settings.azure_ai_foundry_endpoint,
                api_key=settings.azure_ai_foundry_api_key,
                azure_deployment=settings.azure_ai_foundry_model,
                api_version=settings.azure_ai_foundry_api_version,
                temperature=0.1,
                max_tokens=8192,
                max_retries=3,
            )

        case LLMProvider.AZURE_OPENAI:
            from langchain_openai import AzureChatOpenAI

            return AzureChatOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                azure_deployment=settings.azure_openai_deployment,
                api_version=settings.azure_openai_api_version,
                temperature=0.1,
                max_tokens=4096,
                max_retries=3,
            )

        case LLMProvider.OPENAI:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
                temperature=0.1,
                max_tokens=4096,
                max_retries=3,
            )

        case LLMProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                api_key=settings.anthropic_api_key,
                model_name=settings.anthropic_model,
                temperature=0.1,
                max_tokens=4096,
                max_retries=3,
            )

        case _:
            raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")

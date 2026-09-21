import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class LLMEndpoint:
    provider: str
    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class Settings:
    provider: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    ollama_base_url: str
    ollama_model: str

    def endpoint(self) -> LLMEndpoint:
        if self.provider == 'ollama':
            return LLMEndpoint(
                provider='ollama',
                api_key='ollama',  # OpenAI 兼容接口对 key 不校验
                base_url=self.ollama_base_url,
                model=self.ollama_model,
            )
        return LLMEndpoint(
            provider='deepseek',
            api_key=self.deepseek_api_key,
            base_url=self.deepseek_base_url,
            model=self.deepseek_model,
        )


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        provider=os.getenv('GEOMIND_PROVIDER', 'deepseek').strip().lower(),
        deepseek_api_key=os.getenv('DEEPSEEK_API_KEY', ''),
        deepseek_base_url=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
        deepseek_model=os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'),
        ollama_base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'),
        ollama_model=os.getenv('OLLAMA_MODEL', 'qwen3.5:9b'),
    )

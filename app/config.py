from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "呼叫中心合规转写与预警服务"
    app_version: str = "1.0.0"
    debug: bool = True

    asr_mock_mode: bool = True
    asr_mock_delay_seconds: int = 3

    storage_type: str = "memory"

    class Config:
        env_prefix = "COMPLIANCE_"


settings = Settings()

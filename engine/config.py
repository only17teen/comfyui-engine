"""ComfyUI Async Generation Engine v5.1 - Configuration System.

Key fix: Field(env=...) was silently ignored in Pydantic v2.  We now use
pydantic-settings BaseSettings which reads env vars correctly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    _HAS_SETTINGS = True
except ImportError:
    # Graceful degradation: fall back to plain BaseModel (env vars via loader)
    BaseSettings = BaseModel  # type: ignore[assignment,misc]
    SettingsConfigDict = None  # type: ignore[assignment]
    _HAS_SETTINGS = False


# ── Sub-models ─────────────────────────────────────────────────────────────


class LoRAModelConfig(BaseModel):
    """Single LoRA model specification."""

    name: str
    path: str
    weight_range: tuple[float, float] = (0.3, 1.0)

    @field_validator("weight_range")
    @classmethod
    def validate_range(cls, v: tuple[float, float]) -> tuple[float, float]:
        if v[0] < 0 or v[1] > 2.0 or v[0] > v[1]:
            raise ValueError(f"Invalid weight range: {v}. Must be 0 <= min <= max <= 2.0")
        return v


class SamplingConfig(BaseModel):
    """Sampling parameter configuration."""

    steps_range: tuple[int, int] = (20, 40)
    cfg_scale_range: tuple[float, float] = (5.0, 9.0)
    sampler_names: list[str] = Field(default_factory=lambda: ["DPM++ 2M Karras", "Euler a"])
    scheduler: str = "Karras"

    @field_validator("steps_range")
    @classmethod
    def validate_steps(cls, v: tuple[int, int]) -> tuple[int, int]:
        if v[0] < 1 or v[1] > 150 or v[0] > v[1]:
            raise ValueError(f"Invalid steps range: {v}")
        return v

    @field_validator("cfg_scale_range")
    @classmethod
    def validate_cfg(cls, v: tuple[float, float]) -> tuple[float, float]:
        if v[0] < 1.0 or v[1] > 30.0 or v[0] > v[1]:
            raise ValueError(f"Invalid CFG range: {v}")
        return v


class ClothingConfig(BaseModel):
    """Clothing category dictionaries."""

    tops: list[str] = Field(default_factory=list)
    bottoms: list[str] = Field(default_factory=list)
    accessories: list[str] = Field(default_factory=list)
    full_body: list[str] = Field(default_factory=list)


class PromptDictionary(BaseModel):
    """Complete prompt dictionary configuration."""

    trigger_words: list[str] = Field(default_factory=list)
    clothing: ClothingConfig = Field(default_factory=ClothingConfig)
    poses: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    lighting: list[str] = Field(default_factory=list)
    negative_prompt: str = ""


class LoRAConfig(BaseModel):
    """LoRA configuration section."""

    models: list[LoRAModelConfig] = Field(default_factory=list)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    resolutions: list[tuple[int, int]] = Field(default_factory=list)
    batch_size: int = 1
    max_concurrent: int = 3

    @field_validator("resolutions")
    @classmethod
    def validate_resolutions(cls, v: list[tuple[int, int]]) -> list[tuple[int, int]]:
        for w, h in v:
            if w < 64 or h < 64 or w > 4096 or h > 4096:
                raise ValueError(f"Invalid resolution: {w}x{h}")
        return v

    @field_validator("batch_size")
    @classmethod
    def validate_batch(cls, v: int) -> int:
        if v < 1 or v > 16:
            raise ValueError("batch_size must be 1-16")
        return v


# ── Root config: uses BaseSettings for automatic env-var reading ───────────────

if _HAS_SETTINGS and SettingsConfigDict is not None:

    class EngineConfig(BaseSettings):  # type: ignore[misc]
        """Root engine configuration.  Env vars are read automatically by pydantic-settings.

        Priority: env vars > YAML > field defaults.

        Environment variables map 1-to-1 with field names, prefixed by ``ENGINE_``
        (or ``COMFYUI_`` for the two ComfyUI-specific vars).
        """

        model_config = SettingsConfigDict(
            env_prefix="ENGINE_",
            env_nested_delimiter="__",
            case_sensitive=False,
            extra="ignore",
        )

        # Prompt / LoRA sections (loaded from YAML, not env)
        prompts: PromptDictionary = Field(default_factory=PromptDictionary)
        lora: LoRAConfig = Field(default_factory=LoRAConfig)

        # Connection (COMFYUI_URL / COMFYUI_MAX_CONCURRENT override ENGINE_ prefix)
        base_url: str = Field(
            default="http://127.0.0.1:8188",
            alias="COMFYUI_URL",
            validation_alias="COMFYUI_URL",
        )
        max_concurrent: int = Field(
            default=3,
            alias="COMFYUI_MAX_CONCURRENT",
            validation_alias="COMFYUI_MAX_CONCURRENT",
        )

        # Paths
        output_dir: str = "output_models"
        timeout: float = 300.0
        poll_interval: float = 1.0
        retry_max: int = 3
        retry_base_delay: float = 1.0
        circuit_failure_threshold: int = 5
        circuit_recovery_timeout: float = 30.0
        queue_max_size: int = 100
        queue_rate_limit: float | None = None
        log_level: str = "INFO"
        json_logging: bool = True
        metrics_window: int = 1000
        metrics_port: int | None = None

        @field_validator("log_level")
        @classmethod
        def validate_log_level(cls, v: str) -> str:
            valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
            v_upper = v.upper()
            if v_upper not in valid:
                raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
            return v_upper

        @field_validator("base_url", mode="before")
        @classmethod
        def validate_url(cls, v: str) -> str:
            if not str(v).startswith(("http://", "https://")):
                raise ValueError(f"Invalid URL: {v}. Must start with http:// or https://")
            return str(v).rstrip("/")

else:
    # Fallback: plain BaseModel, env vars injected by ConfigLoader
    class EngineConfig(BaseModel):  # type: ignore[no-redef]
        """Root engine configuration (pydantic-settings not installed)."""

        prompts: PromptDictionary = Field(default_factory=PromptDictionary)
        lora: LoRAConfig = Field(default_factory=LoRAConfig)
        base_url: str = "http://127.0.0.1:8188"
        max_concurrent: int = 3
        output_dir: str = "output_models"
        timeout: float = 300.0
        poll_interval: float = 1.0
        retry_max: int = 3
        retry_base_delay: float = 1.0
        circuit_failure_threshold: int = 5
        circuit_recovery_timeout: float = 30.0
        queue_max_size: int = 100
        queue_rate_limit: float | None = None
        log_level: str = "INFO"
        json_logging: bool = True
        metrics_window: int = 1000
        metrics_port: int | None = None


# ── ConfigLoader ────────────────────────────────────────────────────────────


class ConfigLoader:
    """Loads configuration from YAML with pydantic-settings validation.

    When pydantic-settings is available env vars are read automatically.
    When it is not, this class reads ENGINE_* vars manually and injects them.
    """

    @staticmethod
    def load(
        yaml_path: str | Path = "config/prompts.yaml",
    ) -> EngineConfig:
        """Load configuration.  Priority: env vars > YAML file > defaults."""
        yaml_data: dict[str, Any] = {}
        path = Path(yaml_path)
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                yaml_data = yaml.safe_load(fh) or {}
        else:
            print(f"Warning: config not found at {path!r}, using defaults.")

        if _HAS_SETTINGS:
            # pydantic-settings reads env vars automatically; just pass YAML data
            # so non-settings fields (prompts, lora) are populated.
            return EngineConfig(**yaml_data)  # type: ignore[call-arg]

        # Fallback: manually extract env vars
        env = ConfigLoader._extract_env_overrides()
        merged = ConfigLoader._deep_merge(yaml_data, env)
        return EngineConfig(**merged)

    @staticmethod
    def _extract_env_overrides() -> dict[str, Any]:
        """Manual env-var extraction (used only when pydantic-settings is absent)."""
        mapping: dict[str, tuple[str, type]] = {
            "COMFYUI_URL": ("base_url", str),
            "COMFYUI_MAX_CONCURRENT": ("max_concurrent", int),
            "ENGINE_OUTPUT_DIR": ("output_dir", str),
            "ENGINE_TIMEOUT": ("timeout", float),
            "ENGINE_POLL_INTERVAL": ("poll_interval", float),
            "ENGINE_RETRY_MAX": ("retry_max", int),
            "ENGINE_RETRY_BASE_DELAY": ("retry_base_delay", float),
            "ENGINE_CB_FAILURE_THRESHOLD": ("circuit_failure_threshold", int),
            "ENGINE_CB_RECOVERY_TIMEOUT": ("circuit_recovery_timeout", float),
            "ENGINE_QUEUE_MAX_SIZE": ("queue_max_size", int),
            "ENGINE_QUEUE_RATE_LIMIT": ("queue_rate_limit", float),
            "ENGINE_LOG_LEVEL": ("log_level", str),
            "ENGINE_JSON_LOGGING": ("json_logging", bool),
            "ENGINE_METRICS_WINDOW": ("metrics_window", int),
        }
        result: dict[str, Any] = {}
        for env_name, (field_name, cast) in mapping.items():
            raw = os.environ.get(env_name)
            if raw is None:
                continue
            if cast is bool:
                result[field_name] = raw.lower() in ("true", "1", "yes", "on")
            else:
                try:
                    result[field_name] = cast(raw)
                except ValueError:
                    pass
        return result

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = ConfigLoader._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    @staticmethod
    def save_example(path: str | Path = "config/prompts.yaml") -> None:
        """Write a commented example config file."""
        cfg = EngineConfig()
        data = {
            "prompts": cfg.prompts.model_dump(),
            "lora": cfg.lora.model_dump(),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"Example config saved to: {path}")

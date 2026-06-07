"""ComfyUI Async Generation Engine v2.0 - Configuration System
Pydantic-based validation with env var override and YAML merging.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, Field, validator, field_validator


# ───────────────────────────────────────────────────────────────
# Pydantic Models for Configuration
# ───────────────────────────────────────────────────────────────
class LoRAModelConfig(BaseModel):
    """Single LoRA model specification."""

    name: str
    path: str
    weight_range: tuple[float, float] = (0.3, 1.0)

    @field_validator("weight_range")
    @classmethod
    def validate_range(cls, v: tuple[float, float]) -> tuple[float, float]:
        if v[0] < 0 or v[1] > 2.0 or v[0] > v[1]:
            raise ValueError(
                f"Invalid weight range: {v}. Must be 0 <= min <= max <= 2.0"
            )
        return v


class SamplingConfig(BaseModel):
    """Sampling parameter configuration."""

    steps_range: tuple[int, int] = (20, 40)
    cfg_scale_range: tuple[float, float] = (5.0, 9.0)
    sampler_names: list[str] = Field(
        default_factory=lambda: ["DPM++ 2M Karras", "Euler a"]
    )
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


class EngineConfig(BaseModel):
    """Root engine configuration."""

    prompts: PromptDictionary = Field(default_factory=PromptDictionary)
    lora: LoRAConfig = Field(default_factory=LoRAConfig)

    # Runtime overrides (not from YAML, from env/CLI)
    base_url: str = Field(default="http://127.0.0.1:8188", env="COMFYUI_URL")
    max_concurrent: int = Field(default=3, env="COMFYUI_MAX_CONCURRENT")
    output_dir: str = Field(default="output_models", env="ENGINE_OUTPUT_DIR")
    timeout: float = Field(default=300.0, env="ENGINE_TIMEOUT")
    poll_interval: float = Field(default=1.0, env="ENGINE_POLL_INTERVAL")
    retry_max: int = Field(default=3, env="ENGINE_RETRY_MAX")
    retry_base_delay: float = Field(default=1.0, env="ENGINE_RETRY_BASE_DELAY")
    circuit_failure_threshold: int = Field(default=5, env="ENGINE_CB_FAILURE_THRESHOLD")
    circuit_recovery_timeout: float = Field(
        default=30.0, env="ENGINE_CB_RECOVERY_TIMEOUT"
    )
    queue_max_size: int = Field(default=100, env="ENGINE_QUEUE_MAX_SIZE")
    queue_rate_limit: float | None = Field(default=None, env="ENGINE_QUEUE_RATE_LIMIT")
    log_level: str = Field(default="INFO", env="ENGINE_LOG_LEVEL")
    json_logging: bool = Field(default=True, env="ENGINE_JSON_LOGGING")
    metrics_window: int = Field(default=1000, env="ENGINE_METRICS_WINDOW")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
        return v_upper

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL: {v}. Must start with http:// or https://")
        return v.rstrip("/")


# ───────────────────────────────────────────────────────────────
# Configuration Loader with Env Override
# ───────────────────────────────────────────────────────────────
class ConfigLoader:
    """Loads configuration from YAML with Pydantic validation
    and environment variable overrides.
    """

    @staticmethod
    def load(
        yaml_path: str | Path = "config/prompts.yaml",
        env_prefix: str = "ENGINE_",
    ) -> EngineConfig:
        """Load configuration from YAML and override with environment variables.

        Priority: env vars > YAML > defaults.

        Args:
            yaml_path: Path to YAML config file.
            env_prefix: Prefix for environment variable names.

        Returns:
            Validated EngineConfig instance.
        """
        yaml_data: dict[str, Any] = {}
        path = Path(yaml_path)

        if path.exists():
            with open(path, encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
        else:
            print(f"Warning: Config file not found: {path}. Using defaults.")

        # Extract runtime overrides from environment
        env_overrides = ConfigLoader._extract_env_overrides(env_prefix)

        # Merge: YAML data + env overrides
        merged = ConfigLoader._deep_merge(yaml_data, env_overrides)

        return EngineConfig(**merged)

    @staticmethod
    def _extract_env_overrides(prefix: str) -> dict[str, Any]:
        """Extract ENGINE_* environment variables as typed overrides."""
        overrides: dict[str, Any] = {}
        mapping = {
            "COMFYUI_URL": "base_url",
            "COMFYUI_MAX_CONCURRENT": "max_concurrent",
            "ENGINE_OUTPUT_DIR": "output_dir",
            "ENGINE_TIMEOUT": "timeout",
            "ENGINE_POLL_INTERVAL": "poll_interval",
            "ENGINE_RETRY_MAX": "retry_max",
            "ENGINE_RETRY_BASE_DELAY": "retry_base_delay",
            "ENGINE_CB_FAILURE_THRESHOLD": "circuit_failure_threshold",
            "ENGINE_CB_RECOVERY_TIMEOUT": "circuit_recovery_timeout",
            "ENGINE_QUEUE_MAX_SIZE": "queue_max_size",
            "ENGINE_QUEUE_RATE_LIMIT": "queue_rate_limit",
            "ENGINE_LOG_LEVEL": "log_level",
            "ENGINE_JSON_LOGGING": "json_logging",
            "ENGINE_METRICS_WINDOW": "metrics_window",
        }

        for env_name, config_key in mapping.items():
            value = os.environ.get(env_name)
            if value is not None:
                # Type inference
                if config_key in (
                    "max_concurrent",
                    "retry_max",
                    "circuit_failure_threshold",
                    "queue_max_size",
                    "metrics_window",
                ):
                    overrides[config_key] = int(value)
                elif config_key in (
                    "timeout",
                    "poll_interval",
                    "retry_base_delay",
                    "circuit_recovery_timeout",
                    "queue_rate_limit",
                ):
                    overrides[config_key] = float(value)
                elif config_key == "json_logging":
                    overrides[config_key] = value.lower() in ("true", "1", "yes", "on")
                else:
                    overrides[config_key] = value

        return overrides

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Deep merge two dictionaries. Override wins on conflicts."""
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = ConfigLoader._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @staticmethod
    def save_example(path: str | Path = "config/prompts.yaml") -> None:
        """Generate an example configuration file."""
        example = EngineConfig()
        # Convert to dict, excluding runtime-only fields
        data = {
            "prompts": {
                "trigger_words": example.prompts.trigger_words,
                "clothing": {
                    "tops": example.prompts.clothing.tops,
                    "bottoms": example.prompts.clothing.bottoms,
                    "accessories": example.prompts.clothing.accessories,
                    "full_body": example.prompts.clothing.full_body,
                },
                "poses": example.prompts.poses,
                "locations": example.prompts.locations,
                "expressions": example.prompts.expressions,
                "lighting": example.prompts.lighting,
                "negative_prompt": example.prompts.negative_prompt,
            },
            "lora": {
                "models": [m.model_dump() for m in example.lora.models],
                "sampling": example.lora.sampling.model_dump(),
                "resolutions": example.lora.resolutions,
                "batch_size": example.lora.batch_size,
                "max_concurrent": example.lora.max_concurrent,
            },
        }

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, default_flow_style=False, allow_unicode=True, sort_keys=False
            )

        print(f"Example config saved to: {path}")

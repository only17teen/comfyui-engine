# Engine package marker
from engine.config import ConfigLoader, EngineConfig
from engine.core import (
    CircuitBreaker,
    CircuitBreakerConfig,
    MetricsCollector,
    RetryConfig,
    with_retry,
    JobQueue,
    QueueFullError,
    setup_logging,
)
from engine.prompt_manager import PromptManager, GenerationConfig, SeedStrategy, PromptTemplate
from engine.api_client import ComfyUIAsyncClient, ComfyUIJob
from engine.output_handler import OutputHandler
from engine.git_sync import sync_to_git, init_repo, get_git_status, get_repo_info

__version__ = "2.0.0"
__all__ = [
    "ConfigLoader",
    "EngineConfig",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "MetricsCollector",
    "RetryConfig",
    "with_retry",
    "JobQueue",
    "QueueFullError",
    "setup_logging",
    "PromptManager",
    "GenerationConfig",
    "SeedStrategy",
    "PromptTemplate",
    "ComfyUIAsyncClient",
    "ComfyUIJob",
    "OutputHandler",
    "sync_to_git",
    "init_repo",
    "get_git_status",
    "get_repo_info",
]

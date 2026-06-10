"""Feature Flags Module.

Addresses Issue #46: Feature flags — toggle features at runtime without redeployment.
"""
import os
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

class FeatureFlags:
    """Simple in-memory feature flags."""
    
    _flags: Dict[str, bool] = {}
    
    @classmethod
    def load_from_env(cls):
        """Load flags from COMFYUI_FEATURE_FLAGS env variable (JSON formatted)."""
        flags_json = os.getenv("COMFYUI_FEATURE_FLAGS")
        if flags_json:
            try:
                cls._flags.update(json.loads(flags_json))
                logger.info(f"Loaded feature flags from env: {cls._flags}")
            except Exception as e:
                logger.error(f"Failed to load feature flags from env: {e}")
                
    @classmethod
    def set(cls, flag_name: str, enabled: bool):
        """Set a feature flag."""
        cls._flags[flag_name] = enabled
        
    @classmethod
    def get(cls, flag_name: str, default: bool = False) -> bool:
        """Check if a feature is enabled."""
        return cls._flags.get(flag_name, default)
        
    @classmethod
    def all(cls) -> Dict[str, bool]:
        """Get all feature flags."""
        return dict(cls._flags)

# Initialize on module load
FeatureFlags.load_from_env()

def is_feature_enabled(flag_name: str, default: bool = False) -> bool:
    """Helper to check if a feature flag is enabled."""
    return FeatureFlags.get(flag_name, default)

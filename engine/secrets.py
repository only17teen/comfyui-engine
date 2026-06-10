"""Secrets encryption for sensitive workflow values.

Addresses Issue #49: Secrets encryption for sensitive workflow values.
"""
import base64
import os
import logging
from typing import Dict, Any, Union

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

class SecretsManager:
    """Manages encryption and decryption of workflow secrets."""
    
    def __init__(self, key: bytes = None):
        if Fernet is None:
            logger.warning("cryptography package not installed. Secrets encryption disabled.")
            self.fernet = None
            return
            
        key = key or os.getenv("COMFYUI_SECRET_KEY", "").encode()
        if not key:
            # For development only! In production, require a valid key.
            logger.warning("No COMFYUI_SECRET_KEY provided. Using a temporary key for session.")
            key = Fernet.generate_key()
            
        try:
            # Ensure it's a valid Fernet key (url-safe base64-encoded 32-byte key)
            if len(key) != 44:
                # pad or truncate if people pass bad keys just to avoid crashing
                import hashlib
                h = hashlib.sha256(key).digest()
                key = base64.urlsafe_b64encode(h)
                
            self.fernet = Fernet(key)
        except Exception as e:
            logger.error(f"Failed to initialize Fernet: {e}")
            self.fernet = None

    def encrypt(self, data: str) -> str:
        """Encrypt a string."""
        if not self.fernet:
            return data
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt a string."""
        if not self.fernet:
            return encrypted_data
        try:
            return self.fernet.decrypt(encrypted_data.encode()).decode()
        except Exception as e:
            logger.error(f"Failed to decrypt data: {e}")
            return encrypted_data
            
    def process_workflow(self, workflow: Dict[str, Any], decrypt: bool = True) -> Dict[str, Any]:
        """Recursively search for values starting with 'ENC:' and decrypt them."""
        if not self.fernet:
            return workflow
            
        result = {}
        for k, v in workflow.items():
            if isinstance(v, dict):
                result[k] = self.process_workflow(v, decrypt)
            elif isinstance(v, list):
                result[k] = [self.process_workflow(item, decrypt) if isinstance(item, dict) else item for item in v]
            elif isinstance(v, str) and v.startswith("ENC:"):
                result[k] = self.decrypt(v[4:]) if decrypt else v
            else:
                result[k] = v
        return result

# Global singleton
secrets = SecretsManager()

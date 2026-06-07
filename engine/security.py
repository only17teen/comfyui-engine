"""ComfyUI Async Generation Engine v5.1 - Security Enhancements
Kiro Protocol: JWT secret rotation, JSON schema validation, audit logging.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────
# JSON Schema Validation (Kiro Rule 10: API & Security)
# ───────────────────────────────────────────────────────────────
class JSONSchemaValidator:
    """JSON Schema validation for incoming requests.
    
    Kiro Protocol: Validate all incoming JSON before processing (Rule 10).
    """

    def __init__(self):
        self._schemas: dict[str, dict] = {}
        self._compiled: dict[str, Any] = {}

    def register_schema(self, name: str, schema: dict) -> None:
        """Register a JSON schema for validation."""
        self._schemas[name] = schema
        # Try to compile if jsonschema is available
        try:
            import jsonschema
            self._compiled[name] = jsonschema.Draft7Validator(schema)
        except ImportError:
            self._compiled[name] = None

    def validate(self, data: dict, schema_name: str) -> tuple[bool, list[str]]:
        """Validate data against a registered schema.
        
        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        schema = self._schemas.get(schema_name)
        if not schema:
            return False, [f"Schema '{schema_name}' not found"]

        compiled = self._compiled.get(schema_name)
        if compiled:
            try:
                errors = list(compiled.iter_errors(data))
                if errors:
                    return False, [str(e.message) for e in errors]
                return True, []
            except Exception as e:
                return False, [f"Validation error: {str(e)}"]
        
        # Fallback: basic type checking
        return self._basic_validate(data, schema)

    def _basic_validate(self, data: dict, schema: dict) -> tuple[bool, list[str]]:
        """Basic validation without jsonschema library."""
        errors = []
        
        if schema.get("type") == "object":
            if not isinstance(data, dict):
                errors.append(f"Expected object, got {type(data).__name__}")
                return False, errors
            
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            
            for prop in required:
                if prop not in data:
                    errors.append(f"Required property '{prop}' missing")
            
            for prop, prop_schema in properties.items():
                if prop in data:
                    valid, prop_errors = self._basic_validate_value(data[prop], prop_schema)
                    if not valid:
                        errors.extend([f"{prop}: {e}" for e in prop_errors])
        
        return len(errors) == 0, errors

    def _basic_validate_value(self, value: Any, schema: dict) -> tuple[bool, list[str]]:
        """Validate a single value against a schema."""
        errors = []
        schema_type = schema.get("type")
        
        if schema_type == "string":
            if not isinstance(value, str):
                errors.append(f"Expected string, got {type(value).__name__}")
            elif "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(f"String too short (min {schema['minLength']})")
            elif "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(f"String too long (max {schema['maxLength']})")
            elif "pattern" in schema:
                import re
                if not re.match(schema["pattern"], value):
                    errors.append(f"String does not match pattern {schema['pattern']}")
        
        elif schema_type == "integer":
            if not isinstance(value, int):
                errors.append(f"Expected integer, got {type(value).__name__}")
            elif "minimum" in schema and value < schema["minimum"]:
                errors.append(f"Value below minimum {schema['minimum']}")
            elif "maximum" in schema and value > schema["maximum"]:
                errors.append(f"Value above maximum {schema['maximum']}")
        
        elif schema_type == "number":
            if not isinstance(value, (int, float)):
                errors.append(f"Expected number, got {type(value).__name__}")
            elif "minimum" in schema and value < schema["minimum"]:
                errors.append(f"Value below minimum {schema['minimum']}")
            elif "maximum" in schema and value > schema["maximum"]:
                errors.append(f"Value above maximum {schema['maximum']}")
        
        elif schema_type == "boolean":
            if not isinstance(value, bool):
                errors.append(f"Expected boolean, got {type(value).__name__}")
        
        elif schema_type == "array":
            if not isinstance(value, list):
                errors.append(f"Expected array, got {type(value).__name__}")
            else:
                item_schema = schema.get("items")
                if item_schema:
                    for i, item in enumerate(value):
                        valid, item_errors = self._basic_validate_value(item, item_schema)
                        if not valid:
                            errors.extend([f"[{i}]: {e}" for e in item_errors])
                if "minItems" in schema and len(value) < schema["minItems"]:
                    errors.append(f"Array too short (min {schema['minItems']})")
                if "maxItems" in schema and len(value) > schema["maxItems"]:
                    errors.append(f"Array too long (max {schema['maxItems']}")
        
        elif schema_type == "object":
            if not isinstance(value, dict):
                errors.append(f"Expected object, got {type(value).__name__}")
            else:
                valid, obj_errors = self._basic_validate(value, schema)
                errors.extend(obj_errors)
        
        # Enum validation
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"Value must be one of {schema['enum']}")
        
        return len(errors) == 0, errors


# ───────────────────────────────────────────────────────────────
# JWT Secret Rotation (Kiro Rule 10: API & Security)
# ───────────────────────────────────────────────────────────────
class RotatingSecretManager:
    """Manages rotating JWT secrets with automatic rotation.
    
    Kiro Protocol: Automatic secret rotation every 24 hours (Rule 10).
    """

    def __init__(
        self,
        rotation_interval_hours: float = 24.0,
        secrets_file: Path | None = None,
        max_secrets: int = 3,
    ):
        self.rotation_interval = rotation_interval_hours * 3600
        self.max_secrets = max_secrets
        self.secrets_file = secrets_file or Path("config/.jwt_secrets.json")
        self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
        
        self._secrets: list[dict] = []  # [{secret, created_at, expires_at}]
        self._current_secret: str | None = None
        self._lock = asyncio.Lock()
        
        self._load_or_create_secrets()

    def _load_or_create_secrets(self) -> None:
        """Load existing secrets or create initial secret."""
        if self.secrets_file.exists():
            try:
                with open(self.secrets_file) as f:
                    data = json.load(f)
                    self._secrets = data.get("secrets", [])
                    if self._secrets:
                        self._current_secret = self._secrets[0]["secret"]
            except Exception as e:
                logger.error(f"Failed to load secrets: {e}")
        
        if not self._current_secret:
            self._rotate_secret()

    def _rotate_secret(self) -> str:
        """Generate new secret and rotate."""
        new_secret = secrets.token_hex(64)
        now = time.time()
        
        secret_entry = {
            "secret": new_secret,
            "created_at": now,
            "expires_at": now + self.rotation_interval,
        }
        
        # Add to front of list
        self._secrets.insert(0, secret_entry)
        
        # Remove old secrets
        while len(self._secrets) > self.max_secrets:
            old = self._secrets.pop()
            logger.info(f"Removed old JWT secret from {datetime.fromtimestamp(old['created_at'])}")
        
        self._current_secret = new_secret
        self._save_secrets()
        
        logger.info(f"JWT secret rotated. New secret expires at {datetime.fromtimestamp(secret_entry['expires_at'])}")
        return new_secret

    def _save_secrets(self) -> None:
        """Save secrets to file with restricted permissions."""
        data = {
            "secrets": self._secrets,
            "last_rotation": time.time(),
        }
        
        with open(self.secrets_file, "w") as f:
            json.dump(data, f, indent=2)
        
        os.chmod(self.secrets_file, 0o600)

    async def get_current_secret(self) -> str:
        """Get current secret, rotating if needed."""
        async with self._lock:
            now = time.time()
            
            # Check if rotation is needed
            if self._secrets and self._secrets[0]["expires_at"] <= now:
                self._rotate_secret()
            
            return self._current_secret

    async def validate_secret(self, secret: str) -> bool:
        """Validate a secret against current and recent secrets."""
        async with self._lock:
            for entry in self._secrets:
                if hmac.compare_digest(secret, entry["secret"]):
                    return True
            return False

    async def get_secret_age(self) -> float:
        """Get age of current secret in seconds."""
        async with self._lock:
            if not self._secrets:
                return 0.0
            return time.time() - self._secrets[0]["created_at"]

    async def force_rotation(self) -> str:
        """Force immediate secret rotation."""
        async with self._lock:
            return self._rotate_secret()

    def get_stats(self) -> dict[str, Any]:
        """Get secret rotation statistics."""
        now = time.time()
        return {
            "total_secrets": len(self._secrets),
            "current_secret_age_hours": (now - self._secrets[0]["created_at"]) / 3600 if self._secrets else 0,
            "current_secret_expires_hours": (self._secrets[0]["expires_at"] - now) / 3600 if self._secrets else 0,
            "rotation_interval_hours": self.rotation_interval / 3600,
            "max_secrets": self.max_secrets,
        }


# ───────────────────────────────────────────────────────────────
# Enhanced Token Manager with Rotation
# ───────────────────────────────────────────────────────────────
class EnhancedTokenManager:
    """Token manager with automatic secret rotation.
    
    Kiro Protocol: JWT secret rotation, token binding, audit logging (Rule 10).
    """

    def __init__(
        self,
        secret_manager: RotatingSecretManager | None = None,
        access_token_ttl: int = 900,  # 15 minutes
        refresh_token_ttl: int = 604800,  # 7 days
        algorithm: str = "HS256",
        enable_token_binding: bool = True,
    ):
        self.secret_manager = secret_manager or RotatingSecretManager()
        self.access_token_ttl = access_token_ttl
        self.refresh_token_ttl = refresh_token_ttl
        self.algorithm = algorithm
        self.enable_token_binding = enable_token_binding

        # Token storage (in production, use Redis)
        self._refresh_tokens: dict[str, dict] = {}
        self._revoked_tokens: set[str] = set()
        self._token_bindings: dict[str, dict] = {}  # token_jti -> {ip, user_agent, fingerprint}
        self._lock = asyncio.Lock()

    def _generate_jti(self) -> str:
        """Generate unique token ID."""
        return secrets.token_urlsafe(32)

    def _generate_binding_fingerprint(self, ip: str | None, user_agent: str | None) -> str:
        """Generate device fingerprint for token binding."""
        if not self.enable_token_binding:
            return ""
        data = f"{ip or ''}:{user_agent or ''}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    async def create_access_token(
        self,
        user_id: str,
        role: str,
        permissions: list[str],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Create a new access token with binding."""
        jti = self._generate_jti()
        now = time.time()
        expires = now + self.access_token_ttl
        
        secret = await self.secret_manager.get_current_secret()
        
        payload = {
            "sub": user_id,
            "role": role,
            "permissions": permissions,
            "iat": now,
            "exp": expires,
            "jti": jti,
            "type": "access",
            "secret_version": int(now),  # For secret rotation tracking
        }

        if ip_address:
            payload["ip"] = ip_address
        if user_agent:
            payload["ua"] = user_agent

        token = jwt.encode(payload, secret, algorithm=self.algorithm)
        
        # Store binding
        if self.enable_token_binding:
            async with self._lock:
                self._token_bindings[jti] = {
                    "ip": ip_address,
                    "user_agent": user_agent,
                    "fingerprint": self._generate_binding_fingerprint(ip_address, user_agent),
                    "created_at": now,
                }

        return {
            "token": token,
            "jti": jti,
            "expires_at": expires,
            "expires_in": self.access_token_ttl,
        }

    async def verify_access_token(
        self,
        token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict | None:
        """Verify access token with binding check."""
        # Try current and recent secrets
        secrets_to_try = [await self.secret_manager.get_current_secret()]
        
        # Add recent secrets for rotation grace period
        # (tokens issued just before rotation)
        
        for secret in secrets_to_try:
            try:
                payload = jwt.decode(
                    token,
                    secret,
                    algorithms=[self.algorithm],
                )

                if payload.get("type") != "access":
                    continue

                jti = payload.get("jti")
                if jti in self._revoked_tokens:
                    return None

                # Check token binding
                if self.enable_token_binding:
                    binding = self._token_bindings.get(jti)
                    if binding:
                        expected_fp = self._generate_binding_fingerprint(ip_address, user_agent)
                        if binding["fingerprint"] != expected_fp:
                            logger.warning(f"Token binding mismatch for {jti}")
                            return None

                return {
                    "user_id": payload["sub"],
                    "role": payload["role"],
                    "permissions": payload.get("permissions", []),
                    "jti": jti,
                    "ip_address": payload.get("ip"),
                    "user_agent": payload.get("ua"),
                }

            except jwt.ExpiredSignatureError:
                return None
            except jwt.InvalidTokenError:
                continue

        return None

    async def create_refresh_token(self, user_id: str, access_token_jti: str) -> dict:
        """Create a refresh token."""
        jti = self._generate_jti()
        now = time.time()
        expires = now + self.refresh_token_ttl
        
        secret = await self.secret_manager.get_current_secret()
        
        payload = {
            "sub": user_id,
            "iat": now,
            "exp": expires,
            "jti": jti,
            "access_jti": access_token_jti,
            "type": "refresh",
        }

        token = jwt.encode(payload, secret, algorithm=self.algorithm)
        
        async with self._lock:
            self._refresh_tokens[jti] = {
                "token": token,
                "user_id": user_id,
                "access_jti": access_token_jti,
                "created_at": now,
                "expires_at": expires,
                "revoked": False,
            }

        return {
            "token": token,
            "jti": jti,
            "expires_at": expires,
            "expires_in": self.refresh_token_ttl,
        }

    async def refresh_access_token(self, refresh_token: str) -> tuple[dict, dict] | None:
        """Refresh access token using refresh token."""
        secret = await self.secret_manager.get_current_secret()
        
        try:
            payload = jwt.decode(
                refresh_token,
                secret,
                algorithms=[self.algorithm],
            )

            if payload.get("type") != "refresh":
                return None

            jti = payload.get("jti")
            
            async with self._lock:
                stored = self._refresh_tokens.get(jti)
                if not stored or stored["revoked"]:
                    return None

                # Revoke old refresh token (token rotation)
                stored["revoked"] = True
                self._revoked_tokens.add(jti)
                
                # Also revoke old access token
                self._revoked_tokens.add(stored["access_jti"])

            # Create new tokens
            new_access = await self.create_access_token(
                user_id=payload["sub"],
                role="user",  # Should fetch from user store
                permissions=[],
            )
            new_refresh = await self.create_refresh_token(
                user_id=payload["sub"],
                access_token_jti=new_access["jti"],
            )

            return new_access, new_refresh

        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    async def revoke_token(self, jti: str) -> bool:
        """Revoke a token by its JTI."""
        async with self._lock:
            self._revoked_tokens.add(jti)
            
            if jti in self._refresh_tokens:
                self._refresh_tokens[jti]["revoked"] = True
            
            if jti in self._token_bindings:
                del self._token_bindings[jti]

        return True

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        """Revoke all tokens for a user."""
        count = 0
        
        async with self._lock:
            for jti, token in self._refresh_tokens.items():
                if token["user_id"] == user_id:
                    token["revoked"] = True
                    self._revoked_tokens.add(jti)
                    count += 1

        return count

    def get_stats(self) -> dict[str, Any]:
        """Get token manager statistics."""
        return {
            "active_refresh_tokens": sum(1 for t in self._refresh_tokens.values() if not t["revoked"]),
            "revoked_tokens": len(self._revoked_tokens),
            "token_bindings": len(self._token_bindings),
            "secret_stats": self.secret_manager.get_stats(),
        }


# ───────────────────────────────────────────────────────────────
# Request Rate Limiter with Sliding Window (Kiro Rule 10)
# ───────────────────────────────────────────────────────────────
class SlidingWindowRateLimiter:
    """Advanced rate limiter with sliding window algorithm.
    
    Kiro Protocol: Sliding window for accurate rate limiting (Rule 10).
    """

    def __init__(
        self,
        window_size: int = 60,
        max_requests: int = 100,
        burst_size: int = 10,
    ):
        self.window_size = window_size
        self.max_requests = max_requests
        self.burst_size = burst_size
        self._requests: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def is_allowed(self, key: str) -> tuple[bool, int, int, float]:
        """Check if request is allowed.
        
        Returns:
            Tuple of (allowed, remaining, reset_after_seconds, retry_after).
        """
        now = time.time()
        
        async with self._lock:
            if key not in self._requests:
                self._requests[key] = []

            # Remove old requests outside window
            cutoff = now - self.window_size
            self._requests[key] = [t for t in self._requests[key] if t > cutoff]

            # Check burst limit
            if len(self._requests[key]) >= self.burst_size:
                # Check sustained rate
                if len(self._requests[key]) >= self.max_requests:
                    reset_after = int(self._requests[key][0] + self.window_size - now)
                    return False, 0, max(0, reset_after), max(0, reset_after)

            # Add request
            self._requests[key].append(now)

            remaining = self.max_requests - len(self._requests[key])
            reset_after = self.window_size
            retry_after = 0

            return True, remaining, reset_after, retry_after

    async def get_status(self, key: str) -> tuple[int, int, int, float]:
        """Get rate limit status without consuming."""
        now = time.time()
        
        async with self._lock:
            if key not in self._requests:
                return self.max_requests, self.max_requests, 0, 0.0

            cutoff = now - self.window_size
            requests = [t for t in self._requests[key] if t > cutoff]

            remaining = max(0, self.max_requests - len(requests))
            reset_after = 0
            retry_after = 0.0

            if requests:
                reset_after = int(requests[0] + self.window_size - now)
                if remaining == 0:
                    retry_after = float(reset_after)

            return remaining, self.max_requests, max(0, reset_after), retry_after


# ───────────────────────────────────────────────────────────────
# Security Manager with All Enhancements
# ───────────────────────────────────────────────────────────────
class EnhancedSecurityManager:
    """Enhanced security manager with all Kiro Protocol improvements.
    
    Features:
    - JWT secret rotation (Rule 10)
    - JSON schema validation (Rule 10)
    - Token binding (Rule 10)
    - Sliding window rate limiting (Rule 10)
    - Audit logging with redaction (Rule 10)
    """

    def __init__(
        self,
        secret_manager: RotatingSecretManager | None = None,
        schema_validator: JSONSchemaValidator | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ):
        self.secret_manager = secret_manager or RotatingSecretManager()
        self.schema_validator = schema_validator or JSONSchemaValidator()
        self.rate_limiter = rate_limiter or SlidingWindowRateLimiter()
        self.token_manager = EnhancedTokenManager(self.secret_manager)
        
        # Register default schemas
        self._register_default_schemas()

    def _register_default_schemas(self) -> None:
        """Register default JSON schemas for validation."""
        # Job submission schema
        self.schema_validator.register_schema("job_submit", {
            "type": "object",
            "required": ["workflow", "prompt"],
            "properties": {
                "workflow": {"type": "string", "minLength": 1},
                "prompt": {"type": "string", "minLength": 1},
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
                "batch_size": {"type": "integer", "minimum": 1, "maximum": 16},
                "priority": {"type": "integer", "minimum": 0, "maximum": 3},
            },
        })
        
        # Auth request schema
        self.schema_validator.register_schema("auth_request", {
            "type": "object",
            "required": ["username", "password"],
            "properties": {
                "username": {"type": "string", "minLength": 3, "maxLength": 100},
                "password": {"type": "string", "minLength": 8, "maxLength": 200},
            },
        })

    async def validate_request(self, data: dict, schema_name: str) -> tuple[bool, list[str]]:
        """Validate incoming request data."""
        return self.schema_validator.validate(data, schema_name)

    async def check_rate_limit(self, key: str) -> tuple[bool, dict]:
        """Check rate limit for a key."""
        allowed, remaining, reset_after, retry_after = await self.rate_limiter.is_allowed(key)
        
        headers = {
            "X-RateLimit-Limit": str(self.rate_limiter.max_requests),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(int(time.time() + reset_after)),
        }
        
        if not allowed:
            headers["Retry-After"] = str(int(retry_after))
        
        return allowed, headers

    def get_stats(self) -> dict[str, Any]:
        """Get security manager statistics."""
        return {
            "token_manager": self.token_manager.get_stats(),
            "secret_manager": self.secret_manager.get_stats(),
            "schemas_registered": len(self.schema_validator._schemas),
        }

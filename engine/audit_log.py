"""Audit log for all API operations.

Addresses Issue #38: Audit log — immutable record of all API operations.
"""
import logging
import json
from datetime import datetime
from pathlib import Path

# Use a separate logger for the audit log to ensure immutability via append-only files
audit_logger = logging.getLogger("audit_log")
audit_logger.setLevel(logging.INFO)

# Setup append-only file handler
audit_file = Path("logs/audit.log")
audit_file.parent.mkdir(parents=True, exist_ok=True)
handler = logging.FileHandler(audit_file, mode='a')
handler.setFormatter(logging.Formatter('%(message)s'))
audit_logger.addHandler(handler)
audit_logger.propagate = False

def log_audit_event(action: str, user_id: str, resource: str, details: dict):
    """Log an API action to the audit log."""
    event = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "user_id": user_id,
        "resource": resource,
        "details": details
    }
    audit_logger.info(json.dumps(event))

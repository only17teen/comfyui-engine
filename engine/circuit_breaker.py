"""Circuit Breaker for ComfyUI client.

Addresses Issue #25: Circuit breaker for ComfyUI client (fail fast, auto-recover).
"""
import time
import logging

logger = logging.getLogger(__name__)

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        
    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"
            logger.error(f"Circuit Breaker OPEN. Too many failures ({self.failures}).")

    def record_success(self):
        self.failures = 0
        self.state = "CLOSED"
        logger.info("Circuit Breaker CLOSED and reset after success.")

    def can_request(self) -> bool:
        if self.state == "CLOSED":
            return True
            
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
                logger.warning("Circuit Breaker HALF_OPEN. Attempting recovery.")
                return True
            return False
            
        # HALF_OPEN
        return True

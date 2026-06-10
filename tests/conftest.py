import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: mark test as async")

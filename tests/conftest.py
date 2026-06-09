import pytest

# Make all async test functions work without explicit @pytest.mark.asyncio
# by using asyncio_mode = "auto" in pyproject.toml (already set).
# This conftest ensures the asyncio event loop is available globally.

"""OpenAPI 3.1 specification for ComfyUI Engine REST API v5.0"""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from api_server import RESTAPIServer

# Create a temporary instance to generate OpenAPI schema
server = RESTAPIServer(enable_auth=False)
app = server.app

# Generate OpenAPI schema
openapi_schema = get_openapi(
    title="ComfyUI Engine API",
    version="5.0.0",
    description="""
    Production-grade REST API for ComfyUI Engine - AI model generation pipeline.

    Features:
    - Job submission and management
    - Real-time WebSocket streaming
    - Model management
    - Queue operations
    - Metrics and monitoring
    - Webhook support
    - API key authentication
    - Rate limiting

    ## Authentication
    All endpoints (except health checks) require API key authentication via Bearer token:
    ```
    Authorization: Bearer your-api-key
    ```

    ## WebSocket Streaming
    Connect to `/ws` for real-time job progress updates.

    ## Rate Limiting
    API requests are rate-limited per key. Check response headers for limits.
    """,
    routes=app.routes,
)

# Save to file
import json

with open("/workspace/comfyui_engine/docs/openapi.json", "w") as f:
    json.dump(openapi_schema, f, indent=2, ensure_ascii=False)

print("OpenAPI spec generated successfully")
print(f"Title: {openapi_schema['info']['title']}")
print(f"Version: {openapi_schema['info']['version']}")
print(f"Paths: {len(openapi_schema['paths'])}")
print(f"Components: {list(openapi_schema.get('components', {}).keys())}")

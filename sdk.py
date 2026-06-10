"""Python SDK for ComfyUI Engine API.

Addresses Issue #45: Python SDK client library.
"""
from __future__ import annotations

import httpx
from typing import Any, Dict, List, Optional


class ComfyUIEngineClient:
    """Client for interacting with the ComfyUI Engine API."""

    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(base_url=self.base_url, headers=headers)

    def get_status(self) -> Dict[str, Any]:
        """Get engine status and metrics."""
        resp = self.client.get("/api/v1/system/status")
        resp.raise_for_status()
        return resp.json()

    def submit_job(self, workflow: Dict[str, Any], priority: int = 1) -> Dict[str, Any]:
        """Submit a new job."""
        resp = self.client.post("/api/v1/jobs", json={"workflow": workflow, "priority": priority})
        resp.raise_for_status()
        return resp.json()

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Get job status and results."""
        resp = self.client.get(f"/api/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a running or queued job."""
        resp = self.client.delete(f"/api/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()

    def list_jobs(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List recent jobs."""
        params = {"limit": limit}
        if status:
            params["status"] = status
        resp = self.client.get("/api/v1/jobs", params=params)
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class AsyncComfyUIEngineClient:
    """Async Client for interacting with the ComfyUI Engine API."""

    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.AsyncClient(base_url=self.base_url, headers=headers)

    async def get_status(self) -> Dict[str, Any]:
        resp = await self.client.get("/api/v1/system/status")
        resp.raise_for_status()
        return resp.json()

    async def submit_job(self, workflow: Dict[str, Any], priority: int = 1) -> Dict[str, Any]:
        resp = await self.client.post("/api/v1/jobs", json={"workflow": workflow, "priority": priority})
        resp.raise_for_status()
        return resp.json()

    async def get_job(self, job_id: str) -> Dict[str, Any]:
        resp = await self.client.get(f"/api/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        resp = await self.client.delete(f"/api/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()

    async def list_jobs(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"limit": limit}
        if status:
            params["status"] = status
        resp = await self.client.get("/api/v1/jobs", params=params)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

"""Job DAG dependencies logic.

Addresses Issue #28: Job DAG — dependencies between jobs (job B starts after job A).
"""
import asyncio
from typing import Dict, List, Set

class DAGManager:
    def __init__(self):
        self.dependencies: Dict[str, Set[str]] = {} # job_id -> set of required job_ids
        self.completed_jobs: Set[str] = set()
        self.waiting_jobs: Dict[str, asyncio.Event] = {} # job_id -> Event to wait on

    def register_job(self, job_id: str, depends_on: List[str] = None):
        if depends_on:
            self.dependencies[job_id] = set(depends_on)
        self.waiting_jobs[job_id] = asyncio.Event()

    async def wait_for_dependencies(self, job_id: str):
        """Block until all dependencies are completed."""
        deps = self.dependencies.get(job_id, set())
        for dep in deps:
            if dep not in self.completed_jobs:
                if dep not in self.waiting_jobs:
                    self.waiting_jobs[dep] = asyncio.Event()
                await self.waiting_jobs[dep].wait()

    def mark_completed(self, job_id: str):
        """Mark a job as completed and notify waiters."""
        self.completed_jobs.add(job_id)
        if job_id in self.waiting_jobs:
            self.waiting_jobs[job_id].set()

dag_manager = DAGManager()

from __future__ import annotations
import asyncio, sys, pytest
from pathlib import Path
from engine.worker_pool import PoolExhaustedError, WorkerPool

ECHO = """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    req = json.loads(line)
    print(json.dumps({"id":req["id"],"result":req.get("params",{}),"error":None}), flush=True)
"""

@pytest.fixture
def worker(tmp_path: Path) -> Path:
    p = tmp_path/"w.py"; p.write_text(ECHO); return p

@pytest.mark.asyncio
async def test_starts_n_workers(worker: Path) -> None:
    async with WorkerPool([sys.executable, str(worker)], size=3) as pool:
        assert pool.size == 3 and pool.alive_count == 3

@pytest.mark.asyncio
async def test_basic_call(worker: Path) -> None:
    async with WorkerPool([sys.executable, str(worker)], size=2) as pool:
        assert await pool.call("echo",{"k":"v"}) == {"k":"v"}

@pytest.mark.asyncio
async def test_concurrent(worker: Path) -> None:
    async with WorkerPool([sys.executable, str(worker)], size=4) as pool:
        results = await asyncio.gather(*[pool.call("echo",{"n":i}) for i in range(20)])
    assert {r["n"] for r in results} == set(range(20))

@pytest.mark.asyncio
async def test_exhausted(worker: Path) -> None:
    pool = WorkerPool([sys.executable, str(worker)], size=2)
    with pytest.raises(PoolExhaustedError): await pool.call("echo",{})

@pytest.mark.asyncio
async def test_context_manager(worker: Path) -> None:
    async with WorkerPool([sys.executable, str(worker)], size=2) as pool:
        assert pool.alive_count == 2
    assert pool.alive_count == 0

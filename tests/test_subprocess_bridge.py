from __future__ import annotations
import asyncio, sys, pytest
from pathlib import Path
from engine.subprocess_bridge import BridgeError, BridgeTimeoutError, SubprocessBridge

ECHO = """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    req = json.loads(line)
    if req.get("method") == "fail": resp = {"id":req["id"],"result":None,"error":"intentional"}
    elif req.get("method") == "slow": import time; time.sleep(5); resp = {"id":req["id"],"result":"done","error":None}
    else: resp = {"id":req["id"],"result":req.get("params",{}),"error":None}
    print(json.dumps(resp), flush=True)
"""

@pytest.fixture
def worker(tmp_path: Path) -> Path:
    p = tmp_path / "w.py"; p.write_text(ECHO); return p

@pytest.mark.asyncio
async def test_basic_call(worker: Path) -> None:
    async with SubprocessBridge([sys.executable, str(worker)]) as b:
        assert await b.call("echo", {"k":"v"}) == {"k":"v"}

@pytest.mark.asyncio
async def test_concurrent(worker: Path) -> None:
    async with SubprocessBridge([sys.executable, str(worker)]) as b:
        results = await asyncio.gather(*[b.call("echo",{"n":i}) for i in range(5)])
    assert {r["n"] for r in results} == {0,1,2,3,4}

@pytest.mark.asyncio
async def test_error_raises(worker: Path) -> None:
    async with SubprocessBridge([sys.executable, str(worker)]) as b:
        with pytest.raises(BridgeError): await b.call("fail",{})

@pytest.mark.asyncio
async def test_timeout_raises(worker: Path) -> None:
    async with SubprocessBridge([sys.executable, str(worker)], default_timeout=0.05) as b:
        with pytest.raises(BridgeTimeoutError): await b.call("slow",{})

@pytest.mark.asyncio
async def test_is_running(worker: Path) -> None:
    async with SubprocessBridge([sys.executable, str(worker)]) as b:
        assert b.is_running
    assert not b.is_running

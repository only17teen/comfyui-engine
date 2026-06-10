from __future__ import annotations
import asyncio
import uuid
import pytest
from engine.shm_channel import ShmChannel, HEADER_SIZE


def uname() -> str:
    return f"test_{uuid.uuid4().hex[:8]}"


def test_header_64_bytes() -> None:
    assert HEADER_SIZE == 64


def test_write_read() -> None:
    name = uname()
    with ShmChannel.create(name, size=4096) as p:
        c = ShmChannel.open(name, size=4096)
        try:
            assert c.read_if_new() is None
            p.write(b"hello")
            assert c.read_if_new() == b"hello"
            assert c.read_if_new() is None
        finally:
            c.close()


def test_overwrite() -> None:
    name = uname()
    with ShmChannel.create(name, size=4096) as p:
        c = ShmChannel.open(name, size=4096)
        try:
            p.write(b"first")
            p.write(b"second")
            assert c.read_if_new() == b"second"
        finally:
            c.close()


def test_too_large_raises() -> None:
    name = uname()
    with ShmChannel.create(name, size=64) as ch, pytest.raises(ValueError):
        ch.write(b"X" * 65)


@pytest.mark.asyncio
async def test_aread() -> None:
    name = uname()
    with ShmChannel.create(name, size=4096) as p:
        c = ShmChannel.open(name, size=4096)
        try:

            async def delayed() -> None:
                await asyncio.sleep(0.02)
                p.write(b"async")

            result, _ = await asyncio.gather(c.aread(poll_interval=0.005), delayed())
            assert result == b"async"
        finally:
            c.close()

from __future__ import annotations
import asyncio
import ctypes
from multiprocessing.shared_memory import SharedMemory

__all__ = ["ShmChannel", "ShmHeader"]


class ShmHeader(ctypes.Structure):
    """C-compatible ctypes structure for the shared-memory ring-buffer header."""

    _pack_ = 8
    _fields_ = [
        ("write_seq", ctypes.c_uint64),
        ("read_seq", ctypes.c_uint64),
        ("data_len", ctypes.c_uint64),
        ("_pad", ctypes.c_uint8 * 40),
    ]


assert ctypes.sizeof(ShmHeader) == 64
HEADER_SIZE = ctypes.sizeof(ShmHeader)


class ShmChannel:
    """Zero-copy SPSC shared-memory channel compatible with C++ mmap layout."""

    def __init__(
        self, name: str, size: int = 1024 * 1024, *, create: bool = False
    ) -> None:
        self._name = name
        self._data_size = size
        self._shm = SharedMemory(name=name, create=create, size=HEADER_SIZE + size)
        self._header: ShmHeader = ShmHeader.from_buffer(self._shm.buf)  # type: ignore[arg-type]
        self._data_view: memoryview = memoryview(self._shm.buf)[HEADER_SIZE:]
        self._owned = create

    @classmethod
    def create(cls, name: str, size: int = 1024 * 1024) -> ShmChannel:
        """Zero-copy SPSC shared-memory channel compatible with C++ mmap layout."""
        ch = cls(name, size, create=True)
        ch._header.write_seq = ch._header.read_seq = ch._header.data_len = 0
        return ch

    @classmethod
    def open(cls, name: str, size: int = 1024 * 1024) -> ShmChannel:
        """Zero-copy SPSC shared-memory channel compatible with C++ mmap layout."""
        return cls(name, size, create=False)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        n = len(data)
        if n > self._data_size:
            raise ValueError(f"payload too large: {n} > {self._data_size}")
        self._data_view[:n] = data
        self._header.data_len = n
        self._header.write_seq = self._header.write_seq + 1

    def read_if_new(self) -> bytes | None:
        ws, rs, n = self._header.write_seq, self._header.read_seq, self._header.data_len
        if ws == rs:
            return None
        payload = bytes(self._data_view[:n])
        self._header.read_seq = ws
        return payload

    async def aread(self, poll_interval: float = 0.001) -> bytes:
        while True:
            d = self.read_if_new()
            if d is not None:
                return d
            await asyncio.sleep(poll_interval)

    def close(self) -> None:
        try:
            self._data_view.release()
        except Exception:
            pass
        self._shm.close()

    def unlink(self) -> None:
        try:
            self._shm.unlink()
        except Exception:
            pass

    def __enter__(self) -> ShmChannel:
        """Zero-copy SPSC shared-memory channel compatible with C++ mmap layout."""
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
        self.unlink() if self._owned else None

    @property
    def name(self) -> str:
        return self._name

    @property
    def data_size(self) -> int:
        return self._data_size

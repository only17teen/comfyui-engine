from __future__ import annotations
import struct
from typing import Any
from engine.shm_channel import ShmChannel
__all__ = ["TypedChannel","DTYPE_MAP"]
DTYPE_MAP: dict[str, tuple[str, int]] = {"float32":("f",4),"float64":("d",8),"int32":("i",4),"uint8":("B",1)}

class TypedChannel:
    def __init__(self, name: str, dtype: str, length: int, *, create: bool=False) -> None:
        if dtype not in DTYPE_MAP: raise ValueError(f"unsupported dtype {dtype!r}")
        self._dtype = dtype; self._length = length
        fmt_char, elem_size = DTYPE_MAP[dtype]
        self._fmt = f"<{length}{fmt_char}"; self._byte_size = length*elem_size
        self._channel = ShmChannel(name, size=self._byte_size, create=create)
        try:
            import numpy as np
            self._np: Any = np; self._np_dtype = {"float32":np.float32,"float64":np.float64,"int32":np.int32,"uint8":np.uint8}[dtype]
        except ImportError:
            self._np = None; self._np_dtype = None
    @classmethod
    def create(cls, name: str, dtype: str, length: int) -> "TypedChannel": return cls(name, dtype, length, create=True)
    @classmethod
    def open(cls, name: str, dtype: str, length: int) -> "TypedChannel": return cls(name, dtype, length, create=False)
    def write_array(self, data: Any) -> None:
        if self._np is not None and isinstance(data, self._np.ndarray):
            payload = data.astype(self._np_dtype, copy=False).tobytes()
        else:
            payload = struct.pack(self._fmt, *data)
        self._channel.write(payload)
    def read_array(self) -> Any:
        raw = self._channel.read_if_new()
        if raw is None: return None
        return self._np.frombuffer(raw, dtype=self._np_dtype) if self._np is not None else list(struct.unpack(self._fmt, raw))
    async def aread_array(self, poll_interval: float=0.001) -> Any:
        import asyncio
        while True:
            r = self.read_array()
            if r is not None: return r
            await asyncio.sleep(poll_interval)
    def close(self) -> None: self._channel.close()
    def unlink(self) -> None: self._channel.unlink()
    def __enter__(self) -> "TypedChannel": return self
    def __exit__(self, *_: object) -> None: self._channel.__exit__()
    @property
    def dtype(self) -> str: return self._dtype
    @property
    def length(self) -> int: return self._length

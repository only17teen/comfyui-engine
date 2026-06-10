"""JSON vs msgpack serialization benchmark."""
from __future__ import annotations
import json
FLOATS = {"values":[float(i)*1.23456 for i in range(100)]}
JSON_BYTES = json.dumps(FLOATS).encode()
def test_json_encode(benchmark) -> None: benchmark(json.dumps, FLOATS)
def test_json_decode(benchmark) -> None: benchmark(json.loads, JSON_BYTES)
try:
    import msgpack as mp
    MP_BYTES = mp.packb(FLOATS, use_bin_type=True)
    def test_msgpack_encode(benchmark) -> None: benchmark(mp.packb, FLOATS, use_bin_type=True)
    def test_msgpack_decode(benchmark) -> None: benchmark(mp.unpackb, MP_BYTES, raw=False)
    def test_msgpack_smaller_than_json() -> None: assert len(MP_BYTES) < len(JSON_BYTES)
except ImportError: pass

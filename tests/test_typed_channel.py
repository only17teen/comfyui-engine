from __future__ import annotations
import uuid, pytest
from engine.typed_channel import TypedChannel, DTYPE_MAP

def uname() -> str: return f"tc_{uuid.uuid4().hex[:8]}"

def test_dtype_map() -> None:
    assert set(DTYPE_MAP) == {"float32","float64","int32","uint8"}

def test_float32_roundtrip() -> None:
    name = uname()
    data = [1.0,2.5,3.75,-0.5]
    with TypedChannel.create(name,"float32",4) as p:
        c = TypedChannel.open(name,"float32",4)
        try: p.write_array(data); assert list(c.read_array()) == pytest.approx(data, abs=1e-6)
        finally: c.close(); c.unlink()

def test_int32_roundtrip() -> None:
    name = uname()
    data = [10,-20,300,-4000]
    with TypedChannel.create(name,"int32",4) as p:
        c = TypedChannel.open(name,"int32",4)
        try: p.write_array(data); assert list(c.read_array()) == data
        finally: c.close(); c.unlink()

def test_read_none_when_empty() -> None:
    name = uname()
    with TypedChannel.create(name,"float32",4):
        c = TypedChannel.open(name,"float32",4)
        try: assert c.read_array() is None
        finally: c.close()

def test_invalid_dtype() -> None:
    with pytest.raises(ValueError): TypedChannel("x","complex128",10)

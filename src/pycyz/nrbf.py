"""
MS-NRBF (.NET Binary Remoting Format) stream deserializer.

Reads the binary serialization format used internally by CytoSense .cyz files.
No .NET runtime or external dependencies required.
"""

from __future__ import annotations

import struct
from typing import Any, BinaryIO

# ── Record Type constants ──────────────────────────────────────────────
RT_SERIALIZED_STREAM_HEADER = 0x00
RT_CLASS_WITH_ID = 0x01
RT_SYSTEM_CLASS_WITH_MEMBERS = 0x02
RT_CLASS_WITH_MEMBERS = 0x03
RT_SYSTEM_CLASS_WITH_MEMBERS_AND_TYPES = 0x04
RT_CLASS_WITH_MEMBERS_AND_TYPES = 0x05
RT_BINARY_OBJECT_STRING = 0x06
RT_BINARY_ARRAY = 0x07
RT_MEMBER_PRIMITIVE_TYPED = 0x08
RT_MEMBER_REFERENCE = 0x09
RT_OBJECT_NULL = 0x0A
RT_MESSAGE_END = 0x0B
RT_BINARY_LIBRARY = 0x0C
RT_OBJECT_NULL_MULTIPLE_256 = 0x0D
RT_OBJECT_NULL_MULTIPLE = 0x0E
RT_ARRAY_SINGLE_PRIMITIVE = 0x0F
RT_ARRAY_SINGLE_OBJECT = 0x10
RT_ARRAY_SINGLE_STRING = 0x11
RT_METHOD_CALL = 0x15
RT_METHOD_RETURN = 0x16

# Binary Type Enumeration
BT_PRIMITIVE = 0
BT_STRING = 1
BT_OBJECT = 2
BT_SYSTEM_CLASS = 3
BT_CLASS = 4
BT_OBJECT_ARRAY = 5
BT_STRING_ARRAY = 6
BT_PRIMITIVE_ARRAY = 7

# Primitive Type Enumeration
PT_BOOLEAN = 1
PT_BYTE = 2
PT_CHAR = 3
PT_DECIMAL = 5
PT_DOUBLE = 6
PT_INT16 = 7
PT_INT32 = 8
PT_INT64 = 9
PT_SBYTE = 10
PT_SINGLE = 11
PT_TIMESPAN = 12
PT_DATETIME = 13
PT_UINT16 = 14
PT_UINT32 = 15
PT_UINT64 = 16
PT_NULL = 17
PT_STRING = 18

PRIMITIVE_SIZES = {
    PT_BOOLEAN: 1, PT_BYTE: 1, PT_CHAR: 2, PT_SBYTE: 1,
    PT_INT16: 2, PT_UINT16: 2,
    PT_INT32: 4, PT_UINT32: 4, PT_SINGLE: 4,
    PT_INT64: 8, PT_UINT64: 8, PT_DOUBLE: 8, PT_DATETIME: 8,
    PT_TIMESPAN: 8,
}

# Binary Array Type Enumeration
BA_SINGLE = 0
BA_JAGGED = 1
BA_RECTANGULAR = 2

# Sentinel for MessageEnd
_UNRESOLVED = object()


class _MemberRef:
    """Placeholder for an unresolved forward reference."""

    __slots__ = ("ref_id",)

    def __init__(self, ref_id: int):
        self.ref_id = ref_id

    def __repr__(self) -> str:
        return f"MemberRef({self.ref_id})"


class _ClassInfo:
    """Stored class definition reused by ClassWithId records."""

    __slots__ = ("class_name", "member_names", "member_binary_types", "member_type_info")

    def __init__(
        self,
        class_name: str,
        member_names: list[str],
        member_binary_types: list[int],
        member_type_info: list[Any],
    ):
        self.class_name = class_name
        self.member_names = member_names
        self.member_binary_types = member_binary_types
        self.member_type_info = member_type_info


class NRBFReader:
    """
    Deserializes a single MS-NRBF stream into a Python object graph.

    .NET objects become dicts with a ``'__class__'`` key.
    Arrays become lists or bytes.
    Primitives become Python ints/floats/bools.
    """

    def __init__(self, stream: BinaryIO, limit: int | None = None):
        self.stream = stream
        self.start_pos = stream.tell()
        self.limit = limit
        self.objects: dict[int, Any] = {}
        self.class_defs: dict[int, _ClassInfo] = {}
        self.libraries: dict[int, str] = {}
        self.root_id: int = 0

    def read(self) -> Any:
        """Deserialize the stream and return the root object."""
        self._read_header()
        while True:
            value = self._read_value()
            if value is _UNRESOLVED:
                break
        self._resolve_all()
        return self.objects.get(self.root_id)

    # ── Low-level readers ──────────────────────────────────────────────

    def _read_bytes(self, n: int) -> bytes:
        data = self.stream.read(n)
        if len(data) < n:
            raise EOFError(
                f"Expected {n} bytes at offset 0x{self.stream.tell():x}, got {len(data)}"
            )
        return data

    def _read_int8(self) -> int:
        return struct.unpack("<b", self._read_bytes(1))[0]

    def _read_uint8(self) -> int:
        return struct.unpack("<B", self._read_bytes(1))[0]

    def _read_int16(self) -> int:
        return struct.unpack("<h", self._read_bytes(2))[0]

    def _read_uint16(self) -> int:
        return struct.unpack("<H", self._read_bytes(2))[0]

    def _read_int32(self) -> int:
        return struct.unpack("<i", self._read_bytes(4))[0]

    def _read_uint32(self) -> int:
        return struct.unpack("<I", self._read_bytes(4))[0]

    def _read_int64(self) -> int:
        return struct.unpack("<q", self._read_bytes(8))[0]

    def _read_uint64(self) -> int:
        return struct.unpack("<Q", self._read_bytes(8))[0]

    def _read_single(self) -> float:
        return struct.unpack("<f", self._read_bytes(4))[0]

    def _read_double(self) -> float:
        return struct.unpack("<d", self._read_bytes(8))[0]

    def _read_length_prefixed_string(self) -> str:
        """Read a .NET BinaryWriter 7-bit-encoded length-prefixed UTF-8 string."""
        length = 0
        shift = 0
        for _ in range(5):
            b = self._read_uint8()
            length |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        if length == 0:
            return ""
        return self._read_bytes(length).decode("utf-8")

    def _read_primitive(self, ptype: int) -> Any:
        if ptype == PT_BOOLEAN:
            return self._read_uint8() != 0
        elif ptype == PT_BYTE:
            return self._read_uint8()
        elif ptype == PT_CHAR:
            return chr(self._read_uint16())
        elif ptype == PT_DOUBLE:
            return self._read_double()
        elif ptype == PT_INT16:
            return self._read_int16()
        elif ptype == PT_INT32:
            return self._read_int32()
        elif ptype == PT_INT64:
            return self._read_int64()
        elif ptype == PT_SBYTE:
            return self._read_int8()
        elif ptype == PT_SINGLE:
            return self._read_single()
        elif ptype == PT_UINT16:
            return self._read_uint16()
        elif ptype == PT_UINT32:
            return self._read_uint32()
        elif ptype == PT_UINT64:
            return self._read_uint64()
        elif ptype == PT_DATETIME:
            return self._read_int64()  # raw ticks
        elif ptype == PT_TIMESPAN:
            return self._read_int64()  # raw ticks
        elif ptype == PT_DECIMAL:
            return self._read_length_prefixed_string()
        elif ptype == PT_STRING:
            return self._read_length_prefixed_string()
        else:
            raise ValueError(f"Unknown primitive type {ptype}")

    # ── Record-level readers ───────────────────────────────────────────

    def _read_header(self) -> None:
        rt = self._read_uint8()
        if rt != RT_SERIALIZED_STREAM_HEADER:
            raise ValueError(f"Expected SerializedStreamHeader (0x00), got 0x{rt:02x}")
        self.root_id = self._read_int32()
        _header_id = self._read_int32()
        _major = self._read_int32()
        _minor = self._read_int32()

    def _read_value(self) -> Any:
        if self.limit is not None:
            if self.stream.tell() - self.start_pos >= self.limit:
                return _UNRESOLVED

        rt = self._read_uint8()

        if rt == RT_CLASS_WITH_MEMBERS_AND_TYPES:
            return self._read_class_with_members_and_types()
        elif rt == RT_SYSTEM_CLASS_WITH_MEMBERS_AND_TYPES:
            return self._read_system_class_with_members_and_types()
        elif rt == RT_CLASS_WITH_ID:
            return self._read_class_with_id()
        elif rt == RT_BINARY_OBJECT_STRING:
            return self._read_binary_object_string()
        elif rt == RT_MEMBER_REFERENCE:
            return self._read_member_reference()
        elif rt == RT_OBJECT_NULL:
            return None
        elif rt == RT_OBJECT_NULL_MULTIPLE_256:
            count = self._read_uint8()
            return [None] * count
        elif rt == RT_OBJECT_NULL_MULTIPLE:
            count = self._read_int32()
            return [None] * count
        elif rt == RT_BINARY_LIBRARY:
            self._read_binary_library()
            return self._read_value()
        elif rt == RT_ARRAY_SINGLE_PRIMITIVE:
            return self._read_array_single_primitive()
        elif rt == RT_ARRAY_SINGLE_OBJECT:
            return self._read_array_single_object()
        elif rt == RT_ARRAY_SINGLE_STRING:
            return self._read_array_single_string()
        elif rt == RT_BINARY_ARRAY:
            return self._read_binary_array()
        elif rt == RT_MEMBER_PRIMITIVE_TYPED:
            ptype = self._read_uint8()
            return self._read_primitive(ptype)
        elif rt == RT_MESSAGE_END:
            return _UNRESOLVED
        else:
            raise ValueError(
                f"Unknown record type 0x{rt:02x} at offset 0x{self.stream.tell() - 1:x}"
            )

    def _read_binary_library(self) -> None:
        lib_id = self._read_int32()
        lib_name = self._read_length_prefixed_string()
        self.libraries[lib_id] = lib_name

    def _read_binary_object_string(self) -> str:
        obj_id = self._read_int32()
        value = self._read_length_prefixed_string()
        self.objects[obj_id] = value
        return value

    def _read_member_reference(self) -> _MemberRef:
        ref_id = self._read_int32()
        return _MemberRef(ref_id)

    def _read_class_info_and_types(self) -> tuple[int, _ClassInfo, list]:
        obj_id = self._read_int32()
        class_name = self._read_length_prefixed_string()
        num_members = self._read_int32()

        member_names = [self._read_length_prefixed_string() for _ in range(num_members)]
        member_binary_types = [self._read_uint8() for _ in range(num_members)]

        member_type_info: list[Any] = []
        for bt in member_binary_types:
            if bt == BT_PRIMITIVE:
                member_type_info.append(self._read_uint8())
            elif bt == BT_STRING:
                member_type_info.append(None)
            elif bt == BT_OBJECT:
                member_type_info.append(None)
            elif bt == BT_SYSTEM_CLASS:
                member_type_info.append(self._read_length_prefixed_string())
            elif bt == BT_CLASS:
                cn = self._read_length_prefixed_string()
                lib = self._read_int32()
                member_type_info.append((cn, lib))
            elif bt == BT_OBJECT_ARRAY:
                member_type_info.append(None)
            elif bt == BT_STRING_ARRAY:
                member_type_info.append(None)
            elif bt == BT_PRIMITIVE_ARRAY:
                member_type_info.append(self._read_uint8())
            else:
                raise ValueError(f"Unknown binary type {bt}")

        ci = _ClassInfo(class_name, member_names, member_binary_types, member_type_info)
        return obj_id, ci, member_type_info

    def _read_class_with_members_and_types(self) -> dict:
        obj_id, ci, _ti = self._read_class_info_and_types()
        _lib_id = self._read_int32()
        self.class_defs[obj_id] = ci
        return self._read_member_values(obj_id, ci)

    def _read_system_class_with_members_and_types(self) -> dict:
        obj_id, ci, _ti = self._read_class_info_and_types()
        self.class_defs[obj_id] = ci
        return self._read_member_values(obj_id, ci)

    def _read_class_with_id(self) -> dict:
        obj_id = self._read_int32()
        ref_class_id = self._read_int32()
        ci = self.class_defs[ref_class_id]
        self.class_defs[obj_id] = ci
        return self._read_member_values(obj_id, ci)

    def _read_member_values(self, obj_id: int, ci: _ClassInfo) -> dict:
        obj: dict[str, Any] = {"__class__": ci.class_name}
        for i, name in enumerate(ci.member_names):
            bt = ci.member_binary_types[i]
            ti = ci.member_type_info[i]
            if bt == BT_PRIMITIVE:
                obj[name] = self._read_primitive(ti)
            elif bt == BT_PRIMITIVE_ARRAY:
                obj[name] = self._read_value()
            else:
                val = self._read_value()
                if isinstance(val, list) and all(v is None for v in val):
                    obj[name] = None
                else:
                    obj[name] = val
        self.objects[obj_id] = obj
        return obj

    def _read_array_single_primitive(self) -> Any:
        obj_id = self._read_int32()
        length = self._read_int32()
        ptype = self._read_uint8()

        if ptype == PT_BYTE:
            data: Any = self._read_bytes(length)
        else:
            psize = PRIMITIVE_SIZES.get(ptype)
            if psize is None:
                raise ValueError(f"Unsupported primitive array type {ptype}")
            raw = self._read_bytes(length * psize)
            fmt_char = {
                PT_INT32: "i", PT_UINT32: "I",
                PT_INT64: "q", PT_UINT64: "Q",
                PT_INT16: "h", PT_UINT16: "H",
                PT_SINGLE: "f", PT_DOUBLE: "d",
                PT_BOOLEAN: "?", PT_SBYTE: "b",
                PT_DATETIME: "q",
            }.get(ptype)
            if fmt_char:
                data = list(struct.unpack(f"<{length}{fmt_char}", raw))
            else:
                data = raw

        self.objects[obj_id] = data
        return data

    def _read_array_single_object(self) -> list:
        obj_id = self._read_int32()
        length = self._read_int32()
        items: list[Any] = []
        i = 0
        while i < length:
            val = self._read_value()
            if isinstance(val, list) and all(v is None for v in val):
                items.extend(val)
                i += len(val)
            else:
                items.append(val)
                i += 1
        self.objects[obj_id] = items
        return items

    def _read_array_single_string(self) -> list:
        obj_id = self._read_int32()
        length = self._read_int32()
        items: list[Any] = []
        i = 0
        while i < length:
            val = self._read_value()
            if isinstance(val, list) and all(v is None for v in val):
                items.extend(val)
                i += len(val)
            else:
                items.append(val)
                i += 1
        self.objects[obj_id] = items
        return items

    def _read_binary_array(self) -> Any:
        obj_id = self._read_int32()
        array_type = self._read_uint8()
        rank = self._read_int32()
        lengths = [self._read_int32() for _ in range(rank)]

        bt = self._read_uint8()
        ti: Any = None
        if bt == BT_PRIMITIVE:
            ti = self._read_uint8()
        elif bt == BT_SYSTEM_CLASS:
            ti = self._read_length_prefixed_string()
        elif bt == BT_CLASS:
            cn = self._read_length_prefixed_string()
            lib = self._read_int32()
            ti = (cn, lib)
        elif bt == BT_PRIMITIVE_ARRAY:
            ti = self._read_uint8()

        total = 1
        for ln in lengths:
            total *= ln

        if bt == BT_PRIMITIVE and ti is not None:
            psize = PRIMITIVE_SIZES.get(ti, 1)
            raw = self._read_bytes(total * psize)
            if ti == PT_BYTE:
                data: Any = raw
            else:
                fmt_char = {
                    PT_INT32: "i", PT_UINT32: "I",
                    PT_INT64: "q", PT_UINT64: "Q",
                    PT_INT16: "h", PT_UINT16: "H",
                    PT_SINGLE: "f", PT_DOUBLE: "d",
                    PT_BOOLEAN: "?",
                }.get(ti, "B")
                data = list(struct.unpack(f"<{total}{fmt_char}", raw))
            self.objects[obj_id] = data
            return data
        else:
            items: list[Any] = []
            i = 0
            while i < total:
                val = self._read_value()
                if isinstance(val, list) and all(v is None for v in val):
                    items.extend(val)
                    i += len(val)
                else:
                    items.append(val)
                    i += 1
            self.objects[obj_id] = items
            return items

    # ── Reference resolution ───────────────────────────────────────────

    def _resolve_all(self) -> None:
        resolved: set[int] = set()
        for obj_id in list(self.objects):
            self.objects[obj_id] = self._resolve(self.objects[obj_id], resolved)

    def _resolve(self, value: Any, seen: set[int]) -> Any:
        if isinstance(value, _MemberRef):
            return self._resolve(self.objects.get(value.ref_id, value), seen)
        if isinstance(value, dict):
            obj_id = id(value)
            if obj_id in seen:
                return value
            seen.add(obj_id)
            for k in list(value.keys()):
                value[k] = self._resolve(value[k], seen)
            return value
        if isinstance(value, list):
            obj_id = id(value)
            if obj_id in seen:
                return value
            seen.add(obj_id)
            for i in range(len(value)):
                value[i] = self._resolve(value[i], seen)
            return value
        return value

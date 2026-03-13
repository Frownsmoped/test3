import pathlib
from typing import Tuple, Optional


def patch_main_class(class_file: str) -> int:
    p = pathlib.Path(class_file)
    b = bytearray(p.read_bytes())

    def u1(i: int) -> int:
        return b[i]

    def u2(i: int) -> int:
        return (b[i] << 8) | b[i + 1]

    def put_u8(i: int, value: int) -> None:
        b[i] = value & 0xFF

    def put_u4(i: int, value: int) -> None:
        b[i] = (value >> 24) & 0xFF
        b[i + 1] = (value >> 16) & 0xFF
        b[i + 2] = (value >> 8) & 0xFF
        b[i + 3] = value & 0xFF

    def put_u8x8(i: int, value: int) -> None:
        for off in range(8):
            b[i + off] = (value >> (56 - off * 8)) & 0xFF

    def parse_constant_pool() -> Tuple[list, int]:
        cp_count = u2(8)
        cp = [None] * cp_count
        offsets = [None] * cp_count
        i = 10
        idx = 1
        while idx < cp_count:
            offsets[idx] = i
            tag = u1(i)
            i += 1
            if tag == 1:  # Utf8
                l = u2(i)
                i += 2
                cp[idx] = (tag, bytes(b[i:i + l]).decode("utf-8", "replace"))
                i += l
            elif tag in (3, 4):  # Integer/Float
                cp[idx] = (tag,)
                i += 4
            elif tag in (5, 6):  # Long/Double
                cp[idx] = (tag,)
                i += 8
                idx += 1
            elif tag in (7, 8, 16):  # Class/String/MethodType
                cp[idx] = (tag, u2(i))
                i += 2
            elif tag in (9, 10, 11, 12, 18):  # refs / NameAndType / InvokeDynamic
                cp[idx] = (tag, u2(i), u2(i + 2))
                i += 4
            elif tag == 15:  # MethodHandle
                cp[idx] = (tag,)
                i += 3
            else:
                raise SystemExit(f"Unsupported CP tag {tag} at index {idx}")
            idx += 1
        return cp, offsets

    def cp_utf8(cp, idx: int) -> str:
        t = cp[idx]
        return t[1] if t and t[0] == 1 else ""

    def resolve_class_name(cp, class_idx: int) -> str:
        t = cp[class_idx]
        if not t or t[0] != 7:
            return ""
        _, utf8_idx = t
        return cp_utf8(cp, utf8_idx)

    def resolve_name_and_type(cp, nat_idx: int) -> Tuple[str, str]:
        t = cp[nat_idx]
        if not t or t[0] != 12:
            return ("", "")
        _, name_idx, desc_idx = t
        return (cp_utf8(cp, name_idx), cp_utf8(cp, desc_idx))

    def find_fieldref(cp, class_name: str, field_name: str, desc: str) -> Optional[int]:
        for idx, t in enumerate(cp):
            if not t or t[0] != 9:
                continue
            _, class_idx, nat_idx = t
            if resolve_class_name(cp, class_idx) != class_name:
                continue
            name, d = resolve_name_and_type(cp, nat_idx)
            if name == field_name and d == desc:
                return idx
        return None

    def find_methodref(cp, class_name: str, method_name: str, desc: str) -> Optional[int]:
        for idx, t in enumerate(cp):
            if not t or t[0] != 10:
                continue
            _, class_idx, nat_idx = t
            if resolve_class_name(cp, class_idx) != class_name:
                continue
            name, d = resolve_name_and_type(cp, nat_idx)
            if name == method_name and d == desc:
                return idx
        return None

    cp, offsets = parse_constant_pool()

    seconds_field = find_fieldref(cp, "java/util/concurrent/TimeUnit", "SECONDS", "Ljava/util/concurrent/TimeUnit;")
    to_millis = find_methodref(cp, "java/util/concurrent/TimeUnit", "toMillis", "(J)J")
    sleep_method = find_methodref(cp, "java/lang/Thread", "sleep", "(J)V")

    if seconds_field is None or to_millis is None or sleep_method is None:
        raise SystemExit("TARGET_REFS_NOT_FOUND")

    patched = 0
    i = 0
    end = len(b) - 12
    while i < end:
        if (
            b[i] == 0xB2
            and ((b[i + 1] << 8) | b[i + 2]) == seconds_field
            and b[i + 3] == 0x14
            and b[i + 6] == 0xB6
            and ((b[i + 7] << 8) | b[i + 8]) == to_millis
            and b[i + 9] == 0xB8
            and ((b[i + 10] << 8) | b[i + 11]) == sleep_method
        ):
            long_cp_index = (b[i + 4] << 8) | b[i + 5]
            if 0 < long_cp_index < len(cp):
                off = offsets[long_cp_index]
                if off is not None and cp[long_cp_index] and cp[long_cp_index][0] == 5:
                    # Change the CONSTANT_Long value to 0L.
                    put_u8x8(off + 1, 0)
                    patched += 1
                    i += 12
                    continue
        i += 1

    if not patched:
        raise SystemExit("SLEEP_SEQUENCE_NOT_FOUND")

    p.write_bytes(b)
    return patched


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "jar/org/bukkit/craftbukkit/Main.class"
    count = patch_main_class(target)
    print(f"PATCHED Main.class: zeroed {count} sleep delay constant(s) in {target}")

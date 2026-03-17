import argparse
import pathlib
import shutil
import struct
import tempfile
import zipfile
from typing import Tuple, Optional


def _u2(buf: bytes | bytearray, i: int) -> int:
    return (buf[i] << 8) | buf[i + 1]


def _u1(buf: bytes | bytearray, i: int) -> int:
    return buf[i]


def _parse_constant_pool(buf: bytes | bytearray) -> Tuple[list, list]:
    """
    Returns: (cp, offsets)
    - cp[i] is a tuple describing the constant pool entry at index i
    - offsets[i] is the byte offset of the entry in the class file (for patching)
    """
    cp_count = _u2(buf, 8)
    cp = [None] * cp_count
    offsets = [None] * cp_count
    i = 10
    idx = 1
    while idx < cp_count:
        offsets[idx] = i
        tag = _u1(buf, i)
        i += 1
        if tag == 1:  # Utf8
            l = _u2(buf, i)
            i += 2
            cp[idx] = (tag, bytes(buf[i : i + l]).decode("utf-8", "replace"))
            i += l
        elif tag in (3, 4):  # Integer/Float
            cp[idx] = (tag,)
            i += 4
        elif tag in (5, 6):  # Long/Double
            # We don't decode the value here; for verification we read raw bytes via offsets.
            cp[idx] = (tag,)
            i += 8
            idx += 1
        elif tag in (7, 8, 16):  # Class/String/MethodType
            cp[idx] = (tag, _u2(buf, i))
            i += 2
        elif tag in (9, 10, 11, 12, 18):  # refs / NameAndType / InvokeDynamic
            cp[idx] = (tag, _u2(buf, i), _u2(buf, i + 2))
            i += 4
        elif tag == 15:  # MethodHandle
            cp[idx] = (tag,)
            i += 3
        else:
            raise SystemExit(f"Unsupported CP tag {tag} at index {idx}")
        idx += 1
    return cp, offsets


def _cp_utf8(cp, idx: int) -> str:
    t = cp[idx]
    return t[1] if t and t[0] == 1 else ""


def _resolve_class_name(cp, class_idx: int) -> str:
    t = cp[class_idx]
    if not t or t[0] != 7:
        return ""
    _, utf8_idx = t
    return _cp_utf8(cp, utf8_idx)


def _resolve_name_and_type(cp, nat_idx: int) -> Tuple[str, str]:
    t = cp[nat_idx]
    if not t or t[0] != 12:
        return ("", "")
    _, name_idx, desc_idx = t
    return (_cp_utf8(cp, name_idx), _cp_utf8(cp, desc_idx))


def _find_fieldref(cp, class_name: str, field_name: str, desc: str) -> Optional[int]:
    for idx, t in enumerate(cp):
        if not t or t[0] != 9:
            continue
        _, class_idx, nat_idx = t
        if _resolve_class_name(cp, class_idx) != class_name:
            continue
        name, d = _resolve_name_and_type(cp, nat_idx)
        if name == field_name and d == desc:
            return idx
    return None


def _find_methodref(cp, class_name: str, method_name: str, desc: str) -> Optional[int]:
    for idx, t in enumerate(cp):
        if not t or t[0] != 10:
            continue
        _, class_idx, nat_idx = t
        if _resolve_class_name(cp, class_idx) != class_name:
            continue
        name, d = _resolve_name_and_type(cp, nat_idx)
        if name == method_name and d == desc:
            return idx
    return None


def verify_sleep_delay_zero(class_bytes: bytes) -> int:
    """
    Verifies the specific outdated-build wait sequence is effectively neutralized.

    Accepted patched forms:
    1) original Thread.sleep(J)V call still exists, but the referenced CONSTANT_Long is 0L
    2) the Thread.sleep(J)V call was replaced with POP2/NOP/NOP, so no sleep occurs at all

    Returns how many patched sequences were found.
    Returns 0 when the target class simply does not contain this outdated-build wait logic.
    """
    cp, offsets = _parse_constant_pool(class_bytes)

    seconds_field = _find_fieldref(
        cp, "java/util/concurrent/TimeUnit", "SECONDS", "Ljava/util/concurrent/TimeUnit;"
    )
    to_millis = _find_methodref(cp, "java/util/concurrent/TimeUnit", "toMillis", "(J)J")
    sleep_method = _find_methodref(cp, "java/lang/Thread", "sleep", "(J)V")

    if seconds_field is None or to_millis is None or sleep_method is None:
        return 0

    found = 0
    i = 0
    end = len(class_bytes) - 12
    while i < end:
        prefix_matches = (
            class_bytes[i] == 0xB2
            and ((class_bytes[i + 1] << 8) | class_bytes[i + 2]) == seconds_field
            and class_bytes[i + 3] == 0x14
            and class_bytes[i + 6] == 0xB6
            and ((class_bytes[i + 7] << 8) | class_bytes[i + 8]) == to_millis
        )
        if prefix_matches:
            long_cp_index = (class_bytes[i + 4] << 8) | class_bytes[i + 5]
            # Patched form A: replaced "invokestatic Thread.sleep(J)V" with "pop2; nop; nop"
            if class_bytes[i + 9] == 0x58 and class_bytes[i + 10] == 0x00 and class_bytes[i + 11] == 0x00:
                found += 1
                i += 12
                continue

            # Patched form B: original sleep invocation still present, but delay constant is 0L
            if class_bytes[i + 9] == 0xB8 and ((class_bytes[i + 10] << 8) | class_bytes[i + 11]) == sleep_method:
                if 0 < long_cp_index < len(cp):
                    off = offsets[long_cp_index]
                    if off is None or not cp[long_cp_index] or cp[long_cp_index][0] != 5:
                        raise SystemExit("VERIFY_DELAY_LONG_CONSTANT_NOT_FOUND")
                    # CONSTANT_Long is: tag(1) + 8 bytes big-endian value
                    v = int.from_bytes(class_bytes[off + 1 : off + 9], "big", signed=True)
                    if v != 0:
                        raise SystemExit(f"VERIFY_FAILED_DELAY_NOT_ZERO: {v}")
                    found += 1
                    i += 12
                    continue
        i += 1

    return found


def verify_java_version_upper_bound_relaxed(class_bytes: bytes) -> int:
    """
    Verifies the CraftBukkit upper Java class-version guard was relaxed.

    Original bytecode pattern in Main.main:
        fload <java.class.version>
        f2d
        ldc2_w 66.0d
        dcmpl
        ifle ...

    For Java 25, class version is 69.0. We patch the referenced CONSTANT_Double
    from 66.0d to a larger value (currently 99.0d), so newer JDKs are allowed.
    """
    cp, offsets = _parse_constant_pool(class_bytes)

    found = 0
    i = 0
    end = len(class_bytes) - 8
    while i < end:
        if (
            class_bytes[i] == 0x17       # fload
            and class_bytes[i + 2] == 0x8D  # f2d
            and class_bytes[i + 3] == 0x14  # ldc2_w
            and class_bytes[i + 6] == 0x97  # dcmpl
            and class_bytes[i + 7] == 0x9E  # ifle
        ):
            cp_index = (class_bytes[i + 4] << 8) | class_bytes[i + 5]
            if 0 < cp_index < len(cp):
                off = offsets[cp_index]
                if off is not None and cp[cp_index] and cp[cp_index][0] == 6:
                    v = struct.unpack(">d", class_bytes[off + 1 : off + 9])[0]
                    if v <= 66.0:
                        raise SystemExit(f"VERIFY_FAILED_JAVA_VERSION_UPPER_BOUND_NOT_PATCHED: {v}")
                    found += 1
                    i += 8
                    continue
        i += 1

    if found == 0:
        raise SystemExit("VERIFY_FAILED_JAVA_VERSION_CHECK_SEQUENCE_NOT_FOUND")
    return found


def patch_main_class(class_file: str, *, verify: bool = True) -> int:
    p = pathlib.Path(class_file)
    b = bytearray(p.read_bytes())

    def put_u8x8(i: int, value: int) -> None:
        for off in range(8):
            b[i + off] = (value >> (56 - off * 8)) & 0xFF

    def put_u1(i: int, value: int) -> None:
        b[i] = value & 0xFF

    def put_f8(i: int, value: float) -> None:
        b[i : i + 8] = struct.pack(">d", value)

    cp, offsets = _parse_constant_pool(b)

    seconds_field = _find_fieldref(cp, "java/util/concurrent/TimeUnit", "SECONDS", "Ljava/util/concurrent/TimeUnit;")
    to_millis = _find_methodref(cp, "java/util/concurrent/TimeUnit", "toMillis", "(J)J")
    sleep_method = _find_methodref(cp, "java/lang/Thread", "sleep", "(J)V")

    sleep_patch_possible = seconds_field is not None and to_millis is not None and sleep_method is not None

    sleep_patched = 0
    if sleep_patch_possible:
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
                        # Additionally replace invokestatic Thread.sleep(J)V (3 bytes)
                        # with POP2 + NOP + NOP so the sleep call is removed entirely.
                        put_u1(i + 9, 0x58)   # pop2
                        put_u1(i + 10, 0x00)  # nop
                        put_u1(i + 11, 0x00)  # nop
                        sleep_patched += 1
                        i += 12
                        continue
            i += 1

    java_guard_patched = 0
    i = 0
    end = len(b) - 8
    while i < end:
        if (
            b[i] == 0x17       # fload
            and b[i + 2] == 0x8D  # f2d
            and b[i + 3] == 0x14  # ldc2_w
            and b[i + 6] == 0x97  # dcmpl
            and b[i + 7] == 0x9E  # ifle
        ):
            cp_index = (b[i + 4] << 8) | b[i + 5]
            if 0 < cp_index < len(cp):
                off = offsets[cp_index]
                if off is not None and cp[cp_index] and cp[cp_index][0] == 6:
                    v = struct.unpack(">d", bytes(b[off + 1 : off + 9]))[0]
                    if v <= 66.0:
                        put_f8(off + 1, 99.0)
                        java_guard_patched += 1
                        i += 8
                        continue
        i += 1

    if not sleep_patched and not java_guard_patched:
        # Idempotent behavior: if the class is already patched/neutralized,
        # or if this server version simply doesn't contain the outdated-build wait logic,
        # treat that as success instead of failure.
        try:
            verify_sleep_delay_zero(bytes(b))
            verify_java_version_upper_bound_relaxed(bytes(b))
            p.write_bytes(b)
            return 0
        except SystemExit:
            raise SystemExit("PATCH_TARGETS_NOT_FOUND")

    if verify:
        # Verify against the patched bytes before writing to disk.
        verify_sleep_delay_zero(bytes(b))
        verify_java_version_upper_bound_relaxed(bytes(b))

    p.write_bytes(b)
    return sleep_patched + java_guard_patched


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "target",
        nargs="?",
        default="jar/org/bukkit/craftbukkit/Main.class",
        help="Path to org/bukkit/craftbukkit/Main.class OR a spigot jar containing it.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip post-patch verification (not recommended).",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify the 20s-wait sleep delay constant is patched to 0L; do not patch.",
    )
    parser.add_argument(
        "--in-jar",
        action="store_true",
        help="Treat target as a jar and patch org/bukkit/craftbukkit/Main.class inside it (rewrites jar).",
    )
    parser.add_argument(
        "--entry",
        default="org/bukkit/craftbukkit/Main.class",
        help="Jar entry path to patch/verify when using --in-jar.",
    )
    args = parser.parse_args()

    target = pathlib.Path(args.target)
    verify = not args.no_verify
    verify_only = args.verify_only
    in_jar = args.in_jar or (target.suffix.lower() == ".jar")
    entry = args.entry
    entry_fs = pathlib.Path(*entry.split("/"))  # for extracting to filesystem paths
    entry_zip = entry.replace("\\", "/")
    if not entry_zip.startswith("org/"):
        # keep it flexible, but normalize anyway
        entry_zip = entry_zip.lstrip("/")

    if not in_jar:
        raw = target.read_bytes()
        if verify_only:
            n_sleep = verify_sleep_delay_zero(raw)
            n_java = verify_java_version_upper_bound_relaxed(raw)
            print(f"VERIFY OK: sleep={n_sleep}, java-upper-bound={n_java} in {target}")
            raise SystemExit(0)

        count = patch_main_class(str(target), verify=verify)
        if count == 0:
            print(f"ALREADY PATCHED: wait sequence neutralized and Java upper-bound relaxed in {target}")
        else:
            print(f"PATCHED Main.class: applied {count} patch(es) in {target}")
        raise SystemExit(0)

    # --- jar mode ---
    if not target.exists():
        raise SystemExit(f"JAR_NOT_FOUND: {target}")

    with zipfile.ZipFile(target, "r") as z:
        try:
            raw = z.read(entry_zip)
        except KeyError:
            raise SystemExit(f"JAR_ENTRY_NOT_FOUND: {entry_zip} in {target}")

    if verify_only:
        n_sleep = verify_sleep_delay_zero(raw)
        n_java = verify_java_version_upper_bound_relaxed(raw)
        print(f"VERIFY OK: sleep={n_sleep}, java-upper-bound={n_java} in {target}!{entry_zip}")
        raise SystemExit(0)

    # Patch entry by extracting -> patch_main_class -> rebuild jar to avoid duplicate entries.
    with tempfile.TemporaryDirectory(prefix="patch_sleep_") as td:
        td = pathlib.Path(td)
        extracted = td / entry_fs
        extracted.parent.mkdir(parents=True, exist_ok=True)
        extracted.write_bytes(raw)

        count = patch_main_class(str(extracted), verify=verify)

        tmp_jar = td / (target.name + ".tmp")
        with zipfile.ZipFile(target, "r") as zin, zipfile.ZipFile(tmp_jar, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename == entry_zip:
                    continue
                zout.writestr(info, zin.read(info.filename))
            zout.write(extracted, arcname=entry_zip)

        shutil.copyfile(tmp_jar, target)

    if count == 0:
        print(f"ALREADY PATCHED JAR: {target}!{entry_zip} already neutralized / relaxed")
    else:
        print(f"PATCHED JAR: updated {target}!{entry_zip} (applied {count} patch(es))")

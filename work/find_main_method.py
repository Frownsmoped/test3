#!/usr/bin/env python3
import argparse
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class Hit:
    jar: Path
    class_entry: str


def iter_candidate_jars(base_dir: Path) -> Iterable[Path]:
    # Search both new and legacy layouts.
    roots = [
        base_dir / "build" / "META-INF" / "versions",
        base_dir / "build" / "bundler" / "versions",
        base_dir / "build" / "versions",
        base_dir / "build",  # fallback, in case a jar is directly under build/
    ]
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for jar in root.rglob("*.jar"):
            try:
                rp = jar.resolve()
            except OSError:
                rp = jar
            k = str(rp).lower()
            if k in seen:
                continue
            seen.add(k)
            yield rp


def manifest_main_class(server_jar: Path) -> Optional[str]:
    try:
        with zipfile.ZipFile(server_jar, "r") as z:
            raw = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except Exception:
        return None
    for line in raw.splitlines():
        if line.startswith("Main-Class:"):
            return line.split(":", 1)[1].strip() or None
    return None


def jar_contains_main_method(z: zipfile.ZipFile, class_entry: str) -> bool:
    """
    Heuristic: look for constant pool UTF8 'main' and descriptor '([Ljava/lang/String;)V'
    in the classfile bytes. This avoids full bytecode parsing and is good enough for finding entrypoints.
    """
    try:
        data = z.read(class_entry)
    except KeyError:
        return False
    return (b"main" in data) and (b"([Ljava/lang/String;)V" in data)


def find_main_in_jar(jar_path: Path, class_name: str) -> Optional[Hit]:
    class_entry = class_name.replace(".", "/") + ".class"
    try:
        with zipfile.ZipFile(jar_path, "r") as z:
            if class_entry not in z.namelist():
                return None
            if jar_contains_main_method(z, class_entry):
                return Hit(jar=jar_path, class_entry=class_entry)
            return None
    except zipfile.BadZipFile:
        return None


def scan_for_known_mains(jar_path: Path) -> list[Hit]:
    """
    Scan a jar for a small set of known entrypoints typically used by Spigot/CraftBukkit.
    """
    known = [
        "org.bukkit.craftbukkit.bootstrap.Main",
        "org.bukkit.craftbukkit.Main",
        "net.minecraft.server.Main",
        "io.papermc.paperclip.Main",
    ]
    hits: list[Hit] = []
    for cn in known:
        h = find_main_in_jar(jar_path, cn)
        if h:
            hits.append(h)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="Find which jar contains a given main class/method.")
    ap.add_argument(
        "--base",
        default=".",
        help="Project base directory (default: current directory)",
    )
    ap.add_argument(
        "--main-class",
        default="org.bukkit.craftbukkit.Main",
        help="Fully qualified main class to search for (default: org.bukkit.craftbukkit.Main)",
    )
    ap.add_argument(
        "--server-jar",
        default="build/server.jar",
        help="Path to outer server.jar for manifest inspection (default: build/server.jar)",
    )
    ap.add_argument(
        "--scan-known",
        action="store_true",
        help="Also scan for a few known main entrypoints in each candidate jar.",
    )
    args = ap.parse_args()

    base_dir = Path(args.base).resolve()
    server_jar = (base_dir / args.server_jar).resolve()

    print(f"BASE: {base_dir}")
    if server_jar.exists():
        mc = manifest_main_class(server_jar)
        print(f"SERVER.JAR MANIFEST Main-Class: {mc or '<none>'} ({server_jar})")
    else:
        print(f"SERVER.JAR: <missing> ({server_jar})")

    print(f"SEARCH main-class: {args.main_class}")
    print("")

    found: list[Hit] = []
    for jar in iter_candidate_jars(base_dir):
        hit = find_main_in_jar(jar, args.main_class)
        if hit:
            found.append(hit)
            print(f"[HIT] {hit.jar} -> {hit.class_entry}")
        if args.scan_known:
            for h in scan_for_known_mains(jar):
                print(f"[KNOWN] {h.jar} -> {h.class_entry}")

    if not found:
        print("")
        print("No jar containing the requested main-class + main(String[]) signature was found.")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
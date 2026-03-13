#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(cmd, cwd=None):
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd)


def main():
    ap = argparse.ArgumentParser(description="Decompile/inspect CraftBukkit Main for patch validation.")
    ap.add_argument("--javap", required=True, help="Path to javap executable")
    ap.add_argument("--jar", required=True, help="Path to spigot/patched jar")
    ap.add_argument("--out", required=True, help="Output text file")
    args = ap.parse_args()

    javap = Path(args.javap)
    jar = Path(args.jar)
    out = Path(args.out)

    if not javap.exists():
        raise SystemExit(f"javap not found: {javap}")
    if not jar.exists():
        raise SystemExit(f"jar not found: {jar}")

    out.parent.mkdir(parents=True, exist_ok=True)

    # Use javap disassembly for both Main and Main$1 (option parser inner class)
    targets = ["org.bukkit.craftbukkit.Main", "org.bukkit.craftbukkit.Main$1"]
    lines = []

    # Resolve a classpath that actually contains CraftBukkit:
    # - If --jar points to server.jar, look for extracted jars under <build>/META-INF/versions/*/spigot-*.jar
    #   and <build>/META-INF/cache/mojang_*.jar (preferred).
    cp_entries = [jar]
    if jar.name == "server.jar":
        base = jar.parent
        meta_inf = base / "META-INF"
        candidates = []
        if (meta_inf / "versions").exists():
            candidates += sorted((meta_inf / "versions").glob("*/spigot-*.jar"))
        if (meta_inf / "cache").exists():
            candidates += sorted((meta_inf / "cache").glob("mojang_*.jar"))
        # Prefer candidates first on classpath
        for p in reversed(candidates):
            cp_entries.insert(0, p)

    cp = os.pathsep.join(str(p) for p in cp_entries)

    for t in targets:
        cmd = [str(javap), "-classpath", cp, "-c", t]
        print(f"Decompiling {t} ...")
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines.append(f"===== {t} =====\n")
        lines.append(p.stdout)
        lines.append("\n\n")

    out.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote: {out}")


if __name__ == "__main__":
    main()
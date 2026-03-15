#!/usr/bin/env python3
import argparse
import os
import subprocess
from pathlib import Path


def find_runtime_jars_from_server_jar(server_jar: Path) -> list[Path]:
    """
    Resolve the real CraftBukkit/Spigot jar(s) when the input is the outer server.jar.

    Search order:
    1) build/META-INF/versions/*.jar
    2) build/bundler/versions/*.jar
    3) build/META-INF/versions/*/spigot-*.jar   (legacy layout)
    4) build/META-INF/cache/mojang_*.jar        (legacy fallback)
    """
    base = server_jar.parent
    meta_inf = base / "META-INF"
    candidates: list[Path] = []

    preferred_patterns = [
        meta_inf / "versions",
        base / "bundler" / "versions",
    ]
    for root in preferred_patterns:
        if root.exists():
            for pattern in ("craftbukkit-*.jar", "spigot-*.jar", "*.jar"):
                for p in sorted(root.glob(pattern)):
                    if p.exists() and p not in candidates:
                        candidates.append(p)

    legacy_versions = meta_inf / "versions"
    if legacy_versions.exists():
        for p in sorted(legacy_versions.glob("*/spigot-*.jar")):
            if p.exists() and p not in candidates:
                candidates.append(p)

    legacy_cache = meta_inf / "cache"
    if legacy_cache.exists():
        for p in sorted(legacy_cache.glob("mojang_*.jar")):
            if p.exists() and p not in candidates:
                candidates.append(p)

    return candidates


def build_classpath(jar: Path) -> str:
    cp_entries: list[Path] = []

    if jar.name == "server.jar":
        candidates = find_runtime_jars_from_server_jar(jar)
        cp_entries.extend(candidates)

    cp_entries.append(jar)

    deduped: list[Path] = []
    seen: set[str] = set()
    for p in cp_entries:
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        key = os.path.normcase(str(rp))
        if key in seen or not rp.exists():
            continue
        seen.add(key)
        deduped.append(rp)

    return os.pathsep.join(str(p) for p in deduped)


def main():
    ap = argparse.ArgumentParser(description="Decompile/inspect CraftBukkit Main for patch validation.")
    ap.add_argument("--javap", required=True, help="Path to javap executable")
    ap.add_argument("--jar", required=True, help="Path to server/craftbukkit/spigot jar")
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

    targets = ["org.bukkit.craftbukkit.Main", "org.bukkit.craftbukkit.Main$1"]
    cp = build_classpath(jar)

    lines = []
    lines.append(f"# input-jar: {jar}\n")
    lines.append(f"# classpath: {cp}\n\n")

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
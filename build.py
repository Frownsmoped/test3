#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform build script for native-minecraft-server (GraalVM native-image).

What it does:
- Works on Windows / Linux / macOS.
- Downloads Mojang server.jar (only if missing).
- Extracts META-INF/* to get classpath / main-class metadata.
- Searches bundled jars under bundler/ and META-INF/versions/.
- Patches org/bukkit/craftbukkit/Main.class to skip outdated-build 20s sleep.
- Compiles work/SelfMain.java and uses it as native-image entrypoint.

Environment variables:
- GRAALVM_HOME (required)
- SERVER_VERSION (default: 1.21.11)
- GENERATE_CONFIG (optional: 1/true to run agent)
- NO_GUI (optional: 1/true, only used when GENERATE_CONFIG enabled)
- MC_ENTRY_CLASS (optional runtime override written into env helper files)
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR / "configuration"
BUILD_DIR = SCRIPT_DIR / "build"
WORK_DIR = SCRIPT_DIR / "work"

SERVER_VERSION = os.environ.get("SERVER_VERSION", "1.21.11").strip() or "1.21.11"
BINARY_NAME = "native-minecraft-server"

GRAALVM_HOME = os.environ.get("GRAALVM_HOME", "").strip()
if not GRAALVM_HOME:
    print("[ERROR] GRAALVM_HOME is not set. Please provide a GraalVM installation.")
    sys.exit(1)

GRAALVM_HOME_P = Path(GRAALVM_HOME)
NI_EXEC = (GRAALVM_HOME_P / "bin" / ("native-image.cmd" if os.name == "nt" else "native-image")).resolve()
JAVA_EXEC = (GRAALVM_HOME_P / "bin" / ("java.exe" if os.name == "nt" else "java")).resolve()
JAVAC_EXEC = (GRAALVM_HOME_P / "bin" / ("javac.exe" if os.name == "nt" else "javac")).resolve()
JAVAP_EXEC = (GRAALVM_HOME_P / "bin" / ("javap.exe" if os.name == "nt" else "javap")).resolve()

if not NI_EXEC.exists():
    print(f"[ERROR] native-image not found: {NI_EXEC}")
    sys.exit(1)
if not JAVA_EXEC.exists():
    print(f"[ERROR] java not found: {JAVA_EXEC}")
    sys.exit(1)
if not JAVAC_EXEC.exists():
    print(f"[ERROR] javac not found: {JAVAC_EXEC}")
    sys.exit(1)

JAR_PATH = BUILD_DIR / "server.jar"
ZIP_PATH = BUILD_DIR / "server.zip"
META_INF_PATH = BUILD_DIR / "META-INF"
VERSION_MARKER_PATH = BUILD_DIR / "server-version.txt"

CLASSPATH_JOINED_PATH = META_INF_PATH / "classpath-joined"
LIBRARIES_LIST_PATH = META_INF_PATH / "libraries.list"
VERSIONS_LIST_PATH = META_INF_PATH / "versions.list"
MAIN_CLASS_PATH = META_INF_PATH / "main-class"

SELFMAIN_SRC = WORK_DIR / "SelfMain.java"
SELFMAIN_OUT = BUILD_DIR / "selfmain-classes"
NATIVE_IMAGE_ARGS_PATH = BUILD_DIR / "native-image.args"

PATCH_SLEEP_SCRIPT = WORK_DIR / "patch_sleep.py"
DECOMPILE_MAIN_SCRIPT = WORK_DIR / "decompile_main.py"

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest.json"


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    printable = " ".join([f"\"{c}\"" if " " in c else c for c in cmd])
    print(f"[INFO] $ {printable}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def download_json(url: str) -> dict:
    print(f"[INFO] Downloading JSON: {url}")
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_file(url: str, dest: Path) -> None:
    print(f"[INFO] Downloading: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def truthy_env(name: str) -> bool:
    v = os.environ.get(name)
    if v is None:
        return False
    v = v.strip().lower()
    return v not in ("", "0", "false", "no", "off")


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8").strip()


def ensure_build_artifacts() -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    refresh_build_artifacts = True
    if VERSION_MARKER_PATH.exists() and JAR_PATH.exists() and META_INF_PATH.exists():
        current_build_version = VERSION_MARKER_PATH.read_text(encoding="utf-8").strip()
        refresh_build_artifacts = current_build_version != SERVER_VERSION

    if refresh_build_artifacts:
        if META_INF_PATH.exists():
            shutil.rmtree(META_INF_PATH, ignore_errors=True)
        if ZIP_PATH.exists():
            try:
                ZIP_PATH.unlink()
            except OSError:
                pass

        if not JAR_PATH.exists():
            manifest = download_json(VERSION_MANIFEST_URL)
            version_url = None
            for it in manifest.get("versions", []):
                if it.get("id") == SERVER_VERSION:
                    version_url = it.get("url")
                    break
            if not version_url:
                raise SystemExit(f"[ERROR] Unable to find manifest url for SERVER_VERSION={SERVER_VERSION}")

            server_manifest = download_json(version_url)
            server_url = server_manifest.get("downloads", {}).get("server", {}).get("url")
            if not server_url:
                raise SystemExit(f"[ERROR] Unable to find server.jar download url for SERVER_VERSION={SERVER_VERSION}")

            download_file(server_url, JAR_PATH)
            print(f"[INFO] Downloaded server.jar -> {JAR_PATH}")
        else:
            print(f"[INFO] Using existing server.jar: {JAR_PATH}")

        shutil.copy2(JAR_PATH, ZIP_PATH)
        print("[INFO] Extracting resources from server.zip ...")
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            zf.extractall(path=str(BUILD_DIR))
        try:
            ZIP_PATH.unlink()
        except OSError:
            pass

        VERSION_MARKER_PATH.write_text(SERVER_VERSION, encoding="utf-8")

    if not META_INF_PATH.exists():
        raise SystemExit("[ERROR] Unable to determine build metadata (missing build/META-INF)")
    if not CLASSPATH_JOINED_PATH.exists() and not LIBRARIES_LIST_PATH.exists():
        raise SystemExit("[ERROR] Unable to determine classpath (missing META-INF/classpath-joined and META-INF/libraries.list)")


def parse_list_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    names: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        jar_name = parts[1].strip().lstrip("*").strip()
        if jar_name:
            names.append(jar_name)
    return names


def split_joined_classpath(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    sep = ";" if ";" in raw else os.pathsep
    return [part.strip() for part in raw.split(sep) if part.strip()]


def dedupe_existing_paths(paths: list[Path]) -> list[Path]:
    seen_paths: set[str] = set()
    seen_names: set[str] = set()
    result: list[Path] = []
    for p in paths:
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if not rp.exists():
            continue
        path_key = os.path.normcase(str(rp))
        if path_key in seen_paths:
            continue

        name_key = rp.name.lower()
        # Prefer the first jar with a given filename to avoid META-INF/bundler duplicate payloads
        # making the classpath unnecessarily large.
        if rp.suffix.lower() == ".jar" and name_key in seen_names:
            continue

        seen_paths.add(path_key)
        if rp.suffix.lower() == ".jar":
            seen_names.add(name_key)
        result.append(rp)
    return result


def read_jar_main_class() -> str:
    if not JAR_PATH.exists():
        return ""
    with zipfile.ZipFile(JAR_PATH, "r") as zf:
        try:
            raw = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
        except KeyError:
            return ""
    for line in raw.splitlines():
        if line.startswith("Main-Class:"):
            return line.split(":", 1)[1].strip()
    return ""


def find_first_recursive_main_class(base: Path) -> str:
    if not base.exists():
        return ""
    for p in sorted(base.rglob("main-class")):
        if p.is_file():
            value = read_text(p)
            if value:
                return value
    return ""


def determine_main_class(jar_main_class: str) -> str:
    # Priority:
    # 1) META-INF/bundler/versions/**/main-class
    # 2) build/bundler/versions/**/main-class
    # 3) META-INF/main-class, except org.bukkit.craftbukkit.Main
    # 4) jar manifest Main-Class, except org.bukkit.craftbukkit.Main
    for base in (META_INF_PATH / "bundler" / "versions", BUILD_DIR / "bundler" / "versions"):
        main_class = find_first_recursive_main_class(base)
        if main_class:
            return main_class

    if MAIN_CLASS_PATH.exists():
        main_class = read_text(MAIN_CLASS_PATH)
        if main_class and main_class != "org.bukkit.craftbukkit.Main":
            return main_class

    if jar_main_class and jar_main_class != "org.bukkit.craftbukkit.Main":
        return jar_main_class

    return ""


def find_target_server_jar() -> Path | None:
    search_roots = [
        META_INF_PATH / "versions",
        BUILD_DIR / "bundler" / "versions",
        BUILD_DIR / "versions",
    ]

    for jar_name in parse_list_file(VERSIONS_LIST_PATH):
        for root in search_roots:
            candidate = root / jar_name
            if candidate.exists():
                return candidate.resolve()

    for pattern in ("craftbukkit-*.jar", "spigot-*.jar", "*.jar"):
        for root in search_roots:
            if not root.exists():
                continue
            for candidate in sorted(root.glob(pattern)):
                if candidate.exists():
                    return candidate.resolve()

    return None


def build_runtime_classpath() -> str:
    entries: list[Path] = []

    if CLASSPATH_JOINED_PATH.exists():
        for part in split_joined_classpath(read_text(CLASSPATH_JOINED_PATH)):
            entries.append(Path(part))
    else:
        print("[INFO] Reconstructing classpath from META-INF/libraries.list ...")
        for jar_name in parse_list_file(LIBRARIES_LIST_PATH):
            entries.append(META_INF_PATH / "libraries" / jar_name)

    # Explicitly include local server/bundler payloads.
    entries.append(JAR_PATH)

    extra_dirs = [
        BUILD_DIR / "bundler" / "libraries",
        BUILD_DIR / "bundler" / "versions",
        META_INF_PATH / "versions",
    ]
    for base in extra_dirs:
        if not base.exists():
            continue
        for jar_path in sorted(base.glob("*.jar")):
            entries.append(jar_path)

    resolved = dedupe_existing_paths(entries)
    return os.pathsep.join(str(p) for p in resolved)


def patch_target_server_jar(target_server_jar: Path) -> None:
    if not PATCH_SLEEP_SCRIPT.exists():
        raise SystemExit(f"[ERROR] Missing patch script: {PATCH_SLEEP_SCRIPT}")

    print(f"[INFO] Patching target server jar: {target_server_jar}")
    run([sys.executable, str(PATCH_SLEEP_SCRIPT), str(target_server_jar), "--in-jar"])
    run([sys.executable, str(PATCH_SLEEP_SCRIPT), str(target_server_jar), "--in-jar", "--verify-only"])

    if JAVAP_EXEC.exists() and DECOMPILE_MAIN_SCRIPT.exists():
        out_file = BUILD_DIR / "decompile_main_targetserverjar.txt"
        run(
            [
                sys.executable,
                str(DECOMPILE_MAIN_SCRIPT),
                "--javap",
                str(JAVAP_EXEC),
                "--jar",
                str(target_server_jar),
                "--out",
                str(out_file),
            ]
        )


def compile_selfmain(classpath_joined: str) -> None:
    if not SELFMAIN_SRC.exists():
        raise SystemExit(f"[ERROR] Missing entrypoint source: {SELFMAIN_SRC}")

    if SELFMAIN_OUT.exists():
        shutil.rmtree(SELFMAIN_OUT, ignore_errors=True)
    SELFMAIN_OUT.mkdir(parents=True, exist_ok=True)

    cmd = [str(JAVAC_EXEC), "-encoding", "UTF-8"]
    if classpath_joined:
        cmd += ["-cp", classpath_joined]
    cmd += ["-d", str(SELFMAIN_OUT), str(SELFMAIN_SRC)]
    run(cmd)

    if not (SELFMAIN_OUT / "SelfMain.class").exists():
        raise SystemExit("[ERROR] Failed to compile SelfMain.java")


def write_env_files(main_class: str) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    runtime_entry_class = main_class.strip() or "org.bukkit.craftbukkit.Main"

    env_cmd = BUILD_DIR / "env.cmd"
    env_sh = BUILD_DIR / "env.sh"

    env_cmd.write_text(
        "\r\n".join(
            [
                "@echo off",
                "REM Auto-generated by build.py",
                f"set \"MC_ENTRY_CLASS={runtime_entry_class}\"",
                "",
            ]
        )
        + "\r\n",
        encoding="utf-8",
    )

    env_sh.write_text(
        "\n".join(
            [
                "#!/usr/bin/env sh",
                "# Auto-generated by build.py",
                f"export MC_ENTRY_CLASS=\"{runtime_entry_class}\"",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(env_sh, 0o755)
    except OSError:
        pass

    print(f"[INFO] Wrote runtime env helper: {env_cmd}")
    print(f"[INFO] Wrote runtime env helper: {env_sh}")

    os.environ["MC_ENTRY_CLASS"] = runtime_entry_class


def maybe_run_agent(no_gui: bool) -> None:
    if not truthy_env("GENERATE_CONFIG"):
        return

    print(f"[INFO] GENERATE_CONFIG enabled. Running native-image-agent into: {CONFIG_DIR}")
    agent_opt = (
        f"-agentlib:native-image-agent=config-output-dir={CONFIG_DIR},"
        f"experimental-class-loader-support,config-merge-dir={CONFIG_DIR}"
    )

    args = [str(JAVA_EXEC), agent_opt, "-jar", str(JAR_PATH)]
    if no_gui:
        args.append("-nogui")

    run(args, cwd=SCRIPT_DIR)


def build_native_image(classpath_joined: str, extra_args: list[str]) -> None:
    cp_full = str(SELFMAIN_OUT) + (os.pathsep + classpath_joined if classpath_joined else "")

    system_name = platform.system().lower()

    args: list[str] = [
        "--no-fallback",
        "-H:ConfigurationFileDirectories=" + str(CONFIG_DIR),
        r"-H:IncludeResources=\Qjoptsimple/HelpFormatterMessages.properties\E",
        r"-H:IncludeResources=\Qjoptsimple/ExceptionMessages.properties\E",
        r"-H:IncludeResources=^(assets|data)/.*$",
        r"-H:IncludeResources=^pack\.mcmeta$",
        r"-H:IncludeResources=^version\.json$",
        "-H:+AddAllCharsets",
        "-H:+ReportExceptionStackTraces",
        "--enable-url-protocols=https",
        "--initialize-at-run-time=io.netty",
        "--enable-native-access=ALL-UNNAMED",
        "-H:+SharedArenaSupport",
        "--initialize-at-build-time=net.minecraft.util.profiling.jfr.event",
        "--initialize-at-build-time=org.apache.logging.log4j,org.apache.logging.slf4j,org.apache.logging.log4j.core.util.DefaultShutdownCallbackRegistry",
        "--initialize-at-build-time=org.fusesource.jansi",
        "--initialize-at-run-time=joptsimple",
    ]

    if system_name == "linux":
        args.append("--gc=G1")

    if system_name == "darwin":
        args += [
            "--add-modules=java.desktop",
            "--initialize-at-run-time=java.awt",
            "--initialize-at-run-time=javax.swing",
            "--initialize-at-run-time=sun.awt",
        ]

    args += [
        "-H:Name=" + BINARY_NAME,
        "-cp",
        cp_full,
    ]

    args += extra_args

    # IMPORTANT: SelfMain.java is in the default package, so entrypoint is "SelfMain".
    args.append("SelfMain")

    NATIVE_IMAGE_ARGS_PATH.write_text(
        "".join(subprocess.list2cmdline([arg]) + "\n" for arg in args),
        encoding="utf-8",
    )
    print(f"[INFO] Wrote native-image args file: {NATIVE_IMAGE_ARGS_PATH}")

    run([str(NI_EXEC), "@" + str(NATIVE_IMAGE_ARGS_PATH)], cwd=META_INF_PATH)

    out_name = BINARY_NAME + (".exe" if os.name == "nt" else "")
    produced = META_INF_PATH / out_name
    if not produced.exists():
        raise SystemExit(f"[ERROR] Expected output not found: {produced}")

    final_out = SCRIPT_DIR / out_name
    shutil.copy2(produced, final_out)
    print("")
    print("[INFO] Done! Output:")
    print(str(final_out))


def main(argv: list[str]) -> int:
    extra_args: list[str] = []
    if "--" in argv:
        idx = argv.index("--")
        extra_args = argv[idx + 1 :]
        argv = argv[:idx]

    print(f"[INFO] SERVER_VERSION={SERVER_VERSION}")
    ensure_build_artifacts()

    jar_main_class = read_jar_main_class()
    resolved_main_class = determine_main_class(jar_main_class)
    target_server_jar = find_target_server_jar()

    print(f"[INFO] JAR manifest Main-Class: {jar_main_class or '<none>'}")
    print(f"[INFO] Resolved bundled main-class: {resolved_main_class or '<none>'}")

    if target_server_jar is not None:
        print(f"[INFO] Target server jar: {target_server_jar}")
        patch_target_server_jar(target_server_jar)
    else:
        print("[WARN] Unable to locate CraftBukkit/Spigot target jar under META-INF/versions or bundler/versions")

    classpath_joined = build_runtime_classpath()
    if target_server_jar is not None:
        target_server_jar_s = str(target_server_jar)
        if target_server_jar_s not in classpath_joined.split(os.pathsep):
            classpath_joined = target_server_jar_s + (os.pathsep + classpath_joined if classpath_joined else "")

    runtime_entry_class = os.environ.get("MC_ENTRY_CLASS", "").strip()
    if not runtime_entry_class:
        runtime_entry_class = resolved_main_class or ("org.bukkit.craftbukkit.Main" if target_server_jar else jar_main_class)

    print(f"[INFO] Using MC_ENTRY_CLASS for runtime helper files: {runtime_entry_class or '<none>'}")

    write_env_files(runtime_entry_class)
    compile_selfmain(classpath_joined=classpath_joined)
    maybe_run_agent(no_gui=truthy_env("NO_GUI"))
    build_native_image(classpath_joined=classpath_joined, extra_args=extra_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
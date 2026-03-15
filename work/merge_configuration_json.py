#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def merge_values(old_value: Any, current_value: Any, prefer: str) -> Any:
    if old_value is None:
        return current_value
    if current_value is None:
        return old_value

    if isinstance(old_value, dict) and isinstance(current_value, dict):
        merged: dict[str, Any] = {}
        for key in sorted(set(old_value) | set(current_value)):
            if key in old_value and key in current_value:
                merged[key] = merge_values(old_value[key], current_value[key], prefer)
            elif key in current_value:
                merged[key] = current_value[key]
            else:
                merged[key] = old_value[key]
        return merged

    if isinstance(old_value, list) and isinstance(current_value, list):
        merged_list: list[Any] = []
        seen: set[str] = set()

        # old first, then current override/append semantics while still deduping complex items
        for item in old_value + current_value:
            key = canonical(item)
            if key in seen:
                continue
            seen.add(key)
            merged_list.append(item)
        return merged_list

    return current_value if prefer == "current" else old_value


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge top-level JSON files from configuration/old and configuration."
    )
    parser.add_argument(
        "--base-dir",
        default="configuration",
        help="Base configuration directory containing current JSON files (default: configuration)",
    )
    parser.add_argument(
        "--old-dir-name",
        default="old",
        help="Old config subdirectory name under base-dir (default: old)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to <base-dir>/merged",
    )
    parser.add_argument(
        "--prefer",
        choices=("current", "old"),
        default="current",
        help="When scalar values conflict, prefer current or old (default: current)",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    old_dir = (base_dir / args.old_dir_name).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (base_dir / "merged").resolve()

    if not base_dir.is_dir():
        raise SystemExit(f"Base directory not found: {base_dir}")
    if not old_dir.is_dir():
        raise SystemExit(f"Old directory not found: {old_dir}")

    current_files = {p.name: p for p in base_dir.glob("*.json")}
    old_files = {p.name: p for p in old_dir.glob("*.json")}
    all_names = sorted(set(current_files) | set(old_files))

    if not all_names:
        print("No top-level JSON files found to merge.")
        return 0

    print(f"Base dir : {base_dir}")
    print(f"Old dir  : {old_dir}")
    print(f"Output   : {output_dir}")
    print(f"Prefer   : {args.prefer}")
    print("")

    for name in all_names:
        current_path = current_files.get(name)
        old_path = old_files.get(name)
        output_path = output_dir / name

        if current_path and old_path:
            old_data = load_json(old_path)
            current_data = load_json(current_path)
            merged = merge_values(old_data, current_data, args.prefer)
            write_json(output_path, merged)
            print(f"[MERGED] {name}")
        elif current_path:
            write_json(output_path, load_json(current_path))
            print(f"[COPIED current] {name}")
        else:
            write_json(output_path, load_json(old_path))
            print(f"[COPIED old] {name}")

    print("")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
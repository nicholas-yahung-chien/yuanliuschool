#!/usr/bin/env python3
"""Materialize long-route alias HTML files from hashed __pages paths.

This is used before deployment so static hosting can serve all routes directly
without depending on rewrite matching for unicode/encoded paths.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def longpath(path: Path) -> str:
    p = str(path.resolve())
    if os.name == "nt":
        if p.startswith("\\\\"):
            return "\\\\?\\UNC\\" + p[2:]
        return "\\\\?\\" + p
    return p


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(longpath(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read_text(path: Path) -> str:
    with open(longpath(path), "r", encoding="utf-8") as f:
        return f.read()


def remove_file(path: Path) -> None:
    try:
        os.remove(longpath(path))
    except FileNotFoundError:
        pass


def materialize(site_dir: Path) -> int:
    route_map_path = site_dir / "_meta" / "route_map.json"
    if not route_map_path.exists():
        print(f"Missing route map: {route_map_path}")
        return 2

    route_map = json.loads(route_map_path.read_text(encoding="utf-8"))
    created: list[str] = []

    for route, local_rel in sorted(route_map.items()):
        if route == "/":
            continue
        if not local_rel.startswith("__pages/"):
            continue

        alias_rel = f"{route.lstrip('/')}.html"
        src = site_dir / local_rel
        dst = site_dir / alias_rel
        if not src.exists():
            continue

        content = read_text(src)
        write_text(dst, content)
        created.append(alias_rel.replace("\\", "/"))

    manifest = site_dir / "_meta" / "materialized_aliases.txt"
    manifest.write_text("\n".join(created) + ("\n" if created else ""), encoding="utf-8")

    print(f"Materialized aliases: {len(created)}")
    print(f"Manifest: {manifest}")
    return 0


def cleanup(site_dir: Path) -> int:
    manifest = site_dir / "_meta" / "materialized_aliases.txt"
    if not manifest.exists():
        print("No alias manifest found; nothing to cleanup")
        return 0

    removed = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        rel = line.strip()
        if not rel:
            continue
        path = site_dir / rel
        remove_file(path)
        removed += 1

    remove_file(manifest)
    print(f"Removed aliases: {removed}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    site_dir = Path(args.site_dir).resolve()
    if args.cleanup:
        return cleanup(site_dir)
    return materialize(site_dir)


if __name__ == "__main__":
    raise SystemExit(main())

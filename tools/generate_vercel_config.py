#!/usr/bin/env python3
"""Generate site/vercel.json rewrites from route map."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", default="site")
    args = parser.parse_args()

    site_dir = Path(args.site_dir).resolve()
    route_map_path = site_dir / "_meta" / "route_map.json"
    if not route_map_path.exists():
        print(f"Missing route map: {route_map_path}")
        return 2

    route_map = json.loads(route_map_path.read_text(encoding="utf-8"))

    rewrites: list[dict[str, str]] = []

    for route, local_rel in sorted(route_map.items()):
        if route == "/":
            continue
        expected = f"{route.lstrip('/')}.html"
        if local_rel == expected or not local_rel.startswith("__pages/"):
            continue
        # Keep canonical ASCII percent-encoded routes only.
        # Crawl metadata may include mojibake aliases for the same page.
        if "%" not in route or not route.isascii():
            continue
        destination = "/" + local_rel.removesuffix(".html")
        decoded_route = unquote(route)
        # Keep decoded paths (what Vercel matching uses for Unicode routes),
        # and keep encoded path as fallback for safety.
        for source in (decoded_route, route):
            rewrites.append({"source": source, "destination": destination})

    # Deduplicate while preserving order.
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for rule in rewrites:
        key = (rule["source"], rule["destination"])
        if key not in unique:
            unique[key] = rule
    rewrites = list(unique.values())

    config = {
        "cleanUrls": True,
        "trailingSlash": False,
        "headers": [
            {
                "source": "/(.*)",
                "headers": [{"key": "X-Content-Type-Options", "value": "nosniff"}],
            }
        ],
        "rewrites": rewrites,
    }

    (site_dir / "vercel.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )

    print(f"Wrote {site_dir / 'vercel.json'}")
    print(f"Rewrite entries: {len(rewrites)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

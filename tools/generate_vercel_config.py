#!/usr/bin/env python3
"""Generate site/vercel.json from route map with encoded+decoded rewrite sources."""

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
    seen: set[tuple[str, str]] = set()

    for route, local_rel in sorted(route_map.items()):
        if route == "/":
            continue
        expected = f"{route.lstrip('/')}.html"
        if local_rel == expected:
            continue

        candidates = [route]
        decoded = unquote(route)
        if decoded != route:
            candidates.append(decoded)

        for source in candidates:
            key = (source, local_rel)
            if key in seen:
                continue
            seen.add(key)
            rewrites.append({"source": source, "destination": "/" + local_rel})

    config = {
        "cleanUrls": True,
        "trailingSlash": False,
        "headers": [
            {
                "source": "/(.*)",
                "headers": [{"key": "X-Content-Type-Options", "value": "nosniff"}],
            }
        ],
    }
    if rewrites:
        config["rewrites"] = rewrites

    (site_dir / "vercel.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"Wrote {site_dir / 'vercel.json'} with {len(rewrites)} rewrites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

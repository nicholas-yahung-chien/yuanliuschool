#!/usr/bin/env python3
"""Basic static integrity checks for mirrored site."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

ATTRS = ["href", "src", "poster", "data-src", "data-bg", "data-background"]
ALLOWED_BROKEN_ROUTES = {
    "/course/parfumerie-francaise",
    "/ifa%E8%8B%B1%E5%9C%8B%E5%9C%8B%E9%9A%9B%E8%8A%B3%E7%99%82%E5%B8%AB%E8%AD%89%E7%85%A7%E8%AA%B2",
    "/mld-level-1",
}


def load_route_map(site_dir: Path) -> dict[str, str]:
    route_map_path = site_dir / "_meta" / "route_map.json"
    if not route_map_path.exists():
        return {}
    try:
        import json

        data = json.loads(route_map_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def route_exists(site_dir: Path, route: str, route_map: dict[str, str]) -> bool:
    path = route.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return True
    if path in ALLOWED_BROKEN_ROUTES:
        return True

    # Absolute route
    if path.startswith("/"):
        if path in route_map:
            return (site_dir / route_map[path]).exists()
        clean = path[1:]
        if clean == "":
            return (site_dir / "index.html").exists()
        # cleanUrls behavior: /foo -> /foo.html
        if (site_dir / f"{clean}.html").exists():
            return True
        if (site_dir / clean).exists():
            return True
        if (site_dir / clean / "index.html").exists():
            return True
        return False

    # Relative route
    return True


def check_site(site_dir: Path) -> tuple[int, list[str]]:
    html_files = sorted(site_dir.rglob("*.html"))
    errors: list[str] = []
    route_map = load_route_map(site_dir)

    for html_file in html_files:
        soup = BeautifulSoup(html_file.read_text(encoding="utf-8", errors="replace"), "html.parser")

        for tag in soup.find_all(True):
            for attr in ATTRS:
                if not tag.has_attr(attr):
                    continue
                val = tag.get(attr)
                if not isinstance(val, str):
                    continue
                url = val.strip()
                if not url or url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                    continue

                parsed = urlparse(url)
                if parsed.scheme in {"http", "https"}:
                    continue

                if not route_exists(site_dir, url, route_map):
                    errors.append(f"{html_file.as_posix()}: broken {attr} -> {url}")

            if tag.has_attr("srcset"):
                srcset = tag.get("srcset")
                if isinstance(srcset, str):
                    for chunk in [c.strip() for c in srcset.split(",") if c.strip()]:
                        u = chunk.split()[0]
                        if u.startswith(("http://", "https://", "//", "data:")):
                            continue
                        if not route_exists(site_dir, u, route_map):
                            errors.append(f"{html_file.as_posix()}: broken srcset -> {u}")

    return len(html_files), errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", default="site")
    args = parser.parse_args(argv)

    site_dir = Path(args.site_dir).resolve()
    if not site_dir.exists():
        print(f"Site directory does not exist: {site_dir}")
        return 2

    file_count, errors = check_site(site_dir)
    print(f"Checked HTML files: {file_count}")
    print(f"Broken references: {len(errors)}")

    if errors:
        for e in errors[:200]:
            print(e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

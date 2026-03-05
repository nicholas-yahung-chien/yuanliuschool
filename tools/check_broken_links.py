#!/usr/bin/env python3
"""Check internal page links in mirrored HTML files."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

KNOWN_MISSING_ROUTES = {
    "/course/parfumerie-francaise",
    "/mld-level-1",
    "/ifa%E8%8B%B1%E5%9C%8B%E5%9C%8B%E9%9A%9B%E8%8A%B3%E7%99%82%E5%B8%AB%E8%AD%89%E7%85%A7%E8%AA%B2",
}


def load_routes(pages_csv: Path) -> tuple[set[str], dict[str, str], dict[str, str]]:
    routes: set[str] = set()
    route_to_local: dict[str, str] = {}
    local_to_route: dict[str, str] = {}

    with pages_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            route = (row.get("route") or "").strip()
            local_path = (row.get("local_path") or "").strip()
            if not route:
                continue
            routes.add(route)
            if local_path:
                route_to_local[route] = local_path
                local_to_route[local_path.replace("\\", "/")] = route

    return routes, route_to_local, local_to_route


def build_fragment_index(site_dir: Path) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for html in site_dir.rglob("*.html"):
        rel = html.relative_to(site_dir).as_posix()
        soup = BeautifulSoup(html.read_text(encoding="utf-8", errors="replace"), "html.parser")
        ids = {x.get("id") for x in soup.find_all(attrs={"id": True}) if isinstance(x.get("id"), str)}
        names = {x.get("name") for x in soup.find_all(attrs={"name": True}) if isinstance(x.get("name"), str)}
        index[rel] = ids | names
    return index


def normalize_route(current_route: str, href: str, base_hosts: set[str]) -> str | None:
    href = href.strip()
    if not href or href.startswith("#"):
        return current_route
    if href.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None

    parsed = urlparse(href)
    if parsed.scheme in {"http", "https"}:
        if parsed.netloc.lower() not in base_hosts:
            return None
        path = parsed.path or "/"
    else:
        base = f"https://yuanliuschool.com{current_route}"
        resolved = urljoin(base, href)
        p = urlparse(resolved)
        if p.netloc.lower() not in base_hosts:
            return None
        path = p.path or "/"

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--pages-csv", default="site/_meta/pages.csv")
    parser.add_argument("--output", default="site/_meta/broken_links.csv")
    args = parser.parse_args()

    site_dir = Path(args.site_dir).resolve()
    pages_csv = Path(args.pages_csv).resolve()
    output = Path(args.output).resolve()

    if not site_dir.exists() or not pages_csv.exists():
        print("Missing site dir or pages.csv")
        return 2

    routes, route_to_local, local_to_route = load_routes(pages_csv)
    route_map_path = site_dir / "_meta" / "route_map.json"
    if route_map_path.exists():
        route_map = json.loads(route_map_path.read_text(encoding="utf-8"))
        for route, local in route_map.items():
            routes.add(route)
            route_to_local[route] = local
            local_to_route[local.replace("\\", "/")] = route

    fragment_index = build_fragment_index(site_dir)
    base_hosts = {"yuanliuschool.com", "www.yuanliuschool.com", "yuanliuschool.vercel.app"}

    broken: list[tuple[str, str, str]] = []

    for local_rel, current_route in local_to_route.items():
        html_path = site_dir / local_rel
        if not html_path.exists():
            continue
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            target_route = normalize_route(current_route, href, base_hosts)
            if target_route is None:
                continue

            parsed = urlparse(href)
            fragment = parsed.fragment
            if href.startswith("#"):
                fragment = href[1:]

            if target_route not in routes:
                if target_route not in KNOWN_MISSING_ROUTES:
                    broken.append((current_route, href, "missing_route"))
                continue

            if fragment:
                target_local = route_to_local.get(target_route)
                if target_local:
                    anchors = fragment_index.get(target_local.replace("\\", "/"), set())
                    if fragment not in anchors:
                        broken.append((current_route, href, "missing_fragment"))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_route", "href", "reason"])
        for row in broken:
            w.writerow(row)

    print(f"Checked internal links from {len(local_to_route)} pages")
    print(f"Broken links found: {len(broken)}")

    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())

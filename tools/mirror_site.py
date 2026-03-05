#!/usr/bin/env python3
"""Mirror https://yuanliuschool.com into a static site folder.

- Seeds crawl with sitemap entries.
- Crawls same-host HTML pages.
- Downloads required assets into /assets using content hash filenames.
- Rewrites page links for static routing (clean URL style).
- Rewrites asset references in HTML/CSS to local /assets URLs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import posixpath
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple
from urllib.parse import ParseResult, urljoin, urlparse, urlunparse
from urllib.parse import unquote
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BASE_DOMAIN = "yuanliuschool.com"
BASE_URL = f"https://{BASE_DOMAIN}"
SITEMAP_INDEX = f"{BASE_URL}/sitemap.xml"
PAGE_HOSTS = {BASE_DOMAIN, f"www.{BASE_DOMAIN}"}
ASSET_HOST_ALLOWLIST = {
    BASE_DOMAIN,
    f"www.{BASE_DOMAIN}",
    "img1.wsimg.com",
}

# External destinations intentionally kept as external links.
EXTERNAL_LINK_ALLOWLIST = {
    "project01.yuanliuschool.com",
    "forms.gle",
    "docs.google.com",
    "www.google.com",
    "google.com",
    "youtu.be",
    "youtube.com",
    "www.youtube.com",
    "line.me",
    "lin.ee",
    "www.facebook.com",
    "www.instagram.com",
}

REQUEST_HEADERS = {
    "User-Agent": "yuanliu-static-mirror/1.0 (+https://yuanliuschool.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

STYLE_URL_RE = re.compile(r"url\((?P<q>['\"]?)(?P<u>[^'\")]+)(?P=q)\)", re.IGNORECASE)
IMPORT_URL_RE = re.compile(
    r"@import\s+(?:url\((?P<q1>['\"]?)(?P<u1>[^'\")]+)(?P=q1)\)|(?P<q2>['\"])(?P<u2>[^'\"]+)(?P=q2))",
    re.IGNORECASE,
)


@dataclass
class MirrorStats:
    pages_downloaded: int = 0
    pages_written: int = 0
    assets_downloaded: int = 0
    assets_written: int = 0
    assets_failed: int = 0


class Mirror:
    def __init__(
        self,
        output_dir: Path,
        delay_seconds: float = 0.12,
        timeout: int = 25,
        max_pages: int = 500,
    ) -> None:
        self.output_dir = output_dir
        self.site_dir = output_dir
        self.assets_dir = self.site_dir / "assets"
        self.manifest_dir = self.site_dir / "_meta"
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.max_pages = max_pages

        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)

        self.stats = MirrorStats()
        self.discovered_pages: Set[str] = set()
        self.crawled_pages: Set[str] = set()

        self.page_html_by_url: Dict[str, str] = {}
        self.page_route_to_local: Dict[str, str] = {}
        self.page_url_to_local: Dict[str, str] = {}
        self.asset_local_by_url: Dict[str, str] = {}
        self.asset_origin_by_local: Dict[str, str] = {}
        self.failed_assets: Dict[str, str] = {}

    def run(self) -> None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

        seeds = self._load_sitemap_urls()
        if BASE_URL not in seeds:
            seeds.insert(0, BASE_URL)

        queue = deque()
        for seed in seeds:
            canon = self._canonical_page_url(seed)
            if canon and canon not in self.discovered_pages:
                self.discovered_pages.add(canon)
                queue.append(canon)

        while queue and len(self.crawled_pages) < self.max_pages:
            url = queue.popleft()
            if url in self.crawled_pages:
                continue
            html = self._fetch_text(url)
            if html is None:
                continue

            self.crawled_pages.add(url)
            self.page_html_by_url[url] = html
            self.stats.pages_downloaded += 1

            for new_page in self._discover_internal_pages(url, html):
                if new_page not in self.discovered_pages:
                    self.discovered_pages.add(new_page)
                    queue.append(new_page)

            time.sleep(self.delay_seconds)

        # Rewrite and write pages after crawl is complete.
        for url in sorted(self.crawled_pages):
            rewritten = self._rewrite_page_html(url, self.page_html_by_url[url])
            route = self._page_url_to_route(url)
            local_rel = self._route_to_local_path(route)
            self.page_route_to_local[route] = local_rel
            self.page_url_to_local[url] = local_rel
            local_path = self.site_dir / local_rel
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_text(local_path, rewritten)
            self.stats.pages_written += 1

        self._write_reports()

    def _write_reports(self) -> None:
        summary = {
            "base_url": BASE_URL,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pages_discovered": len(self.discovered_pages),
            "pages_crawled": len(self.crawled_pages),
            "assets_downloaded": self.stats.assets_downloaded,
            "assets_written": self.stats.assets_written,
            "assets_failed": self.stats.assets_failed,
        }
        (self.manifest_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        with (self.manifest_dir / "pages.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["url", "route", "local_path"])
            for url in sorted(self.crawled_pages):
                route = self._page_url_to_route(url)
                w.writerow([url, route, self.page_url_to_local[url]])

        (self.manifest_dir / "route_map.json").write_text(
            json.dumps(self.page_route_to_local, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with (self.manifest_dir / "assets.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["origin_url", "local_path"])
            for local, origin in sorted(self.asset_origin_by_local.items()):
                w.writerow([origin, local])

        with (self.manifest_dir / "assets_failed.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["origin_url", "error"])
            for origin, err in sorted(self.failed_assets.items()):
                w.writerow([origin, err])

        self._write_vercel_config()

    def _write_vercel_config(self) -> None:
        rewrites = []
        seen_rewrites: Set[Tuple[str, str]] = set()
        for route, local_rel in sorted(self.page_route_to_local.items()):
            if route == "/":
                continue
            expected = f"{route.lstrip('/')}.html"
            if local_rel != expected:
                candidates = [route]
                decoded = unquote(route)
                if decoded != route:
                    candidates.append(decoded)
                for source in candidates:
                    key = (source, local_rel)
                    if key in seen_rewrites:
                        continue
                    seen_rewrites.add(key)
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

        (self.site_dir / "vercel.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )

    def _load_sitemap_urls(self) -> list[str]:
        idx_text = self._fetch_text(SITEMAP_INDEX)
        if not idx_text:
            raise RuntimeError(f"Cannot fetch sitemap index: {SITEMAP_INDEX}")

        sitemaps = []
        root = ET.fromstring(idx_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        if root.tag.endswith("sitemapindex"):
            for node in root.findall("sm:sitemap/sm:loc", ns):
                if node.text:
                    sitemaps.append(node.text.strip())
        elif root.tag.endswith("urlset"):
            # Fallback if server returns flat sitemap directly.
            sitemaps.append(SITEMAP_INDEX)
        else:
            raise RuntimeError("Unknown sitemap format")

        urls: list[str] = []
        for sitemap_url in sitemaps:
            sm_text = self._fetch_text(sitemap_url)
            if not sm_text:
                continue
            try:
                sm_root = ET.fromstring(sm_text)
            except ET.ParseError:
                continue
            if not sm_root.tag.endswith("urlset"):
                continue
            for node in sm_root.findall("sm:url/sm:loc", ns):
                if node.text:
                    urls.append(node.text.strip())

        deduped: list[str] = []
        seen: Set[str] = set()
        for u in urls:
            canon = self._canonical_page_url(u)
            if canon and canon not in seen:
                seen.add(canon)
                deduped.append(canon)
        return deduped

    def _fetch_text(self, url: str) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
            return resp.text
        except Exception:
            return None

    def _fetch_bytes(self, url: str) -> Optional[Tuple[bytes, str]]:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "")
            return resp.content, ct
        except Exception as exc:
            self.failed_assets[url] = str(exc)
            self.stats.assets_failed += 1
            return None

    def _discover_internal_pages(self, page_url: str, html: str) -> Set[str]:
        soup = BeautifulSoup(html, "html.parser")
        discovered: Set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            resolved = self._resolve_url(page_url, href)
            if not resolved:
                continue
            parsed = urlparse(resolved)
            if parsed.netloc.lower() in PAGE_HOSTS and not self._is_probably_page(parsed):
                continue
            canon = self._canonical_page_url(resolved)
            if canon:
                discovered.add(canon)
        return discovered

    def _rewrite_page_html(self, page_url: str, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")

        # Remove analytics scripts to avoid runtime third-party dependencies.
        for s in soup.find_all("script", src=True):
            src = (s.get("src") or "").lower()
            if "googletagmanager.com" in src or "signals/js/clients" in src:
                s.decompose()

        for tag in soup.find_all(True):
            self._rewrite_tag_attrs(page_url, tag)

        # Style blocks
        for style in soup.find_all("style"):
            if style.string:
                style.string.replace_with(self._rewrite_css_text(style.string, page_url))

        return str(soup)

    def _rewrite_tag_attrs(self, page_url: str, tag) -> None:
        # A href: keep external links, rewrite internal page links.
        if tag.name == "a" and tag.has_attr("href"):
            href = tag.get("href")
            if isinstance(href, str):
                tag["href"] = self._rewrite_anchor_href(page_url, href)

        resource_attrs = ["src", "href", "poster", "data-src", "data-bg", "data-background"]
        for attr in resource_attrs:
            if not tag.has_attr(attr):
                continue
            val = tag.get(attr)
            if not isinstance(val, str):
                continue

            if attr == "href" and tag.name == "a":
                continue

            rewritten = self._rewrite_resource_url(page_url, val, tag_name=tag.name, attr_name=attr)
            if rewritten:
                tag[attr] = rewritten

        if tag.has_attr("style"):
            style_value = tag.get("style")
            if isinstance(style_value, str):
                tag["style"] = self._rewrite_css_text(style_value, page_url)

        # Social meta images.
        if tag.name == "meta" and tag.has_attr("content"):
            prop = (tag.get("property") or "").lower()
            name = (tag.get("name") or "").lower()
            if prop == "og:image" or name == "twitter:image":
                content = tag.get("content")
                if isinstance(content, str):
                    rewritten = self._rewrite_resource_url(page_url, content, tag_name="meta", attr_name="content")
                    if rewritten:
                        tag["content"] = rewritten

    def _rewrite_anchor_href(self, page_url: str, href: str) -> str:
        href = href.strip()
        if not href or href.startswith("#"):
            return href
        if href.startswith(("mailto:", "tel:", "javascript:")):
            return href

        resolved = self._resolve_url(page_url, href)
        if not resolved:
            return href
        parsed = urlparse(resolved)

        if parsed.netloc.lower() in PAGE_HOSTS:
            if self._is_probably_page(parsed):
                return self._internal_route_from_parsed(parsed)
            # Non-page same-host resource, localize if possible.
            local = self._download_asset(resolved)
            return local or href

        return href

    def _rewrite_resource_url(self, page_url: str, raw_url: str, tag_name: str, attr_name: str) -> Optional[str]:
        raw_url = raw_url.strip()
        if not raw_url:
            return raw_url
        if raw_url.startswith(("data:", "mailto:", "tel:", "javascript:", "#")):
            return raw_url

        resolved = self._resolve_url(page_url, raw_url)
        if not resolved:
            return raw_url
        parsed = urlparse(resolved)

        if tag_name == "iframe":
            # Keep iframe-based external embeds as requested.
            return raw_url if parsed.netloc.lower() not in PAGE_HOSTS else self._internal_route_from_parsed(parsed)

        if parsed.netloc.lower() in PAGE_HOSTS and self._is_probably_page(parsed):
            return self._internal_route_from_parsed(parsed)

        local = self._download_asset(resolved)
        return local or raw_url

    def _rewrite_css_text(self, css_text: str, base_url: str) -> str:
        def repl_import(m: re.Match[str]) -> str:
            u = m.group("u1") or m.group("u2") or ""
            if not u or u.startswith(("data:", "#")):
                return m.group(0)
            resolved = self._resolve_url(base_url, u)
            if not resolved:
                return m.group(0)
            local = self._download_asset(resolved)
            if not local:
                return m.group(0)
            return m.group(0).replace(u, local)

        css_text = IMPORT_URL_RE.sub(repl_import, css_text)

        def repl_url(m: re.Match[str]) -> str:
            u = m.group("u") or ""
            if not u or u.startswith(("data:", "#")):
                return m.group(0)
            resolved = self._resolve_url(base_url, u)
            if not resolved:
                return m.group(0)
            local = self._download_asset(resolved)
            if not local:
                return m.group(0)
            return m.group(0).replace(u, local)

        return STYLE_URL_RE.sub(repl_url, css_text)

    def _download_asset(self, asset_url: str) -> Optional[str]:
        canon = self._canonical_asset_url(asset_url)
        if not canon:
            return None
        if canon in self.asset_local_by_url:
            return self.asset_local_by_url[canon]

        parsed = urlparse(canon)
        host = parsed.netloc.lower()

        # Do not attempt to localize known external app links.
        if host in EXTERNAL_LINK_ALLOWLIST:
            return canon
        if host not in ASSET_HOST_ALLOWLIST:
            return canon

        # Localize all same-site and wsimg resources. Others are attempted too for visual fidelity.
        fetched = self._fetch_bytes(canon)
        if fetched is None:
            return None
        content, content_type = fetched

        ext = self._guess_extension(parsed.path, content_type)
        digest = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:20]
        filename = f"{digest}{ext}"
        local_rel = f"/assets/{filename}"
        local_path = self.assets_dir / filename

        if self._is_css(content_type, parsed.path):
            css_text = content.decode("utf-8", errors="replace")
            css_text = self._rewrite_css_text(css_text, canon)
            self._write_text(local_path, css_text)
        else:
            local_path.write_bytes(content)

        self.asset_local_by_url[canon] = local_rel
        self.asset_origin_by_local[local_rel] = canon
        self.stats.assets_downloaded += 1
        self.stats.assets_written += 1
        return local_rel

    def _guess_extension(self, path: str, content_type: str) -> str:
        ext = Path(path).suffix.lower()
        if ext and len(ext) <= 8 and re.fullmatch(r"\.[a-z0-9]+", ext):
            return ext

        clean_ct = content_type.split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(clean_ct) if clean_ct else None
        if guessed == ".jpe":
            return ".jpg"
        if guessed:
            return guessed
        return ".bin"

    def _is_css(self, content_type: str, path: str) -> bool:
        if path.lower().endswith(".css"):
            return True
        return "text/css" in content_type.lower()

    def _resolve_url(self, base_url: str, raw_url: str) -> Optional[str]:
        raw_url = raw_url.strip()
        if not raw_url:
            return None
        if raw_url.startswith("//"):
            return "https:" + raw_url
        return urljoin(base_url, raw_url)

    def _canonical_page_url(self, raw_url: str) -> Optional[str]:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = parsed.netloc.lower()
        if host not in PAGE_HOSTS:
            return None

        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        normalized = ParseResult(
            scheme="https",
            netloc=BASE_DOMAIN,
            path=path,
            params="",
            query="",
            fragment="",
        )
        return urlunparse(normalized)

    def _canonical_asset_url(self, raw_url: str) -> Optional[str]:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        normalized = ParseResult(
            scheme="https",
            netloc=parsed.netloc.lower(),
            path=parsed.path or "/",
            params="",
            query=parsed.query,
            fragment="",
        )
        return urlunparse(normalized)

    def _is_probably_page(self, parsed: ParseResult) -> bool:
        path = parsed.path or "/"
        if path.endswith("/") or path == "/":
            return True
        name = posixpath.basename(path)
        if "." not in name:
            return True
        ext = posixpath.splitext(name)[1].lower()
        return ext in {".html", ".htm"}

    def _internal_route_from_parsed(self, parsed: ParseResult) -> str:
        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        route = path
        if parsed.query:
            route += f"?{parsed.query}"
        if parsed.fragment:
            route += f"#{parsed.fragment}"
        return route

    def _page_url_to_route(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return path

    def _route_to_local_path(self, route: str) -> str:
        if route == "/":
            return "index.html"

        clean = route.lstrip("/")
        segments = clean.split("/")
        too_long = len(clean) > 120 or any(len(seg) > 80 for seg in segments)
        if too_long:
            digest = hashlib.sha1(route.encode("utf-8")).hexdigest()[:20]
            return f"__pages/{digest}.html"
        return f"{clean}.html"

    def _write_text(self, path: Path, text: str) -> None:
        # Avoid Windows MAX_PATH issues when deeply nested paths appear.
        target = str(path.resolve())
        if os.name == "nt":
            if target.startswith("\\\\"):
                target = "\\\\?\\UNC\\" + target[2:]
            else:
                target = "\\\\?\\" + target
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mirror yuanliuschool.com into static files")
    p.add_argument("--output", default="site", help="Output directory (default: site)")
    p.add_argument("--delay", type=float, default=0.12, help="Delay between page requests")
    p.add_argument("--timeout", type=int, default=25, help="HTTP timeout seconds")
    p.add_argument("--max-pages", type=int, default=500, help="Maximum pages to crawl")
    return p.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    out = Path(args.output).resolve()
    mirror = Mirror(
        output_dir=out,
        delay_seconds=args.delay,
        timeout=args.timeout,
        max_pages=args.max_pages,
    )
    mirror.run()

    summary_path = out / "_meta" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

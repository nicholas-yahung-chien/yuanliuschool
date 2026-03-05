#!/usr/bin/env python3
"""Acceptance audit: compare source and mirrored pages route-by-route."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


def normalize_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return " ".join(text.split())


def html_metrics(html: str) -> dict[str, int | str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    return {
        "title": title,
        "images": len(soup.find_all("img")),
        "links": len(soup.find_all("a", href=True)),
        "forms": len(soup.find_all("form")),
        "scripts": len(soup.find_all("script")),
    }


def load_routes(pages_csv: Path) -> list[str]:
    routes: list[str] = []
    with pages_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            route = (row.get("route") or "").strip()
            if route:
                routes.append(route)
    return sorted(set(routes))


def fetch(session: requests.Session, url: str, timeout: int) -> tuple[int, str, str]:
    try:
        resp = session.get(url, timeout=timeout)
        ct = resp.headers.get("Content-Type", "")
        text = resp.text if "text/html" in ct else ""
        return resp.status_code, ct, text
    except requests.RequestException:
        return 0, "", ""


def summarize(rows: list[dict], report_md: Path, report_csv: Path) -> None:
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", encoding="utf-8", newline="") as f:
        fields = [
            "route",
            "source_status",
            "target_status",
            "title_match",
            "text_similarity",
            "source_images",
            "target_images",
            "source_links",
            "target_links",
            "source_forms",
            "target_forms",
            "notes",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    total = len(rows)
    target_ok = sum(1 for r in rows if r["target_status"] == 200)
    source_ok = sum(1 for r in rows if r["source_status"] == 200)
    low_similarity = [r for r in rows if r["text_similarity"] < 0.90]
    title_mismatch = [r for r in rows if r["title_match"] == 0]
    missing_target = [r for r in rows if r["target_status"] != 200]

    top_low = sorted(low_similarity, key=lambda r: r["text_similarity"])[:30]

    lines = [
        "# Acceptance Audit Report",
        "",
        f"- Generated: {dt.datetime.now(dt.UTC).strftime('%Y-%m-%d %H:%M:%SZ')}",
        f"- Total routes: {total}",
        f"- Source HTTP 200: {source_ok}/{total}",
        f"- Target HTTP 200: {target_ok}/{total}",
        f"- Low text similarity (<0.90): {len(low_similarity)}",
        f"- Title mismatch: {len(title_mismatch)}",
        f"- Target non-200: {len(missing_target)}",
        "",
        "## Top Low Similarity Routes",
        "",
        "| Route | Similarity | Source Img | Target Img | Notes |",
        "|---|---:|---:|---:|---|",
    ]

    for row in top_low:
        lines.append(
            f"| `{row['route']}` | {row['text_similarity']:.3f} | {row['source_images']} | {row['target_images']} | {row['notes']} |"
        )

    lines.extend([
        "",
        "## Target Non-200 Routes",
        "",
        "| Route | Source | Target | Notes |",
        "|---|---:|---:|---|",
    ])
    for row in missing_target[:50]:
        lines.append(f"| `{row['route']}` | {row['source_status']} | {row['target_status']} | {row['notes']} |")

    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(routes: Iterable[str], source_base: str, target_base: str, timeout: int) -> list[dict]:
    s = requests.Session()
    s.headers.update({"User-Agent": "yuanliu-acceptance-audit/1.0"})

    rows: list[dict] = []
    for route in routes:
        source_url = urljoin(source_base.rstrip("/") + "/", route.lstrip("/"))
        target_url = urljoin(target_base.rstrip("/") + "/", route.lstrip("/"))

        src_status, src_ct, src_html = fetch(s, source_url, timeout)
        tgt_status, tgt_ct, tgt_html = fetch(s, target_url, timeout)

        src_metrics = html_metrics(src_html) if src_html else {"title": "", "images": 0, "links": 0, "forms": 0, "scripts": 0}
        tgt_metrics = html_metrics(tgt_html) if tgt_html else {"title": "", "images": 0, "links": 0, "forms": 0, "scripts": 0}

        similarity = 0.0
        if src_html and tgt_html:
            src_text = normalize_text(src_html)
            tgt_text = normalize_text(tgt_html)
            similarity = difflib.SequenceMatcher(a=src_text, b=tgt_text).ratio()

        notes = []
        if src_status != 200:
            notes.append("source_non_200")
        if tgt_status != 200:
            notes.append("target_non_200")
        if src_metrics["images"] != tgt_metrics["images"]:
            notes.append("image_count_diff")
        if src_metrics["forms"] != tgt_metrics["forms"]:
            notes.append("form_count_diff")

        row = {
            "route": route,
            "source_status": src_status,
            "target_status": tgt_status,
            "title_match": 1 if src_metrics["title"] == tgt_metrics["title"] else 0,
            "text_similarity": round(similarity, 6),
            "source_images": src_metrics["images"],
            "target_images": tgt_metrics["images"],
            "source_links": src_metrics["links"],
            "target_links": tgt_metrics["links"],
            "source_forms": src_metrics["forms"],
            "target_forms": tgt_metrics["forms"],
            "notes": ",".join(notes),
        }
        rows.append(row)

    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-base", default="https://yuanliuschool.com")
    parser.add_argument("--target-base", default="https://yuanliuschool.vercel.app")
    parser.add_argument("--pages-csv", default="site/_meta/pages.csv")
    parser.add_argument("--report-dir", default="reports/acceptance")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--fail-on-target-non200", action="store_true")
    args = parser.parse_args()

    pages_csv = Path(args.pages_csv).resolve()
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    routes = load_routes(pages_csv)
    rows = run(routes, args.source_base, args.target_base, args.timeout)

    report_csv = report_dir / "acceptance-audit.csv"
    report_md = report_dir / "acceptance-summary.md"
    summarize(rows, report_md, report_csv)

    target_non_200 = sum(1 for r in rows if r["target_status"] != 200)
    print(f"Total routes: {len(rows)}")
    print(f"Target non-200: {target_non_200}")
    print(f"Report: {report_md}")

    if args.fail_on_target_non200 and target_non_200 > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

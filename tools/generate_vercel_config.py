#!/usr/bin/env python3
"""Generate site/vercel.json and route fallback page from route map."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path


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

    fallback_map: dict[str, str] = {}

    for route, local_rel in sorted(route_map.items()):
        if route == "/":
            continue
        expected = f"{route.lstrip('/')}.html"
        if local_rel == expected or not local_rel.startswith("__pages/"):
            continue
        destination = "/" + local_rel.removesuffix(".html")
        fallback_map[route] = destination

    config = {
        "cleanUrls": True,
        "trailingSlash": False,
        "headers": [
            {
                "source": "/(.*)",
                "headers": [{"key": "X-Content-Type-Options", "value": "nosniff"}],
            }
        ],
        "routes": [
            {"handle": "filesystem"},
            {"src": "/(.*)", "dest": "/route_fallback.html"},
        ],
    }

    (site_dir / "vercel.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )

    fallback_map_json = json.dumps(
        dict(sorted(fallback_map.items())), ensure_ascii=False, separators=(",", ":")
    )
    fallback_map_b64 = base64.b64encode(fallback_map_json.encode("utf-8")).decode("ascii")

    fallback_html = f"""<!doctype html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Loading...</title>
  <style>
    body {{ font-family: sans-serif; padding: 24px; }}
  </style>
</head>
<body>
  <p>Loading page...</p>
  <script>
    const routeMap = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob('{fallback_map_b64}'), c => c.charCodeAt(0))));
    const raw = window.location.pathname.replace(/\\/$/, '') || '/';
    const candidates = [raw];
    try {{
      candidates.push(decodeURI(raw));
    }} catch (_e) {{}}
    try {{
      candidates.push(encodeURI(raw));
    }} catch (_e) {{}}

    let target = null;
    for (const c of candidates) {{
      if (routeMap[c]) {{
        target = routeMap[c];
        break;
      }}
    }}

    if (!target) {{
      document.title = '404';
      document.body.innerHTML = '<h1>404</h1><p>Page not found.</p>';
    }} else {{
      fetch(target, {{ credentials: 'same-origin' }})
        .then(r => r.ok ? r.text() : Promise.reject(new Error('fetch_failed')))
        .then(html => {{
          document.open();
          document.write(html);
          document.close();
        }})
        .catch(() => {{
          document.title = '404';
          document.body.innerHTML = '<h1>404</h1><p>Page not found.</p>';
        }});
    }}
  </script>
</body>
</html>
"""
    (site_dir / "route_fallback.html").write_text(fallback_html, encoding="utf-8", newline="\n")

    print(f"Wrote {site_dir / 'vercel.json'}")
    print(f"Wrote {site_dir / 'route_fallback.html'}")
    print(f"Fallback route map entries: {len(fallback_map)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

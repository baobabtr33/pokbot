"""Build the static site for GitHub Pages.

    python3 scripts/build_pages.py --site https://user.github.io/pokbot \
        --api https://pokbot-server.example --out public

Site URLs (canonical, Open Graph, agent docs) point at --site; the installer, API and
websockets stay on --api, the game server. Run the server with POKBOT_SITE_URL=<site> so its
own copies of these pages redirect here and /api allows the site's origin. Stdlib only.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
TEMPLATED = ("index.html", "llms.txt", "bots.md")
COPIED = ("style.css", "og.png")
PAGES = ("/", "/bots.md", "/llms.txt")  # keep in sync with SITE_PAGES in server app.py


def build(site: str, api: str, out: Path) -> None:
    site, api = site.rstrip("/"), api.rstrip("/")
    out.mkdir(parents=True, exist_ok=True)
    for name in TEMPLATED:
        text = (WEB / name).read_text().replace("__POKBOT_SITE__", site).replace("__POKBOT_BASE__", api)
        (out / name).write_text(text)
    for name in COPIED:
        shutil.copyfile(WEB / name, out / name)
    for path in WEB.glob("google*.html"):  # Search Console ownership files; must stay published
        shutil.copyfile(path, out / path.name)
    urls = "".join(f"<url><loc>{site}{p}</loc></url>" for p in PAGES)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'
    )
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site}/sitemap.xml\n")
    (out / ".nojekyll").write_text("")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", required=True, help="public URL of the static site")
    parser.add_argument("--api", required=True, help="public URL of the game server")
    parser.add_argument("--out", type=Path, default=Path("public"))
    args = parser.parse_args()
    build(args.site, args.api, args.out)
    print(f"built {args.out} for {args.site} (api {args.api})")


if __name__ == "__main__":
    main()

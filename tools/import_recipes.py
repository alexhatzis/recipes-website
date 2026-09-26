#!/usr/bin/env python3
"""
One-time importer: turn a Google Sheets "Web page (.html)" export into recipe
Markdown files in this repo's format.

The sheet is expected to have one column per category, with each cell being a
hyperlink whose text is the recipe name and whose target is the recipe URL.

For every recipe it:
  - fetches the page (browser-like User-Agent)
  - extracts data via recipe-scrapers (site parsers + JSON-LD "wild mode")
  - downloads the recipe image into img/<category>/<slug>.<ext>
  - writes recipes/<category>/<slug>.md with frontmatter + ingredients/steps

Filter metadata (the normalized `ingredients:` / `tags:` arrays used by the
site's filter) is intentionally NOT auto-filled — it's left as TODO comments
for a manual / later pass, because it needs curated, normalized terms.

Usage:
  python tools/import_recipes.py SHEET.html --dry-run        # parse only, print entries
  python tools/import_recipes.py SHEET.html --limit 3        # live test on first 3
  python tools/import_recipes.py SHEET.html                  # full run
  python tools/import_recipes.py SHEET.html --skip-images    # text only
"""
import argparse
import os
import re
import sys
import time
import mimetypes
import unicodedata
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from recipe_scrapers import scrape_html

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPES_DIR = os.path.join(REPO, "recipes")
IMG_DIR = os.path.join(REPO, "img")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def slugify(text):
    # Transliterate accents to ASCII so "Crème Brûlée" -> "creme-brulee".
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"^-+|-+$", "", text) or "untitled"


def unwrap_google_url(href):
    """Google Sheets often wraps links as https://www.google.com/url?q=REAL&sa=..."""
    if not href:
        return None
    p = urlparse(href)
    if p.netloc.endswith("google.com") and p.path == "/url":
        q = parse_qs(p.query).get("q")
        if q:
            return q[0]
    return href


def _looks_like_column_letters(texts):
    """A row that is just spreadsheet column letters (A, B, ... AA) — gridline, not data."""
    vals = [t for t in texts if t]
    return bool(vals) and all(re.fullmatch(r"[A-Z]{1,2}", t) for t in vals)


def parse_sheet(html_path):
    """Yield (category, title, url) from the exported sheet HTML.

    Google's export includes gridline headers (column letters as <th>, row
    numbers as <th>); we look only at <td> cells. The category-name row is the
    first row that has text but no hyperlinks — recipe rows always have links.
    """
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    table = soup.find("table")
    if not table:
        sys.exit("No <table> found in the HTML export.")

    # Data cells only — drops the column-letter and row-number <th> gridlines.
    rows = [tr.find_all("td") for tr in table.find_all("tr")]
    rows = [r for r in rows if r]

    header = None
    start = 0
    for idx, cells in enumerate(rows):
        texts = [c.get_text(strip=True) for c in cells]
        has_link = any(c.find("a") for c in cells)
        if any(texts) and not has_link and not _looks_like_column_letters(texts):
            header = texts
            start = idx + 1
            break

    if header is None:
        sys.exit("Could not find a category header row (a row of names with no links).")

    for cells in rows[start:]:
        for i, cell in enumerate(cells):
            link = cell.find("a")
            if not link:
                continue
            url = unwrap_google_url(link.get("href"))
            title = link.get_text(strip=True)
            category = (header[i] if i < len(header) else "") or "uncategorized"
            if url and title:
                yield category.strip(), title, url


def scrape(url):
    """Return a dict of recipe fields, or raise on failure."""
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    resp.raise_for_status()
    scraper = scrape_html(resp.text, org_url=url, wild_mode=True)

    def safe(fn):
        try:
            return fn()
        except Exception:
            return None

    return {
        "title": safe(scraper.title),
        "ingredients": safe(scraper.ingredients) or [],
        "instructions": safe(scraper.instructions_list)
        or ([s for s in (safe(scraper.instructions) or "").split("\n") if s.strip()]),
        "total_time": safe(scraper.total_time),  # minutes (int)
        "yields": safe(scraper.yields),          # e.g. "4 servings"
        "image": safe(scraper.image),            # URL
    }


def servings_number(yields):
    if not yields:
        return None
    m = re.search(r"\d+", str(yields))
    return m.group(0) if m else None


def download_image(url, dest_dir, slug):
    os.makedirs(dest_dir, exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    resp.raise_for_status()
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = mimetypes.guess_extension(resp.headers.get("content-type", "").split(";")[0]) or ".jpg"
    if ext == ".jpe":
        ext = ".jpg"
    fname = slug + ext
    with open(os.path.join(dest_dir, fname), "wb") as f:
        f.write(resp.content)
    return fname


def yaml_escape(s):
    s = str(s).replace('"', '\\"')
    return f'"{s}"'


def write_markdown(category_slug, slug, data, source_url, image_rel):
    fm = ["---", f"title: {yaml_escape(data['title'])}"]
    if image_rel:
        fm.append(f"image: {image_rel}")
    if data.get("total_time"):
        fm.append(f"time: {data['total_time']} minutes")
    sv = servings_number(data.get("yields"))
    if sv:
        fm.append(f"servings: {sv}")
    fm.append(f"source: {source_url}")
    fm.append("# ingredients: []   # TODO: normalized filter terms (e.g. tomato, garlic)")
    fm.append("# tags: []          # TODO: filter tags (e.g. vegetarian, quick)")
    fm.append("---")

    body = ["", f"# {data['title']}", ""]
    if data["ingredients"]:
        body.append("## ingredients")
        body += [f"* {i}" for i in data["ingredients"]]
        body.append("")
    if data["instructions"]:
        body.append("## steps")
        body += [f"{n}. {s}" for n, s in enumerate(data["instructions"], 1)]
        body.append("")
    body += ["## based on", f"* {source_url}", ""]

    out_dir = os.path.join(RECIPES_DIR, category_slug)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, slug + ".md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(fm + body))
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet_html", help="Path to the Google Sheets .html export")
    ap.add_argument("--limit", type=int, help="Only process the first N recipes (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="Parse the sheet and list entries; no fetching or writing")
    ap.add_argument("--skip-images", action="store_true", help="Do not download images")
    ap.add_argument("--delay", type=float, default=1.5, help="Seconds to wait between requests (default 1.5)")
    args = ap.parse_args()

    entries = list(parse_sheet(args.sheet_html))
    if args.limit:
        entries = entries[: args.limit]
    print(f"Parsed {len(entries)} recipe link(s).\n")

    if args.dry_run:
        for cat, title, url in entries:
            print(f"  [{cat}] {title}\n        {url}")
        return

    ok, failed = [], []
    for idx, (category, title, url) in enumerate(entries, 1):
        cat_slug = slugify(category)
        slug = slugify(title)
        print(f"[{idx}/{len(entries)}] {category}/{title}")
        try:
            data = scrape(url)
            # The sheet's name is canonical — you've curated/adapted these
            # (e.g. "Chickpea Tikka Masala" from a chicken recipe), so it wins
            # over the scraped page title.
            data["title"] = title
            if not data["ingredients"] and not data["instructions"]:
                raise ValueError("no ingredients or instructions extracted")

            image_rel = None
            if data["image"] and not args.skip_images:
                try:
                    fname = download_image(data["image"], os.path.join(IMG_DIR, cat_slug), slug)
                    image_rel = f"{cat_slug}/{fname}"
                except Exception as e:
                    print(f"    image download failed: {e}")

            path = write_markdown(cat_slug, slug, data, url, image_rel)
            print(f"    -> {os.path.relpath(path, REPO)}")
            ok.append((title, url))
        except Exception as e:
            print(f"    FAILED: {e}")
            failed.append((category, title, url, str(e)))
        time.sleep(args.delay)

    print(f"\nDone. {len(ok)} imported, {len(failed)} failed.")
    if failed:
        report = os.path.join(REPO, "tools", "import-failures.txt")
        with open(report, "w", encoding="utf-8") as f:
            for cat, title, url, err in failed:
                f.write(f"[{cat}] {title}\t{url}\t{err}\n")
        print(f"Failures written to {os.path.relpath(report, REPO)} — handle these manually.")


if __name__ == "__main__":
    main()

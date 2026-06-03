# Recipe importer (one-time)

`import_recipes.py` bulk-imports recipes from a Google Sheets export into this
repo's Markdown format. See the docstring at the top of the script for details.

## Setup

```bash
cd tools
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Get the sheet

In Google Sheets: **File → Download → Web page (.html)**. If it downloads a
`.zip`, unzip it; you want the `.html` file. The sheet should have one column
per category, each cell a hyperlink whose text is the recipe name.

## Run

```bash
# 1. Verify the sheet parses correctly — prints every (category, title, url), no network:
python import_recipes.py /path/to/sheet.html --dry-run

# 2. Live test on the first few before committing to all 100:
python import_recipes.py /path/to/sheet.html --limit 3

# 3. Full run:
python import_recipes.py /path/to/sheet.html
```

Other flags: `--skip-images` (text only), `--delay 2` (seconds between requests).

## After importing

- Sites that block automated requests or lack structured data are written to
  `tools/import-failures.txt` — handle those by hand (paste into cooked.wiki, or
  copy/paste manually into a `.md`).
- The importer fills `title`, `image`, `time`, `servings`, and `source`. It
  leaves `ingredients:` / `tags:` (the normalized **filter** vocabulary) as
  TODO comments — fill those with `fill_filter_vocab.py` (below).
- Run `npm run build` from the repo root and review `index.html`.

## Filling the filter vocabulary

`fill_filter_vocab.py` reads each recipe whose `ingredients:`/`tags:` are still
TODO comments, asks Claude (`claude-opus-4-8`) to derive a normalized,
filter-ready ingredient list + tags from the title and ingredient section, and
rewrites those two frontmatter lines. Files with a real `ingredients:` key are
skipped, so it's safe to re-run.

Requires an API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python fill_filter_vocab.py --dry-run     # list recipes needing vocab; no API calls
python fill_filter_vocab.py --limit 3     # process 3, then review `git diff`
python fill_filter_vocab.py               # process all
```

It is **faithful to the recipe text**: an un-edited adapted recipe that still
lists "chicken" will get `chicken` in its ingredients. Edit the adapted recipes
listed in `needs-review.txt` first, then re-run this to refresh them.


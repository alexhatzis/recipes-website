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
  TODO comments — fill those in a later pass.
- Run `npm run build` from the repo root and review `index.html`.

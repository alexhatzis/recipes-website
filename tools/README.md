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
TODO comments, asks Claude to derive a normalized, filter-ready ingredient list +
tags from the title and ingredient section, and rewrites those two frontmatter
lines. Files with a real `ingredients:` key are skipped, so it's safe to re-run.

It calls Claude through Apple's internal **Floodgate** interactive API (the
standard Anthropic SDK pointed at `https://floodgate.g.apple.com/api/anthropic`).
By default it mints an auth token via `appleconnect getToken`; set
`FLOODGATE_TOKEN` to supply your own, or override the command with `--auth-cmd`.

> **TLS note:** On the corporate network, requests go through TLS inspection with
> an internal Apple root CA that Python doesn't trust out of the box (curl works
> because it reads the macOS keychain). Pick whichever applies:
> - **Python 3.10+:** `pip install truststore` (in `requirements.txt`) — reads the
>   keychain automatically, no flags needed.
> - **Python 3.9** (truststore unavailable): export the keychain to a PEM and pass
>   `--ca-bundle`:
>   ```bash
>   security find-certificate -a -p /Library/Keychains/System.keychain > /tmp/ca.pem
>   security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> /tmp/ca.pem
>   python fill_filter_vocab.py --ca-bundle /tmp/ca.pem --list-models
>   ```
>   (`REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` env vars work too.)
> - **Last resort:** `--insecure` skips verification entirely.

```bash
python fill_filter_vocab.py --list-models   # show available model IDs (e.g. anthropic.claude-*)
python fill_filter_vocab.py --dry-run       # list recipes needing vocab; no API calls
python fill_filter_vocab.py --limit 3       # process 3, then review `git diff`
python fill_filter_vocab.py                 # process all
python fill_filter_vocab.py --model anthropic.claude-opus-4-8   # if an Opus ID is available
```

Default model is `anthropic.claude-sonnet-4-6` (plenty for this normalization).
Run `--list-models` to see what else Floodgate offers.

It is **faithful to the recipe text**: an un-edited adapted recipe that still
lists "chicken" will get `chicken` in its ingredients. Edit the adapted recipes
listed in `needs-review.txt` first, then re-run this to refresh them.

### Controlled-vocabulary mode (recommended for consistency)

Open extraction tends to drift ("vegetable broth" vs "veggie broth" vs "vegetable
stock") and includes more than you'd filter on. To instead tag against a fixed
list you curate (e.g. just produce + proteins):

```bash
# 1. Bootstrap: dump the ingredients currently tagged, with counts (no API call)
python fill_filter_vocab.py --dump-ingredients > my-ingredients.txt

# 2. Edit my-ingredients.txt down to the terms you want to filter on
#    (one per line; '#' starts a comment; pick ONE spelling per ingredient).
#    You can leave the leading counts in place — they're stripped on load.

# 3. Re-tag every recipe's `ingredients:` with the subset of your list it contains
python fill_filter_vocab.py --vocab my-ingredients.txt --dry-run   # preview which files
python fill_filter_vocab.py --vocab my-ingredients.txt --limit 3   # spot-check
python fill_filter_vocab.py --vocab my-ingredients.txt             # all
```
I also had to do this:
security find-certificate -a -p /Library/Keychains/System.keychain > /tmp/ca.pem
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> /tmp/ca.pem
python fill_filter_vocab.py --ca-bundle /tmp/ca.pem --list-models

In `--vocab` mode the model maps recipe wording onto your list ("garbanzo beans"
→ `chickpea`), and a hard post-filter drops anything not in the list — so the
output is always a subset of what you curated. It rewrites only `ingredients:`
and leaves `tags:` untouched. Re-run it whenever you add recipes or edit the list.



# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A static recipe website. Recipes are authored as Markdown (with YAML frontmatter)
under `recipes/`, compiled to standalone HTML pages plus a filterable `index.html`,
and deployed to GitHub Pages. There is no framework, server, or test suite — the build
is a single Node script (`build.mjs`).

## Commands

```bash
npm install        # install deps (marked, gray-matter)
npm run build      # build: recipes/**/*.md → .html pages + index.html (runs node build.mjs)
```

There is no lint or test step. To preview, open the generated `index.html` in a browser
(or serve the repo root statically). The build runs anywhere with Node ≥ 20 — including
locally on macOS, unlike the previous bash-based build.

## Architecture

`build.mjs` is the entire build, in three passes:
1. **Collect** — recursively read every `recipes/**/*.md`, split frontmatter from body
   with `gray-matter`, render the body with `marked`, and resolve per-recipe metadata
   (with fallbacks, below).
2. **Write recipe pages** — one `.html` per recipe, output at the repo root mirroring the
   recipe's subfolder (`recipes/Curries/aloo-matar.md` → `Curries/aloo-matar.html`). The
   path back to the root (for `styles.css` and `img/`) is computed from folder depth, so
   nesting deeper than one level works.
3. **Write `index.html`** — recipe cards grouped by category, plus a client-side filter bar.

Generated `.html` files and `node_modules/` are git-ignored (`*.html`) — the site is built
fresh, never committed. CI rebuilds on every push.

## Recipe frontmatter

All fields are optional; recipes with no frontmatter still build via fallbacks.

```yaml
---
title: Aloo Matar              # falls back to first '# H1', then filename
image: aloo-matar.jpg          # falls back to <filename>.jpg; omitted if file missing
ingredients: [tomato, garlic]  # filter terms — normalized: lowercased, trimmed, singular
tags: [vegetarian, indian]     # filter terms (same mechanism as ingredients)
time: 45 minutes               # shown on the index card
servings: 4                    # shown on the index card
source: https://...
draft: true                    # excludes the recipe from the build
---
```

`ingredients`/`tags` are deliberately a curated, normalized vocabulary for filtering —
they are NOT auto-extracted from the recipe body (the body keeps the full prose list with
quantities). Use canonical lowercase nouns (`tomato`, not `2 medium tomatoes`), and list
only ingredients worth filtering on (skip salt/oil/water).

## Index filtering

The index renders a checkbox per distinct ingredient and tag. A small inline vanilla-JS
script filters recipe cards by set membership against each card's `data-ingredients` /
`data-tags` (pipe-delimited) attributes, with an AND/OR ("all"/"any") toggle. It's fully
static — no fetch, no backend, works as-is on GitHub Pages.

## Conventions

- **Category** = the recipe's containing folder (`recipes/Curries/` → "Curries");
  files directly in `recipes/` map to "(root)".
- Add a recipe by dropping a `.md` in a category folder (and optionally a matching image
  in `img/`). No code changes needed.
- All user-supplied strings are HTML-escaped in `build.mjs`; `data-*` filter values are
  pipe-delimited so multi-word terms (e.g. `green chili`) survive.

## Deployment

`.github/workflows/static.yml` runs on push to `main` (and manual dispatch): `npm install`,
then `npm run build`, then uploads the repo as the GitHub Pages artifact. Pushing recipe
Markdown to `main` is enough to publish.

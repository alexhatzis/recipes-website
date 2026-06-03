#!/usr/bin/env python3
"""
Fill the `ingredients:` / `tags:` filter vocabulary on imported recipes.

The importer leaves these as TODO comments:

    # ingredients: []   # TODO: normalized filter terms (e.g. tomato, garlic)
    # tags: []          # TODO: filter tags (e.g. vegetarian, quick)

This tool reads each such recipe, asks Claude to derive a normalized,
filter-ready ingredient list and tags from the recipe's title + ingredient
section, and rewrites those two lines with real YAML arrays. Files that already
have a real `ingredients:` key are skipped, so it's safe to re-run.

It is faithful to the recipe text as written: if an un-edited adapted recipe
still lists "chicken", the ingredients will include "chicken". Edit the adapted
recipes (see tools/needs-review.txt) first, then re-run this to refresh them.

Requires ANTHROPIC_API_KEY in the environment.

Usage:
  python tools/fill_filter_vocab.py --dry-run        # list recipes needing vocab; no API calls
  python tools/fill_filter_vocab.py --limit 3        # process the first 3 (review the diff)
  python tools/fill_filter_vocab.py                  # process all
"""
import argparse
import glob
import os
import re
import sys

import anthropic

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "claude-opus-4-8"

SYSTEM = """You normalize recipe data into a controlled vocabulary used to power \
ingredient/tag filtering on a personal (vegetarian-leaning) recipe website.

You are given a recipe title and its raw ingredient list. Call the record_filters \
tool exactly once with two arrays.

ingredients — the recognizable ingredients a person would actually filter by, each \
as a canonical, lowercase, SINGULAR noun. Rules:
- Strip quantities, brands, and prep words: "2 medium tomatoes, diced" -> "tomato",
  "1 (14 oz) can chickpeas, drained" -> "chickpea", "fresh cilantro, chopped" -> "cilantro".
- Keep multi-word ingredients that name a distinct item: "coconut milk", "green chili",
  "soy sauce", "bell pepper", "feta cheese".
- INCLUDE: proteins, vegetables, legumes, fruits, dairy/cheeses, grains/pasta, nuts,
  and defining aromatics (garlic, ginger, onion) and sauces/pastes.
- EXCLUDE basic seasonings and pantry staples nobody filters on: salt, pepper, water,
  oil (any kind), sugar, and individual dried/ground spices (cumin, turmeric, paprika,
  oregano, etc.). Keep them out unless the spice is the defining ingredient.
- Deduplicate. Aim for the ~5-12 most defining ingredients, not an exhaustive list.

tags — 3 to 6 lowercase tags describing the dish, drawn from:
- diet: vegetarian, vegan (only if clearly so from the ingredients)
- cuisine: indian, italian, thai, mexican, mediterranean, chinese, japanese, etc.
- dish/meal type: soup, salad, curry, pasta, noodles, sandwich, wrap, bowl, stew,
  stir-fry, bread, dessert, breakfast, side
Only assign tags you are confident about from the title and ingredients."""

TOOL = {
    "name": "record_filters",
    "description": "Record the normalized ingredient and tag vocabulary for one recipe.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ingredients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Canonical lowercase singular ingredient nouns.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Lowercase diet/cuisine/dish-type tags.",
            },
        },
        "required": ["ingredients", "tags"],
        "additionalProperties": False,
    },
}

TODO_INGREDIENTS = re.compile(r"^# ingredients:.*$", re.M)
TODO_TAGS = re.compile(r"^# tags:.*$", re.M)
HAS_REAL_INGREDIENTS = re.compile(r"^ingredients:", re.M)


def needs_vocab(text):
    return bool(TODO_INGREDIENTS.search(text)) and not HAS_REAL_INGREDIENTS.search(text)


def split_frontmatter(text):
    parts = text.split("---", 2)
    return (parts[1], parts[2]) if len(parts) == 3 else ("", text)


def get_title(frontmatter, fallback):
    m = re.search(r'^title:\s*"?(.*?)"?\s*$', frontmatter, re.M)
    return m.group(1) if m else fallback


def get_ingredients_section(body):
    m = re.search(r"^##\s*ingredients\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S | re.I)
    section = m.group(1).strip() if m else ""
    return section or body.strip()[:1500]


def yaml_flow_list(items):
    out = []
    seen = set()
    for raw in items:
        item = str(raw).strip().lower()
        if not item or item in seen:
            continue
        seen.add(item)
        if re.search(r"[,:\[\]{}#&*!|>'\"%@`]", item):
            out.append('"' + item.replace('"', '\\"') + '"')
        else:
            out.append(item)
    return "[" + ", ".join(out) + "]"


def derive(client, title, ingredients_text):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        # Stable prefix (tools + system) gets a cache breakpoint. Note: Opus only
        # caches prefixes >= 4096 tokens, so this may not engage at this size —
        # it's correct practice and harmless if it doesn't.
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        tools=[TOOL],
        # Forcing the tool guarantees a structured result. This is incompatible
        # with extended/adaptive thinking, which is fine — normalization is light.
        tool_choice={"type": "tool", "name": "record_filters"},
        messages=[{"role": "user", "content": f"Title: {title}\n\nIngredients:\n{ingredients_text}"}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_filters":
            return block.input.get("ingredients", []), block.input.get("tags", [])
    raise ValueError(f"no tool_use in response (stop_reason={resp.stop_reason})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="Only process the first N recipes")
    ap.add_argument("--dry-run", action="store_true", help="List recipes needing vocab; no API calls")
    args = ap.parse_args()

    todo = []
    for f in sorted(glob.glob(os.path.join(REPO, "recipes", "**", "*.md"), recursive=True)):
        if needs_vocab(open(f, encoding="utf-8").read()):
            todo.append(f)
    if args.limit:
        todo = todo[: args.limit]

    print(f"{len(todo)} recipe(s) need filter vocabulary.\n")
    if args.dry_run:
        for f in todo:
            print(f"  {os.path.relpath(f, REPO)}")
        return
    if not todo:
        return

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    ok = 0
    for idx, f in enumerate(todo, 1):
        text = open(f, encoding="utf-8").read()
        fm, body = split_frontmatter(text)
        title = get_title(fm, os.path.basename(f))
        print(f"[{idx}/{len(todo)}] {title}")
        try:
            ingredients, tags = derive(client, title, get_ingredients_section(body))
            text = TODO_INGREDIENTS.sub("ingredients: " + yaml_flow_list(ingredients), text, count=1)
            text = TODO_TAGS.sub("tags: " + yaml_flow_list(tags), text, count=1)
            with open(f, "w", encoding="utf-8") as out:
                out.write(text)
            print(f"    ingredients: {yaml_flow_list(ingredients)}")
            print(f"    tags: {yaml_flow_list(tags)}")
            ok += 1
        except Exception as e:
            print(f"    FAILED: {e}")

    print(f"\nDone. {ok}/{len(todo)} updated. Review with `git diff`, then `npm run build`.")


if __name__ == "__main__":
    main()

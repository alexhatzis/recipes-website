#!/usr/bin/env python3
"""
Fill the `ingredients:` / `tags:` filter vocabulary on imported recipes, using
Claude via Apple's internal Floodgate interactive API (Anthropic-compatible).

The importer leaves these as TODO comments:

    # ingredients: []   # TODO: normalized filter terms (e.g. tomato, garlic)
    # tags: []          # TODO: filter tags (e.g. vegetarian, quick)

This tool reads each such recipe, asks Claude to derive a normalized,
filter-ready ingredient list + tags from the title and ingredient section, and
rewrites those two lines with real YAML arrays. Files that already have a real
`ingredients:` key are skipped, so it's safe to re-run.

It is faithful to the recipe text as written: if an un-edited adapted recipe
still lists "chicken", the ingredients will include "chicken". Edit the adapted
recipes (see tools/needs-review.txt) first, then re-run this to refresh them.

Auth: by default it mints a Floodgate token via `appleconnect getToken`. Set
FLOODGATE_TOKEN to supply one yourself, or override the command with --auth-cmd.

Usage:
  python tools/fill_filter_vocab.py --list-models     # show available model IDs
  python tools/fill_filter_vocab.py --dry-run         # list recipes needing vocab; no API calls
  python tools/fill_filter_vocab.py --limit 3         # process the first 3 (review the diff)
  python tools/fill_filter_vocab.py                   # process all
"""
import argparse
import glob
import os
import re
import shlex
import subprocess

import anthropic
import httpx

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = "https://floodgate.g.apple.com/api/anthropic"
MODEL = "anthropic.claude-sonnet-4-6"
AUTH_CMD = (
    "/usr/local/bin/appleconnect getToken -C hvys3fcwcteqrvw3qzkvtk86viuoqv "
    "--token-type=oauth --interactivity-type=none -E prod -G pkce "
    "-o openid,dsid,accountname,profile,groups"
)

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
ANY_INGREDIENTS = re.compile(r"^(?:#\s*)?ingredients:.*$", re.M)  # real or TODO line


def get_token(auth_cmd):
    """Floodgate Bearer token: FLOODGATE_TOKEN if set, else mint via appleconnect.

    appleconnect prints several lines; the OAuth token is the field after
    'oauth-id' (mirrors `... | grep oauth-id | cut -d' ' -f2`).
    """
    env = os.environ.get("FLOODGATE_TOKEN")
    if env:
        return env.strip()
    res = subprocess.run(shlex.split(auth_cmd), capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"Failed to mint token via:\n  {auth_cmd}\n{res.stderr.strip()}")
    for line in res.stdout.splitlines():
        if "oauth-id" in line:
            toks = line.split()
            for i, t in enumerate(toks):
                if "oauth-id" in t and i + 1 < len(toks):
                    return toks[i + 1]
            if len(toks) >= 2:
                return toks[1]
    raise SystemExit(f"No 'oauth-id' token found in appleconnect output:\n{res.stdout.strip()}")


def make_http_client(insecure, ca_bundle):
    """httpx client that trusts the corporate root CA used for TLS inspection.

    The default certifi bundle doesn't include the internal Apple root, so
    verification fails without one of these (in order of preference):
      - an explicit CA bundle (--ca-bundle, or REQUESTS_CA_BUNDLE/SSL_CERT_FILE)
      - truststore, which reads the OS keychain (Python 3.10+ only)
      - --insecure, which skips verification entirely (last resort)
    """
    if insecure:
        return httpx.Client(verify=False)
    bundle = ca_bundle or os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if bundle:
        return httpx.Client(verify=bundle)
    try:
        import ssl
        import truststore
    except ImportError:
        raise SystemExit(
            "TLS verification needs the corporate root CA, and `truststore` is\n"
            "unavailable on this Python (it needs 3.10+). Pick one:\n"
            "  1) Export the keychain to a PEM and point the tool at it:\n"
            "       security find-certificate -a -p /Library/Keychains/System.keychain > /tmp/ca.pem\n"
            "       security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> /tmp/ca.pem\n"
            "       python fill_filter_vocab.py --ca-bundle /tmp/ca.pem ...\n"
            "  2) Recreate the venv on Python 3.10+, then `pip install truststore`.\n"
            "  3) Pass --insecure to skip verification (last resort)."
        )
    return httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


def make_client(auth_cmd, insecure, ca_bundle):
    # auth_token sends Authorization: Bearer. Drop any stray ANTHROPIC_API_KEY so
    # the SDK doesn't also send x-api-key (sending both is rejected with a 401).
    os.environ.pop("ANTHROPIC_API_KEY", None)
    return anthropic.Anthropic(
        auth_token=get_token(auth_cmd),
        base_url=BASE_URL,
        http_client=make_http_client(insecure, ca_bundle),
        default_headers={"User-Agent": "recipes-website/1.0"},
    )


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
    out, seen = [], set()
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


def derive(client, model, title, ingredients_text):
    # Forcing the tool guarantees a structured result. Incompatible with extended
    # thinking, which is fine here — normalization is a light task.
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "record_filters"},
        messages=[{"role": "user", "content": f"Title: {title}\n\nIngredients:\n{ingredients_text}"}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_filters":
            return block.input.get("ingredients", []), block.input.get("tags", [])
    raise ValueError(f"no tool_use in response (stop_reason={resp.stop_reason})")


# ---- controlled-vocabulary mode (--vocab) ---------------------------------

VOCAB_TOOL = {
    "name": "record_ingredients",
    "description": "Record which controlled-vocabulary ingredients the recipe contains.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ingredients": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subset of the controlled vocabulary present in the recipe.",
            },
        },
        "required": ["ingredients"],
        "additionalProperties": False,
    },
}


def load_vocab(path):
    vocab, seen = [], set()
    for line in open(path, encoding="utf-8"):
        term = line.split("#", 1)[0].strip()
        # Tolerate a leading count from `--dump-ingredients` output ("8  tomato" -> "tomato").
        term = re.sub(r"^\d+\s+", "", term)
        if term and term.lower() not in seen:
            seen.add(term.lower())
            vocab.append(term)
    if not vocab:
        raise SystemExit(f"No ingredients found in vocab file: {path}")
    return vocab


def vocab_system(vocab):
    return (
        "You tag recipes against a FIXED, controlled ingredient vocabulary used by a "
        "recipe website's filter.\n\nControlled vocabulary (return ONLY these exact terms):\n"
        + "\n".join(f"- {v}" for v in vocab)
        + "\n\nGiven a recipe title and its raw ingredient list, call record_ingredients with "
        "the subset of the controlled vocabulary that the recipe actually contains. Map "
        'variants/synonyms onto the closest vocabulary term (e.g. "garbanzo beans" -> '
        '"chickpea", "scallions" -> "green onion", "coriander" -> "cilantro") only when the '
        "recipe truly contains it. Never output a term that is not in the vocabulary, and "
        "never guess ingredients that are not present."
    )


def derive_from_vocab(client, model, system, vocab, title, ingredients_text):
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        tools=[VOCAB_TOOL],
        tool_choice={"type": "tool", "name": "record_ingredients"},
        messages=[{"role": "user", "content": f"Title: {title}\n\nIngredients:\n{ingredients_text}"}],
    )
    canon = {v.lower(): v for v in vocab}  # hard post-filter: nothing outside the vocab
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_ingredients":
            out, seen = [], set()
            for it in block.input.get("ingredients", []):
                key = str(it).strip().lower()
                if key in canon and key not in seen:
                    seen.add(key)
                    out.append(canon[key])
            return out
    raise ValueError(f"no tool_use in response (stop_reason={resp.stop_reason})")


def parse_ingredients_line(frontmatter):
    m = re.search(r"^ingredients:\s*\[(.*)\]\s*$", frontmatter, re.M)
    if not m:
        return []
    items = []
    for part in m.group(1).split(","):
        p = part.strip().strip('"').strip("'").strip()
        if p:
            items.append(p)
    return items


def collect_ingredient_counts():
    from collections import Counter

    counts = Counter()
    for f in sorted(glob.glob(os.path.join(REPO, "recipes", "**", "*.md"), recursive=True)):
        fm, _ = split_frontmatter(open(f, encoding="utf-8").read())
        for ing in parse_ingredients_line(fm):
            counts[ing.lower()] += 1
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="Only process the first N recipes")
    ap.add_argument("--dry-run", action="store_true", help="List recipes needing vocab; no API calls")
    ap.add_argument("--list-models", action="store_true", help="List available model IDs and exit")
    ap.add_argument("--model", default=MODEL, help=f"Model ID (default {MODEL})")
    ap.add_argument("--dump-ingredients", action="store_true",
                    help="Print every distinct tagged ingredient with counts, then exit (no API)")
    ap.add_argument("--vocab", metavar="FILE",
                    help="Controlled-vocabulary mode: re-tag each recipe's ingredients: with the "
                         "subset of this list it contains (one term per line, # comments allowed)")
    ap.add_argument("--auth-cmd", default=AUTH_CMD, help="Command that prints a Floodgate token")
    ap.add_argument("--ca-bundle", help="Path to a CA bundle PEM that includes the corporate root")
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verification (last resort)")
    args = ap.parse_args()

    if args.dump_ingredients:
        counts = collect_ingredient_counts()
        for term, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"{n:4d}  {term}")
        print(f"\n{len(counts)} distinct ingredients across {sum(counts.values())} tags.")
        return

    if args.list_models:
        for m in make_client(args.auth_cmd, args.insecure, args.ca_bundle).models.list():
            print(m.id)
        return

    if args.vocab:
        vocab = load_vocab(args.vocab)
        recipes = [
            f
            for f in sorted(glob.glob(os.path.join(REPO, "recipes", "**", "*.md"), recursive=True))
            if ANY_INGREDIENTS.search(open(f, encoding="utf-8").read())
        ]
        if args.limit:
            recipes = recipes[: args.limit]
        print(f"Re-tagging {len(recipes)} recipe(s) against {len(vocab)} controlled ingredients.\n")
        if args.dry_run:
            for f in recipes:
                print(f"  {os.path.relpath(f, REPO)}")
            return

        client = make_client(args.auth_cmd, args.insecure, args.ca_bundle)
        system = vocab_system(vocab)
        ok = 0
        for idx, f in enumerate(recipes, 1):
            text = open(f, encoding="utf-8").read()
            fm, body = split_frontmatter(text)
            title = get_title(fm, os.path.basename(f))
            print(f"[{idx}/{len(recipes)}] {title}")
            try:
                found = derive_from_vocab(client, args.model, system, vocab, title, get_ingredients_section(body))
                text = ANY_INGREDIENTS.sub("ingredients: " + yaml_flow_list(found), text, count=1)
                with open(f, "w", encoding="utf-8") as out:
                    out.write(text)
                print(f"    ingredients: {yaml_flow_list(found)}")
                ok += 1
            except Exception as e:
                print(f"    FAILED: {e}")
        print(f"\nDone. {ok}/{len(recipes)} re-tagged. Review with `git diff`, then `npm run build`.")
        return

    todo = [
        f
        for f in sorted(glob.glob(os.path.join(REPO, "recipes", "**", "*.md"), recursive=True))
        if needs_vocab(open(f, encoding="utf-8").read())
    ]
    if args.limit:
        todo = todo[: args.limit]

    print(f"{len(todo)} recipe(s) need filter vocabulary.\n")
    if args.dry_run:
        for f in todo:
            print(f"  {os.path.relpath(f, REPO)}")
        return
    if not todo:
        return

    client = make_client(args.auth_cmd, args.insecure, args.ca_bundle)
    ok = 0
    for idx, f in enumerate(todo, 1):
        text = open(f, encoding="utf-8").read()
        fm, body = split_frontmatter(text)
        title = get_title(fm, os.path.basename(f))
        print(f"[{idx}/{len(todo)}] {title}")
        try:
            ingredients, tags = derive(client, args.model, title, get_ingredients_section(body))
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

import fs from "fs";
import path from "path";
import { marked } from "marked";
import matter from "gray-matter";

const INPUT_DIR = "recipes";
const OUTPUT_DIR = ".";
const IMG_DIR = "img";

// Site-specific config (title + category labels), kept separate so the build
// framework can be reused for another site by swapping this file alone.
const config = JSON.parse(fs.readFileSync("site.config.json", "utf-8"));
const SITE_TITLE = config.title || "Recipes";

// ---- helpers --------------------------------------------------------------

function findMarkdown(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...findMarkdown(full));
    else if (entry.isFile() && entry.name.endsWith(".md")) out.push(full);
  }
  return out;
}

function firstH1(markdown) {
  const m = markdown.match(/^#\s+(.+?)\s*$/m);
  return m ? m[1] : null;
}

// Frontmatter lists may be an array or a comma-separated string. Normalize to
// lowercased, trimmed, de-duplicated terms suitable for filtering.
function normalizeList(value) {
  if (!value) return [];
  const arr = Array.isArray(value) ? value : String(value).split(",");
  const seen = new Set();
  for (const item of arr) {
    const term = String(item).trim().toLowerCase();
    if (term) seen.add(term);
  }
  return [...seen];
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function isExternal(src) {
  return /^https?:\/\//.test(src) || src.startsWith("/");
}

// Explicit display names for category folders whose slug can't capture the
// intended punctuation (commas, ampersands). Defined in site.config.json;
// anything not listed is derived from the slug by prettyCategory().
const CATEGORY_LABELS = config.categoryLabels || {};

// Words kept lowercase mid-phrase when title-casing a category slug.
const MINOR_WORDS = new Set(["and", "or", "the", "of", "with", "a", "an", "in", "on", "to", "for"]);

// Folder names are slugs (e.g. "main-dishes"); show them title-cased
// ("Main Dishes"), with minor words lowercased ("pasta-and-noodles" ->
// "Pasta and Noodles"). CATEGORY_LABELS overrides take precedence.
function prettyCategory(folder) {
  if (folder === "(root)") return folder;
  if (CATEGORY_LABELS[folder]) return CATEGORY_LABELS[folder];
  return folder
    .replace(/[-_]+/g, " ")
    .split(" ")
    .map((w, i) =>
      i > 0 && MINOR_WORDS.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)
    )
    .join(" ");
}

// ---- collect recipes ------------------------------------------------------

const recipes = [];
for (const file of findMarkdown(INPUT_DIR)) {
  const { data, content } = matter(fs.readFileSync(file, "utf-8"));
  if (data.draft) {
    console.log(`Skipping draft: ${file}`);
    continue;
  }

  const relHtml = path.relative(INPUT_DIR, file).replace(/\.md$/, ".html");
  const base = path.basename(file, ".md");
  const folder = path.dirname(relHtml);
  const category = folder === "." ? "(root)" : folder;
  const depth = folder === "." ? 0 : folder.split(path.sep).length;
  const prefix = depth === 0 ? "./" : "../".repeat(depth); // path back to repo root

  // Resolve the image: explicit frontmatter, else <filename>.jpg. Skip if the
  // file doesn't exist (and isn't an external URL) so we never emit a broken img.
  // Stored relative to the repo root; imgSrc() rebases it per page below.
  const imageName = data.image || `${base}.jpg`;
  let imageRoot = null; // path from repo root, or an external URL
  let imageExternal = false;
  if (isExternal(imageName)) {
    imageRoot = imageName;
    imageExternal = true;
  } else if (fs.existsSync(path.join(IMG_DIR, imageName))) {
    imageRoot = `${IMG_DIR}/${imageName}`;
  }

  const title = (data.title && String(data.title).trim()) || firstH1(content) || base;

  recipes.push({
    relHtml,
    category,
    prefix,
    title,
    imageRoot,
    imageExternal,
    ingredients: normalizeList(data.ingredients),
    tags: normalizeList(data.tags),
    time: data.time ? String(data.time).trim() : null,
    servings: data.servings != null ? String(data.servings).trim() : null,
    body: marked(content),
  });
}

// Rebase a recipe's image onto a given path prefix ("./" for the root index,
// "../" etc. for a recipe page). External URLs are returned as-is.
function imgSrc(r, prefix) {
  if (!r.imageRoot) return null;
  return r.imageExternal ? r.imageRoot : `${prefix}${r.imageRoot}`;
}

// ---- write recipe pages ---------------------------------------------------

for (const r of recipes) {
  const outFile = path.join(OUTPUT_DIR, r.relHtml);
  fs.mkdirSync(path.dirname(outFile), { recursive: true });

  const src = imgSrc(r, r.prefix);
  const img = src
    ? `<img class="recipe-image" src="${escapeHtml(src)}" alt="${escapeHtml(r.title)}">\n`
    : "";

  const page = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(r.title)}</title>
<link rel="stylesheet" href="${r.prefix}styles.css">
</head>
<body>
<p><a href="${r.prefix}index.html">&larr; All recipes</a></p>
${img}${r.body}</body>
</html>`;

  fs.writeFileSync(outFile, page);
  console.log(`Converted ${r.relHtml}`);
}

// ---- build the index ------------------------------------------------------

// Filter vocabulary: every ingredient / tag used across all recipes, sorted.
const vocab = (key) =>
  [...new Set(recipes.flatMap((r) => r[key]))].sort();
const allIngredients = vocab("ingredients");
const allTags = vocab("tags");

function checkboxes(terms, kind) {
  if (!terms.length) return "";
  const items = terms
    .map(
      (t) =>
        `        <label class="chip"><input type="checkbox" data-kind="${kind}" value="${escapeHtml(
          t
        )}"> ${escapeHtml(t)}</label>`
    )
    .join("\n");
  const label = kind === "ingredients" ? "Ingredients" : "Tags";
  return `    <details class="filter-group">
      <summary>${label} <span class="sel-count" data-kind="${kind}"></span></summary>
      <div class="chip-list">
${items}
      </div>
    </details>`;
}

function card(r) {
  const src = imgSrc(r, "./"); // index lives at the repo root
  const img = src
    ? `<img src="${escapeHtml(src)}" alt="${escapeHtml(r.title)}">`
    : `<div class="card-noimg" aria-hidden="true"></div>`;
  const meta = [r.time, r.servings && `${r.servings} servings`]
    .filter(Boolean)
    .join(" · ");
  return `      <article class="recipe-card" data-ingredients="${escapeHtml(
    r.ingredients.join("|")
  )}" data-tags="${escapeHtml(r.tags.join("|"))}">
        <a class="card-link" href="${escapeHtml(r.relHtml)}">
          ${img}
          <div class="card-body">
            <h3>${escapeHtml(r.title)}</h3>
            ${meta ? `<p class="card-meta">${escapeHtml(meta)}</p>` : ""}
          </div>
        </a>
      </article>`;
}

const categories = [...new Set(recipes.map((r) => r.category))].sort();
const sections = categories
  .map((cat) => {
    const cards = recipes
      .filter((r) => r.category === cat)
      .sort((a, b) => a.title.localeCompare(b.title))
      .map(card)
      .join("\n");
    return `  <section class="category">
    <h2 class="folder-title">${escapeHtml(prettyCategory(cat))}</h2>
    <div class="card-grid">
${cards}
    </div>
  </section>`;
  })
  .join("\n");

const filterBar =
  allIngredients.length || allTags.length
    ? `  <div class="filter-bar">
    <div class="filter-controls">
      <label>Match
        <select id="match-mode">
          <option value="all">all selected</option>
          <option value="any">any selected</option>
        </select>
      </label>
      <button id="clear-filters" type="button">Clear</button>
      <span id="result-count"></span>
    </div>
${[checkboxes(allIngredients, "ingredients"), checkboxes(allTags, "tags")]
  .filter(Boolean)
  .join("\n")}
  </div>`
    : "";

const script = `<script>
  const cards = [...document.querySelectorAll(".recipe-card")];
  const boxes = [...document.querySelectorAll(".filter-group input[type=checkbox]")];
  const mode = document.getElementById("match-mode");
  const count = document.getElementById("result-count");
  const noMatches = document.getElementById("no-matches");

  const selected = (kind) => boxes.filter((b) => b.checked && b.dataset.kind === kind).map((b) => b.value);
  const setOf = (card, key) => (card.dataset[key] ? card.dataset[key].split("|") : []);

  function apply() {
    const ing = selected("ingredients");
    const tag = selected("tags");
    const any = mode && mode.value === "any";
    let shown = 0;
    for (const card of cards) {
      const tests = [
        ...ing.map((x) => setOf(card, "ingredients").includes(x)),
        ...tag.map((x) => setOf(card, "tags").includes(x)),
      ];
      const ok = tests.length === 0 || (any ? tests.some(Boolean) : tests.every(Boolean));
      card.hidden = !ok;
      if (ok) shown++;
    }
    document.querySelectorAll(".category").forEach((sec) => {
      sec.hidden = ![...sec.querySelectorAll(".recipe-card")].some((c) => !c.hidden);
    });
    document.querySelectorAll(".sel-count").forEach((el) => {
      const n = selected(el.dataset.kind).length;
      el.textContent = n ? "(" + n + ")" : "";
    });
    if (count) count.textContent = shown + (shown === 1 ? " recipe" : " recipes");
    if (noMatches) noMatches.hidden = shown !== 0;
  }

  boxes.forEach((b) => b.addEventListener("change", apply));
  if (mode) mode.addEventListener("change", apply);
  const clear = document.getElementById("clear-filters");
  if (clear) clear.addEventListener("click", () => { boxes.forEach((b) => (b.checked = false)); apply(); });
  apply();
</script>`;

const index = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(SITE_TITLE)}</title>
<link rel="stylesheet" href="./styles.css">
</head>
<body>
<h1>${escapeHtml(SITE_TITLE)}</h1>
${filterBar}
${sections}
  <p id="no-matches" hidden>No recipes match your filters.</p>
${script}
</body>
</html>`;

fs.writeFileSync(path.join(OUTPUT_DIR, "index.html"), index);
console.log(`Index page generated with ${recipes.length} recipe(s).`);

INPUT_DIR="recipes"
OUTPUT_DIR="."
INDEX_FILE="$OUTPUT_DIR/index.html"

unset GROUPS
declare -A GROUPS     # folder → html list entries

# --- Convert files and collect index info ---
while IFS= read -r file; do
  # relative.md path
  rel_md="${file#$INPUT_DIR/}"

  # relative.html path
  rel_html="${rel_md%.md}.html"

  # determine output dir
  out_dir="$OUTPUT_DIR/$(dirname "$rel_html")"
  mkdir -p "$out_dir"

  out_file="$out_dir/$(basename "$rel_html")"

  # ---- Extract title from first Markdown H1 line ----
  # If no H1 is found, fallback to filename
  title=$(grep -m 1 '^# ' "$file" | sed 's/^# //')
  if [ -z "$title" ]; then
    title=$(basename "$file" .md)
  fi

  # ---- Convert Markdown to HTML ----
  node md-to-html.mjs "$file" "$out_file"

  # ---- Determine group (folder) ----
  folder=$(dirname "$rel_html")
  if [ "$folder" = "." ]; then
    folder="(root)"
  fi

  # ---- Add entry to the GROUPS map ----
  GROUPS["$folder"]+=$(printf '    <div class="row"><div class="col-md-6"><img src="./img/aloo-matar.jpg" alt="still testing"></img></div><div class="col-md-6"><a href="%s">%s</a></div></div>\n\n' "$rel_html" "$title")

  echo "Ran script on $file → $out_file"

done < <(find "$INPUT_DIR" -type f -name "*.md")

# --- Build the index page ---
mkdir -p "$OUTPUT_DIR"

cat > "$INDEX_FILE" <<EOF
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Recipe Index</title>
<link rel="stylesheet" href="./styles.css">
</head>
<body>
<h1>Recipe Index</h1>
EOF

# Sort folder names and output grouped entries
for folder in $(printf "%s\n" "${!GROUPS[@]}" | sort); do
  echo "<h2 class=\"folder-title\">$folder</h2>" >> "$INDEX_FILE"
  #echo "<ul>" >> "$INDEX_FILE"
  echo "<div class="container">" >> "$INDEX_FILE"
  printf "%b" "${GROUPS[$folder]}" >> "$INDEX_FILE"
  echo "</ul>" >> "$INDEX_FILE"
done

cat >> "$INDEX_FILE" <<EOF
</body>
</html>
EOF

echo "Index page generated at: $INDEX_FILE"
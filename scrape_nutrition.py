import json
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://www.halalmeals.ca/select-meal"
OUTPUT_TABLE = "nutrition_table.txt"
OUTPUT_JSON = "nutrition_data.json"
OUTPUT_RANKING = "ranking.md"

# --- Ranking tunables -------------------------------------------------
# "Reasonable calories" window used to filter the muscle-gain ranking so a
# 150-calorie side dish with a lot of protein-per-gram doesn't outrank a
# real meal. Adjust these if the menu's typical meal size is different.
MUSCLE_GAIN_MIN_CALORIES = 300
MUSCLE_GAIN_MAX_CALORIES = 900
TOP_N = 10

# This runs inside the browser page context. It walks the React fiber tree
# to find the array of meal-card components and pulls the underlying
# `product` data object out of each one (title, nutrition_per_serving, etc.)
EXTRACTION_JS = """
() => {
  function getFiber(el) {
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
    return key ? el[key] : null;
  }

  // Anchor the fiber walk on a "View" button's text node, which sits inside
  // a meal card. Picking the first text node in the body is unreliable: it
  // can land on inline Next.js hydration <script> payloads, whose fiber
  // ancestry never reaches the meal-card array.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node, target = null;
  while (node = walker.nextNode()) {
    const parentTag = node.parentElement && node.parentElement.tagName;
    if (parentTag === 'SCRIPT' || parentTag === 'STYLE') continue;
    if (node.textContent.trim().toLowerCase() === 'view') { target = node.parentElement; break; }
  }
  if (!target) return null;

  function findArrays(obj, depth, seen, results) {
    if (!obj || depth > 8 || results.length > 0) return;
    if (typeof obj === 'object') {
      if (seen.has(obj)) return;
      seen.add(obj);
    }
    if (Array.isArray(obj) && obj.length > 15 && obj.length < 100 &&
        obj[0] && typeof obj[0] === 'object' && obj[0].props) {
      results.push(obj);
      return;
    }
    if (obj && typeof obj === 'object') {
      for (const k of Object.keys(obj)) {
        try { findArrays(obj[k], depth + 1, seen, results); } catch (e) {}
      }
    }
  }

  let f = getFiber(target);
  let results = [];
  let count = 0;
  const seen = new Set();
  while (f && count < 40 && results.length < 1) {
    if (f.memoizedProps) findArrays(f.memoizedProps, 0, seen, results);
    if (f.memoizedState) findArrays(f.memoizedState, 0, seen, results);
    f = f.return;
    count++;
  }

  if (!results.length) return null;
  return results[0]
    .map(el => el.props && el.props.children && el.props.children.props
         ? el.props.children.props.product : null)
    .filter(Boolean);
}
"""


def extract_row(item):
    nutrition = item.get("nutrition_per_serving") or {}

    def pick(*keys, source):
        for k in keys:
            v = source.get(k)
            if v not in (None, ""):
                return v
        return None

    calories = pick("calories", source=nutrition) or pick("kcal", source=item)
    carbs = pick("carbs", source=nutrition) or pick("carbs", source=item)
    fat = pick("fat", source=nutrition) or pick("fats", source=item)
    protein = pick("protein", source=nutrition) or pick("protien", source=item)
    sodium = pick("sodium", source=nutrition) or pick("sodium", source=item)
    fiber = pick("fiber", source=nutrition) or pick("fiber", source=item)
    spice = item.get("spice_level")

    def fmt(v):
        return str(v) if v not in (None, "") else "-"

    return {
        "title": item.get("title", "Unknown"),
        "calories": fmt(calories),
        "carbs": fmt(carbs),
        "fat": fmt(fat),
        "protein": fmt(protein),
        "spice": fmt(spice),
        "sodium": fmt(sodium),
        "fiber": fmt(fiber),
    }


def build_table(rows):
    headers = ["#", "Meal", "Calories", "Carbs", "Fat", "Protein", "Spice", "Sodium", "Fiber"]
    table_rows = []
    for i, r in enumerate(rows, start=1):
        table_rows.append([
            str(i), r["title"], r["calories"], r["carbs"],
            r["fat"], r["protein"], r["spice"], r["sodium"], r["fiber"]
        ])

    widths = [len(h) for h in headers]
    for row in table_rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def format_row(cells):
        return " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(cells))

    lines = [format_row(headers), "-+-".join("-" * w for w in widths)]
    lines.extend(format_row(row) for row in table_rows)
    return "\n".join(lines)


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_rankings(rows):
    """Returns (fat_loss_ranked, muscle_gain_ranked), each a list of dicts
    with title, calories, protein, and the computed score, sorted best-first.
    Meals missing calories or protein are excluded from both rankings."""
    usable = []
    for r in rows:
        cals = _to_float(r["calories"])
        protein = _to_float(r["protein"])
        if cals is None or protein is None or cals <= 0:
            continue
        usable.append({"title": r["title"], "calories": cals, "protein": protein})

    # Fat loss: highest protein-per-calorie (protein density), highest first.
    fat_loss = sorted(usable, key=lambda m: m["protein"] / m["calories"], reverse=True)
    for m in fat_loss:
        m["score"] = round(m["protein"] / m["calories"], 4)

    # Muscle gain: highest total protein among meals with a "reasonable"
    # calorie count for a full meal, so tiny high-density snacks don't win
    # over a substantial meal. Falls back to all meals if the window is too
    # narrow for this menu.
    windowed = [
        m for m in usable
        if MUSCLE_GAIN_MIN_CALORIES <= m["calories"] <= MUSCLE_GAIN_MAX_CALORIES
    ]
    muscle_pool = windowed if len(windowed) >= 3 else usable
    muscle_gain = sorted(muscle_pool, key=lambda m: m["protein"], reverse=True)
    for m in muscle_gain:
        m["score"] = m["protein"]

    return fat_loss[:TOP_N], muscle_gain[:TOP_N]


def build_ranking_markdown(fat_loss, muscle_gain):
    lines = ["# Halal Meals — Daily Ranking", ""]

    lines.append("## Best for fat loss (highest protein per calorie)")
    lines.append("")
    lines.append("| # | Meal | Calories | Protein (g) | Protein/Calorie |")
    lines.append("|---|------|----------|-------------|------------------|")
    for i, m in enumerate(fat_loss, start=1):
        lines.append(f"| {i} | {m['title']} | {m['calories']:.0f} | {m['protein']:.0f} | {m['score']:.3f} |")
    lines.append("")

    lines.append("## Best for muscle gain (highest protein, reasonable calories)")
    lines.append("")
    lines.append("| # | Meal | Calories | Protein (g) |")
    lines.append("|---|------|----------|-------------|")
    for i, m in enumerate(muscle_gain, start=1):
        lines.append(f"| {i} | {m['title']} | {m['calories']:.0f} | {m['protein']:.0f} |")
    lines.append("")

    return "\n".join(lines)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("text=VIEW", timeout=15000)  # wait for meal cards to render

        products = page.evaluate(EXTRACTION_JS)
        browser.close()

    if not products:
        print("Could not extract product data — the page structure may have changed.")
        raise SystemExit(1)

    Path(OUTPUT_JSON).write_text(json.dumps(products, indent=2), encoding="utf-8")

    rows = [extract_row(p) for p in products]
    table = build_table(rows)
    Path(OUTPUT_TABLE).write_text(table, encoding="utf-8")

    fat_loss, muscle_gain = build_rankings(rows)
    ranking_md = build_ranking_markdown(fat_loss, muscle_gain)
    Path(OUTPUT_RANKING).write_text(ranking_md, encoding="utf-8")

    print(f"Extracted {len(rows)} meals.")
    print(f"Raw JSON saved to {OUTPUT_JSON}")
    print(f"Table saved to {OUTPUT_TABLE}")
    print(f"Ranking saved to {OUTPUT_RANKING}")
    print()
    print(table)
    print()
    print(ranking_md)


if __name__ == "__main__":
    main()

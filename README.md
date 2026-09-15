# halal-meals-tracker

Scrapes the daily menu + macros from halalmeals.ca and commits a fresh
`nutrition_data.json`, `nutrition_table.txt`, and `ranking.md` to this repo
every morning via GitHub Actions (which has normal internet access, unlike
Claude's cloud sandbox — that's the whole reason this lives here instead of
in a Claude scheduled task).

## Manual setup

1. **Create the repo.** On GitHub: New repository → name it (e.g.
   `halal-meals-tracker`) → Public (simplest — no secrets needed to read the
   results later) or Private, your call. Don't initialize with a README
   (you already have one here).

2. **Push these files.** From this folder:

   ```bash
   git init
   git add .
   git commit -m "Initial scraper + workflow"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```

3. **Check it runs.** Go to the repo's **Actions** tab → you should see the
   "Scrape halalmeals.ca menu" workflow. Click **Run workflow** to trigger it
   by hand the first time (don't wait for the 9am UTC schedule) and confirm
   it completes green and commits `nutrition_data.json` / `ranking.md`.

4. **That's it for this half.** Once a run has succeeded at least once, tell
   me:
   - your GitHub username and the repo name
   - whether you made it public or private

   and I'll finish the other half: a daily Claude scheduled task that reads
   `ranking.md` from this repo, remembers what it last saw, and only
   messages/notifies you when the menu (and therefore the ranking) has
   actually changed — not every single day.

## Files

- `scrape_nutrition.py` — drives a headless Chromium browser to the menu
  page, pulls the meal data straight out of the page's React state, and
  writes `nutrition_data.json` (raw), `nutrition_table.txt` (readable table),
  and `ranking.md` (the fat-loss / muscle-gain rankings).
- `.github/workflows/scrape.yml` — runs the script daily at 9:00 UTC and
  commits the output files if they changed.
- `requirements.txt` — just `playwright`.

## Ranking logic (adjustable)

- **Fat loss**: highest protein-per-calorie ratio (protein density) first.
- **Muscle gain**: highest total protein first, restricted to meals between
  300–900 calories so a tiny high-protein snack doesn't outrank an actual
  meal (falls back to all meals if fewer than 3 meals fall in that window).

Both windows and the top-N cutoff are constants near the top of
`scrape_nutrition.py` (`MUSCLE_GAIN_MIN_CALORIES`, `MUSCLE_GAIN_MAX_CALORIES`,
`TOP_N`) — edit and push if you want different thresholds.

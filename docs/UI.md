# LaneForge UI notes

The UI follows the scouting-dossier direction in `DESIGN.md`: a printed matchup
report, not a dashboard. Typography and hairlines do the work. There are no
cards, shadows, pills or icon fonts.

## Running the preview

    .venv/bin/python scripts/ui_preview.py      # http://127.0.0.1:8010/

The index lists every template at `/preview/<name>`. Add `?anon=1` to see a
page signed out. Fake data comes from the real dataclasses. Champion and item
names and stats are read from `data/ddragon/*.json`, and the matchup numbers are
internally consistent: pick rate is games / sample, and every interval is a real
Wilson interval. `/matchup/core` and `/matchup/situational` also run in the
preview, so the htmx shell (`/preview/matchup-htmx`) loads the way it does in
production.

`tests/test_templates.py` renders every page and checks the key strings. It
also parses each template and fails when a template reads a context variable
outside the CONTRACT table or uses a filter outside the CONTRACT set. That is
the drift guard between the UI and the routes.

## What is where

| File | Holds |
|---|---|
| `static/site.css` (210 lines) | tokens, dark scheme, type, masthead, flash line, forms (the sentence form, underlined selects), ledger tables, tags |
| `static/dossier.css` (245 lines) | answered-at line and ladder, core build rows, item strip, `.ci` range bar, comp stacked bar and meters, situational rows, champion grid, team sheet, saved-build entries, print |
| `templates/_macros.html` | `item_icon`, `champ_icon`, `ci_bar`, `stat_line`, `stat_rows`, `wr`, `matchup_href` |
| `templates/partials/answered.html` | the trust line. The full page includes it. Under htmx, `partials/core.html` sends it as an out-of-band `innerHTML` swap into a stable `aria-live` container |
| `static/vendor/htmx.min.js` | htmx 2.0.4, the only script. The two inline scripts (champion filter, item sort) are about 20 lines each |

## Decisions worth knowing

- **Range bar scale.** The `.ci` bar maps 30% to 70% win rate onto 96px, with a
  faint tick at 50%. Plotting 0 to 100% would squash a 47–60 interval into
  12px. The footnote under the table states the scale.
- **Dark scheme oxblood.** `#7a1f1f` on the charcoal paper is 1.65:1, which fails
  AA. Dark mode lifts the accent to `#d8766a` (5.4:1). It still reads as oxblood
  ink under lamplight.
- **Mute text.** `--mute #9a958a` is 2.6:1 on paper, so it is only used for lines
  and bars. Grey text ("insufficient data", thin rows) uses `--mute-ink
  #67625a` (4.8:1 on paper, 4.3:1 on the stripe), with italics as the second
  cue.
- **Situational preview comp.** The brief asked for magic and healing rules
  on the Zed + Lee Sin / Jinx / Thresh / Darius comp. That comp is about 75%
  physical, and a League player would notice at once. So the main preview
  triggers armor + anti-heal (Plated Steelcaps is the "stat model only" row).
  The fallback preview uses a partial Lux + Galio comp, which triggers magic
  resist + tenacity with Banshee's Veil and Mercury's Treads. Every rule type is
  exercised.
- **Item names** come from Data Dragon 16.18.1, so 6655 is "Luden's Echo" and
  3118 is "Malignance", not the older names in the brief.
- **Save form** only appears on sequence rows. A per-item row is one item, and
  a build needs three. Enemy ids come from `request.args.getlist('enemy')`, so
  the core partial needs no context beyond CONTRACT.

## Screenshots

In `docs/screenshots/`, taken with Chrome at 1280px and 400px:
`matchup-1280.png`, `matchup-1280-dark.png`, `matchup-400.png`,
`fallback-1280.png`, `index-1280.png`, `index-400.png`, `champion-1280.png`,
`items-1280.png`, `matches-1280.png`, `match-1280.png`, `match-400.png`,
`builds-1280.png`, `build-1280.png`, `build-400.png`.

## Self-critique

What works: the matchup page reads top to bottom like a report. It gives the
verdict (answered at level N, with the four-step ladder showing which levels
were skipped), then the ledger, then the situational notes. Numbers are tabular
mono and align down each column. The sentence form reads as one line of prose
at desktop width.

What I would change with more time:

1. **Core table width.** At 1280px there is dead space between the item strip
   and the pick-rate column. A fixed-width ledger (about 900px) with the save
   control in the margin would feel more like print.
2. **Save disclosure.** The open form floats over the next row. It has a hairline
   border and no shadow, but an inline expansion that pushes the row down would
   be more honest.
3. **`field-sizing: content`** sizes the sentence selects in Chromium only.
   Firefox and Safari fall back to width-of-longest-option, so the sentence
   breaks earlier there. A small script that measures the selected option would
   fix it everywhere.
4. **Print was not verified visually.** The DevTools tools here cannot emulate
   print media. The print sheet hides nav and forms, forces the light palette
   with `print-color-adjust: exact` so bars survive, and avoids breaks inside
   rows. It still needs one real print preview.
5. **Match timelines** list every completed purchase, components included. A
   toggle for "legendaries only" would make ten rows easier to scan.
6. The comp-profile meter's scale (1.4 × max(value, p75)) is arbitrary. A fixed
   scale per measure, taken from the dataset's p95, would make two dossiers
   comparable.

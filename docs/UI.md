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
| `static/site.css` (231 lines) | tokens, dark scheme, type, masthead, flash line, forms (the sentence form, underlined selects), ledger tables, tags |
| `static/dossier.css` (291 lines) | answered-at line and ladder, core build rows, item strip, `.ci` range bar, comp stacked bar and meters, situational rows, champion grid, team sheet, saved-build entries, print |
| `templates/_macros.html` | `item_icon`, `champ_icon`, `ci_bar`, `stat_line`, `stat_rows`, `wr`, `matchup_href` |
| `templates/partials/answered.html` | the trust line. The full page includes it. Under htmx, `partials/core.html` sends it as an out-of-band `innerHTML` swap into a stable `aria-live` container |
| `static/vendor/htmx.min.js` | htmx 2.0.4, the only script. The two inline scripts (champion filter, item sort) are about 20 lines each |

## Decisions worth knowing

- **Range bar scale.** The `.ci` bar maps 30% to 70% win rate onto 96px, with a
  faint tick at 50%. Plotting 0 to 100% would squash a 47–60 interval into
  12px. The footnote under the table states the scale.
- **Dark scheme oxblood.** `#7a1f1f` on the charcoal paper is 1.65:1, which fails
  AA. The critique asked to try `#c8665c`; computed with the WCAG formula it is
  4.46:1 on `#1e1c19` and 4.01:1 on the `#282520` stripe, so it misses AA. Dark
  mode now uses `#d27066`, the closest step on that hue that passes both:
  5.06:1 on paper and 4.54:1 on the stripe (the old `#d8766a` was 5.44:1). The
  paper on it, for the answered-step chip, is the same 5.06:1.
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
- **Save form** only appears on sequence rows and only when signed in. A
  per-item row is one item, and a build needs three. The row holds a
  `<details>` summary; the form (name, max 60, and optional notes, max 2,000)
  lives in the next `<tr class="save-row">`, shown by
  `tr:has(.save-build[open]) + tr.save-row`, so it expands as a full-width
  row and pushes the table down. Without `:has()` every save row stays open.
  Signed out, the column is gone and one footnote says "Sign in to save any of
  these." Enemy ids come from `request.args.getlist('enemy')`.
- **Trust line.** `ladder.answered` False means no level's leading row reached
  30 games: the line reads "no level reached 30 games" in `--mute-ink`, every
  step is struck through, none is `aria-current`, and `fallback_note` shows.
  `sample_size == 0` collapses to that one line, and the core block says only
  "No {champion} games at {role} in this dataset."
- **Situational evidence.** A rule with a `rule_notes[key]` sentence, or any
  rule when `thin_sample` is true, lists compact rows (icon, name, score). The
  thin-sample sentence appears once under the SITUATIONAL heading. The query
  layer sends no rule notes when the answer is thin, so without the second
  condition the old "stat model only, no sample" line would repeat on every
  row.
- **Win rates at two games or fewer** print wins–losses ("1–1"), greyed, on the
  core table, the champion page and the item page.
- **Errors.** Flashes use categories. `error` flashes read "Not saved:" in ink
  roman on the oxblood rule; successes stay oxblood italic. `build.html`
  overrides the `notices` block so errors sit under the swap form instead of
  on top (this includes rename errors, which the route also flashes as
  `error`). A failed GET `/matchup` renders `index.html` with `form_error`
  under the sentence, prefixed "Not compiled:", with the form pre-filled from
  `request.args`.
- **Sentence form.** Unchosen selects read as an italic ink-2 placeholder, not
  as invalid. The oxblood underline for an invalid field appears only on
  `:user-invalid` or after a submit attempt (a capturing `invalid` listener
  adds `.tried`). The same 15-line script disables empty enemy selects on
  submit, so URLs carry no bare `enemy=`.

## Screenshots

In `docs/screenshots/`, taken with Chrome at 1280px and 400px:
the first pass:
`matchup-1280.png`, `matchup-1280-dark.png`, `matchup-400.png`,
`fallback-1280.png`, `index-1280.png`, `index-400.png`, `champion-1280.png`,
`items-1280.png`, `matches-1280.png`, `match-1280.png`, `match-400.png`,
`builds-1280.png`, `build-1280.png`, `build-400.png`.

After the design critique (`docs/screenshots/critique/` holds the "before"
set), in `docs/screenshots/after/`: `before-nodata-1280.png` (the no-data page
before the fix), `nodata-ahri-bot-1280.png`, `fallback-ahri-zed-1280.png`,
`level1-senna-bard-1280.png`, `level1-400-dark.png` (save row open),
`save-inline-1280.png`, `build-observed-1280.png` (fallback-level save, so no
observed line), `build-swap-error-1280.png` (observed line and the inline
error), `index-error-400-dark.png`, `matches-400-dark.png`,
`champion-ahri-1280.png`, `match-1280.png`, `items-1280.png`,
`preview-unanswered-1280-dark.png`.

## Critique fixes (this pass)

Blockers: the trust line no longer claims an answer level when none was
reached; zero-game pages say so in one sentence; situational rules with a note
or a thin sample list compact rows; evidence sentences end "(lo–hi%)"; score
text never wraps at 1280px (fixed 16rem column, `nowrap`).

Should: one sign-in footnote instead of per-row links; fallback, partial-comp
and thin-sample notes in ink italic on a 2px oxblood rule; `/items` names the
kind as mono small caps "legendary"/"boots"; `form_error` on the index; enemy
selects on their own line with "anyone" placeholders; kickers read
"Dossier · patch 16.18 · change matchup", "Champion · patch", "Item ·
legendary · patch"; insufficient rows greyed and labelled on the champion and
item pages; win-rate numbers in a 6.5ch right-aligned slot; CI labels
"34–62%"; inline save row with notes; flash categories; the phone nav takes
two lines (the `ul` becomes `display: contents` so "Saved builds" shares a
line with the account); `/matches` rows stack under 600px; build dates never
wrap; swap form is a 3-column grid; "Save name & notes"; observed builds show
"Observed in 219 of 664 games against Bard · won 52.1% (45–59%)" with the bar.

Nice: match headline "Red side won in 30:46" with the date in the kicker and a
visible 2px oxblood rule under legendary icons; meters say "75th percentile of
comps: 801/min"; footnotes say games; `/matches` has room between length and
tier and `title` on every team icon; the dark accent (above); no nav "Sign in"
on `/signin`; empty enemies stripped on submit; `/champions` keeps the grid
(the route passes no overview data) with names in the mono face.

## Self-critique

What works: the matchup page reads top to bottom like a report. It gives the
verdict (answered at level N, with the four-step ladder showing which levels
were skipped), then the ledger, then the situational notes. Numbers are tabular
mono and align down each column. The sentence form reads as one line of prose
at desktop width.

What I would still change:

- **Champion page noise.** On a thin champion every matchup row says
  "insufficient data". A single footnote plus greyed numbers would carry it.
  `/champions` as a ledger (main role, games, win rate) needs the route to
  pass per-champion totals; it only passes `champions` today.
- **Observed line wording.** "games against Bard" assumes the observed row is
  the level-1 matchup row, which is what `saved.py` computes today.
- **Rename errors** land beside the swap form, because both routes flash the
  same `error` category. A second category (`error-rename`) would place them.

1. **Core table width.** At 1280px there is dead space between the item strip
   and the pick-rate column. A fixed-width ledger (about 900px) with the save
   control in the margin would feel more like print.
2. **`field-sizing: content`** sizes the sentence selects in Chromium only.
   Firefox and Safari fall back to width-of-longest-option, so the sentence
   breaks earlier there. A small script that measures the selected option would
   fix it everywhere.
3. **Print was not verified visually.** The DevTools tools here cannot emulate
   print media. The print sheet hides nav and forms, forces the light palette
   with `print-color-adjust: exact` so bars survive, and avoids breaks inside
   rows. It still needs one real print preview.
4. **Match timelines** list every completed purchase, components included. A
   toggle for "legendaries only" would make ten rows easier to scan.
5. The comp-profile meter's scale (1.4 × max(value, p75)) is arbitrary. A fixed
   scale per measure, taken from the dataset's p95, would make two dossiers
   comparable.

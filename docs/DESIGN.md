# LaneForge visual direction: the scouting dossier

LaneForge reads like a printed matchup report a coach hands a player before a
game, not like a dashboard. The user chose this over a dark "forge" theme and a
broadcast-overlay theme. Hold the line on it.

## What it must never look like

No dark-mode-by-default gradient cards. No purple-to-blue glows. No rounded
pill badges everywhere. No Inter-on-white with 12px grey captions. No icon
libraries. No hero sections. No "AI slop" of that kind. If a component would
look at home in a generic SaaS admin template, redesign it.

## Palette (CSS custom properties on `:root`)

| Token | Value | Use |
|---|---|---|
| `--paper` | `#f4f1ea` | page ground |
| `--paper-2` | `#ebe6da` | table stripes, insets |
| `--ink` | `#1b1a17` | text |
| `--ink-2` | `#5b574d` | secondary text, captions |
| `--rule` | `#c9c2b2` | hairlines |
| `--oxblood` | `#7a1f1f` | the one accent: answered-level marker, links, active state |
| `--oxblood-2` | `#a83a3a` | hover |
| `--moss` | `#3f5f3a` | win-rate text when above 50% and sufficient |
| `--mute` | `#9a958a` | "insufficient data" and disabled |

Dark scheme: provide a `prefers-color-scheme: dark` override that swaps to a
warm charcoal paper (`#1e1c19`), bone ink (`#e9e4d8`) and keeps oxblood. It
should still read as paper, just at night.

## Type

- Display and headings: **Fraunces** (Google Fonts), optical size on, weight 600,
  tight leading. Champion names and the matchup headline use it large.
- Body: **Source Serif 4**, 17px on desktop, 1.55 line height.
- Numbers and labels: **JetBrains Mono** with `font-variant-numeric: tabular-nums`.
  Pick rates, win rates, game counts, and gold all use it so columns align.
- Fallback stacks: Georgia / serif and ui-monospace / Menlo.

## Layout

- Max content width 1080px, generous side margins, a top masthead that says
  LANEFORGE in small caps with a hairline under it and the dataset line on the
  right: "Patch 16.18 · NA · Gold–Platinum · 5,000 games".
- Sections separated by hairlines with small-caps run-in headings
  ("CORE BUILDS", "SITUATIONAL", "SAVED BUILDS"), never boxed cards.
- The matchup form is a single line of selects reading like a sentence:
  "I play [Ahri] in [Mid] against [Zed] with enemies [ ][ ][ ][ ]". Selects
  are styled as underlined fields, not bordered boxes. Keep them native and
  keyboard friendly.
- Core build rows: rank numeral in Fraunces, then the three item icons at 36px
  in purchase order with thin arrows between, item names under the icons in
  mono small caps, then pick rate, win rate with the interval drawn as a thin
  range bar (`├──●──┤`, drawn with CSS, 96px wide, the dot at the point
  estimate), then games. Insufficient rows: numbers in `--mute`, the bar
  replaced by the words "insufficient data".
- The "answered at" line under the headline is the trust signal: "answered at:
  matchup · 212 games" in oxblood with the level number. When the page fell
  back, say so plainly: "not enough Ahri vs Zed games; showing Ahri, mid, all
  opponents".
- Situational block: each suggestion is one row: item icon, name, the rule that
  triggered it in ink-2, the stat-model score in mono, and the evidence line
  as a full sentence under it (or "stat model only, no sample" in mute).
- Comp profile: a single horizontal stacked bar for physical / magic / true
  share, oxblood for the dominant segment, plus healing and CC per minute with
  a small "above 75th pct" marker when triggered.
- Saved builds: a list of dossier entries; customized builds carry a small
  mono tag "CUSTOMIZED" in oxblood; observed builds "OBSERVED". The edit form is
  three selects in a row with the stat totals recomputing on submit.
- Match page: a two-column team sheet, blue team left, red team right, winner
  marked with a small oxblood dagger, each participant's purchase timeline as a
  horizontal strip of item icons with minute marks.

## Motion and interaction

- htmx swaps for the two matchup partials; show a mono "…compiling" text while
  loading, no spinners.
- Focus rings are 2px oxblood outlines. Everything works with keyboard only.
- No animation longer than 150ms. No parallax, no hover lifts.

## Assets

- Champion squares and item icons from the Data Dragon CDN (helpers in
  `laneforge/web/ddragon.py`). Icons get a 1px `--rule` border and 2px radius.
- One inline SVG favicon: an oxblood square with a bone "L".

## Accessibility

- WCAG AA contrast on both schemes (oxblood on paper is 8.9:1).
- Range bars have `aria-label` with the numbers; icons have `alt` with the item
  name; the "answered at" line is an `aria-live="polite"` region.

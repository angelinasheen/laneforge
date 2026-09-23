# LaneForge queries

The interesting SQL behind the matchup page and saved builds. Every statement is
a module-level constant in `laneforge/queries/` and runs through psycopg 3 with
named parameters (`%(name)s`); nothing a user types is ever spliced into SQL
text. `participant_core` is the materialized view from `sql/views.sql` with one
row per participant: outcome (`won`), lane opponent, the first three completed
legendaries `item1..item3` in order, and the enemy team's expected threat
profile (`enemy_*`).

Plans below were taken with `EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF)` on a
private test database holding the real Data Dragon catalogue (173 champions, 231
items) and 20,000 synthetic matches (200,000 participants, 987,099 purchase
events), for the most-played matchup in that data (664 full-core games).

## 1. Core builds, level 1: ordered sequences in the matchup (`builds.SQL_MATCHUP_SEQUENCES`)

```sql
SELECT pc.item1, pc.item2, pc.item3,
       COUNT(*)                                  AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END)   AS wins,
       SUM(COUNT(*)) OVER ()                     AS sample_size
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
  AND pc.opponent_champion_id = %(opponent_id)s
  AND pc.legendary_completions >= %(full_core)s     -- 3: a full core
GROUP BY pc.item1, pc.item2, pc.item3
ORDER BY games DESC, pc.item1, pc.item2, pc.item3
LIMIT %(max_rows)s                                  -- 5
```

The query groups the matchup's full-core participants by their exact
first-three sequence and counts games and wins, ordering by games (pick rate)
and never by win rate. The window `SUM(COUNT(*)) OVER ()` runs before `LIMIT`,
so every returned row also carries the denominator (all full-core participants
in the matchup), which becomes the sample size and the pick-rate divisor.

Level 3 (`SQL_CHAMPION_SEQUENCES`) is the same statement without the opponent
predicate. It is a separate constant, not an `OR %(opponent)s IS NULL` trick,
so the index condition survives prepared (generic) plans.

Plan (1.1 ms): `Bitmap Index Scan on participant_core_lookup_idx` with all three
of `(champion_id, role, opponent_champion_id)` as the index condition (664
rows), `Sort` + `GroupAggregate` into 241 sequences, `WindowAgg`, then a top-N
heapsort for the five rows. Level 3 uses the same index on its
`(champion_id, role)` prefix (4,784 rows, `HashAggregate`, 4.2 ms).

## 2. Core builds, level 2: per item in the matchup (`builds.SQL_MATCHUP_ITEMS`)

```sql
SELECT u.item_id,
       COUNT(*)                                  AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END)   AS wins
FROM participant_core pc
CROSS JOIN LATERAL (
  SELECT DISTINCT x AS item_id FROM unnest(ARRAY[pc.item1, pc.item2, pc.item3]) AS x
) u
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
  AND pc.opponent_champion_id = %(opponent_id)s
  AND pc.legendary_completions >= %(full_core)s
GROUP BY u.item_id
ORDER BY games DESC, u.item_id
LIMIT %(max_rows)s
```

The lateral join turns each participant's first three items into rows, so the
aggregate counts, for every legendary, the full-core games in which it was
among the first three, plus the wins in those games. `DISTINCT` inside the
lateral subquery counts a participant once even if they finished the same
legendary twice; the sample size is the level-1 denominator for the same scope.

Plan (1.0 ms): the same bitmap index scan, a nested loop over `unnest` (three
rows per participant), `HashAggregate` into 38 items, top-N sort.

The ladder in `builds.core_builds` runs these four statements in order and stops
at the first level whose top row has at least 30 games; a full answer costs
between one and four index-backed GROUP BYs plus one batched item lookup
(`item_id = ANY(%(ids)s)`). Measured end to end: 1.7 ms.

## 3. Situational evidence for one rule (`evidence.SQL_ITEM_EVIDENCE`)

```sql
SELECT x.item_id,
       COUNT(*)                                 AS games,
       SUM(CASE WHEN x.won THEN 1 ELSE 0 END)   AS wins
FROM (
  SELECT DISTINCT pc.match_id, pc.participant_number, pc.won, pe.item_id
  FROM participant_core pc
  JOIN purchase_event pe
    ON pe.match_id = pc.match_id AND pe.participant_number = pc.participant_number
  WHERE pc.champion_id = %(champion_id)s
    AND pc.role = %(role)s
    AND pe.item_id = ANY(%(item_ids)s)             -- the rule's candidate items
    AND CASE %(rule)s
          WHEN 'magic'    THEN pc.enemy_magic_share    >= %(magic_share_min)s
          WHEN 'physical' THEN pc.enemy_physical_share >= %(physical_share_min)s
          WHEN 'healing'  THEN pc.enemy_healing_pm     >  %(healing_pm_p75)s
          WHEN 'cc'       THEN pc.enemy_cc_pm          >  %(cc_pm_p75)s
          WHEN 'pen'      THEN pc.opponent_champion_id =  %(opponent_id)s
          ELSE FALSE
        END
) x
GROUP BY x.item_id
HAVING COUNT(*) >= %(min_games)s                    -- 30
```

For one triggered rule, this joins the champion's historical games whose enemy
comp met the rule's condition (e.g. at least 55% expected magic damage) to that
participant's completed-item purchases, keeping only the rule's candidate
items, and aggregates games and wins per item. `HAVING` drops any item with
fewer than 30 such games, so the page shows an evidence line only where the
sample supports one and "stat model only, no sample" everywhere else.

The rule key only selects a branch of a fixed `CASE`; column names are never
built from input. The class-level line ("players who completed a magic-resist
item won 54%") is `evidence.SQL_CLASS_EVIDENCE`, one pass over the champion's
`participant_core` rows with `COUNT(*) FILTER (WHERE ...)` per rule using the
`completed_*` flags (bitmap index scan, 7 ms).

Plan: the planner chooses a parallel seq scan of `participant_core` filtered on
champion, role and the enemy share (2,756 rows), then a nested loop into
`purchase_event_pkey` on `(match_id, participant_number)`, `Unique`, and a
`GroupAggregate` with the `HAVING` filter: 16 ms warm. Forcing the bitmap index
scan measured the same (18 ms), so the choice is left to the planner. A whole
situational block (profile, thresholds, candidates, one evidence query per
triggered rule, class evidence) measured 60 ms cold.

## 4. Saving a build (`saved.create_build`)

```sql
-- inside one transaction (psycopg `with conn.transaction()` then commit)
INSERT INTO saved_build (user_id, champion_id, opponent_champion_id, name, notes, role, is_customized)
VALUES (%(user_id)s, %(champion_id)s, %(opponent_champion_id)s, %(name)s, %(notes)s, %(role)s,
        %(is_customized)s)
RETURNING build_id;

INSERT INTO saved_build_enemy (build_id, champion_id)            -- 0..4 rows
VALUES (%(build_id)s, %(champion_id)s);

INSERT INTO build_item (build_id, item_id, position)            -- exactly 3 rows
VALUES (%(build_id)s, %(item_id)s, %(position)s);
```

Before any write, the application validates everything it can (name 1–60
characters, notes up to 2,000, a known role, champion ≠ opponent, 0–4 distinct
enemies excluding both, exactly three distinct existing legendary non-boots
items, the user exists) and sets `is_customized` unless the three items are an
observed first-three sequence for that champion and role (constraint 11,
checked with `SELECT 1 FROM participant_core WHERE champion_id = ... AND role =
... AND item1 = ... AND item2 = ... AND item3 = ... LIMIT 1`). The three
statements then run in one transaction, so the database's own checks (the
enemy trigger for constraint 3, the legendary trigger for constraint 8,
`UNIQUE (build_id, position)`, the champion ≠ opponent `CHECK`) either all pass
or leave nothing behind, and their messages are re-raised as user-facing
`ValidationError`s.

Editing items (`saved.update_items`) is the same pattern: `UPDATE saved_build
SET is_customized = TRUE`, `DELETE FROM build_item WHERE build_id = ...`, three
`INSERT`s, one transaction.

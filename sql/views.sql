-- Derived data as materialized views. Nothing here is an independent fact: every
-- view is rebuilt from the base tables by sql/refresh.sql after each load.
-- Draft 4, "Derived, not stored": threat profiles are per-(champion, role)
-- per-minute means; historical comps are profiled from the enemy champions'
-- EXPECTED profiles, never from what they dealt in that game.

-- 1. Per-(champion, role) per-minute means over that champion's participant rows.
CREATE MATERIALIZED VIEW champion_role_profile AS
SELECT p.champion_id,
       p.role,
       COUNT(*)                                             AS games,
       AVG(p.physical_damage * 60.0 / m.duration_seconds)   AS physical_pm,
       AVG(p.magic_damage    * 60.0 / m.duration_seconds)   AS magic_pm,
       AVG(p.true_damage     * 60.0 / m.duration_seconds)   AS true_pm,
       AVG(p.healing_done    * 60.0 / m.duration_seconds)   AS healing_pm,
       AVG(p.cc_seconds      * 60.0 / m.duration_seconds)   AS cc_pm
FROM participant p
JOIN match m USING (match_id)
GROUP BY p.champion_id, p.role;

CREATE UNIQUE INDEX champion_role_profile_pk ON champion_role_profile (champion_id, role);

-- 2. Per-champion means across all roles (the fallback below 50 games).
CREATE MATERIALIZED VIEW champion_profile AS
SELECT p.champion_id,
       COUNT(*)                                             AS games,
       AVG(p.physical_damage * 60.0 / m.duration_seconds)   AS physical_pm,
       AVG(p.magic_damage    * 60.0 / m.duration_seconds)   AS magic_pm,
       AVG(p.true_damage     * 60.0 / m.duration_seconds)   AS true_pm,
       AVG(p.healing_done    * 60.0 / m.duration_seconds)   AS healing_pm,
       AVG(p.cc_seconds      * 60.0 / m.duration_seconds)   AS cc_pm
FROM participant p
JOIN match m USING (match_id)
GROUP BY p.champion_id;

CREATE UNIQUE INDEX champion_profile_pk ON champion_profile (champion_id);

-- 3. The profile actually used for a (champion, role): role-specific when it
--    rests on at least 50 games, otherwise the champion's all-role mean.
--    One row for every champion x role, so comp sums never lose a member;
--    a champion never seen in the data has games = 0 and NULL means.
CREATE MATERIALIZED VIEW champion_effective_profile AS
SELECT c.champion_id,
       r.role,
       CASE WHEN COALESCE(cr.games, 0) >= 50 THEN 'role' ELSE 'champion' END AS source,
       COALESCE(cr.games, 0)                                                  AS role_games,
       COALESCE(cp.games, 0)                                                  AS champion_games,
       CASE WHEN COALESCE(cr.games, 0) >= 50 THEN cr.physical_pm ELSE cp.physical_pm END AS physical_pm,
       CASE WHEN COALESCE(cr.games, 0) >= 50 THEN cr.magic_pm    ELSE cp.magic_pm    END AS magic_pm,
       CASE WHEN COALESCE(cr.games, 0) >= 50 THEN cr.true_pm     ELSE cp.true_pm     END AS true_pm,
       CASE WHEN COALESCE(cr.games, 0) >= 50 THEN cr.healing_pm  ELSE cp.healing_pm  END AS healing_pm,
       CASE WHEN COALESCE(cr.games, 0) >= 50 THEN cr.cc_pm       ELSE cp.cc_pm       END AS cc_pm
FROM champion c
CROSS JOIN (VALUES ('TOP'), ('JUNGLE'), ('MIDDLE'), ('BOTTOM'), ('UTILITY')) AS r(role)
LEFT JOIN champion_role_profile cr ON cr.champion_id = c.champion_id AND cr.role = r.role
LEFT JOIN champion_profile      cp ON cp.champion_id = c.champion_id;

CREATE UNIQUE INDEX champion_effective_profile_pk ON champion_effective_profile (champion_id, role);

-- 4. Expected threat profile of each team in each historical match: the sum of
--    its five champions' effective profiles. Shares are of expected damage.
CREATE MATERIALIZED VIEW team_comp_profile AS
SELECT p.match_id,
       p.team,
       SUM(e.magic_pm)    / NULLIF(SUM(e.physical_pm + e.magic_pm + e.true_pm), 0) AS magic_share,
       SUM(e.physical_pm) / NULLIF(SUM(e.physical_pm + e.magic_pm + e.true_pm), 0) AS physical_share,
       SUM(e.healing_pm)                                                           AS healing_pm,
       SUM(e.cc_pm)                                                                AS cc_pm
FROM participant p
JOIN champion_effective_profile e ON e.champion_id = p.champion_id AND e.role = p.role
GROUP BY p.match_id, p.team;

CREATE UNIQUE INDEX team_comp_profile_pk ON team_comp_profile (match_id, team);

-- 5. The 75th percentiles that the healing and crowd-control rules compare against.
CREATE MATERIALIZED VIEW comp_thresholds AS
SELECT COUNT(*)                                                       AS comps,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY healing_pm)        AS healing_pm_p75,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY cc_pm)             AS cc_pm_p75
FROM team_comp_profile;

-- 6. One row per participant with everything the build queries group over:
--    outcome, lane opponent, first three completed legendaries in order, item
--    class flags over all completed items, and the ENEMY team's expected profile.
CREATE MATERIALIZED VIEW participant_core AS
WITH legendary_completion AS (
  SELECT pe.match_id,
         pe.participant_number,
         pe.item_id,
         ROW_NUMBER() OVER (PARTITION BY pe.match_id, pe.participant_number
                            ORDER BY pe.game_time_ms, pe.event_number) AS rn
  FROM purchase_event pe
  JOIN item i ON i.item_id = pe.item_id
  WHERE i.is_legendary AND NOT i.is_boots
),
core AS (
  SELECT match_id,
         participant_number,
         COUNT(*)                                   AS legendary_completions,
         MAX(CASE WHEN rn = 1 THEN item_id END)     AS item1,
         MAX(CASE WHEN rn = 2 THEN item_id END)     AS item2,
         MAX(CASE WHEN rn = 3 THEN item_id END)     AS item3
  FROM legendary_completion
  GROUP BY match_id, participant_number
),
class_flags AS (
  SELECT pe.match_id,
         pe.participant_number,
         BOOL_OR(i.magic_resist > 0)          AS completed_magic_resist,
         BOOL_OR(i.armor > 0)                 AS completed_armor,
         BOOL_OR(i.applies_grievous_wounds)   AS completed_grievous_wounds,
         BOOL_OR(i.tenacity_pct > 0)          AS completed_tenacity
  FROM purchase_event pe
  JOIN item i ON i.item_id = pe.item_id
  GROUP BY pe.match_id, pe.participant_number
)
SELECT p.match_id,
       p.participant_number,
       p.champion_id,
       p.role,
       p.team,
       (p.team = m.winning_team)                        AS won,
       o.champion_id                                    AS opponent_champion_id,
       m.duration_seconds,
       m.seed_tier,
       COALESCE(c.legendary_completions, 0)             AS legendary_completions,
       c.item1,
       c.item2,
       c.item3,
       COALESCE(f.completed_magic_resist, FALSE)        AS completed_magic_resist,
       COALESCE(f.completed_armor, FALSE)               AS completed_armor,
       COALESCE(f.completed_grievous_wounds, FALSE)     AS completed_grievous_wounds,
       COALESCE(f.completed_tenacity, FALSE)            AS completed_tenacity,
       t.magic_share                                    AS enemy_magic_share,
       t.physical_share                                 AS enemy_physical_share,
       t.healing_pm                                     AS enemy_healing_pm,
       t.cc_pm                                          AS enemy_cc_pm
FROM participant p
JOIN match m ON m.match_id = p.match_id
JOIN participant o ON o.match_id = p.match_id AND o.role = p.role AND o.team <> p.team
LEFT JOIN core c        ON c.match_id = p.match_id AND c.participant_number = p.participant_number
LEFT JOIN class_flags f ON f.match_id = p.match_id AND f.participant_number = p.participant_number
LEFT JOIN team_comp_profile t ON t.match_id = p.match_id AND t.team <> p.team;

CREATE UNIQUE INDEX participant_core_pk ON participant_core (match_id, participant_number);
CREATE INDEX participant_core_lookup_idx ON participant_core (champion_id, role, opponent_champion_id);

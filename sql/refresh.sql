-- Rebuild every derived view after a load. Order matters: each view reads the
-- ones above it. CONCURRENTLY keeps the web app's readers unblocked during the
-- rebuild (a plain REFRESH takes ACCESS EXCLUSIVE for several seconds at 20k
-- matches); it needs the unique index each view declares in views.sql.
REFRESH MATERIALIZED VIEW CONCURRENTLY champion_role_profile;
REFRESH MATERIALIZED VIEW CONCURRENTLY champion_profile;
REFRESH MATERIALIZED VIEW CONCURRENTLY champion_effective_profile;
REFRESH MATERIALIZED VIEW CONCURRENTLY team_comp_profile;
REFRESH MATERIALIZED VIEW CONCURRENTLY comp_thresholds;
REFRESH MATERIALIZED VIEW CONCURRENTLY participant_core;
ANALYZE;

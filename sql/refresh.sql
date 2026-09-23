-- Rebuild every derived view after a load. Order matters: each view reads the
-- ones above it.
REFRESH MATERIALIZED VIEW champion_role_profile;
REFRESH MATERIALIZED VIEW champion_profile;
REFRESH MATERIALIZED VIEW champion_effective_profile;
REFRESH MATERIALIZED VIEW team_comp_profile;
REFRESH MATERIALIZED VIEW comp_thresholds;
REFRESH MATERIALIZED VIEW participant_core;
ANALYZE;

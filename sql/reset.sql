-- Drop everything LaneForge owns, in dependency order. Used by tests and by
-- `scripts/db.sh reset`. Safe on an empty database.
DROP MATERIALIZED VIEW IF EXISTS participant_core;
DROP MATERIALIZED VIEW IF EXISTS comp_thresholds;
DROP MATERIALIZED VIEW IF EXISTS team_comp_profile;
DROP MATERIALIZED VIEW IF EXISTS champion_effective_profile;
DROP MATERIALIZED VIEW IF EXISTS champion_profile;
DROP MATERIALIZED VIEW IF EXISTS champion_role_profile;
DROP TABLE IF EXISTS build_item;
DROP TABLE IF EXISTS saved_build_enemy;
DROP TABLE IF EXISTS saved_build;
DROP TABLE IF EXISTS purchase_event;
DROP TABLE IF EXISTS participant;
DROP TABLE IF EXISTS match;
DROP TABLE IF EXISTS item;
DROP TABLE IF EXISTS champion;
DROP TABLE IF EXISTS user_profile;
DROP FUNCTION IF EXISTS check_saved_build_enemy();
DROP FUNCTION IF EXISTS check_saved_build_champions();
DROP FUNCTION IF EXISTS check_purchase_event_time();
DROP FUNCTION IF EXISTS check_build_item_legendary();

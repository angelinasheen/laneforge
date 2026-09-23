-- LaneForge schema. Mirrors project1-part1-draft4.md section "Draft SQL schema".
-- Kept PostgreSQL-10 compatible (no generated columns, no MATERIALIZED CTE keyword)
-- because the class server may be older than the local Postgres 17.
--
-- Constraint numbers in comments refer to the table "Constraints not expressible
-- in E/R notation" in draft 4.

CREATE TABLE user_profile (
  user_id       SERIAL PRIMARY KEY,
  display_name  VARCHAR(40) NOT NULL UNIQUE CHECK (length(trim(display_name)) >= 2),
  created_at    TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE champion (
  champion_id                INTEGER PRIMARY KEY,
  name                       VARCHAR(40) NOT NULL UNIQUE,
  ddragon_key                VARCHAR(40) NOT NULL UNIQUE,   -- e.g. 'MonkeyKing' (image file stem)
  -- base stats and per-level growth, from Data Dragon champion.json
  base_health                INTEGER NOT NULL CHECK (base_health > 0),
  health_per_level           NUMERIC(6,2) NOT NULL CHECK (health_per_level >= 0),
  base_armor                 INTEGER NOT NULL CHECK (base_armor >= 0),
  armor_per_level            NUMERIC(5,2) NOT NULL CHECK (armor_per_level >= 0),
  base_magic_resist          INTEGER NOT NULL CHECK (base_magic_resist >= 0),
  magic_resist_per_level     NUMERIC(5,2) NOT NULL CHECK (magic_resist_per_level >= 0),
  base_attack_damage         INTEGER NOT NULL CHECK (base_attack_damage >= 0),
  attack_damage_per_level    NUMERIC(5,2) NOT NULL CHECK (attack_damage_per_level >= 0),
  base_attack_speed          NUMERIC(5,3) NOT NULL CHECK (base_attack_speed > 0),
  attack_speed_per_level_pct NUMERIC(5,3) NOT NULL CHECK (attack_speed_per_level_pct >= 0)
);

CREATE TABLE item (
  item_id                 INTEGER PRIMARY KEY,
  name                    VARCHAR(60) NOT NULL,
  gold_cost               INTEGER NOT NULL CHECK (gold_cost >= 0),
  is_legendary            BOOLEAN NOT NULL,
  is_boots                BOOLEAN NOT NULL,
  -- stat line, straight from Data Dragon stats (zero where absent)
  attack_damage           INTEGER NOT NULL DEFAULT 0 CHECK (attack_damage >= 0),
  ability_power           INTEGER NOT NULL DEFAULT 0 CHECK (ability_power >= 0),
  health                  INTEGER NOT NULL DEFAULT 0 CHECK (health >= 0),
  mana                    INTEGER NOT NULL DEFAULT 0 CHECK (mana >= 0),
  attack_speed_pct        NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (attack_speed_pct >= 0),   -- fraction, 0.450 = 45%
  crit_chance_pct         NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (crit_chance_pct >= 0),   -- fraction
  move_speed              INTEGER NOT NULL DEFAULT 0 CHECK (move_speed >= 0),
  life_steal_pct          NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (life_steal_pct >= 0),    -- fraction
  armor                   INTEGER NOT NULL DEFAULT 0 CHECK (armor >= 0),
  magic_resist            INTEGER NOT NULL DEFAULT 0 CHECK (magic_resist >= 0),
  -- parsed from the description's <stats> block at load time
  ability_haste           INTEGER NOT NULL DEFAULT 0 CHECK (ability_haste >= 0),
  lethality               INTEGER NOT NULL DEFAULT 0 CHECK (lethality >= 0),
  armor_pen_pct           NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (armor_pen_pct >= 0),     -- fraction
  magic_pen_flat          INTEGER NOT NULL DEFAULT 0 CHECK (magic_pen_flat >= 0),
  magic_pen_pct           NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (magic_pen_pct >= 0),     -- fraction
  tenacity_pct            NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (tenacity_pct >= 0),      -- fraction
  applies_grievous_wounds BOOLEAN NOT NULL DEFAULT FALSE,
  CHECK (NOT (is_legendary AND is_boots))                                                 -- constraint 9
);

CREATE TABLE match (
  match_id          VARCHAR(20) PRIMARY KEY,
  game_version      VARCHAR(20) NOT NULL,
  start_time        TIMESTAMP NOT NULL,
  duration_seconds  INTEGER NOT NULL CHECK (duration_seconds > 0),
  winning_team      INTEGER NOT NULL CHECK (winning_team IN (100, 200)),
  seed_tier         VARCHAR(12) NOT NULL
);

-- Weak entity: key = identifying entity's key + discriminator.
-- Plays As folds in as a NOT NULL foreign key because it is "exactly one".
CREATE TABLE participant (
  match_id            VARCHAR(20) NOT NULL REFERENCES match ON DELETE CASCADE,
  participant_number  INTEGER NOT NULL CHECK (participant_number BETWEEN 1 AND 10),
  team                INTEGER NOT NULL CHECK (team IN (100, 200)),
  role                VARCHAR(8) NOT NULL
                      CHECK (role IN ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')),
  champion_id         INTEGER NOT NULL REFERENCES champion,
  physical_damage     INTEGER NOT NULL CHECK (physical_damage >= 0),   -- constraint 10
  magic_damage        INTEGER NOT NULL CHECK (magic_damage >= 0),
  true_damage         INTEGER NOT NULL CHECK (true_damage >= 0),
  healing_done        INTEGER NOT NULL CHECK (healing_done >= 0),
  cc_seconds          INTEGER NOT NULL CHECK (cc_seconds >= 0),
  PRIMARY KEY (match_id, participant_number),
  UNIQUE (match_id, team, role),        -- constraint 4
  UNIQUE (match_id, champion_id)        -- constraint 5
);

-- Weak entity nested two deep: the key grows to three columns.
-- Only surviving ITEM_PURCHASED events for completed items are stored, so the
-- event type is constant and is not an attribute.
CREATE TABLE purchase_event (
  match_id            VARCHAR(20) NOT NULL,
  participant_number  INTEGER NOT NULL,
  event_number        INTEGER NOT NULL CHECK (event_number >= 1),
  game_time_ms        INTEGER NOT NULL CHECK (game_time_ms >= 0),
  item_id             INTEGER NOT NULL REFERENCES item,
  PRIMARY KEY (match_id, participant_number, event_number),
  FOREIGN KEY (match_id, participant_number)
    REFERENCES participant ON DELETE CASCADE
);

-- Owns, Build For, Laned Against fold in as NOT NULL foreign keys.
CREATE TABLE saved_build (
  build_id              SERIAL PRIMARY KEY,
  user_id               INTEGER NOT NULL REFERENCES user_profile ON DELETE CASCADE,
  champion_id           INTEGER NOT NULL REFERENCES champion,
  opponent_champion_id  INTEGER NOT NULL REFERENCES champion,
  name                  VARCHAR(60) NOT NULL CHECK (length(trim(name)) >= 1),
  notes                 TEXT,
  role                  VARCHAR(8) NOT NULL
                        CHECK (role IN ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')),
  is_customized         BOOLEAN NOT NULL DEFAULT FALSE,
  created_at            TIMESTAMP NOT NULL DEFAULT now(),
  CHECK (champion_id <> opponent_champion_id)   -- constraint 2
);

-- Against Comp: many-to-many, own table. "At most four" and the exclusion of
-- the build's own champions are enforced by trigger (constraint 3).
CREATE TABLE saved_build_enemy (
  build_id      INTEGER NOT NULL REFERENCES saved_build ON DELETE CASCADE,
  champion_id   INTEGER NOT NULL REFERENCES champion,
  PRIMARY KEY (build_id, champion_id)
);

-- Contains: many-to-many with a descriptive attribute.
CREATE TABLE build_item (
  build_id   INTEGER NOT NULL REFERENCES saved_build ON DELETE CASCADE,
  item_id    INTEGER NOT NULL REFERENCES item,
  position   INTEGER NOT NULL CHECK (position IN (1, 2, 3)),
  PRIMARY KEY (build_id, item_id),      -- items distinct, from the relationship key
  UNIQUE (build_id, position)           -- constraint 1 (one item per position)
);

------------------------------------------------------------------------------
-- Indexes for the query paths (see docs/CONTRACT.md).
------------------------------------------------------------------------------
CREATE INDEX participant_champion_role_idx ON participant (champion_id, role);
CREATE INDEX purchase_event_item_idx       ON purchase_event (item_id);
CREATE INDEX saved_build_user_idx          ON saved_build (user_id, created_at DESC);
CREATE INDEX match_start_time_idx          ON match (start_time DESC);

------------------------------------------------------------------------------
-- Triggers for the cross-table constraints (3, 6, 8).
------------------------------------------------------------------------------

-- Constraint 3: at most four comp champions, none equal to the build's own
-- champion or its lane opponent.
CREATE OR REPLACE FUNCTION check_saved_build_enemy() RETURNS trigger AS $$
DECLARE
  n_enemies  INTEGER;
  own_champ  INTEGER;
  own_opp    INTEGER;
BEGIN
  SELECT champion_id, opponent_champion_id INTO own_champ, own_opp
    FROM saved_build WHERE build_id = NEW.build_id;
  IF NEW.champion_id = own_champ OR NEW.champion_id = own_opp THEN
    RAISE EXCEPTION 'comp champion % duplicates the build''s own champion or lane opponent',
      NEW.champion_id USING ERRCODE = 'check_violation';
  END IF;
  SELECT COUNT(*) INTO n_enemies FROM saved_build_enemy
    WHERE build_id = NEW.build_id AND champion_id <> NEW.champion_id;
  IF n_enemies >= 4 THEN
    RAISE EXCEPTION 'a saved build may name at most four other enemy champions'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER saved_build_enemy_check
  BEFORE INSERT OR UPDATE ON saved_build_enemy
  FOR EACH ROW EXECUTE PROCEDURE check_saved_build_enemy();

-- Constraint 3 (other direction): changing a build's champion or opponent must
-- not collide with an already-listed comp champion.
CREATE OR REPLACE FUNCTION check_saved_build_champions() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM saved_build_enemy
             WHERE build_id = NEW.build_id
               AND champion_id IN (NEW.champion_id, NEW.opponent_champion_id)) THEN
    RAISE EXCEPTION 'build champion or opponent collides with a listed comp champion'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER saved_build_champions_check
  BEFORE UPDATE OF champion_id, opponent_champion_id ON saved_build
  FOR EACH ROW EXECUTE PROCEDURE check_saved_build_champions();

-- Constraint 6: a purchase event's game time lies within its match's duration.
CREATE OR REPLACE FUNCTION check_purchase_event_time() RETURNS trigger AS $$
DECLARE
  dur INTEGER;
BEGIN
  SELECT duration_seconds INTO dur FROM match WHERE match_id = NEW.match_id;
  IF NEW.game_time_ms > dur * 1000 THEN
    RAISE EXCEPTION 'purchase at % ms is after the end of match % (% s)',
      NEW.game_time_ms, NEW.match_id, dur USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER purchase_event_time_check
  BEFORE INSERT OR UPDATE ON purchase_event
  FOR EACH ROW EXECUTE PROCEDURE check_purchase_event_time();

-- Constraint 8: a saved build only contains legendary, non-boots items.
CREATE OR REPLACE FUNCTION check_build_item_legendary() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM item
                 WHERE item_id = NEW.item_id AND is_legendary AND NOT is_boots) THEN
    RAISE EXCEPTION 'item % is not a legendary, non-boots item', NEW.item_id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER build_item_legendary_check
  BEFORE INSERT OR UPDATE ON build_item
  FOR EACH ROW EXECUTE PROCEDURE check_build_item_legendary();

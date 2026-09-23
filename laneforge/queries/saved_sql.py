"""SQL text for saved builds (kept apart so saved.py stays readable)."""

SQL_USER_EXISTS = "SELECT 1 FROM user_profile WHERE user_id = %(user_id)s"

SQL_INSERT_BUILD = """
INSERT INTO saved_build (user_id, champion_id, opponent_champion_id, name, notes, role, is_customized)
VALUES (%(user_id)s, %(champion_id)s, %(opponent_champion_id)s, %(name)s, %(notes)s, %(role)s,
        %(is_customized)s)
RETURNING build_id
"""
SQL_INSERT_ENEMY = """
INSERT INTO saved_build_enemy (build_id, champion_id) VALUES (%(build_id)s, %(champion_id)s)
"""
SQL_INSERT_BUILD_ITEM = """
INSERT INTO build_item (build_id, item_id, position) VALUES (%(build_id)s, %(item_id)s, %(position)s)
"""
SQL_MARK_CUSTOMIZED = "UPDATE saved_build SET is_customized = TRUE WHERE build_id = %(build_id)s"
SQL_DELETE_BUILD_ITEMS = "DELETE FROM build_item WHERE build_id = %(build_id)s"
SQL_RENAME_BUILD = """
UPDATE saved_build SET name = %(name)s, notes = %(notes)s WHERE build_id = %(build_id)s
"""
SQL_DELETE_BUILD = "DELETE FROM saved_build WHERE build_id = %(build_id)s"

# Constraint 11: the exact first-three sequence occurs for this champion and role.
SQL_OBSERVED_SEQUENCE = """
SELECT 1
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s AND pc.role = %(role)s
  AND pc.item1 = %(item1)s AND pc.item2 = %(item2)s AND pc.item3 = %(item3)s
LIMIT 1
"""

_BUILD_SELECT = """
SELECT b.build_id, b.name, b.notes, b.champion_id, b.opponent_champion_id, b.role,
       b.is_customized, b.created_at AS build_created_at,
       u.user_id, u.display_name, u.created_at
FROM saved_build b
JOIN user_profile u ON u.user_id = b.user_id
"""
SQL_LIST_BUILDS = _BUILD_SELECT + "WHERE b.user_id = %(user_id)s ORDER BY b.created_at DESC, b.build_id DESC"
SQL_GET_BUILD = _BUILD_SELECT + "WHERE b.build_id = %(build_id)s"

SQL_BUILD_ENEMIES = """
SELECT e.build_id, e.champion_id
FROM saved_build_enemy e JOIN champion c ON c.champion_id = e.champion_id
WHERE e.build_id = ANY(%(ids)s)
ORDER BY e.build_id, c.name
"""
SQL_BUILD_ITEMS = """
SELECT build_id, item_id, position FROM build_item
WHERE build_id = ANY(%(ids)s)
ORDER BY build_id, position
"""

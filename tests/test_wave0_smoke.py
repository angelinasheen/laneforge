from tests import factories as f
from laneforge import db

def test_factories_and_views_roundtrip(conn):
    for cid, name in [(103,'Ahri'),(238,'Zed'),(1,'Annie'),(2,'Olaf'),(3,'Galio'),(4,'Twisted Fate'),(5,'Xin Zhao'),(6,'Urgot'),(7,'LeBlanc'),(8,'Vladimir')]:
        f.champion(conn, cid, name)
    f.item(conn, 6655, "Luden's Companion", ability_power=95)
    f.item(conn, 3089, "Rabadon's Deathcap", ability_power=130)
    f.item(conn, 4645, "Shadowflame", ability_power=110, magic_pen_flat=15)
    f.item(conn, 3111, "Mercury's Treads", is_legendary=False, is_boots=True, magic_resist=20, tenacity_pct=0.3, gold_cost=1200)
    f.match(conn, "NA1_1", [1,2,103,4,5], [6,7,238,8,3], winning_team=100)
    f.purchases(conn, "NA1_1", 3, [3111, 6655, 4645, 3089])
    conn.commit()
    db.refresh_views(conn)
    row = conn.execute("select * from participant_core where match_id='NA1_1' and participant_number=3").fetchone()
    assert row["opponent_champion_id"] == 238 and row["won"] is True
    assert (row["item1"], row["item2"], row["item3"]) == (6655, 4645, 3089)
    assert row["legendary_completions"] == 3 and row["completed_tenacity"] is True
    assert row["enemy_magic_share"] is not None

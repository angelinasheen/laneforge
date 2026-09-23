"""Stack replay of timeline item events (draft 4 "Item events")."""
from __future__ import annotations

from laneforge.ingest.completion import completed_item_ids
from laneforge.ingest.timeline import (
    item_events_by_participant,
    replay,
    replay_purchases,
)

# A tiny item.json slice with the shapes that matter.
BF_SWORD, PICKAXE, CLOAK = 1038, 1037, 1018
INFINITY_EDGE, BLOODTHIRSTER = 3031, 3072
POTION, WARD = 2003, 3340
BOOTS, GREAVES = 1001, 3006
MANAMUNE, MURAMANA = 3004, 3042

ITEM_JSON = {"data": {
    str(BF_SWORD): {"into": ["3031"], "tags": ["Damage"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(PICKAXE): {"into": ["3031"], "tags": ["Damage"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(CLOAK): {"into": ["3031"], "tags": ["CriticalStrike"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(INFINITY_EDGE): {"from": ["1038"], "tags": ["Damage"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(BLOODTHIRSTER): {"tags": ["Damage"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(POTION): {"tags": ["Consumable"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(WARD): {"tags": ["Trinket"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(BOOTS): {"into": ["3006"], "tags": ["Boots"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(GREAVES): {"from": ["1001", "1042"], "into": ["3172"], "tags": ["Boots"], "maps": {"11": True},
                   "gold": {"purchasable": True}},
    str(MANAMUNE): {"tags": ["Mana"], "maps": {"11": True}, "gold": {"purchasable": True}},
    str(MURAMANA): {"specialRecipe": 3004, "tags": ["Mana"], "maps": {"11": True},
                    "gold": {"purchasable": True}},   # purchasable on purpose: specialRecipe alone excludes it
}}
COMPLETED = completed_item_ids(ITEM_JSON)
is_completed = COMPLETED.__contains__


def buy(item_id, t):
    return {"type": "ITEM_PURCHASED", "itemId": item_id, "participantId": 1, "timestamp": t}


def destroy(item_id, t):
    return {"type": "ITEM_DESTROYED", "itemId": item_id, "participantId": 1, "timestamp": t}


def sell(item_id, t):
    return {"type": "ITEM_SOLD", "itemId": item_id, "participantId": 1, "timestamp": t}


def undo(before_id, after_id, t):
    return {"type": "ITEM_UNDO", "beforeId": before_id, "afterId": after_id, "goldGain": 0,
            "participantId": 1, "timestamp": t}


def items(purchases):
    return [p.item_id for p in purchases]


def test_completed_set_excludes_components_consumables_trinkets_and_transformations():
    # Tier-2 boots keep an `into` on this patch (Feats of Strength upgrades) but are completed items.
    assert COMPLETED == {INFINITY_EDGE, BLOODTHIRSTER, MANAMUNE, GREAVES}


def test_plain_purchase_is_kept():
    result = replay_purchases([buy(INFINITY_EDGE, 900_000)], is_completed)

    assert items(result) == [INFINITY_EDGE]
    assert result[0].event_number == 1 and result[0].game_time_ms == 900_000


def test_undo_of_a_purchase_removes_it_and_restores_nothing_else():
    # Arrange
    events = [buy(BF_SWORD, 100), buy(BLOODTHIRSTER, 5_000), undo(BLOODTHIRSTER, 0, 6_000)]

    # Act
    state = replay(events)

    # Assert
    assert [r.item_id for r in state.live_purchases()] == [BF_SWORD]
    assert state.live_destroyed() == ()
    assert replay_purchases(events, is_completed) == ()


def test_undo_of_a_completion_restores_its_destroyed_components():
    # Arrange: BF and pickaxe are consumed into IE at t=10s, then IE is undone
    events = [
        buy(BF_SWORD, 1_000), buy(PICKAXE, 2_000),
        destroy(BF_SWORD, 10_000), destroy(PICKAXE, 10_000), buy(INFINITY_EDGE, 10_000),
        undo(INFINITY_EDGE, 0, 12_000),
    ]

    # Act
    state = replay(events)

    # Assert: the destructions are cancelled, the components still owned
    assert state.live_destroyed() == ()
    assert [r.item_id for r in state.live_purchases()] == [BF_SWORD, PICKAXE]
    assert replay_purchases(events, is_completed) == ()


def test_undo_only_restores_components_destroyed_at_the_undone_purchase():
    events = [
        destroy(POTION, 3_000),
        buy(BF_SWORD, 1_000), destroy(BF_SWORD, 10_000), buy(INFINITY_EDGE, 10_000),
        undo(INFINITY_EDGE, 0, 11_000),
    ]

    state = replay(events)

    assert [r.item_id for r in state.live_destroyed()] == [POTION]


def test_undo_pops_the_most_recent_matching_purchase():
    events = [buy(BLOODTHIRSTER, 1_000), buy(BLOODTHIRSTER, 2_000), undo(BLOODTHIRSTER, 0, 2_500)]

    result = replay_purchases(events, is_completed)

    assert [(p.item_id, p.game_time_ms) for p in result] == [(BLOODTHIRSTER, 1_000)]


def test_undo_of_a_sale_cancels_the_sale_and_keeps_the_purchase():
    events = [buy(INFINITY_EDGE, 1_000), sell(INFINITY_EDGE, 2_000), undo(0, INFINITY_EDGE, 2_500)]

    state = replay(events)

    assert state.live_sales() == ()
    assert items(replay_purchases(events, is_completed)) == [INFINITY_EDGE]


def test_sold_item_still_counts_as_a_completion():
    events = [buy(INFINITY_EDGE, 1_000), sell(INFINITY_EDGE, 900_000), buy(BLOODTHIRSTER, 950_000)]

    assert items(replay_purchases(events, is_completed)) == [INFINITY_EDGE, BLOODTHIRSTER]


def test_consumables_components_trinkets_and_basic_boots_are_excluded():
    events = [buy(POTION, 0), buy(WARD, 0), buy(BOOTS, 100), buy(BF_SWORD, 200),
              buy(GREAVES, 300), buy(INFINITY_EDGE, 400)]

    assert items(replay_purchases(events, is_completed)) == [GREAVES, INFINITY_EDGE]


def test_transformation_purchase_is_dropped_and_base_completion_kept():
    events = [buy(MANAMUNE, 600_000), destroy(MANAMUNE, 1_200_000), buy(MURAMANA, 1_200_000)]

    assert items(replay_purchases(events, is_completed)) == [MANAMUNE]


def test_ties_in_timestamp_keep_original_order():
    events = [buy(BLOODTHIRSTER, 5_000), buy(MANAMUNE, 5_000), buy(INFINITY_EDGE, 5_000)]

    assert items(replay_purchases(events, is_completed)) == [BLOODTHIRSTER, MANAMUNE, INFINITY_EDGE]


def test_events_out_of_order_are_sorted_by_timestamp():
    events = [buy(INFINITY_EDGE, 9_000), buy(BLOODTHIRSTER, 3_000)]

    assert items(replay_purchases(events, is_completed)) == [BLOODTHIRSTER, INFINITY_EDGE]


def test_event_numbers_are_contiguous_and_increase_with_time():
    events = [buy(MANAMUNE, 100), buy(POTION, 150), buy(BLOODTHIRSTER, 200),
              undo(BLOODTHIRSTER, 0, 250), buy(INFINITY_EDGE, 300), buy(BLOODTHIRSTER, 400)]

    result = replay_purchases(events, is_completed)

    assert [p.event_number for p in result] == [1, 2, 3]
    assert [p.game_time_ms for p in result] == sorted(p.game_time_ms for p in result)


def test_events_are_grouped_by_participant_across_frames():
    timeline = {"info": {"frames": [
        {"events": [buy(INFINITY_EDGE, 10), {"type": "WARD_PLACED", "timestamp": 11},
                    dict(buy(BLOODTHIRSTER, 12), participantId=2)]},
        {"events": [sell(INFINITY_EDGE, 60_010)]},
    ]}}

    grouped = item_events_by_participant(timeline)

    assert [e["type"] for e in grouped[1]] == ["ITEM_PURCHASED", "ITEM_SOLD"]
    assert grouped[2][0]["itemId"] == BLOODTHIRSTER

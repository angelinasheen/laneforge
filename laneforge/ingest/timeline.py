"""Timeline item-event replay (draft 4 "Item events").

Each participant's item events are replayed in (timestamp, original order):

- ITEM_PURCHASED pushes a purchase.
- ITEM_DESTROYED records that a component (or consumable) was consumed at T.
- ITEM_SOLD records a sale. Sales never remove earlier purchases: the core
  build is completion order, not final inventory.
- ITEM_UNDO with beforeId != 0 pops the most recent live purchase of beforeId
  and restores the components destroyed at that purchase's timestamp.
  ITEM_UNDO with beforeId == 0 undoes a sale: the most recent live sale of
  afterId is cancelled.

Only surviving purchases of completed items are kept, numbered 1..n.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

log = logging.getLogger(__name__)

PURCHASED = "ITEM_PURCHASED"
SOLD = "ITEM_SOLD"
DESTROYED = "ITEM_DESTROYED"
UNDO = "ITEM_UNDO"
ITEM_EVENT_TYPES = frozenset({PURCHASED, SOLD, DESTROYED, UNDO})
NO_ITEM = 0


@dataclass(frozen=True)
class Purchase:
    event_number: int
    item_id: int
    game_time_ms: int


@dataclass(frozen=True)
class ItemRecord:
    """One purchase, destruction or sale, with liveness after replay."""

    item_id: int
    timestamp: int
    order: int
    live: bool = True


@dataclass(frozen=True)
class ReplayState:
    purchases: tuple[ItemRecord, ...] = ()
    destroyed: tuple[ItemRecord, ...] = ()
    sales: tuple[ItemRecord, ...] = ()

    def live_purchases(self) -> tuple[ItemRecord, ...]:
        return tuple(r for r in self.purchases if r.live)

    def live_destroyed(self) -> tuple[ItemRecord, ...]:
        return tuple(r for r in self.destroyed if r.live)

    def live_sales(self) -> tuple[ItemRecord, ...]:
        return tuple(r for r in self.sales if r.live)


def item_events_by_participant(timeline: Mapping[str, Any]) -> dict[int, tuple[dict, ...]]:
    """Group a match-v5 timeline's item events by participantId, in file order."""
    grouped: dict[int, list[dict]] = {}
    for frame in timeline.get("info", {}).get("frames", ()):
        for event in frame.get("events", ()):
            if event.get("type") in ITEM_EVENT_TYPES and "participantId" in event:
                grouped.setdefault(int(event["participantId"]), []).append(event)
    return {pid: tuple(events) for pid, events in grouped.items()}


def replay(events: Iterable[Mapping[str, Any]]) -> ReplayState:
    """Apply the stack replay to one participant's item events."""
    ordered = sorted(enumerate(events), key=lambda pair: (int(pair[1]["timestamp"]), pair[0]))
    state = ReplayState()
    for order, event in ordered:
        state = _apply(state, event, order)
    return state


def replay_purchases(
    events: Iterable[Mapping[str, Any]],
    is_completed: Callable[[int], bool],
) -> tuple[Purchase, ...]:
    """Surviving ITEM_PURCHASED events for completed items, numbered 1..n by
    (timestamp, original order)."""
    survivors = [r for r in replay(events).live_purchases() if is_completed(r.item_id)]
    survivors.sort(key=lambda r: (r.timestamp, r.order))
    return tuple(
        Purchase(event_number=n, item_id=r.item_id, game_time_ms=r.timestamp)
        for n, r in enumerate(survivors, start=1)
    )


def _apply(state: ReplayState, event: Mapping[str, Any], order: int) -> ReplayState:
    kind = event.get("type")
    timestamp = int(event["timestamp"])
    if kind == PURCHASED:
        record = ItemRecord(int(event["itemId"]), timestamp, order)
        return ReplayState(state.purchases + (record,), state.destroyed, state.sales)
    if kind == DESTROYED:
        record = ItemRecord(int(event["itemId"]), timestamp, order)
        return ReplayState(state.purchases, state.destroyed + (record,), state.sales)
    if kind == SOLD:
        record = ItemRecord(int(event["itemId"]), timestamp, order)
        return ReplayState(state.purchases, state.destroyed, state.sales + (record,))
    if kind == UNDO:
        return _undo(state, int(event.get("beforeId", NO_ITEM)), int(event.get("afterId", NO_ITEM)))
    return state


def _undo(state: ReplayState, before_id: int, after_id: int) -> ReplayState:
    if before_id == NO_ITEM:
        return _undo_sale(state, after_id)
    index = _last_live_index(state.purchases, before_id)
    if index is None:
        log.debug("ITEM_UNDO of %s matches no live purchase; ignored", before_id)
        return state
    undone = state.purchases[index]
    purchases = _with_dead(state.purchases, index)
    destroyed = tuple(
        _killed(r) if r.live and r.timestamp == undone.timestamp else r
        for r in state.destroyed
    )
    return ReplayState(purchases, destroyed, state.sales)


def _undo_sale(state: ReplayState, item_id: int) -> ReplayState:
    index = _last_live_index(state.sales, item_id)
    if index is None:
        log.debug("ITEM_UNDO of a sale of %s matches no live sale; ignored", item_id)
        return state
    return ReplayState(state.purchases, state.destroyed, _with_dead(state.sales, index))


def _last_live_index(records: tuple[ItemRecord, ...], item_id: int) -> int | None:
    for index in range(len(records) - 1, -1, -1):
        if records[index].live and records[index].item_id == item_id:
            return index
    return None


def _with_dead(records: tuple[ItemRecord, ...], index: int) -> tuple[ItemRecord, ...]:
    return records[:index] + (_killed(records[index]),) + records[index + 1:]


def _killed(record: ItemRecord) -> ItemRecord:
    return ItemRecord(record.item_id, record.timestamp, record.order, live=False)

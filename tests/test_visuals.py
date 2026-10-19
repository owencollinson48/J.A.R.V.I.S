"""Visuals are bounded data, private to their conversation, and reload reliably."""

import copy
import json

import pytest

from jarvis.store import Store
from jarvis.visuals import Visuals


@pytest.fixture
def visual_store(tmp_path):
    store = Store(tmp_path / "visuals.db")
    visuals = Visuals(store)
    conversation = store.conversation("My day")["id"]
    yield visuals, store, conversation
    store.db.close()


def payload(kind="timeline"):
    return {"kind": kind, "title": "My day", "subtitle": "Tuesday, 20 October",
            "unit": "minutes", "items": [
                {"label": "Morning focus", "detail": "Work on the proposal", "time": "09:00",
                 "value": 45, "status": "active"},
                {"label": "Lunch", "time": "12:30", "value": 30}]}


@pytest.mark.parametrize("kind", ["timeline", "cards", "bars"])
def test_each_visual_type_saves_complete_data_and_defaults_status(visual_store, kind):
    visuals, store, conversation = visual_store
    given = payload(kind)
    original = copy.deepcopy(given)
    visual = visuals.show(conversation, given)
    assert visual["id"] and visual["created_at"]
    assert visual["conversation_id"] == conversation
    assert visual["items"][1]["status"] == "planned"
    assert visual["items"][0] == given["items"][0]
    assert visuals.latest(conversation) == visual
    assert given == original
    stored = json.loads(store.row("SELECT payload FROM conversation_visuals")["payload"])
    assert stored == {key: value for key, value in visual.items()
                      if key not in {"id", "conversation_id", "created_at"}}


def test_optional_fields_absent_and_text_is_retained_as_inert_data(visual_store):
    visuals, _, conversation = visual_store
    markup = '<script>alert("hello")</script>'
    visual = visuals.show(conversation, {"kind": "cards", "title": markup,
                                         "items": [{"label": "<svg onload=run()>", "detail": markup}]})
    assert visual["title"] == markup
    assert visual["items"][0]["detail"] == markup
    assert visual["items"][0]["label"] == "<svg onload=run()>"
    assert "unit" not in visual and "subtitle" not in visual
    assert "time" not in visual["items"][0] and "value" not in visual["items"][0]


@pytest.mark.parametrize("replacement", [None, [], "<svg/>", True, 12])
def test_nonobject_visuals_rejected(visual_store, replacement):
    visuals, store, conversation = visual_store
    with pytest.raises(ValueError):
        visuals.show(conversation, replacement)
    assert store.rows("SELECT * FROM conversation_visuals") == []


@pytest.mark.parametrize("field,value", [
    ("kind", "svg"), ("kind", []), ("title", None), ("title", "  "),
    ("title", "a" * 121), ("subtitle", "a" * 241), ("unit", "a" * 25),
    ("subtitle", {}), ("unit", 9), ("items", []), ("items", [{}] * 13),
    ("items", {}), ("items", ["row"]), ("html", "<svg/>"), ("id", "chosen-id"),
])
def test_invalid_top_level_fields_rejected_without_saving(visual_store, field, value):
    visuals, store, conversation = visual_store
    given = payload()
    given[field] = value
    with pytest.raises(ValueError):
        visuals.show(conversation, given)
    assert visuals.latest(conversation) is None
    assert store.rows("SELECT * FROM conversation_visuals") == []


@pytest.mark.parametrize("field,value", [
    ("label", None), ("label", " "), ("label", "a" * 101), ("detail", "a" * 241),
    ("time", "a" * 41), ("detail", []), ("time", 9), ("status", "complete"),
    ("status", []), ("html", "<svg/>"), ("onclick", "doSomething()"),
    ("value", float("nan")), ("value", float("inf")), ("value", -float("inf")),
    ("value", True), ("value", "12"), ("value", 10 ** 1000),
])
def test_invalid_item_fields_rejected_without_saving(visual_store, field, value):
    visuals, store, conversation = visual_store
    given = payload()
    given["items"][0][field] = value
    with pytest.raises(ValueError):
        visuals.show(conversation, given)
    assert store.rows("SELECT * FROM conversation_visuals") == []


@pytest.mark.parametrize("value", [None, -1, -0.5, float("nan"), float("inf"), True, "3"])
def test_bars_require_finite_nonnegative_number_for_every_item(visual_store, value):
    visuals, _, conversation = visual_store
    given = payload("bars")
    given["items"][1]["value"] = value
    with pytest.raises(ValueError):
        visuals.show(conversation, given)
    given["items"][1].pop("value")
    with pytest.raises(ValueError):
        visuals.show(conversation, given)


def test_zero_bars_and_optional_negative_card_values_are_valid(visual_store):
    visuals, _, conversation = visual_store
    bar = visuals.show(conversation, {"kind": "bars", "title": "Time", "items": [{"label": "Free", "value": 0}]})
    assert bar["items"][0]["value"] == 0
    card = visuals.show(conversation, {"kind": "cards", "title": "Change", "items": [{"label": "Difference", "value": -2.5}]})
    assert card["items"][0]["value"] == -2.5


def test_exact_text_and_item_limits_are_valid(visual_store):
    visuals, _, conversation = visual_store
    visual = visuals.show(conversation, {"kind": "timeline", "title": "t" * 120,
        "subtitle": "s" * 240, "unit": "u" * 24, "items": [
            {"label": "l" * 100, "detail": "d" * 240, "time": "t" * 40, "status": "done"}
            for _ in range(12)]})
    assert len(visual["items"]) == 12 and visual["items"][-1]["status"] == "done"


def test_latest_and_dismiss_are_conversation_scoped(visual_store):
    visuals, store, conversation = visual_store
    other = store.conversation("Other")["id"]
    visual = visuals.show(conversation, payload())
    assert visuals.latest(other) is None
    with pytest.raises(ValueError, match="not found"):
        visuals.dismiss(other, visual["id"])
    with pytest.raises(ValueError, match="not found"):
        visuals.dismiss(conversation, "missing")
    assert visuals.latest(conversation) == visual
    assert visuals.dismiss(conversation, visual["id"]) == {"id": visual["id"], "dismissed": True}
    assert visuals.dismiss(conversation, visual["id"])["dismissed"] is True
    assert visuals.latest(conversation) is None


def test_dismissed_latest_does_not_resurface_older_graphic(visual_store, monkeypatch):
    visuals, _, conversation = visual_store
    # Equal timestamps still use insertion order, not a random UUID tie-break.
    monkeypatch.setattr("jarvis.visuals.now", lambda: "2026-10-20T09:00:00+00:00")
    older = visuals.show(conversation, payload())
    current = visuals.show(conversation, {**payload(), "title": "Updated day"})
    assert visuals.latest(conversation)["id"] == current["id"]
    visuals.dismiss(conversation, current["id"])
    assert visuals.latest(conversation) is None
    newest = visuals.show(conversation, {**payload(), "title": "Evening"})
    assert visuals.latest(conversation)["id"] == newest["id"]
    visuals.dismiss(conversation, older["id"])
    assert visuals.latest(conversation)["id"] == newest["id"]


def test_new_visual_requires_existing_conversation(visual_store):
    visuals, store, _ = visual_store
    with pytest.raises(ValueError, match="Conversation not found"):
        visuals.show("missing", payload())
    assert store.rows("SELECT * FROM conversation_visuals") == []


def test_reopening_store_preserves_visuals_and_dismissal(tmp_path):
    path = tmp_path / "reload.db"
    first = Store(path)
    conversation = first.conversation()["id"]
    visual = Visuals(first).show(conversation, payload())
    first.db.close()
    second = Store(path)
    visuals = Visuals(second)
    assert visuals.latest(conversation) == visual
    visuals.dismiss(conversation, visual["id"])
    second.db.close()
    third = Store(path)
    try:
        assert Visuals(third).latest(conversation) is None
        assert third.row("SELECT dismissed FROM conversation_visuals")["dismissed"] == 1
    finally:
        third.db.close()


def test_deleting_conversation_cascades_only_its_visuals(visual_store):
    visuals, store, conversation = visual_store
    other = store.conversation()["id"]
    visuals.show(conversation, payload())
    retained = visuals.show(other, payload("cards"))
    store.execute("DELETE FROM conversations WHERE id=?", (conversation,))
    assert visuals.latest(conversation) is None
    assert visuals.latest(other) == retained
    assert len(store.rows("SELECT * FROM conversation_visuals")) == 1

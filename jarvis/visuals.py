"""Small, data-only conversation visuals for the JARVIS display."""

import json
import math

from .store import identifier, now


KINDS = {"timeline", "cards", "bars"}
STATUSES = {"planned", "active", "done"}


def _text(value, name, limit, *, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if len(value) > limit:
        raise ValueError(f"{name} must be at most {limit} characters")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    return value


def _number(value, name, *, nonnegative=False):
    # bool is an int in Python, but is not a chart value in the JSON contract.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (nonnegative and value < 0):
        suffix = " nonnegative" if nonnegative else ""
        raise ValueError(f"{name} must be a finite{suffix} number")
    return value


def validate_visual(payload):
    """Validate bounded JSON data; text is never interpreted as markup or code."""
    if not isinstance(payload, dict):
        raise ValueError("A visual must be an object")
    allowed = {"kind", "title", "subtitle", "unit", "items"}
    if set(payload) - allowed:
        raise ValueError("Unknown visual fields")
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Visual kind must be timeline, cards, or bars")
    result = {"kind": kind, "title": _text(payload.get("title"), "Title", 120, required=True)}
    for field, limit in (("subtitle", 240), ("unit", 24)):
        if field in payload:
            result[field] = _text(payload[field], field.title(), limit)
    items = payload.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 12:
        raise ValueError("A visual needs between 1 and 12 items")
    result["items"] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each visual item must be an object")
        if set(item) - {"label", "detail", "time", "value", "status"}:
            raise ValueError("Unknown visual item fields")
        entry = {"label": _text(item.get("label"), "Item label", 100, required=True)}
        for field, limit in (("detail", 240), ("time", 40)):
            if field in item:
                entry[field] = _text(item[field], f"Item {field}", limit)
        if kind == "bars" or "value" in item:
            entry["value"] = _number(item.get("value"), "Item value", nonnegative=kind == "bars")
        status = item.get("status", "planned")
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError("Item status must be planned, active, or done")
        entry["status"] = status
        result["items"].append(entry)
    return result


class Visuals:
    """Store conversation-owned visuals without changing task or reminder data."""

    def __init__(self, store):
        self.store = store
        store.execute("""CREATE TABLE IF NOT EXISTS conversation_visuals (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, dismissed INTEGER DEFAULT 0,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE)""")

    def show(self, conversation_id, payload):
        data = validate_visual(payload)
        visual = {"id": identifier(), "conversation_id": conversation_id,
                  **data, "created_at": now()}
        with self.store.lock:
            if not self.store.row("SELECT id FROM conversations WHERE id=?", (conversation_id,)):
                raise ValueError("Conversation not found")
            self.store.execute("""INSERT INTO conversation_visuals
                (id,conversation_id,payload,created_at) VALUES(?,?,?,?)""",
                (visual["id"], conversation_id, json.dumps(data, ensure_ascii=False, allow_nan=False),
                 visual["created_at"]))
        return visual

    def latest(self, conversation_id):
        # Inspect the newest record before checking dismissal. Filtering dismissed
        # rows in SQL would bring an older graphic back after the user closes one.
        # Insertion order also stays correct when Windows adjusts its clock.
        row = self.store.row("""SELECT * FROM conversation_visuals WHERE conversation_id=?
            ORDER BY rowid DESC LIMIT 1""", (conversation_id,))
        if not row or row["dismissed"]:
            return None
        return {"id": row["id"], "conversation_id": row["conversation_id"],
                **json.loads(row["payload"]), "created_at": row["created_at"]}

    def dismiss(self, conversation_id, visual_id):
        count = self.store.execute("""UPDATE conversation_visuals SET dismissed=1
            WHERE id=? AND conversation_id=?""", (visual_id, conversation_id))
        if not count:
            raise ValueError("Visual not found in this conversation")
        return {"id": visual_id, "dismissed": True}

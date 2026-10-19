"""Conversation-bound progress that resumes a request without performing actions.

Checkout readiness needs current, successful UI evidence. Screenshot existence is
structural evidence only: interpreting its pixels remains the model's job.
"""

import json
import re
import unicodedata
from pathlib import Path

from .store import identifier, now


STATUSES = {"working", "awaiting_details", "ready_for_checkout", "completed", "cancelled"}
OBSERVATION_TOOLS = {"browser_action", "inspect_window", "desktop_screenshot",
                     "desktop_pointer", "desktop_action"}
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")
CART = re.compile(r"(?im)(?:^|\n)\s*(?:(?:your|shopping)\s+)?(?:cart|basket|order\s+summary|your\s+order)\b")
CART_SUMMARY = re.compile(r"\b(?:subtotal|total|quantity|qty|remove\s+(?:item|from\s+(?:cart|basket)))\b", re.I)
CART_SECTION_END = re.compile(r"(?im)^\s*(?:menu|our\s+menu|recommended(?:\s+for\s+you)?|"
                              r"you\s+may\s+also\s+like|popular\s+(?:items|pizzas)|more\s+pizzas)\s*[:.!]?\s*$")
EMPTY_CART = re.compile(r"\b(?:cart|basket)\s*(?:is\s+)?(?:empty|has\s+(?:no|0)\s+items?)\b|"
                        r"\b(?:0|no)\s+items?\b|\b(?:cart|basket)\s*[(:]?\s*0\b", re.I)
REDACTED_TEXT = re.compile(r"\[\d+ characters\]\Z")


def _text(value, label, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{label} must contain {'1 to ' if required else 'at most '}{maximum} characters")
    # Keep the actual request and summary, including punctuation/spacing, intact.
    return value


def validate_checkpoint(payload):
    if not isinstance(payload, dict):
        raise ValueError("A task checkpoint must be an object")
    allowed = {"objective", "summary", "status", "questions", "evidence_action_ids", "merchant", "items"}
    if set(payload) - allowed:
        raise ValueError("Unknown task checkpoint fields")
    status = payload.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("Invalid task checkpoint status")
    result = {"objective": _text(payload.get("objective"), "Objective", 300, required=True),
              "summary": _text(payload.get("summary"), "Summary", 1200, required=True), "status": status}
    if status == "awaiting_details":
        questions = payload.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
            raise ValueError("Awaiting details needs 1 to 3 specific questions")
        result["questions"] = [_text(q, "Question", 240, required=True) for q in questions]
    elif "questions" in payload:
        raise ValueError("Only awaiting_details may contain questions")
    if "merchant" in payload:
        result["merchant"] = _text(payload["merchant"], "Merchant", 120)
    if "items" in payload:
        items = payload["items"]
        if not isinstance(items, list) or len(items) > 12:
            raise ValueError("Use at most 12 requested items")
        result["items"] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"name", "quantity"}:
                raise ValueError("Each item needs only a name and quantity")
            quantity = item["quantity"]
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 20:
                raise ValueError("Item quantity must be an integer from 1 to 20")
            result["items"].append({"name": _text(item["name"], "Item name", 100, required=True),
                                    "quantity": quantity})
    if "evidence_action_ids" in payload:
        ids = payload["evidence_action_ids"]
        if (not isinstance(ids, list) or len(ids) > 3 or
                any(not isinstance(i, str) or not IDENTIFIER.fullmatch(i) for i in ids) or
                len(set(ids)) != len(ids)):
            raise ValueError("Use at most 3 unique action evidence identifiers")
        result["evidence_action_ids"] = list(ids)
    if status == "ready_for_checkout":
        if not result.get("merchant", "").strip() or not result.get("items"):
            raise ValueError("Checkout readiness needs the merchant and requested items")
        if not result.get("evidence_action_ids"):
            raise ValueError("Checkout readiness needs current cart observation evidence")
    return result


def _tokens(text):
    return set(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def _observed_text(observation):
    """Use UI content, never a tool note, navigation argument or model summary."""
    parts = []
    text = observation.get("text")
    if isinstance(text, str) and text.strip() and not REDACTED_TEXT.fullmatch(text.strip()):
        parts.append(text[:30000])
    elements = observation.get("elements")
    if isinstance(elements, list):
        for element in elements[:300]:
            if isinstance(element, dict):
                name = element.get("name")
                if isinstance(name, str) and name.strip():
                    parts.append(name[:1000])
    # Titles can identify a cart view, but cannot prove a product by themselves.
    title = observation.get("title")
    if not isinstance(title, str):
        window = observation.get("window")
        title = window.get("title") if isinstance(window, dict) else None
    if isinstance(title, str) and CART.search(title):
        parts.insert(0, title[:1000])
    return "\n".join(parts)


def _cart_sections(observation):
    """Keep cart contents separate from navigation and menu/recommendation text.

    A link labelled Cart is a navigation affordance, not evidence that the
    following menu products are in it. A real cart page title, a cart heading or
    an explicit cart summary is needed before product names can count.
    """
    title = observation.get("title")
    if not isinstance(title, str):
        window = observation.get("window")
        title = window.get("title") if isinstance(window, dict) else None
    cart_view = isinstance(title, str) and bool(CART.search(title))
    sources = []
    text = observation.get("text")
    if isinstance(text, str) and not REDACTED_TEXT.fullmatch(text.strip()):
        sources.append((text[:30000], False))
    elements = observation.get("elements")
    if isinstance(elements, list):
        names, headings, navigation = [], set(), set()
        for element in elements[:300]:
            if not isinstance(element, dict) or not isinstance(element.get("name"), str):
                continue
            name = element["name"][:1000]
            role = str(element.get("role", element.get("type", ""))).lower()
            if CART.search(name):
                if role in {"heading", "header"}:
                    headings.add(len(names))
                elif role in {"link", "hyperlink", "button", "menuitem"}:
                    navigation.add(len(names))
            names.append(name)
        for index, name in enumerate(names):
            if CART.search(name) and index not in navigation:
                sources.append(("\n".join(names[index:]), index in headings))
        if cart_view:
            sources.append(("\n".join(names), True))
    sections = []
    for source, heading in sources:
        markers = list(CART.finditer(source))
        if not markers and cart_view:
            markers = [None]
        for index, marker in enumerate(markers):
            start = marker.start() if marker else 0
            end = markers[index + 1].start() if index + 1 < len(markers) else len(source)
            section = source[start:end]
            boundary = CART_SECTION_END.search(section)
            if boundary:
                section = section[:boundary.start()]
            marker_text = marker.group(0).strip().lower() if marker else ""
            explicit = (heading or cart_view or marker_text.startswith(("your ", "shopping ", "order summary")))
            # A generic Cart label only counts when it is a heading or the
            # actual page title; generic navigation buttons remain ambiguous.
            if explicit and (cart_view or heading or CART_SUMMARY.search(section)):
                sections.append(section)
    return sections


class Checkpoints:
    def __init__(self, store, image_resolver=None):
        self.store, self.image_resolver = store, image_resolver
        store.execute("""CREATE TABLE IF NOT EXISTS conversation_checkpoints (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL UNIQUE,
            workflow_id TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY(workflow_id) REFERENCES workflows(id) ON DELETE CASCADE)""")

    def _image(self, observation):
        image_id = observation.get("image_id")
        if not isinstance(image_id, str) or not IDENTIFIER.fullmatch(image_id) or not self.image_resolver:
            return None
        try:
            path = self.image_resolver(image_id)
            if isinstance(path, (str, Path)) and Path(path).is_file():
                return image_id
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _checkout_evidence(self, conversation_id, workflow_id, data):
        summaries, observations = [], []
        for action_id in data["evidence_action_ids"]:
            action = self.store.row("SELECT *,rowid AS observation_seq FROM action_log WHERE id=?", (action_id,))
            if (not action or action["conversation_id"] != conversation_id or
                    action["workflow_id"] != workflow_id or action["status"] != "completed" or
                    action["name"] not in OBSERVATION_TOOLS):
                raise ValueError("Checkout evidence must be a completed observation from this request")
            try:
                result = json.loads(action["result"])
            except (TypeError, ValueError):
                raise ValueError("Checkout evidence has no valid UI observation") from None
            if not isinstance(result, dict) or any(result.get(k) for k in ("error", "observation_error", "declined", "cancelled")) or result.get("verified") is False:
                raise ValueError("Failed or declined actions cannot verify a cart")
            observation = result.get("observation", result)
            if not isinstance(observation, dict) or any(observation.get(k) for k in ("error", "observation_error", "declined", "cancelled")):
                raise ValueError("Checkout evidence has no successful UI observation")
            text = _observed_text(observation)
            image_id = self._image(observation) or self._image(result)
            if not text and not image_id:
                raise ValueError("Checkout evidence needs observed controls, page text or a saved screenshot")
            marker = CART.search(text)
            observed_cart_text = text[marker.start():] if marker else ""
            if observed_cart_text and EMPTY_CART.search(observed_cart_text):
                raise ValueError("The observed cart is empty; it is not ready for checkout")
            sections = _cart_sections(observation)
            complete = any(all(_tokens(item["name"]) <= _tokens(section) for item in data["items"])
                           for section in sections)
            observations.append({"sequence": action["observation_seq"], "complete": complete, "image_id": image_id})
            summary = {"action_id": action_id, "tool": action["name"], "observed_cart": bool(sections),
                       "matched_items": [item["name"] for item in data["items"]
                                         if any(_tokens(item["name"]) <= _tokens(section) for section in sections)]}
            if image_id:
                summary["image_id"] = image_id
            summaries.append(summary)
        # Never combine products seen in separate carts or let an older complete
        # cart override a newer incomplete observation. Caller order is untrusted.
        current = max(observations, key=lambda item: item["sequence"])
        text_proves_items = current["complete"]
        if not text_proves_items and not current["image_id"]:
            raise ValueError("Observe the current cart containing every requested item before reporting checkout readiness")
        return {"actions": summaries,
                "basis": "observed_cart_text" if text_proves_items else "saved_screenshot",
                "note": ("Current UI text includes the requested item names; verify quantities and choices against the UI. No order was submitted." if text_proves_items else
                         "Screenshot exists for this request; the model must visually verify its cart contents, quantities and choices. No order was submitted.")}

    def save(self, conversation_id, workflow_id, payload):
        data = validate_checkpoint(payload)
        with self.store.lock:
            workflow = self.store.row("SELECT conversation_id FROM workflows WHERE id=?", (workflow_id,))
            if not workflow or workflow["conversation_id"] != conversation_id:
                raise ValueError("A task checkpoint must belong to this conversation's active request")
            if data["status"] == "ready_for_checkout":
                data["evidence_summary"] = self._checkout_evidence(conversation_id, workflow_id, data)
            previous = self.store.row("SELECT * FROM conversation_checkpoints WHERE conversation_id=?", (conversation_id,))
            timestamp = now()
            item = {"id": previous["id"] if previous else identifier(), "conversation_id": conversation_id,
                    "workflow_id": workflow_id, "payload": json.dumps(data, ensure_ascii=False, allow_nan=False),
                    "created_at": previous["created_at"] if previous else timestamp, "updated_at": timestamp}
            self.store.execute("""INSERT INTO conversation_checkpoints
                (id,conversation_id,workflow_id,payload,created_at,updated_at)
                VALUES(:id,:conversation_id,:workflow_id,:payload,:created_at,:updated_at)
                ON CONFLICT(conversation_id) DO UPDATE SET workflow_id=excluded.workflow_id,
                payload=excluded.payload,updated_at=excluded.updated_at""", item)
        return self.latest(conversation_id)

    def latest(self, conversation_id):
        row = self.store.row("SELECT * FROM conversation_checkpoints WHERE conversation_id=?", (conversation_id,))
        if not row:
            return None
        return {key: value for key, value in row.items() if key != "payload"} | json.loads(row["payload"])

    def clear(self, conversation_id):
        self.store.execute("DELETE FROM conversation_checkpoints WHERE conversation_id=?", (conversation_id,))
        return {"cleared": True}

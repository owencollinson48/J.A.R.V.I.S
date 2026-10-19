"""Request continuation is durable; checkout handoff requires observed cart state."""

import copy
import json

import pytest
from PIL import Image

from jarvis.checkpoints import Checkpoints
from jarvis.execution import Decision, RunContext
from jarvis.store import Store, identifier


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "checkpoints.db")
    conversation = store.conversation("Order a pizza")["id"]
    context = RunContext(store, conversation, "Order me a pepperoni pizza")
    checkpoints = Checkpoints(store)
    yield checkpoints, store, conversation, context
    store.db.close()


def payload(status="working"):
    result = {"objective": "Order me a pepperoni pizza", "summary": "Opened the restaurant and reviewed its menu.",
              "status": status}
    if status == "awaiting_details":
        result["questions"] = ["Delivery or pickup, sir?", "What address or pickup suburb should I use?"]
    if status == "ready_for_checkout":
        result.update(merchant="Domino's", items=[{"name": "Pepperoni pizza", "quantity": 1}])
    return result


def action(context, name="browser_action", result=None, args=None, status=None):
    action_id = context.log(name, args or {"action": "read"}, Decision())
    context.logged_result(action_id, result or {"observation": {"elements": [
        {"name": "Your cart", "role": "heading"}, {"name": "Pepperoni pizza", "role": "text"},
        {"name": "Checkout", "role": "button"}]}}, status=status)
    return action_id


def ready(context, **overrides):
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [action(context)]
    given.update(overrides)
    return given


def test_missing_details_resume_after_reload_with_original_request_and_summary(state):
    checkpoints, store, conversation, context = state
    given = payload("awaiting_details")
    given["objective"] = "  Order my usual pepperoni pizza — pickup if delivery is unavailable.  "
    given["summary"] = "Opened Domino's.\nNeed the suburb; no address was guessed.  "
    before = copy.deepcopy(given)
    saved = checkpoints.save(conversation, context.id, given)
    assert saved["objective"] == given["objective"]
    assert saved["summary"] == given["summary"]
    assert saved["questions"] == given["questions"]
    assert saved["workflow_id"] == context.id
    assert given == before
    assert Checkpoints(store).latest(conversation) == saved
    assert store.rows("SELECT * FROM action_log") == []


def test_followup_replaces_state_without_changing_identity_or_other_conversation(state):
    checkpoints, store, conversation, context = state
    saved = checkpoints.save(conversation, context.id, payload("awaiting_details"))
    other = store.conversation("Another task")["id"]
    other_context = RunContext(store, other, "Work on notes")
    second = checkpoints.save(other, other_context.id, payload())
    followup = RunContext(store, conversation, "Pickup in Sydney")
    updated = checkpoints.save(conversation, followup.id, payload())
    assert updated["id"] == saved["id"]
    assert updated["created_at"] == saved["created_at"]
    assert updated["workflow_id"] == followup.id
    assert "questions" not in updated
    assert checkpoints.latest(other) == second


@pytest.mark.parametrize("field,value", [
    ("objective", ""), ("objective", "  "), ("objective", "a" * 301), ("objective", 3),
    ("summary", ""), ("summary", "a" * 1201), ("summary", []),
    ("status", "ready"), ("status", []), ("unexpected", "value"),
    ("merchant", "a" * 121), ("merchant", {}),
    ("items", [{}] * 13), ("items", "pizza"),
    ("items", [{"name": "pizza", "quantity": True}]),
    ("items", [{"name": "pizza", "quantity": 0}]),
    ("items", [{"name": "pizza", "quantity": 21}]),
    ("items", [{"name": "pizza", "quantity": 1.2}]),
    ("items", [{"name": "a" * 101, "quantity": 1}]),
    ("items", [{"name": "", "quantity": 1}]),
    ("items", [{"name": "pizza", "quantity": 1, "price": 5}]),
    ("items", [{"name": "pizza"}]),
    ("evidence_action_ids", ["not-an-action"]),
    ("evidence_action_ids", [identifier()] * 2),
    ("evidence_action_ids", [identifier() for _ in range(4)]),
])
def test_strict_bounds_keep_previous_checkpoint_on_invalid_input(state, field, value):
    checkpoints, _, conversation, context = state
    previous = checkpoints.save(conversation, context.id, payload("awaiting_details"))
    given = payload()
    given[field] = value
    with pytest.raises(ValueError):
        checkpoints.save(conversation, context.id, given)
    assert checkpoints.latest(conversation) == previous


@pytest.mark.parametrize("questions", [None, [], [""], ["a" * 241], ["Q"] * 4, "Where?"])
def test_awaiting_details_requires_bounded_specific_questions(state, questions):
    checkpoints, _, conversation, context = state
    given = payload("awaiting_details")
    given["questions"] = questions
    with pytest.raises(ValueError):
        checkpoints.save(conversation, context.id, given)
    assert checkpoints.latest(conversation) is None


@pytest.mark.parametrize("status", ["working", "ready_for_checkout", "completed", "cancelled"])
def test_questions_cannot_be_saved_under_another_status(state, status):
    checkpoints, _, conversation, context = state
    given = payload(status)
    given["questions"] = ["Delivery or pickup?"]
    with pytest.raises(ValueError, match="questions"):
        checkpoints.save(conversation, context.id, given)


@pytest.mark.parametrize("missing", ["merchant", "items", "evidence_action_ids"])
def test_checkout_needs_merchant_items_and_evidence(state, missing):
    checkpoints, _, conversation, context = state
    given = ready(context)
    given.pop(missing)
    with pytest.raises(ValueError):
        checkpoints.save(conversation, context.id, given)
    assert checkpoints.latest(conversation) is None


def test_successful_current_cart_controls_prove_items_without_storing_page_text(state):
    checkpoints, store, conversation, context = state
    given = ready(context)
    checkpoint = checkpoints.save(conversation, context.id, given)
    assert checkpoint["status"] == "ready_for_checkout"
    assert checkpoint["evidence_summary"]["basis"] == "observed_cart_text"
    assert checkpoint["evidence_summary"]["actions"] == [{
        "action_id": given["evidence_action_ids"][0], "tool": "browser_action",
        "observed_cart": True, "matched_items": ["Pepperoni pizza"]}]
    assert "Checkout" not in json.dumps(checkpoint["evidence_summary"])
    assert len(store.rows("SELECT * FROM action_log")) == 1
    assert store.rows("SELECT * FROM approvals") == []


def test_real_current_text_cart_accepts_all_expected_products(state):
    checkpoints, store, conversation, context = state
    action_id = action(context)
    # Some adapters retain bounded text rather than element names. Both use the
    # same provenance check; neither a model summary nor a navigation URL counts.
    store.execute("UPDATE action_log SET result=? WHERE id=?", (json.dumps({"observation": {
        "text": "Your basket\n1 × Large Pepperoni Pizza\n1 × Garlic Bread\nTotal $19.00\nCheckout"}}), action_id))
    given = payload("ready_for_checkout")
    given.update(evidence_action_ids=[action_id], items=[{"name": "Large pepperoni pizza", "quantity": 1},
                                                       {"name": "Garlic bread", "quantity": 1}])
    saved = checkpoints.save(conversation, context.id, given)
    assert saved["evidence_summary"]["actions"][0]["matched_items"] == ["Large pepperoni pizza", "Garlic bread"]


@pytest.mark.parametrize("result", [
    {"performed": "navigate", "url": "https://www.dominos.com.au/"},
    {"observation": {"title": "Domino's", "elements": [{"name": "Pepperoni pizza"}, {"name": "Add to cart"}]}},
    {"observation": {"elements": [{"name": "Your cart"}, {"name": "Margherita pizza"}]}},
    {"observation": {"elements": [{"name": "Your cart"}, {"name": "0 items"}, {"name": "Pepperoni pizza"}]}},
    {"observation": {"elements": [{"name": "Your cart is empty"}, {"name": "Pepperoni pizza"}]}},
    {"summary": "Pepperoni pizza is in the cart", "note": "Your cart: Pepperoni pizza"},
    {"observation": {"text": "[13000 characters]"}},
    {"image_id": "a" * 32},
    {"error": "Unresponsive app", "observation": {"elements": [{"name": "Your cart\nPepperoni pizza"}]}},
    {"observation_error": "Capture failed", "observation": {"elements": [{"name": "Your cart\nPepperoni pizza"}]}},
    {"verified": False, "observation": {"elements": [{"name": "Your cart\nPepperoni pizza"}]}},
    {"declined": True, "observation": {"elements": [{"name": "Your cart\nPepperoni pizza"}]}},
])
def test_menu_navigation_wrong_item_empty_or_failed_observation_cannot_claim_ready(state, result):
    checkpoints, _, conversation, context = state
    previous = checkpoints.save(conversation, context.id, payload("awaiting_details"))
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [action(context, result=result)]
    with pytest.raises(ValueError):
        checkpoints.save(conversation, context.id, given)
    assert checkpoints.latest(conversation) == previous


@pytest.mark.parametrize("kind", ["different_conversation", "previous_workflow", "plan", "declined", "missing"])
def test_unrelated_or_unperformed_action_never_proves_current_cart(state, kind):
    checkpoints, store, conversation, context = state
    if kind == "different_conversation":
        other = store.conversation("Different user request")["id"]
        evidence_context = RunContext(store, other, "Inspect another cart")
    elif kind == "previous_workflow":
        evidence_context = RunContext(store, conversation, "Old pizza order")
    else:
        evidence_context = context
    evidence_id = action(evidence_context, name="plan_workflow" if kind == "plan" else "browser_action",
                         status="declined" if kind == "declined" else None)
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [identifier() if kind == "missing" else evidence_id]
    with pytest.raises(ValueError, match="completed observation"):
        checkpoints.save(conversation, context.id, given)


def test_existing_screenshot_is_structural_evidence_for_models_visual_check(state, tmp_path):
    checkpoints, store, conversation, context = state
    image_id = identifier()
    screenshot = tmp_path / f"{image_id}.jpg"
    Image.new("RGB", (100, 100), "blue").save(screenshot)
    checkpoints.image_resolver = lambda identity: screenshot if identity == image_id else None
    evidence_id = action(context, "desktop_screenshot", {"image_id": image_id, "width": 100, "height": 100,
                                                        "window": {"title": "Domino's — Chrome"}}, {"window_id": 42})
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [evidence_id]
    saved = checkpoints.save(conversation, context.id, given)
    assert saved["evidence_summary"]["basis"] == "saved_screenshot"
    assert saved["evidence_summary"]["actions"][0]["image_id"] == image_id
    assert "model must visually verify" in saved["evidence_summary"]["note"]
    screenshot.unlink()
    with pytest.raises(ValueError):
        checkpoints.save(conversation, context.id, given)
    assert checkpoints.latest(conversation) == saved


@pytest.mark.parametrize("elements", [
    [{"name": "Domino's navigation"}, {"name": "Pizza"}],
    [{"name": "Your cart"}, {"name": "Garlic bread"}],
])
def test_incomplete_accessibility_can_use_real_image_for_models_visual_assessment(state, tmp_path, elements):
    checkpoints, _, conversation, context = state
    image_id = identifier()
    screenshot = tmp_path / f"{image_id}.jpg"
    Image.new("RGB", (20, 20)).save(screenshot)
    checkpoints.image_resolver = lambda _: screenshot
    evidence_id = action(context, result={"observation": {"image_id": image_id, "elements": elements}})
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [evidence_id]
    saved = checkpoints.save(conversation, context.id, given)
    assert saved["evidence_summary"]["basis"] == "saved_screenshot"
    assert "model must visually verify" in saved["evidence_summary"]["note"]


def test_real_image_cannot_override_explicit_empty_cart_observation(state, tmp_path):
    checkpoints, _, conversation, context = state
    image_id = identifier()
    screenshot = tmp_path / f"{image_id}.jpg"
    Image.new("RGB", (20, 20)).save(screenshot)
    checkpoints.image_resolver = lambda _: screenshot
    evidence_id = action(context, result={"observation": {"image_id": image_id,
                        "elements": [{"name": "Your cart is empty"}]}})
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [evidence_id]
    with pytest.raises(ValueError, match="empty"):
        checkpoints.save(conversation, context.id, given)


def test_merchant_cart_title_can_support_observed_item_controls(state):
    checkpoints, _, conversation, context = state
    evidence_id = action(context, result={"observation": {"title": "Your cart — Domino's",
                                                        "elements": [{"name": "Pepperoni pizza"}]}})
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [evidence_id]
    assert checkpoints.save(conversation, context.id, given)["status"] == "ready_for_checkout"


@pytest.mark.parametrize("elements", [
    [{"name": "Cart", "role": "link"}, {"name": "Pepperoni pizza", "role": "button"},
     {"name": "Add to cart", "role": "button"}],
    [{"name": "Cart", "role": "button"}, {"name": "Pepperoni pizza", "role": "button"},
     {"name": "Checkout", "role": "link"}],
    [{"name": "Your cart", "role": "link"}, {"name": "Pepperoni pizza", "role": "button"},
     {"name": "Checkout", "role": "link"}],
    [{"name": "Your cart", "role": "button"}, {"name": "Pepperoni pizza quantity 1", "role": "button"},
     {"name": "Checkout", "role": "link"}],
    [{"name": "Your cart", "role": "link"}, {"name": "Menu", "role": "heading"},
     {"name": "Pepperoni pizza", "role": "button"}, {"name": "Checkout", "role": "link"}],
])
def test_cart_navigation_does_not_turn_menu_product_into_cart_evidence(state, elements):
    checkpoints, _, conversation, context = state
    evidence_id = action(context, result={"observation": {"title": "Domino's menu", "elements": elements}})
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [evidence_id]
    with pytest.raises(ValueError, match="current cart containing every requested item"):
        checkpoints.save(conversation, context.id, given)


@pytest.mark.parametrize("reverse_ids", [False, True])
def test_different_observed_carts_cannot_collectively_prove_requested_items(state, reverse_ids):
    checkpoints, _, conversation, context = state
    first = action(context, result={"observation": {"title": "Your cart — Shop", "elements": [
        {"name": "Pepperoni pizza quantity 1"}, {"name": "Checkout"}]}})
    second = action(context, result={"observation": {"title": "Your cart — Shop", "elements": [
        {"name": "Garlic bread quantity 1"}, {"name": "Checkout"}]}})
    given = payload("ready_for_checkout")
    given["items"].append({"name": "Garlic bread", "quantity": 1})
    given["evidence_action_ids"] = [second, first] if reverse_ids else [first, second]
    with pytest.raises(ValueError, match="every requested item"):
        checkpoints.save(conversation, context.id, given)


def test_newer_incomplete_cart_cannot_be_overridden_by_older_complete_cart(state):
    checkpoints, _, conversation, context = state
    older = action(context)
    newer = action(context, result={"observation": {"title": "Your cart — Shop", "elements": [
        {"name": "Garlic bread quantity 1"}, {"name": "Checkout"}]}})
    given = payload("ready_for_checkout")
    given["evidence_action_ids"] = [newer, older]
    with pytest.raises(ValueError, match="every requested item"):
        checkpoints.save(conversation, context.id, given)


def test_distinct_cart_sections_in_single_observation_cannot_be_combined(state):
    checkpoints, _, conversation, context = state
    evidence_id = action(context, result={"observation": {"elements": [
        {"name": "Your cart", "role": "heading"}, {"name": "Pepperoni pizza"}, {"name": "Checkout"},
        {"name": "Your cart", "role": "heading"}, {"name": "Garlic bread"}, {"name": "Checkout"}]}})
    given = payload("ready_for_checkout")
    given["items"].append({"name": "Garlic bread", "quantity": 1})
    given["evidence_action_ids"] = [evidence_id]
    with pytest.raises(ValueError, match="every requested item"):
        checkpoints.save(conversation, context.id, given)


def test_wrong_workflow_cannot_modify_checkpoint_even_when_state_has_no_actions(state):
    checkpoints, store, conversation, context = state
    saved = checkpoints.save(conversation, context.id, payload())
    other = store.conversation("Other")["id"]
    other_context = RunContext(store, other, "Other task")
    for workflow_id in (identifier(), other_context.id):
        with pytest.raises(ValueError, match="this conversation"):
            checkpoints.save(conversation, workflow_id, payload("completed"))
        assert checkpoints.latest(conversation) == saved


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_terminal_states_are_retained_and_never_perform_purchase(state, status):
    checkpoints, store, conversation, context = state
    checkpoints.save(conversation, context.id, payload("awaiting_details"))
    saved = checkpoints.save(conversation, context.id, payload(status))
    assert saved["status"] == status
    assert checkpoints.latest(conversation) == saved
    assert store.rows("SELECT * FROM action_log") == []
    assert store.rows("SELECT * FROM approvals") == []


def test_clear_only_removes_state_and_conversation_deletion_cascades(state):
    checkpoints, store, conversation, context = state
    checkpoints.save(conversation, context.id, payload("awaiting_details"))
    store.message(conversation, {"role": "user", "content": "Pickup please"})
    assert checkpoints.clear(conversation) == {"cleared": True}
    assert checkpoints.latest(conversation) is None
    assert len(store.history(conversation)) == 1
    assert store.row("SELECT id FROM workflows WHERE id=?", (context.id,))
    checkpoints.save(conversation, context.id, payload("cancelled"))
    store.execute("DELETE FROM conversations WHERE id=?", (conversation,))
    assert checkpoints.latest(conversation) is None
    assert store.rows("SELECT * FROM conversation_checkpoints") == []

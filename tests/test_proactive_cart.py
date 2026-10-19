"""A local pizza shop exercises proactive work without creating a real order.

Only the model's next decision is scripted. HTTP streaming, tool/result pairs,
the chat API, Chromium forms, cart changes, and saved screenshots are real.
"""

from collections import Counter
from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
import threading
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.app import create_app


OBJECTIVE = "Prepare one pepperoni pizza at Domino's and stop before checkout"
MERCHANT = "Domino's local test shop"
ITEMS = [{"name": "Pepperoni pizza", "quantity": 1}]
ADDRESS = "10 Example Street, Sydney"


@pytest.fixture
def pizza_shop():
    state = {"fulfilment": None, "address": "", "cart": 0, "purchases": 0,
             "requests": []}

    class Shop(BaseHTTPRequestHandler):
        def page(self):
            # Real cart item controls make persisted accessibility observations
            # useful even when general page text is redacted in the activity log.
            title = "Your cart - Local test pizza shop" if state["cart"] else "Local test pizza shop"
            choice = ("<p>Choose pickup or delivery.</p>" if not state["fulfilment"] else
                      f"<p>Selected: {state['fulfilment']}</p>")
            address = (f'<form method="post" action="/address"><label>Delivery address '
                       f'<input name="address" aria-label="Delivery address" value="{escape(state["address"], quote=True)}"></label>'
                       '<button>Save delivery address</button></form>'
                       if state["fulfilment"] == "delivery" else "")
            ready = state["fulfilment"] and (state["fulfilment"] == "pickup" or state["address"])
            disabled = "" if ready else " disabled"
            cart = (f'<button disabled>Pepperoni pizza quantity {state["cart"]}</button>'
                    if state["cart"] else '<button disabled>Cart empty</button>')
            return f'''<!doctype html><html><head><title>{title}</title></head>
                <body><h1>Local Domino's test kitchen</h1>{choice}
                <form method="post" action="/delivery"><button>Delivery</button></form>
                <form method="post" action="/pickup"><button>Pickup at Sydney store</button></form>
                {address}<h2>Menu</h2><p>Pepperoni pizza</p>
                <form method="post" action="/cart"><button{disabled}>Add pepperoni pizza</button></form>
                <h2>Your cart</h2>{cart}
                <form method="post" action="/purchase"><button>Pay and place order</button></form>
                </body></html>'''.encode()

        def respond(self):
            body = self.page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["requests"].append(("GET", self.path))
            self.respond()

        def do_POST(self):
            fields = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            state["requests"].append(("POST", self.path))
            if self.path in {"/pickup", "/delivery"}:
                state["fulfilment"] = self.path[1:]
            elif self.path == "/address":
                state["address"] = fields.get("address", [""])[0]
            elif self.path == "/cart":
                assert state["fulfilment"] == "pickup" or state["address"], "The shop needs fulfilment details"
                state["cart"] += 1
            elif self.path == "/purchase":
                state["purchases"] += 1
            self.respond()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Shop)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def tool_completion(identity, name, arguments, *following):
    calls = [(identity, name, arguments), *following]
    delta = {"tool_calls": [{"index": index, "id": call_id, "type": "function",
                             "function": {"name": tool_name, "arguments": json.dumps(args)}}
                            for index, (call_id, tool_name, args) in enumerate(calls)]}
    chunk = {"choices": [{"delta": delta, "finish_reason": "tool_calls"}]}
    return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
                          headers={"content-type": "text/event-stream"})


def final_completion(text):
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}
    return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
                          headers={"content-type": "text/event-stream"})


def tool_result(messages):
    return json.loads(next(message["content"] for message in reversed(messages) if message["role"] == "tool"))


def button(result, name):
    return next(item["id"] for item in result["observation"]["elements"] if item["name"] == name)


def current_context(messages):
    return json.loads(messages[0]["content"].split("Current context (data):\n", 1)[1])


@contextmanager
def client_with_provider(tmp_path, monkeypatch, decide):
    executable = shutil.which("chromium")
    if not executable:
        pytest.skip("System Chromium is unavailable")
    monkeypatch.setenv("JARVIS_BROWSER_EXECUTABLE", executable)
    posted = []

    def transport(request):
        assert request.url.host == "model.fixture.invalid"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["tools"] and body["stream"]
        messages = body["messages"]
        calls = [call["id"] for message in messages for call in message.get("tool_calls", [])]
        results = [message["tool_call_id"] for message in messages if message["role"] == "tool"]
        assert Counter(calls) == Counter(results), "Every actual tool call needs its real result"
        posted.append(messages)
        return decide(messages, len(posted))

    original_client = httpx.AsyncClient
    monkeypatch.setattr("jarvis.provider.httpx.AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(transport)))
    app = create_app(tmp_path / "private")
    app.state.settings.update({"api_key": "synthetic-model-key", "model": "gpt-6.1-sol",
                               "provider_url": "https://model.fixture.invalid/v1",
                               "permissions_mode": "autonomous", "browser_mode": "managed",
                               "native_notifications": False})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/")
        client.headers["X-Jarvis-Client"] = "dashboard"
        client.jarvis = app
        yield client, posted


def ask(client, cid, content):
    response = client.post(f"/api/conversations/{cid}/chat", json={"content": content})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert not any(event["type"] in {"error", "approval"} for event in events), events
    return events


def message(events):
    return next(event["content"] for event in reversed(events) if event["type"] == "message")


def assert_suppressed_call(client, cid, identity, element_id):
    history = client.jarvis.state.store.model_history(cid)
    result = json.loads(next(item["content"] for item in history
                             if item.get("tool_call_id") == identity))
    assert result.get("not_executed") and "action_id" not in result
    actions = client.jarvis.state.store.rows("SELECT arguments FROM action_log WHERE conversation_id=?", (cid,))
    assert all(json.loads(action["arguments"]).get("element_id") != element_id for action in actions), \
        "A call after the terminal checkpoint must never reach preflight, the action log, or input"


@pytest.mark.parametrize("details_known", [False, True])
@pytest.mark.parametrize("terminal_batch", [False, True])
def test_requested_pizza_is_prepared_and_handed_back_without_payment(
        tmp_path, monkeypatch, pizza_shop, details_known, terminal_batch):
    shop, url = pizza_shop
    evidence = {}
    steps = ["plan", "navigate"]
    if not details_known:
        steps.append("ask_details")
    steps += (["pickup"] if details_known else ["resume_read", "delivery", "address", "save_address"])
    steps += ["add_pizza", "read_cart", "screenshot", "handoff"]

    def decide(messages, number):
        step = steps[number - 1]
        result = tool_result(messages) if number > 1 else None
        call = lambda name, args, *following: tool_completion(f"pizza-step-{number}", name, args, *following)
        if step == "plan":
            return call("plan_workflow", {"outcome": OBJECTIVE, "steps": [
                "Open the pizza shop", "Resolve required fulfilment details", "Add the requested pizza",
                "Observe the cart and hand checkout back to the user"]})
        if step == "navigate":
            return call("browser_action", {"action": "navigate", "url": url})
        if step == "ask_details":
            assert "Choose pickup or delivery" in result["observation"]["text"]
            following = []
            if terminal_batch:
                evidence["blocked_add"] = button(result, "Add pepperoni pizza")
                following = [("premature-add", "browser_action",
                              {"action": "click", "element_id": evidence["blocked_add"]})]
            return call("task_checkpoint", {"objective": OBJECTIVE,
                "summary": "Opened the pizza shop. One pepperoni pizza requested; fulfilment details are missing.",
                "status": "awaiting_details", "questions": [
                    "Would you like pickup or delivery, sir?", "If delivery, what address should I use?"]}, *following)
        if step == "resume_read":
            active = current_context(messages)["active_task"]
            assert active["objective"] == OBJECTIVE and active["status"] == "awaiting_details"
            assert ADDRESS in current_context(messages)["current_request"]
            # Cross-turn element IDs are retired; resume with a fresh page read.
            return call("browser_action", {"action": "read"})
        if step == "delivery":
            return call("browser_action", {"action": "click", "element_id": button(result, "Delivery")})
        if step == "address":
            return call("browser_action", {"action": "fill", "element_id": button(result, "Delivery address"), "text": ADDRESS})
        if step == "save_address":
            return call("browser_action", {"action": "click", "element_id": button(result, "Save delivery address")})
        if step == "pickup":
            return call("browser_action", {"action": "click", "element_id": button(result, "Pickup at Sydney store")})
        if step == "add_pizza":
            return call("browser_action", {"action": "click", "element_id": button(result, "Add pepperoni pizza")})
        if step == "read_cart":
            return call("browser_action", {"action": "read"})
        if step == "screenshot":
            assert "Pepperoni pizza quantity 1" in result["observation"]["text"]
            evidence["cart"] = result["action_id"]
            evidence["pay_target"] = button(result, "Pay and place order")
            return call("browser_action", {"action": "screenshot"})
        assert step == "handoff"
        assert result["image_id"]
        evidence["image"] = result["image_id"]
        evidence["screenshot"] = result["action_id"]
        following = ([("premature-pay", "browser_action",
                       {"action": "click", "element_id": evidence["pay_target"]})]
                     if terminal_batch else [])
        return call("task_checkpoint", {"objective": OBJECTIVE,
            "summary": "One pepperoni pizza is in the cart. Fulfilment is selected; no order has been placed.",
            "status": "ready_for_checkout", "merchant": MERCHANT, "items": ITEMS,
            "evidence_action_ids": [evidence["cart"], evidence["screenshot"]]}, *following)

    with client_with_provider(tmp_path, monkeypatch, decide) as (client, posted):
        cid = client.post("/api/conversations").json()["id"]
        text = ("Order one pepperoni pizza from Domino's for pickup at the Sydney store and tell me when it is ready to check out."
                if details_known else "I want you to order me a peperoni pizza and tell me when it is ready to check out.")
        events = ask(client, cid, text)
        if not details_known:
            assert len(posted) == 3, "A details question must stop immediately without another model completion"
            assert "pickup" in message(events).lower() and "address" in message(events).lower()
            saved = client.get(f"/api/conversations/{cid}").json()
            assert saved["workflow"]["status"] == "awaiting_details"
            assert saved["task_checkpoint"]["status"] == "awaiting_details"
            assert any(event["type"] == "checkpoint" and event["checkpoint"]["status"] == "awaiting_details"
                       for event in events)
            assert shop["cart"] == 0 and shop["purchases"] == 0
            assert len(client.get("/api/activity").json()["actions"]) == 3
            if terminal_batch:
                assert_suppressed_call(client, cid, "premature-add", evidence["blocked_add"])
            events = ask(client, cid, f"Delivery to {ADDRESS}, please.")
        assert len(posted) == len(steps), "Checkout handoff must not make another model request"
        assert shop["cart"] == 1 and shop["purchases"] == 0
        assert shop["fulfilment"] == ("pickup" if details_known else "delivery")
        assert shop["address"] == ("" if details_known else ADDRESS)
        assert ("POST", "/purchase") not in shop["requests"]
        assert shop["requests"].count(("POST", "/cart")) == 1, "Do not replay a delivered add-to-cart"
        handoff = message(events).lower()
        assert "pepperoni" in handoff and ("checkout" in handoff or "check out" in handoff)
        assert not any(word in handoff for word in ("purchased", "ordered successfully", "payment complete"))
        saved = client.get(f"/api/conversations/{cid}").json()
        assert saved["workflow"]["status"] == "ready_for_checkout"
        assert saved["task_checkpoint"]["status"] == "ready_for_checkout"
        assert saved["task_checkpoint"]["items"] == ITEMS
        assert any(event["type"] == "checkpoint" and event["checkpoint"]["status"] == "ready_for_checkout"
                   for event in events)
        assert saved["approvals"] == []
        actions = client.get("/api/activity").json()["actions"]
        assert len(actions) == len(steps), "Suppressed batch calls must not create input actions"
        assert all(action["status"] == "completed" for action in actions), actions
        assert {evidence["cart"], evidence["screenshot"]} <= {action["id"] for action in actions}
        history = client.jarvis.state.store.model_history(cid)
        called = [call["id"] for item in history for call in item.get("tool_calls", [])]
        returned = [item["tool_call_id"] for item in history if item["role"] == "tool"]
        assert Counter(called) == Counter(returned), "Terminal calls also need matching tool results"
        if terminal_batch:
            assert_suppressed_call(client, cid, "premature-pay", evidence["pay_target"])
        with Image.open(client.jarvis.state.tools.images.path(evidence["image"])) as image:
            assert image.width > 100 and image.height > 100


def test_opening_the_shop_cannot_verify_a_pizza_in_the_cart(tmp_path, monkeypatch, pizza_shop):
    shop, url = pizza_shop
    def decide(messages, number):
        if number == 1:
            return tool_completion("open-shop", "browser_action", {"action": "navigate", "url": url})
        result = tool_result(messages)
        if number == 2:
            assert "Cart empty" in result["observation"]["text"]
            return tool_completion("unverified-cart", "task_checkpoint", {"objective": OBJECTIVE,
                "summary": "The pizza is ready for checkout.", "status": "ready_for_checkout",
                "merchant": MERCHANT, "items": ITEMS, "evidence_action_ids": [result["action_id"]]})
        assert number == 3 and result.get("error"), "A menu/empty cart must reject checkout readiness"
        return final_completion("Your pepperoni pizza is ready for checkout, sir.")

    with client_with_provider(tmp_path, monkeypatch, decide) as (client, posted):
        cid = client.post("/api/conversations").json()["id"]
        events = ask(client, cid, "Prepare one pepperoni pizza and let me know when the cart is ready to check out.")
        assert len(posted) == 3
        assert shop["cart"] == shop["purchases"] == 0
        assert message(events) != "Your pepperoni pizza is ready for checkout, sir."
        saved = client.get(f"/api/conversations/{cid}").json()
        assert saved["workflow"]["status"] == "failed"
        assert not saved["task_checkpoint"] or saved["task_checkpoint"]["status"] != "ready_for_checkout"

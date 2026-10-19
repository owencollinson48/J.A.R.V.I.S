import json

import httpx


class ProviderError(Exception):
    pass


class Provider:
    def __init__(self, settings):
        self.settings = settings

    def headers(self):
        key = self.settings.api_key
        # Ollama does not need a key. Do not forward a saved cloud key locally.
        if self.settings.local_provider:
            return {}
        if not key and not self.settings.local_provider:
            raise ProviderError("Connect a model in Settings to start chatting. Your files, planner and memory already work locally.")
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def check(self):
        headers = self.headers()
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.post(self.settings.values["provider_url"] + "/chat/completions", headers=headers,
                    json={"model": self.settings.values["model"], "messages": [{"role": "user", "content": "Reply with OK."}],
                          "tools": [{"type": "function", "function": {"name": "connection_check", "description": "Connection test", "parameters": {"type": "object", "properties": {}}}}],
                          "stream": False})
                self.check_response(response)
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ProviderError("Provider returned an invalid response. Check the API base URL.") from exc
                if not data.get("choices"):
                    raise ProviderError("Provider returned no completion. Check the model name.")
            return {"ok": True, "model": self.settings.values["model"], "message": "Model connected; chat and tool schema accepted."}
        except httpx.RequestError as exc:
            raise ProviderError("Could not reach the model provider. Check its address and that the server is running.") from exc

    @staticmethod
    def check_response(response):
        if response.status_code >= 400:
            hints = {401: "The provider rejected your API key.", 403: "The provider denied access to this model.",
                     404: "Model or API endpoint not found. Check the base URL and model name.",
                     429: "The provider's rate or billing limit was reached."}
            raise ProviderError(hints.get(response.status_code, f"Model provider returned HTTP {response.status_code}. Check that it supports tool calling."))

    async def complete(self, messages, tools):
        headers = self.headers()
        body = {"model": self.settings.values["model"], "messages": messages, "tools": tools, "stream": True}
        text, calls, completed = "", {}, False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as client:
                async with client.stream("POST", self.settings.values["provider_url"] + "/chat/completions", json=body, headers=headers) as response:
                    self.check_response(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError as exc:
                            raise ProviderError("The provider sent an invalid streaming response") from exc
                        if event.get("error"):
                            raise ProviderError("The provider reported an error while generating a response")
                        if not event.get("choices"):
                            continue
                        choice = event["choices"][0]
                        delta = choice.get("delta", {})
                        if delta.get("content"):
                            text += delta["content"]
                            yield {"type": "token", "text": delta["content"]}
                        for call in delta.get("tool_calls", []):
                            index = call["index"]
                            existing = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            existing["id"] += call.get("id", "")
                            existing["function"]["name"] += call.get("function", {}).get("name", "")
                            existing["function"]["arguments"] += call.get("function", {}).get("arguments", "")
                        finish = choice.get("finish_reason")
                        if finish:
                            if finish not in {"stop", "tool_calls"}:
                                raise ProviderError(f"Model response stopped early ({finish}). Try a smaller request.")
                            completed = True
            if not completed:
                raise ProviderError("The model connection ended before its response was complete. Try again.")
            message = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = [calls[index] for index in sorted(calls)]
                if any(not c["id"] or not c["function"]["name"] for c in message["tool_calls"]):
                    raise ProviderError("The model returned an incomplete tool call")
            yield {"type": "completion", "message": message}
        except httpx.RequestError as exc:
            raise ProviderError("Lost connection to the model provider. Check your connection and try again.") from exc

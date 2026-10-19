import json
import re

import httpx


class ProviderError(Exception):
    def __init__(self, message, *, status_code=None, code=None, param=None, request_id=None):
        super().__init__(message)
        self.status_code = status_code
        self.status = status_code
        self.code = code
        self.param = param
        self.request_id = request_id


class Provider:
    ERROR_BODY_LIMIT = 16_384

    def __init__(self, settings):
        self.settings = settings

    def headers(self):
        key = self.settings.api_key
        # Ollama does not need a key. Do not forward a saved cloud key locally.
        if self.settings.local_provider:
            return {}
        if not key:
            raise ProviderError("Connect a model in Settings to start chatting. Your files, planner and memory already work locally.")
        return {"Authorization": f"Bearer {key}"}

    async def models(self):
        """Return identifiers offered to this account; never guess model access."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                async with client.stream("GET", self.settings.values["provider_url"] + "/models", headers=self.headers()) as response:
                    await self._check_response(response)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 262_144:
                            raise ProviderError("Provider model list was too large. Enter a model identifier in Settings instead.")
                        body.extend(chunk)
                    try:
                        data = json.loads(body)
                        rows = data["data"]
                        if not isinstance(rows, list):
                            raise ValueError()
                        models = sorted({row["id"] for row in rows[:1000] if isinstance(row, dict)
                                         and isinstance(row.get("id"), str)
                                         and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+@-]{0,199}", row["id"])})
                    except (ValueError, KeyError, TypeError) as exc:
                        raise ProviderError("Provider returned an invalid model list. Check its API base URL.") from exc
                    return models[:1000]
        except httpx.RequestError as exc:
            raise ProviderError("Could not reach the model provider. Check its address and that the server is running.") from exc

    async def available_models(self):
        return await self.models()

    async def check(self):
        # Exercise the same stream, schemas and follow-up protocol as a real task.
        # This tool is deliberately inert: connection checks never run local tools.
        from .tools import TOOLS
        probe = {"type": "function", "function": {
            "name": "connection_check", "description": "Inert connection test; has no computer access.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}
        tools = [*TOOLS, probe]
        messages = [{"role": "user", "content": "Connection test only. Call connection_check once with no arguments. Do not call any other tool."}]
        initial = await self._probe_completion(messages, tools, {"type": "function", "function": {"name": "connection_check"}})
        calls = initial.get("tool_calls", [])
        if len(calls) != 1 or calls[0]["function"]["name"] != "connection_check":
            raise ProviderError("This model did not return the requested tool call. Choose a model with function/tool calling in Settings.")
        try:
            arguments = json.loads(calls[0]["function"]["arguments"])
            if arguments != {}:
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise ProviderError("This model returned invalid tool arguments. Choose a model with function/tool calling in Settings.") from exc
        messages.extend([initial, {"role": "tool", "tool_call_id": calls[0]["id"], "content": '{"ok":true}'}])
        final = await self._probe_completion(messages, tools, "none")
        if final.get("tool_calls") or not final.get("content"):
            raise ProviderError("This model did not finish the tool-result continuation. Choose a model with function/tool calling in Settings.")
        return {"ok": True, "model": self.settings.values["model"],
                "message": "Connected: JARVIS tool schemas, streaming and tool-result continuation passed.",
                "capabilities": {"tools": True, "streaming": True, "tool_results": True}}

    async def _probe_completion(self, messages, tools, tool_choice):
        completion = None
        async for event in self._complete(messages, tools, tool_choice=tool_choice):
            if event["type"] == "completion":
                completion = event["message"]
        if completion is None:
            raise ProviderError("Provider returned no completion. Check the model name.")
        return completion

    @staticmethod
    def _private_values(messages):
        values = []
        def collect(value):
            if isinstance(value, str) and value:
                values.append(value)
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        for message in messages:
            content = message.get("content")
            collect(content)
            if isinstance(content, str):
                try:
                    collect(json.loads(content))
                except ValueError:
                    pass
            for call in message.get("tool_calls", []):
                arguments = call.get("function", {}).get("arguments")
                collect(arguments)
                if isinstance(arguments, str):
                    try:
                        collect(json.loads(arguments))
                    except ValueError:
                        pass
        return values

    @staticmethod
    def _safe_diagnostic(value, *, secrets=(), private_values=(), known_words=(), identifier=False):
        if not isinstance(value, str):
            return None
        # Provider errors are untrusted and sometimes echo input or credentials.
        value = value[:4096]
        for private in [*secrets, *private_values]:
            if isinstance(private, str) and private:
                for representation in (private, json.dumps(private, ensure_ascii=False)[1:-1]):
                    value = value.replace(representation, "[redacted]")
                # Also remove individual pasted lines when an error echoes one.
                for line in private.splitlines():
                    if len(line.strip()) >= 16:
                        value = value.replace(line.strip(), "[redacted]")
        value = re.sub(r"(?i)\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]+", "[redacted]", value)
        value = re.sub(r"https?://\S+|data:[^\s]+", "[redacted]", value)
        if '<' in value or '>' in value or '&lt;' in value.lower():
            return None
        value = ' '.join(value.split())
        if identifier:
            return value if re.fullmatch(r"[A-Za-z0-9_.:\[\]/-]{1,160}", value) else None
        # Quoted values may be excerpts from a document/tool result. Keep only
        # schema vocabulary; selected model and field are reported separately.
        safe_words = {'required', 'properties', 'additionalProperties', 'type', 'object', 'string',
                      'array', 'integer', 'number', 'boolean', 'null', 'function', 'tools',
                      'messages', 'content', 'tool_calls', 'tool_call_id', 'tool_choice',
                      'stream', 'model', 'parameters', 'strict', *known_words}
        def quoted(match):
            return match.group(0) if match.group(2) in safe_words else '[redacted]'
        value = re.sub(r"([\"`])(.{0,4096}?)\1|(?<!\w)(')([^']{0,4096})'", lambda match:
                       quoted(match) if match.group(1) else (match.group(0) if match.group(4) in safe_words else '[redacted]'), value)
        return value[:500] or None

    @classmethod
    def check_response(cls, response, *, body=None, secrets=(), private_values=(), known_words=()):
        """Raise a bounded, redacted error; accepts an explicitly read stream body."""
        if response.status_code < 400:
            return
        error = None
        try:
            if body is None:
                body = response.content[:cls.ERROR_BODY_LIMIT]
            payload = json.loads(body)
            if isinstance(payload, dict):
                error = payload.get('error')
        except (ValueError, httpx.ResponseNotRead):
            pass
        raise cls._error(response.status_code, error, response.headers, secrets=secrets,
                         private_values=private_values, known_words=known_words)

    @classmethod
    def _error(cls, status, error, headers, *, secrets=(), private_values=(), known_words=()):
        metadata = error if isinstance(error, dict) else {}
        sanitizer = dict(secrets=secrets, private_values=private_values, known_words=known_words)
        code = cls._safe_diagnostic(metadata.get('code') or metadata.get('type'), identifier=True, **sanitizer)
        param = cls._safe_diagnostic(metadata.get('param'), identifier=True, **sanitizer)
        request_id = cls._safe_diagnostic(headers.get('x-request-id') or headers.get('request-id'), identifier=True, **sanitizer)
        detail = cls._safe_diagnostic(metadata.get('message') if metadata else error, **sanitizer)
        hints = {401: "The provider rejected your API key.", 403: "The provider denied access to this model.",
                 404: "Model or API endpoint not found. Check the base URL and model name.",
                 429: "The provider's rate or billing limit was reached."}
        prefix = hints.get(status, f"Model provider returned HTTP {status}." if status else "The provider reported a streaming error.")
        parts = [prefix]
        if detail:
            parts.append(detail)
        diagnostics = [f"code: {code}" if code else None, f"field: {param}" if param else None,
                       f"request: {request_id}" if request_id else None]
        if any(diagnostics):
            parts.append('(' + '; '.join(item for item in diagnostics if item) + ')')
        if status == 400 or code in {'model_not_found', 'unsupported_model', 'invalid_model'}:
            parts.append("In Settings, check the model and API base URL, then run Test connection.")
        return ProviderError(' '.join(parts), status_code=status, code=code, param=param, request_id=request_id)

    async def _check_response(self, response, messages=(), tools=()):
        if response.status_code < 400:
            return
        body = bytearray()
        if response.is_stream_consumed:
            body.extend(response.content[:self.ERROR_BODY_LIMIT])
        else:
            async for chunk in response.aiter_bytes():
                body.extend(chunk[:self.ERROR_BODY_LIMIT - len(body)])
                if len(body) >= self.ERROR_BODY_LIMIT:
                    break
        known_words = {tool.get('function', {}).get('name', '') for tool in tools}
        for tool in tools:
            known_words.update(tool.get('function', {}).get('parameters', {}).get('properties', {}))
        self.check_response(response, body=bytes(body), secrets=(self.settings.api_key,),
                            private_values=self._private_values(messages), known_words=known_words)

    async def complete(self, messages, tools):
        async for event in self._complete(messages, tools):
            yield event

    async def _complete(self, messages, tools, *, tool_choice=None):
        headers = self.headers()
        body = {"model": self.settings.values["model"], "messages": messages, "stream": True}
        # An empty tools list is rejected by some otherwise compatible servers.
        if tools:
            body['tools'] = tools
        if tool_choice is not None:
            body['tool_choice'] = tool_choice
        text, calls, completed = "", {}, False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=False) as client:
                async with client.stream("POST", self.settings.values["provider_url"] + "/chat/completions", json=body, headers=headers) as response:
                    await self._check_response(response, messages, tools)
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
                        if not isinstance(event, dict):
                            raise ProviderError("The provider sent an invalid streaming response")
                        if event.get("error"):
                            raise self._error(None, event['error'], response.headers, secrets=(self.settings.api_key,),
                                              private_values=self._private_values(messages))
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

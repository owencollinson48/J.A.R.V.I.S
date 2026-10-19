import json

import httpx
import pytest

from jarvis.config import Settings
from jarvis.provider import Provider, ProviderError
from jarvis.tools import TOOLS


HTTP_CLIENT = httpx.AsyncClient


def mock_service(monkeypatch, handler):
    monkeypatch.setattr('jarvis.provider.httpx.AsyncClient', lambda **kwargs:
                        HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))


def settings_for(tmp_path):
    settings = Settings(tmp_path)
    settings.update({'api_key': 'sk-private-test-account-key', 'model': 'account-model'})
    return settings


def stream(*events, headers=None):
    text = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
    return httpx.Response(200, text=text, headers={'content-type': 'text/event-stream', **(headers or {})})


def tool_completion(name='connection_check', call_id='probe-1', arguments='{}'):
    return {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': call_id,
             'function': {'name': name, 'arguments': arguments}}]}, 'finish_reason': 'tool_calls'}]}


def text_completion(text='Connected.'):
    return {'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}]}


@pytest.mark.asyncio
async def test_check_exercises_actual_schemas_stream_and_tool_result(monkeypatch, tmp_path):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == '/v1/chat/completions'
        assert body['stream'] is True
        assert body['tools'][:-1] == TOOLS
        if len(requests) == 1:
            assert body['tool_choice'] == {'type': 'function', 'function': {'name': 'connection_check'}}
            return stream(tool_completion())
        assert len(requests) == 2
        assert body['tool_choice'] == 'none'
        assistant, result = body['messages'][-2:]
        assert assistant['role'] == 'assistant' and assistant['content'] is None
        assert assistant['tool_calls'][0]['id'] == result['tool_call_id'] == 'probe-1'
        assert result == {'role': 'tool', 'tool_call_id': 'probe-1', 'content': '{"ok":true}'}
        return stream(text_completion())
    mock_service(monkeypatch, handler)
    result = await Provider(settings_for(tmp_path)).check()
    assert result['ok'] and result['capabilities']['tool_results']
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_check_reports_failure_after_tool_result_without_retry(monkeypatch, tmp_path):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return stream(tool_completion())
        assert body['messages'][-1]['role'] == 'tool'
        return httpx.Response(400, json={'error': {
            'message': "Invalid parameter: messages[2].tool_call_id does not match a previous tool call.",
            'code': 'invalid_tool_result', 'param': 'messages[2].tool_call_id'}}, headers={'x-request-id': 'req-followup'})
    mock_service(monkeypatch, handler)
    with pytest.raises(ProviderError) as failure:
        await Provider(settings_for(tmp_path)).check()
    assert failure.value.status == 400
    assert failure.value.code == 'invalid_tool_result'
    assert failure.value.param == 'messages[2].tool_call_id'
    assert 'does not match' in str(failure.value)
    assert 'req-followup' in str(failure.value)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_http_400_body_is_read_from_stream_and_actionable(monkeypatch, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={'error': {
            'message': "Invalid schema for function 'read_file': 'required' is missing.",
            'type': 'invalid_request_error', 'code': 'invalid_function_parameters',
            'param': 'tools[2].function.parameters'}}, headers={'x-request-id': 'req-schema'})
    mock_service(monkeypatch, handler)
    with pytest.raises(ProviderError) as failure:
        [event async for event in Provider(settings_for(tmp_path)).complete([{'role':'user','content':'Read my document'}], TOOLS)]
    error = failure.value
    assert error.status_code == 400 and error.code == 'invalid_function_parameters'
    assert error.param == 'tools[2].function.parameters'
    assert "'read_file'" in str(error) and "'required' is missing" in str(error)
    assert 'Test connection' in str(error)
    assert len(requests) == 1  # A protocol failure must never replay computer work.


@pytest.mark.asyncio
async def test_errors_redact_key_message_and_tool_contents(monkeypatch, tmp_path):
    settings = settings_for(tmp_path)
    user_text = 'Please open the PRIVATE ACCOUNT LEDGER today'
    tool_content = 'PRIVATE LEDGER CONTENT WITH CUSTOMER DETAILS'
    messages = [{'role':'user','content':user_text}, {'role':'tool','tool_call_id':'done', 'content':tool_content}]
    provider_text = f"Invalid request: {user_text}; result: {tool_content}; key Bearer {settings.api_key}; excerpt `SECRET EXCERPT`."
    mock_service(monkeypatch, lambda request: httpx.Response(400, json={'error': {
        'message': provider_text, 'code':'invalid_request_error', 'param':'messages[1].content'}}))
    with pytest.raises(ProviderError) as failure:
        [event async for event in Provider(settings).complete(messages, TOOLS)]
    text = str(failure.value)
    assert settings.api_key not in text and user_text not in text and tool_content not in text
    assert 'SECRET EXCERPT' not in text
    assert 'messages[1].content' in text
    assert 'PRIVATE ACCOUNT' not in settings.path.read_text()


@pytest.mark.asyncio
async def test_stream_error_preserves_safe_diagnostics_and_does_not_retry(monkeypatch, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return stream({'choices':[{'delta':{'content':'Starting '}}]},
                      {'error': {'message':'Tool results are not accepted by this model.', 'code':'unsupported_value', 'param':'messages[2].role'}},
                      headers={'x-request-id':'req-stream'})
    mock_service(monkeypatch, handler)
    events = []
    with pytest.raises(ProviderError, match='Tool results are not accepted') as failure:
        async for event in Provider(settings_for(tmp_path)).complete([], TOOLS):
            events.append(event)
    assert events == [{'type':'token','text':'Starting '}]
    assert failure.value.param == 'messages[2].role'
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_html_and_oversized_error_bodies_are_not_reported(monkeypatch, tmp_path):
    body = b'<html><body>PRIVATE LOGIN PAGE</body></html>' + b'x' * 40000
    chunks_read = []
    class ChunkedError(httpx.AsyncByteStream):
        async def __aiter__(self):
            for start in range(0, len(body), 1024):
                chunks_read.append(start)
                yield body[start:start+1024]
    mock_service(monkeypatch, lambda request: httpx.Response(400, stream=ChunkedError(), headers={'content-type':'text/html'}))
    with pytest.raises(ProviderError) as failure:
        [event async for event in Provider(settings_for(tmp_path)).complete([], [])]
    assert 'PRIVATE LOGIN' not in str(failure.value) and '<html>' not in str(failure.value)
    assert 'HTTP 400' in str(failure.value)
    assert len(chunks_read) <= 16


@pytest.mark.asyncio
async def test_model_list_uses_account_auth_and_validates_identifiers(monkeypatch, tmp_path):
    settings = settings_for(tmp_path)
    def handler(request):
        assert request.method == 'GET' and request.url.path == '/v1/models'
        assert request.headers['authorization'] == f'Bearer {settings.api_key}'
        return httpx.Response(200, json={'data':[{'id':'gpt-account-model'}, {'id':'gpt-account-model'},
            {'id':'provider/model:latest'}, {'id':'<script>bad</script>'}, {'id':None}, 'bad']})
    mock_service(monkeypatch, handler)
    assert await Provider(settings).models() == ['gpt-account-model','provider/model:latest']


@pytest.mark.asyncio
async def test_model_list_never_forwards_cloud_key_to_local_provider(monkeypatch, tmp_path):
    settings = settings_for(tmp_path)
    settings.update({'provider_url':'http://localhost:11434/v1'})
    def handler(request):
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'data':[{'id':'local-model'}]})
    mock_service(monkeypatch, handler)
    assert await Provider(settings).models() == ['local-model']


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [{}, {'data':'not a list'}, {'data':None}, ['wrong envelope']])
async def test_invalid_model_list_fails_clearly(monkeypatch, tmp_path, payload):
    mock_service(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError, match='invalid model list'):
        await Provider(settings_for(tmp_path)).models()


@pytest.mark.asyncio
async def test_model_list_is_bounded(monkeypatch, tmp_path):
    mock_service(monkeypatch, lambda request: httpx.Response(200, content=b'x' * 262145))
    with pytest.raises(ProviderError, match='too large'):
        await Provider(settings_for(tmp_path)).models()


@pytest.mark.asyncio
async def test_check_does_not_execute_an_unexpected_tool(monkeypatch, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return stream(tool_completion('launch_application', arguments='{"name":"Outlook"}'))
    mock_service(monkeypatch, handler)
    with pytest.raises(ProviderError, match='did not return the requested tool call'):
        await Provider(settings_for(tmp_path)).check()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_empty_tools_omitted_for_compatible_chat_provider(monkeypatch, tmp_path):
    def handler(request):
        assert 'tools' not in json.loads(request.content)
        return stream(text_completion())
    mock_service(monkeypatch, handler)
    events = [event async for event in Provider(settings_for(tmp_path)).complete([], [])]
    assert events[-1]['message']['content'] == 'Connected.'

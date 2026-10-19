"""Explicit GPT-6.1 Sol selection verifies the model without changing providers."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.connection import use_sol
from jarvis.provider import ProviderError
from test_connection_repair import completion, install_transport


def test_use_sol_checks_both_tool_rounds_before_saving_the_exact_model(tmp_path, monkeypatch):
    requests = []
    app = create_app(tmp_path/'private')
    app.state.settings.update({'api_key':'synthetic-sol-key','model':'gpt-4.1','native_notifications':False})
    def handler(request):
        requests.append(request)
        assert request.method == 'POST' and request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic-sol-key'
        assert app.state.settings.values['model'] == 'gpt-4.1', 'save only after the full check'
        body = json.loads(request.content)
        assert body['model'] == 'gpt-6.1-sol' and body['reasoning_effort'] == 'none'
        assert body['tools'] and body['stream']
        if isinstance(body['tool_choice'], dict):
            return completion({'tool_calls':[{'id':'sol-check','type':'function',
                'function':{'name':'connection_check','arguments':'{}'}}]})
        assert body['messages'][-1]['tool_call_id'] == 'sol-check'
        return completion({'content':'Ready.'})
    install_transport(monkeypatch, handler)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.get('/'); client.headers['X-Jarvis-Client']='dashboard'
        response = client.post('/api/provider/use-sol')
        assert response.status_code == 200, response.text
        assert response.json()['model'] == 'gpt-6.1-sol' and response.json()['changed']
        assert 'synthetic-sol-key' not in response.text
        assert app.state.settings.values['model'] == 'gpt-6.1-sol'
        assert app.state.settings.values['provider_url'] == 'https://api.openai.com/v1'
        assert app.state.store.row('SELECT count(*) AS n FROM action_log')['n'] == 0
    assert len(requests) == 2, 'no discovery, model substitution or computer actions'


def test_rejected_sol_preserves_the_current_provider_key_and_model(tmp_path, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) == 'https://compatible-provider.example/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic-custom-key'
        return httpx.Response(400, json={'error':{'message':'This account cannot use the requested model.',
                                                 'code':'unsupported_model','param':'model'}})
    install_transport(monkeypatch, handler)
    app = create_app(tmp_path/'private')
    app.state.settings.update({'api_key':'synthetic-custom-key','model':'custom-model',
                               'provider_url':'https://compatible-provider.example/v1','native_notifications':False})
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.get('/'); client.headers['X-Jarvis-Client']='dashboard'
        response = client.post('/api/provider/use-sol')
        assert response.status_code == 400 and 'unsupported_model' in response.text
        assert app.state.settings.values['model'] == 'custom-model'
        assert app.state.settings.api_key == 'synthetic-custom-key'
        assert app.state.settings.values['provider_url'] == 'https://compatible-provider.example/v1'
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_sol_selection_does_not_override_a_concurrent_manual_model_change(tmp_path):
    settings = Settings(tmp_path)
    settings.update({'model':'current-model','api_key':'synthetic-key'})
    class ConcurrentChange:
        def __init__(self, probe): self.probe=probe
        async def check(self):
            assert self.probe.values['model'] == 'gpt-6.1-sol'
            settings.update({'model':'manually-selected'})
            return {'ok':True}
    with pytest.raises(ProviderError, match='settings changed'):
        await use_sol(settings, ConcurrentChange)
    assert settings.values['model'] == 'manually-selected'


@pytest.mark.asyncio
async def test_sol_selection_during_an_active_task_sends_no_probe(tmp_path):
    settings = Settings(tmp_path)
    def no_provider(probe): raise AssertionError('An active task must prevent model selection')
    with pytest.raises(ProviderError, match='active task'):
        await use_sol(settings, no_provider, lambda:True)

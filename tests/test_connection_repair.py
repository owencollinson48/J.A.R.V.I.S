"""Repair verifies real streamed tool exchanges without replaying computer actions."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.connection import candidate_models, repair_connection
from jarvis.provider import Provider, ProviderError

HTTP_CLIENT=httpx.AsyncClient


def completion(message):
    if message.get('tool_calls'):
        call=message['tool_calls'][0]
        delta={'tool_calls':[{'index':0,**call}]};finish='tool_calls'
    else:delta={'content':message['content']};finish='stop'
    body={'choices':[{'delta':delta,'finish_reason':finish}]}
    return httpx.Response(200,text='data: '+json.dumps(body)+'\n\ndata: [DONE]\n\n')


def install_transport(monkeypatch,handler):
    monkeypatch.setattr('jarvis.provider.httpx.AsyncClient',lambda **kwargs:HTTP_CLIENT(transport=httpx.MockTransport(handler),**kwargs))


def test_repair_selects_only_offered_model_that_passes_tool_and_result_roundtrip(tmp_path,monkeypatch):
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers['authorization']=='Bearer synthetic-account-key'
        if request.method=='GET':
            assert request.url.path=='/v1/models'
            return httpx.Response(200,json={'data':[{'id':name} for name in ['gpt-6.1-sol','text-embedding-3-large','gpt-4.1']]})
        body=json.loads(request.content)
        if body['model']=='gpt-6.1-sol':
            return httpx.Response(400,json={'error':{'message':'This model is unavailable for this endpoint.','code':'unsupported_model','param':'model'}})
        assert body['model']=='gpt-4.1'
        assert 'desktop_action' in {tool['function']['name'] for tool in body['tools']}
        if isinstance(body['tool_choice'],dict):
            return completion({'tool_calls':[{'id':'connection-fixture','type':'function','function':{'name':'connection_check','arguments':'{}'}}]})
        assert body['tool_choice']=='none'
        assert body['messages'][-1]=={'role':'tool','tool_call_id':'connection-fixture','content':'{"ok":true}'}
        return completion({'content':'OK'})
    install_transport(monkeypatch,handler)
    app=create_app(tmp_path/'private')
    app.state.settings.update({'api_key':'synthetic-account-key','model':'gpt-6.1-sol','native_notifications':False})
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.get('/');client.headers['X-Jarvis-Client']='dashboard'
        response=client.post('/api/provider/repair')
        assert response.status_code==200,response.text
        assert response.json()['changed'] and response.json()['model']=='gpt-4.1'
        assert client.get('/api/settings').json()['model']=='gpt-4.1'
        assert 'synthetic-account-key' not in response.text
        assert app.state.store.row('SELECT count(*) AS n FROM action_log')['n']==0
        assert app.state.store.row('SELECT count(*) AS n FROM conversations')['n']==0
    assert len(requests)==4
    assert json.loads((tmp_path/'private/settings.json').read_text())['model']=='gpt-4.1'


def test_failed_probes_do_not_change_saved_model_or_run_local_actions(tmp_path,monkeypatch):
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'gpt-4.1'},{'id':'gpt-4o'}]})
        return httpx.Response(400,json={'error':{'message':'Function tools unsupported.','code':'unsupported_value','param':'tools'}})
    install_transport(monkeypatch,handler)
    app=create_app(tmp_path/'private');app.state.settings.update({'api_key':'synthetic-key','model':'current-custom'})
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.get('/');client.headers['X-Jarvis-Client']='dashboard'
        response=client.post('/api/provider/repair')
        assert response.status_code==400
        assert 'Function tools unsupported' in response.json()['detail']
        assert 'No model was changed' in response.json()['detail']
        assert client.get('/api/settings').json()['model']=='current-custom'
        assert app.state.store.row('SELECT count(*) AS n FROM action_log')['n']==0


def test_authentication_failure_is_actionable_and_does_not_probe_other_models(tmp_path,monkeypatch):
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(401,json={'error':{'message':'Invalid API key.','code':'invalid_api_key'}})
    install_transport(monkeypatch,handler)
    app=create_app(tmp_path/'private');app.state.settings.update({'api_key':'synthetic-key'})
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.get('/');client.headers['X-Jarvis-Client']='dashboard'
        response=client.post('/api/provider/repair')
        assert response.status_code==400 and 'API key' in response.json()['detail']
        assert len(requests)==1


def test_candidate_list_is_bounded_excludes_audio_and_retains_compatible_baseline():
    selected=candidate_models(['gpt-6.1-sol','gpt-6-astra','gpt-6-sol','gpt-5.5','gpt-4.1','gpt-4o','gpt-4o-mini-tts','text-embedding-3-large'],'broken-model')
    assert len(selected)==3 and 'gpt-4.1' in selected
    assert 'gpt-4o-mini-tts' not in selected and 'text-embedding-3-large' not in selected
    assert candidate_models(['only-current'],'only-current')==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_connection', ['saved_settings', 'environment_key'])
async def test_discovery_keeps_frozen_connection_across_settings_or_key_changes(tmp_path, monkeypatch, changed_connection):
    settings = Settings(tmp_path / 'private')
    original_key = 'original-connection-key'
    if changed_connection == 'environment_key':
        monkeypatch.setenv('JARVIS_API_KEY', original_key)
        saved_key = ''
    else:
        saved_key = original_key
    settings.update({'api_key': saved_key, 'model': 'broken-model'})
    listing_started, continue_listing = asyncio.Event(), asyncio.Event()
    requests = []
    async def handler(request):
        requests.append(request)
        assert str(request.url).startswith('https://api.openai.com/v1/')
        assert request.headers['authorization'] == 'Bearer ' + original_key
        if request.method == 'GET':
            listing_started.set()
            await continue_listing.wait()
            return httpx.Response(200, json={'data':[{'id':'gpt-4.1'}]})
        body = json.loads(request.content)
        if body['model'] == 'broken-model':
            return httpx.Response(400, json={'error': {'message':'Model unavailable.', 'code':'unsupported_model', 'param':'model'}})
        assert body['model'] == 'gpt-4.1'
        if isinstance(body['tool_choice'], dict):
            return completion({'tool_calls':[{'id':'frozen-probe','type':'function','function':{'name':'connection_check','arguments':'{}'}}]})
        assert body['messages'][-1]['tool_call_id'] == 'frozen-probe'
        return completion({'content':'OK'})
    install_transport(monkeypatch, handler)
    repair = asyncio.create_task(repair_connection(settings, Provider))
    try:
        await asyncio.wait_for(listing_started.wait(), 2)
        if changed_connection == 'environment_key':
            monkeypatch.setenv('JARVIS_API_KEY', 'rotated-connection-key')
        else:
            settings.update({'provider_url':'https://other-provider.example/v1',
                             'api_key':'new-connection-key', 'model':'manually-selected'})
        continue_listing.set()
        with pytest.raises(ProviderError, match='Connection settings changed'):
            await repair
    finally:
        continue_listing.set()
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)
    assert len(requests) == 4
    # A successful probe cannot overwrite a concurrent manual choice or key rotation.
    if changed_connection == 'environment_key':
        assert settings.api_key == 'rotated-connection-key'
        assert settings.values['model'] == 'broken-model'
    else:
        assert settings.api_key == 'new-connection-key'
        assert settings.values['model'] == 'manually-selected'
    persisted = json.loads(settings.path.read_text())
    assert persisted['model'] == settings.values['model']


@pytest.mark.asyncio
async def test_current_model_success_rejects_concurrent_connection_change(tmp_path, monkeypatch):
    settings = Settings(tmp_path / 'private')
    settings.update({'api_key':'original-key', 'model':'working-model'})
    check_started, continue_check = asyncio.Event(), asyncio.Event()
    requests = []
    async def handler(request):
        requests.append(request)
        assert request.method == 'POST'  # A stale successful check must not start discovery.
        assert request.headers['authorization'] == 'Bearer original-key'
        assert str(request.url) == 'https://api.openai.com/v1/chat/completions'
        body = json.loads(request.content)
        assert body['model'] == 'working-model'
        if isinstance(body['tool_choice'], dict):
            check_started.set()
            await continue_check.wait()
            return completion({'tool_calls':[{'id':'current-probe','type':'function','function':{'name':'connection_check','arguments':'{}'}}]})
        return completion({'content':'OK'})
    install_transport(monkeypatch, handler)
    repair = asyncio.create_task(repair_connection(settings, Provider))
    try:
        await asyncio.wait_for(check_started.wait(), 2)
        settings.update({'provider_url':'https://other-provider.example/v1', 'api_key':'new-key', 'model':'manual-model'})
        continue_check.set()
        with pytest.raises(ProviderError, match='Connection settings changed'):
            await repair
    finally:
        continue_check.set()
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)
    assert len(requests) == 2
    assert settings.values['model'] == 'manual-model'
    assert json.loads(settings.path.read_text())['model'] == 'manual-model'


@pytest.mark.asyncio
async def test_task_starting_during_candidate_check_prevents_model_commit(tmp_path, monkeypatch):
    settings = Settings(tmp_path / 'private')
    settings.update({'api_key':'account-key', 'model':'broken-model'})
    candidate_started, continue_candidate = asyncio.Event(), asyncio.Event()
    active = {'value':False}
    requests = []
    async def handler(request):
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data':[{'id':'gpt-4.1'}]})
        body = json.loads(request.content)
        if body['model'] == 'broken-model':
            return httpx.Response(400, json={'error': {'message':'Model unavailable.', 'code':'unsupported_model'}})
        assert body['model'] == 'gpt-4.1'
        if isinstance(body['tool_choice'], dict):
            candidate_started.set()
            await continue_candidate.wait()
            return completion({'tool_calls':[{'id':'candidate-probe','type':'function','function':{'name':'connection_check','arguments':'{}'}}]})
        return completion({'content':'OK'})
    install_transport(monkeypatch, handler)
    repair = asyncio.create_task(repair_connection(settings, Provider, lambda:active['value']))
    try:
        await asyncio.wait_for(candidate_started.wait(), 2)
        active['value'] = True
        continue_candidate.set()
        with pytest.raises(ProviderError, match='task started'):
            await repair
    finally:
        continue_candidate.set()
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)
    assert len(requests) == 4
    assert settings.values['model'] == 'broken-model'
    assert json.loads(settings.path.read_text())['model'] == 'broken-model'

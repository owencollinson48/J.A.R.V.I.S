"""Regression for GPT-6.1 Sol's Chat Completions function-tool restriction."""
import json
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.applications import app_entry
from jarvis.config import Settings
from jarvis.provider import Provider, ProviderError
from jarvis.tools import TOOLS
from test_post_launch_workflow import controls, final_completion, tool_completion


HTTP_CLIENT = httpx.AsyncClient
COMPATIBILITY_ERROR = (
    "Function tools with reasoning_effort are not supported for gpt-6.1-sol in /v1/chat/completions. "
    "To use function tools use /v1/responses or set reasoning_effort to 'none'."
)


def mock_provider(monkeypatch, handler):
    monkeypatch.setattr('jarvis.provider.httpx.AsyncClient', lambda **kwargs:
                        HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs))


def configured(tmp_path, *, model='gpt-6.1-sol', provider='https://api.openai.com/v1'):
    settings=Settings(tmp_path)
    settings.update({'api_key':'synthetic-reasoning-test-key','model':model,'provider_url':provider})
    return settings


def unsupported_tools():
    return httpx.Response(400,json={'error':{'message':COMPATIBILITY_ERROR,
        'code':'unsupported_parameter','param':'reasoning_effort'}})


@pytest.mark.asyncio
async def test_connection_check_sets_compatible_reasoning_for_probe_and_tool_result(monkeypatch,tmp_path):
    posted=[]
    def service(request):
        body=json.loads(request.content);posted.append(body)
        assert request.url.path=='/v1/chat/completions'
        assert body['model']=='gpt-6.1-sol' and body['tools'] and body['stream']
        if body.get('reasoning_effort')!='none': return unsupported_tools()
        if len(posted)==1:
            assert body['tool_choice']=={'type':'function','function':{'name':'connection_check'}}
            return tool_completion('probe-reasoning','connection_check',{})
        assert len(posted)==2
        assert body['tool_choice']=='none'
        assert body['messages'][-1]=={'role':'tool','tool_call_id':'probe-reasoning','content':'{"ok":true}'}
        return final_completion('The tool result was accepted.')
    mock_provider(monkeypatch,service)
    settings=configured(tmp_path)
    result=await Provider(settings).check()
    assert result['ok'] and result['capabilities']['tool_results']
    assert len(posted)==2
    assert all(body['reasoning_effort']=='none' for body in posted)
    assert settings.values['model']=='gpt-6.1-sol'


def test_requested_app_work_continues_with_same_model_after_launch(monkeypatch,tmp_path,controls):
    desktop,state,window,_,_=controls
    posted=[]
    requested='Complete this note after opening the app'
    def service(request):
        body=json.loads(request.content);posted.append(body)
        assert request.url.path=='/v1/chat/completions'
        assert body['model']=='gpt-6.1-sol' and body['tools']
        if body.get('reasoning_effort')!='none': return unsupported_tools()
        messages=body['messages']
        called=[call['id'] for message in messages for call in message.get('tool_calls',[])]
        returned=[message['tool_call_id'] for message in messages if message['role']=='tool']
        assert Counter(called)==Counter(returned)
        if len(posted)==1: return tool_completion('launch-reasoning','launch_application',{'name':'Notes'})
        result=json.loads(next(message['content'] for message in reversed(messages) if message['role']=='tool'))
        if len(posted)==2:
            assert result['verified'] and result['window_id']==window['id']
            return tool_completion('inspect-reasoning','inspect_window',{'window_id':window['id']})
        if len(posted)==3:
            editor=next(item for item in result['elements'] if item['type']=='Edit')
            return tool_completion('edit-reasoning','desktop_action',{'action':'set_text','window_id':window['id'],
                'element_id':editor['id'],'text':requested})
        if len(posted)==4:
            assert next(item for item in result['observation']['elements'] if item['type']=='Edit')['text']==requested
            button=next(item for item in result['observation']['elements'] if item['name']=='Save note')
            return tool_completion('save-reasoning','desktop_action',{'action':'click','window_id':window['id'],'element_id':button['id']})
        assert len(posted)==5
        assert any(item['name']=='Saved note: '+requested for item in result['observation']['elements'])
        return final_completion('Your note is saved.')
    mock_provider(monkeypatch,service)
    app=create_app(tmp_path/'private')
    app.state.settings.update({'api_key':'synthetic-reasoning-test-key','model':'gpt-6.1-sol',
        'provider_url':'https://api.openai.com/v1','permissions_mode':'autonomous','native_notifications':False})
    entry=app_entry('Notes','exe',r'C:\Apps\notes.exe','test',r'C:\Apps\notes.exe')
    class Windows:
        supported=True
        def __init__(self): self.visible=[];self.activations=0
        def discover(self,folders): return [entry],[]
        def windows(self): return self.visible[:]
        def activate(self,launcher): self.activations+=1;self.visible.append(window);return window['pid']
        def focus(self,identity): return True
    adapter=Windows();app.state.tools.applications.adapter=adapter;app.state.tools.desktop=desktop
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.get('/');client.headers['X-Jarvis-Client']='dashboard'
        cid=client.post('/api/conversations').json()['id']
        response=client.post(f'/api/conversations/{cid}/chat',json={'content':'Open Notes, replace the note and save it: '+requested})
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert not any(event['type'] in {'error','approval'} for event in events),events
        assert state['saved']==requested and adapter.activations==1
        activity=client.get('/api/activity').json()
        assert activity['workflows'][0]['status']=='completed'
        assert len(activity['actions'])==4 and all(action['status']=='completed' for action in activity['actions'])
        assert app.state.settings.values['model']=='gpt-6.1-sol'
    assert len(posted)==5 and all(body['reasoning_effort']=='none' for body in posted)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,provider,tools',[
    ('gpt-4.1','https://api.openai.com/v1',TOOLS),
    ('gpt-6-astra','https://api.openai.com/v1',TOOLS),
    ('custom-model','https://compatible-provider.example/v1',TOOLS),
    ('gpt-6.1-sol','https://compatible-provider.example/v1',TOOLS),
    ('gpt-6.1-sol','http://localhost:11434/v1',TOOLS),
    ('gpt-6.1-sol','https://api.openai.com/v1',[]),
])
async def test_unaffected_models_servers_and_plain_chat_receive_no_reasoning_override(monkeypatch,tmp_path,model,provider,tools):
    posted=[]
    def service(request):
        body=json.loads(request.content);posted.append(body)
        assert 'reasoning_effort' not in body
        assert body['model']==model
        if 'localhost' in provider: assert 'authorization' not in request.headers
        return final_completion('Ready.')
    mock_provider(monkeypatch,service)
    events=[event async for event in Provider(configured(tmp_path,model=model,provider=provider)).complete(
        [{'role':'user','content':'Hello'}],tools)]
    assert events[-1]['message']['content']=='Ready.' and len(posted)==1


@pytest.mark.asyncio
async def test_provider_rejection_does_not_retry_and_keeps_known_reasoning_advice(monkeypatch,tmp_path):
    posted=[]
    def service(request):
        posted.append(json.loads(request.content))
        return unsupported_tools()
    mock_provider(monkeypatch,service)
    settings=configured(tmp_path)
    with pytest.raises(ProviderError) as failure:
        [event async for event in Provider(settings).complete([{'role':'user','content':'Open Notes'}],TOOLS)]
    assert len(posted)==1 and posted[0]['reasoning_effort']=='none'
    assert settings.values['model']=='gpt-6.1-sol'
    assert failure.value.status_code==400 and failure.value.param=='reasoning_effort'
    assert "reasoning_effort to 'none'" in str(failure.value)
    assert '/v1/responses' in str(failure.value)

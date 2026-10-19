import json
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.computer import Computer
from jarvis.config import Settings
from jarvis.provider import Provider, ProviderError
from jarvis.store import Store


HEADERS = {"X-Jarvis-Client": "dashboard"}


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


class ScriptedProvider:
    """Deterministic provider exercises the real engine and tools, not a live model."""
    def __init__(self, settings):
        self.settings = settings
        self.requests = []

    def headers(self):
        return {}

    async def check(self):
        return {"ok": True}

    async def complete(self, messages, tools):
        self.requests.append(messages)
        last = messages[-1]
        if last["role"] == "user":
            if last["content"].startswith("write "):
                call = {"id":"call-write", "type":"function", "function":{"name":"write_document", "arguments":json.dumps({"path":last["content"][6:], "content":"Updated by JARVIS"})}}
            elif last["content"].startswith("remember "):
                call = {"id":"call-memory", "type":"function", "function":{"name":"remember", "arguments":json.dumps({"content":last["content"][9:]})}}
            else:
                call = {"id":"call-task", "type":"function", "function":{"name":"create_task", "arguments":json.dumps({"title":last["content"]})}}
            yield {"type":"completion", "message":{"role":"assistant", "content":None, "tool_calls":[call]}}
        else:
            yield {"type":"token", "text":"Action processed."}
            yield {"type":"completion", "message":{"role":"assistant", "content":"Action processed."}}


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "data", provider_factory=ScriptedProvider)
    root = tmp_path / "documents"
    root.mkdir()
    app.state.settings.update({"roots":[str(root)], "native_notifications":False, 'permissions_mode':'review'})
    with TestClient(app, base_url='http://127.0.0.1') as c:
        c.get("/")
        c.headers.update(HEADERS)
        c.root = root
        c.app_instance = app
        yield c


def conversation(client):
    return client.post('/api/conversations').json()['id']


def test_real_tools_create_task_and_retrieve_cross_conversation_memory(client):
    cid = conversation(client)
    result = events(client.post(f'/api/conversations/{cid}/chat', json={"content":"Prepare project proposal"}))
    assert any(e['type'] == 'token' for e in result)
    assert client.get('/api/tasks').json()[0]['title'] == 'Prepare project proposal'
    events(client.post(f'/api/conversations/{cid}/chat', json={"content":"remember I prefer focused mornings"}))
    next_id = conversation(client)
    events(client.post(f'/api/conversations/{next_id}/chat', json={"content":"Plan my mornings"}))
    history = client.app_instance.state.assistant.provider.requests[-1]
    assert 'I prefer focused mornings' in history[0]['content']
    result = client.get('/api/memory/search?q=proposal').json()
    assert any('proposal' in m['content'] for m in result['past_conversations'])


def test_file_edit_pauses_until_approval_and_backups_original(client):
    path = client.root / 'notes.txt'
    path.write_text('Original notes', encoding='utf-8')
    cid = conversation(client)
    result = events(client.post(f'/api/conversations/{cid}/chat', json={"content":f"write {path}"}))
    approval = next(e['approval'] for e in result if e['type'] == 'approval')
    assert path.read_text() == 'Original notes'
    blocked = client.post(f'/api/conversations/{cid}/chat', json={"content":"Something else"})
    assert blocked.status_code == 400
    result = events(client.post('/api/approvals/' + approval['id'], json={'approved':True}))
    assert path.read_text() == 'Updated by JARVIS'
    backups = list((client.app_instance.state.settings.data_dir / 'backups').iterdir())
    assert len(backups) == 1 and backups[0].read_text() == 'Original notes'
    assert any(e['type'] == 'tool' and e['ok'] for e in result)
    assert client.post('/api/approvals/' + approval['id'], json={'approved':True}).status_code == 400


def test_declined_and_stale_edits_do_not_change_file(client):
    path = client.root / 'notes.txt'
    path.write_text('Original')
    for approved in [False, True]:
        cid = conversation(client)
        events(client.post(f'/api/conversations/{cid}/chat', json={'content':f'write {path}'}))
        approval = client.get(f'/api/conversations/{cid}').json()['approvals'][0]
        if approved:
            path.write_text('Changed elsewhere')
        result = events(client.post('/api/approvals/' + approval['id'], json={'approved':approved}))
        assert any(e['type'] == 'tool' and not e['ok'] for e in result)
    assert path.read_text() == 'Changed elsewhere'


def test_file_scope_and_symlink_escape_are_rejected(client, tmp_path):
    outside = tmp_path / 'outside.txt'
    outside.write_text('Private')
    assert client.get('/api/files/read', params={'path':str(outside)}).status_code == 400
    link = client.root / 'escape.txt'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('OS does not permit symlink creation')
    assert client.get('/api/files/read', params={'path':str(link)}).status_code == 400
    assert client.get('/api/files/read', params={'path':'../outside.txt'}).status_code == 400


def test_local_session_origin_and_host_protection(client):
    assert client.post('/api/tasks', json={'title':'Injected'}, headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.get('/api/tasks', headers={'Host':'evil.example'}).status_code == 403
    other = TestClient(client.app_instance, base_url='http://127.0.0.1')
    assert other.get('/api/tasks').status_code == 401
    other.get('/')
    assert other.post('/api/tasks', json={'title':'Missing header'}).status_code == 403


def test_goal_progress_reminder_delivery_and_dismissal(client):
    goal = client.post('/api/goals', json={'title':'Learn piano'}).json()
    task = client.post('/api/tasks', json={'title':'Practice scales', 'goal_id':goal['id']}).json()
    assert client.patch('/api/tasks/' + task['id'], json={'status':'done'}).json()['status'] == 'done'
    due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    reminder = client.post('/api/reminders', json={'title':'Take a break', 'due_at':due}).json()
    fired = client.app_instance.state.scheduler.tick()
    assert [r['id'] for r in fired] == [reminder['id']]
    assert client.app_instance.state.scheduler.tick() == []
    assert client.get('/api/reminders').json()[0]['fired_at']
    assert client.patch('/api/reminders/' + reminder['id'], json={'acknowledged':1}).json()['acknowledged'] == 1
    assert client.post('/api/reminders', json={'title':'Invalid', 'due_at':'2026-10-20T09:00'}).status_code == 400


def test_docx_roundtrip_command_and_exclusive_move(client):
    computer = client.app_instance.state.assistant.tools.computer
    target = client.root / 'draft.docx'
    result = computer.write_document(str(target), 'First paragraph\nSecond paragraph')
    assert result['backup'] is None
    assert computer.read_file(str(target))['content'] == 'First paragraph\nSecond paragraph'
    output = computer.run_command([sys.executable, '-c', 'print("functional command")'])
    assert output['exit_code'] == 0 and output['stdout'].strip() == 'functional command'
    destination = client.root / 'renamed.docx'
    computer.move_file(str(target), str(destination), computer.fingerprint(target))
    assert destination.exists() and not target.exists()


def test_persistence_survives_reopening_database(tmp_path):
    path = tmp_path / 'jarvis.db'
    first = Store(path)
    c = first.conversation()
    first.message(c['id'], {'role':'user', 'content':'I like astronomy'})
    first.create_item('memories', {'content':'I enjoy astronomy'})
    first.create_item('tasks', {'title':'Read about Saturn'})
    first.db.close()
    second = Store(path)
    assert second.history(c['id'])[0]['content'] == 'I like astronomy'
    assert second.search_memory('astronomy')['memories'][0]['content'] == 'I enjoy astronomy'
    assert second.rows('SELECT * FROM tasks')[0]['title'] == 'Read about Saturn'
    second.db.close()


def test_restart_closes_unanswered_tool_calls_without_repeating_them(tmp_path):
    store = Store(tmp_path / 'recovery.db')
    cid = store.conversation()['id']
    store.message(cid, {'role':'user', 'content':'Open my notes'})
    calls = [{'id':name,'type':'function','function':{'name':'open_file','arguments':'{}'}} for name in ['unregistered','running','pending']]
    store.message(cid, {'role':'assistant','content':None,'tool_calls':calls})
    for name in ['running','pending']:
        store.execute('INSERT INTO approvals(id,conversation_id,tool_call_id,name,arguments,preview,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                      (name,cid,name,'open_file','{}','{}',name,'2026-01-01T00:00:00+00:00'))
    store.recover()
    history = store.model_history(cid)
    answered = [m['tool_call_id'] for m in history if m['role'] == 'tool']
    assert sorted(answered) == ['running','unregistered']
    assert [a['id'] for a in store.pending(cid)] == ['pending']
    store.recover()
    assert len([m for m in store.model_history(cid) if m['role'] == 'tool']) == 2
    store.db.close()


def test_local_provider_does_not_receive_saved_cloud_key(tmp_path):
    settings = Settings(tmp_path)
    settings.update({'api_key':'cloud-test-key','provider_url':'http://localhost:11434/v1'})
    assert Provider(settings).headers() == {}


def test_memory_search_includes_old_conversations(tmp_path):
    store = Store(tmp_path / 'long-memory.db')
    cid = store.conversation()['id']
    store.message(cid, {'role':'user', 'content':'My favorite telescope is a Dobsonian'})
    with store.db:
        store.db.executemany('INSERT INTO messages(id,conversation_id,role,content,payload,created_at) VALUES(?,?,?,?,?,?)',
            [(str(i),cid,'user','Unrelated daily planning','{}','2026-10-19T00:00:00+00:00') for i in range(1510)])
    assert store.search_memory('telescope')['past_conversations'][0]['content'] == 'My favorite telescope is a Dobsonian'
    store.db.close()


def test_api_key_is_not_returned_and_insecure_provider_rejected(client):
    response = client.patch('/api/settings', json={'api_key':'test-value-never-returned'})
    assert response.status_code == 200
    assert 'test-value-never-returned' not in response.text
    assert 'api_key' not in response.json()
    assert response.json()['has_api_key']
    assert client.patch('/api/settings', json={'provider_url':'http://remote.example/v1'}).status_code == 400


@pytest.mark.asyncio
async def test_provider_parses_fragmented_tool_call(monkeypatch, tmp_path):
    settings = Settings(tmp_path)
    settings.update({'provider_url':'http://localhost:11434/v1'})
    chunks = [
        {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','function':{'name':'create_task','arguments':'{"title":'}}]}}]},
        {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"Call Alice"}'}}]},'finish_reason':'tool_calls'}]},
    ]
    real_client = httpx.AsyncClient
    def handler(request):
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, text=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n')
    monkeypatch.setattr('jarvis.provider.httpx.AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    output = [event async for event in Provider(settings).complete([{'role':'user','content':'Hi'}], [])]
    message = output[-1]['message']
    assert json.loads(message['tool_calls'][0]['function']['arguments']) == {'title':'Call Alice'}


@pytest.mark.asyncio
async def test_provider_reports_interrupted_stream(monkeypatch, tmp_path):
    settings = Settings(tmp_path)
    settings.update({'provider_url':'http://localhost:11434/v1'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr('jarvis.provider.httpx.AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text='data: [DONE]\n\n')), **kwargs))
    with pytest.raises(ProviderError, match='before its response was complete'):
        [e async for e in Provider(settings).complete([], [])]

"""Keep recent evidence useful without resending old screens or huge tool trees."""
import json
import ctypes
import types
from pathlib import Path

from PIL import Image

from jarvis.app import create_app
from jarvis.assistant import EXECUTION_MODES, SYSTEM
from jarvis.context import build_context
from jarvis.execution import RunContext
from jarvis.computer import Computer
from jarvis.config import Settings


def context(app, cid):
    return build_context(app.state.assistant, cid, SYSTEM, EXECUTION_MODES)


def tool(store, cid, identity, name, result):
    store.message(cid, {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': identity, 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}]})
    store.message(cid, {'role': 'tool', 'tool_call_id': identity, 'content': json.dumps(result)})


def test_new_request_retains_history_but_does_not_resend_old_screens_or_controls(tmp_path):
    app = create_app(tmp_path/'private'); store = app.state.store
    cid = store.conversation()['id']
    store.message(cid, {'role': 'user', 'content': 'Read my earlier browser task'})
    image = app.state.tools.images.save(Image.new('RGB', (120, 80), 'navy'))
    large = {'image_id': image['image_id'], 'elements': [{'id': 'old-control', 'name': 'old text '*20000}]}
    tool(store, cid, 'old-call', 'browser_action', large)
    store.message(cid, {'role': 'assistant', 'content': 'The earlier task finished.'})
    store.message(cid, {'role': 'user', 'content': 'Hello, how are you?'})
    messages = context(app, cid)
    assert not any(isinstance(message.get('content'), list) for message in messages)
    earlier = next(message for message in messages if message['role'] == 'tool')
    assert earlier['tool_call_id'] == 'old-call'
    assert len(earlier['content']) < 2500 and 'old-control' not in earlier['content']
    assert 'not proof of current computer state' in earlier['content']
    assert any(message.get('content') == 'The earlier task finished.' for message in messages)
    # The UI and saved history retain the full original record.
    original = next(message for message in store.model_history(cid) if message['role'] == 'tool')
    assert json.loads(original['content']) == large
    assert app.state.tools.images.path(image['image_id']).is_file()
    store.db.close()


def test_current_request_keeps_latest_observations_and_vision_without_stale_ids(tmp_path):
    app = create_app(tmp_path/'private'); store = app.state.store
    cid = store.conversation()['id']
    store.message(cid, {'role': 'user', 'content': 'Inspect and finish the current browser task'})
    captures = []
    for index in range(3):
        image = app.state.tools.images.save(Image.new('RGB', (80, 80), 'blue'))
        captures.append(image['image_id'])
        tool(store, cid, f'call-{index}', 'browser_action', {**image, 'window_id':10+index,
            'original_width':160,'original_height':160,'capture_offset':{'x':8,'y':30},'elements': [
            {'id': f'control-{index}', 'name': 'Current field '+str(index)}]})
    messages = context(app, cid)
    results = [json.loads(message['content']) for message in messages if message['role'] == 'tool']
    assert 'earlier_result' in results[0]
    assert results[1]['elements'][0]['id'] == 'control-1'
    assert results[2]['elements'][0]['id'] == 'control-2'
    images = [item for message in messages if isinstance(message.get('content'), list)
              for item in message['content'] if item['type'] == 'image_url']
    assert len(images) == 2 and all(item['image_url']['url'].startswith('data:image/jpeg;base64,') for item in images)
    assert 'Untrusted screenshots' in messages[-1]['content'][0]['text']
    parts = messages[-1]['content']
    assert [part['type'] for part in parts] == ['text','text','image_url','text','image_url']
    for index, offset in ((1,1),(2,3)):
        caption = parts[offset]['text']
        assert captures[index] in caption and f'"window_id": {10+index}' in caption
        assert '"original_width": 160' in caption and 'coordinate_space="preview"' in caption
    assert captures[0] not in str(parts)
    # Every model tool call still has its corresponding result after compaction.
    calls = [call['id'] for message in messages for call in message.get('tool_calls', [])]
    assert calls == [message['tool_call_id'] for message in messages if message['role'] == 'tool']
    store.db.close()


def test_per_request_snapshot_avoids_repeated_archive_and_drive_scans(tmp_path, monkeypatch):
    app = create_app(tmp_path/'private'); store = app.state.store
    cid = store.conversation()['id']; assistant = app.state.assistant
    store.message(cid, {'role': 'user', 'content': 'Help plan my project'})
    current = RunContext(store, cid, 'Help plan my project'); assistant.contexts[cid] = current
    counts = {'memory': 0, 'drives': 0}
    original_search = store.search_memory
    def memory(*args):
        counts['memory'] += 1
        return original_search(*args)
    def drives():
        counts['drives'] += 1
        return ['/']
    monkeypatch.setattr(store, 'search_memory', memory)
    monkeypatch.setattr(assistant.tools.computer, 'drives', drives)
    context(app, cid)
    tool(store, cid, 'fresh', 'list_windows', {'windows': []})
    context(app, cid)
    assert counts == {'memory': 1, 'drives': 1}
    current.finish('completed')
    store.message(cid, {'role': 'user', 'content': 'Now plan another task'})
    assistant.contexts[cid] = RunContext(store, cid, 'Now plan another task')
    context(app, cid)
    assert counts == {'memory': 2, 'drives': 2}, 'a new request gets a fresh snapshot'
    store.db.close()


def test_windows_drive_enumeration_does_not_probe_offline_filesystems(tmp_path, monkeypatch):
    computer = Computer(Settings(tmp_path))
    monkeypatch.setattr('jarvis.computer.platform.system', lambda: 'Windows')
    def query(): return (1 << 2) | (1 << 3) | (1 << 25)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: types.SimpleNamespace(GetLogicalDrives=query), raising=False)
    def no_probe(*args): raise AssertionError('Drive listing must not access offline/network paths')
    monkeypatch.setattr(Path, 'exists', no_probe)
    assert computer.drives() == ['C:\\', 'D:\\', 'Z:\\']


def test_old_spotify_receipts_do_not_dominate_a_new_outlook_request(tmp_path):
    app = create_app(tmp_path/'private'); store = app.state.store
    old = store.conversation()['id']
    store.message(old, {'role':'assistant','content':'Spotify is now open for you, sir.'})
    cid = store.conversation()['id']
    store.message(cid, {'role':'user','content':'Open Spotify'})
    store.message(cid, {'role':'assistant','content':'Spotify is now open for you, sir.'})
    store.message(cid, {'role':'user','content':'Spotify is not open. Open Outlook instead.'})
    store.create_item('memories', {'content':'My office is open on Monday and Tuesday.'})
    messages = context(app, cid)
    assert 'Spotify is now open for you, sir.' not in json.dumps(messages)
    details = json.loads(messages[0]['content'].split('Current context (data):\n',1)[1])
    assert details['current_request'] == 'Spotify is not open. Open Outlook instead.'
    assert any(item['content']=='My office is open on Monday and Tuesday.' for item in details['memory']['memories'])
    assert any(message.get('content')=='Spotify is not open. Open Outlook instead.' for message in messages)
    assert any(message['content']=='Spotify is now open for you, sir.' for message in store.history(cid)), 'saved history stays intact'
    store.db.close()


def test_explicit_history_question_keeps_the_previous_receipt_available(tmp_path):
    app = create_app(tmp_path/'private'); store = app.state.store
    cid = store.conversation()['id']
    store.message(cid, {'role':'user','content':'Open Spotify'})
    store.message(cid, {'role':'assistant','content':'Spotify is now open for you, sir.'})
    store.message(cid, {'role':'user','content':'What did you say about Spotify earlier?'})
    assert any(message.get('content')=='Spotify is now open for you, sir.' for message in context(app,cid))
    store.db.close()

"""Real provider protocol/assistant loop with a simulated Windows accessibility tree."""
import json
import sys
import types
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.applications import app_entry
from jarvis.desktop import DesktopControl, Images


class ValuePattern:
    def __init__(self, state): self.state = state
    @property
    def CurrentValue(self): return self.state['text']
    def SetValue(self, value): self.state['text'] = value


class Control:
    def __init__(self, identity, name, kind='Button', state=None):
        self.state, self.children = state, []
        self.element_info = types.SimpleNamespace(name=name, control_type=kind,
            automation_id=str(identity), runtime_id=(identity,),
            element=types.SimpleNamespace(CurrentIsPassword=False, CurrentHasKeyboardFocus=False))
    def is_visible(self): return True
    def descendants(self, **kwargs): return self.children
    @property
    def iface_value(self):
        if self.element_info.control_type != 'Edit': raise AttributeError('No ValuePattern')
        return ValuePattern(self.state)
    def invoke(self):
        self.state['saved'] = self.state['text']
        self.state['status'].element_info.name = 'Saved note: ' + self.state['saved']
    def set_focus(self): pass


@pytest.fixture
def controls(tmp_path, monkeypatch):
    state = {'text':'Previous note', 'saved':None}
    wrapper = Control(1, 'Notes', 'Window')
    editor = Control(2, 'Note contents', 'Edit', state)
    button = Control(3, 'Save note', state=state)
    status = Control(4, 'Ready', 'Text')
    wrapper.children = [editor, button, status]; state['status'] = status
    window = {'id':901, 'pid':801, 'title':'Notes', 'process':'notes.exe'}
    desktop = DesktopControl(Images(tmp_path))
    desktop.window = lambda identity:(window, wrapper)
    desktop.ensure_focus = lambda identity:None
    uia = types.ModuleType('pywinauto.uia_defines')
    uia.NoPatternInterfaceError = type('NoPatternInterfaceError', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'pywinauto.uia_defines', uia)
    return desktop, state, window, wrapper, editor


def tool_completion(identity, name, args):
    chunk = {'choices':[{'delta':{'tool_calls':[{'index':0,'id':identity,'type':'function',
        'function':{'name':name,'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}]}
    return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n',
                          headers={'content-type':'text/event-stream'})


def final_completion(text):
    chunk = {'choices':[{'delta':{'content':text},'finish_reason':'stop'}]}
    return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n',
                          headers={'content-type':'text/event-stream'})


@pytest.mark.parametrize('work_inside_app', [True, False])
def test_provider_tool_result_round_trips_continue_after_verified_launch(tmp_path, monkeypatch, controls, work_inside_app):
    desktop, state, window, _, _ = controls
    requested_text = 'A note completed by JARVIS'
    posted = []
    def transport(request):
        assert request.url.path == '/v1/chat/completions'
        body = json.loads(request.content)
        assert body['tools'] and body['stream']
        messages = body['messages']; posted.append(messages)
        called = [call['id'] for message in messages for call in message.get('tool_calls', [])]
        returned = [message['tool_call_id'] for message in messages if message['role']=='tool']
        # A rejected follow-up after launch would reproduce the user's visible
        # symptom, so exercise every actual Provider HTTP request in the loop.
        assert Counter(called) == Counter(returned)
        if len(posted)==1:
            return tool_completion('launch-step', 'launch_application', {'name':'Notes'})
        latest = json.loads(next(message['content'] for message in reversed(messages) if message['role']=='tool'))
        if len(posted)==2:
            assert latest['verified'] and latest['window_id']==window['id']
            if not work_inside_app: return final_completion('Notes is open.')
            return tool_completion('inspect-step', 'inspect_window', {'window_id':latest['window_id']})
        if len(posted)==3:
            editor = next(item for item in latest['elements'] if item['type']=='Edit')
            assert editor['text']=='Previous note'
            return tool_completion('text-step', 'desktop_action', {'action':'set_text','window_id':window['id'],
                'element_id':editor['id'],'text':requested_text})
        if len(posted)==4:
            observation = latest['observation']
            assert next(item for item in observation['elements'] if item['type']=='Edit')['text']==requested_text
            button = next(item for item in observation['elements'] if item['name']=='Save note')
            return tool_completion('save-step', 'desktop_action', {'action':'click','window_id':window['id'], 'element_id':button['id']})
        assert len(posted)==5
        assert any(item['name']=='Saved note: ' + requested_text for item in latest['observation']['elements'])
        return final_completion('Your note is saved.')
    original_client = httpx.AsyncClient
    monkeypatch.setattr('jarvis.provider.httpx.AsyncClient', lambda **kwargs:original_client(
        **kwargs, transport=httpx.MockTransport(transport)))
    app = create_app(tmp_path/'private')
    app.state.settings.update({'api_key':'synthetic-model-key','model':'gpt-4.1',
        'provider_url':'https://api.openai.com/v1','permissions_mode':'autonomous','native_notifications':False})
    entry = app_entry('Notes','exe',r'C:\Apps\notes.exe','test',r'C:\Apps\notes.exe')
    class Windows:
        supported = True
        def __init__(self): self.visible = []
        def discover(self, folders): return [entry], []
        def windows(self): return self.visible[:]
        def activate(self, launcher): self.visible.append(window); return window['pid']
        def focus(self, identity): return True
    app.state.tools.applications.adapter = Windows()
    app.state.tools.desktop = desktop
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.get('/'); client.headers['X-Jarvis-Client']='dashboard'
        cid = client.post('/api/conversations').json()['id']
        request = 'Open Notes and write and save this note: ' + requested_text if work_inside_app else 'Open Notes'
        response = client.post(f'/api/conversations/{cid}/chat', json={'content':request})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert not any(event['type'] in {'error','approval'} for event in events), events
        activity = client.get('/api/activity').json()
        assert activity['workflows'][0]['status']=='completed'
        assert all(action['status']=='completed' for action in activity['actions'])
        assert len(activity['actions'])==(4 if work_inside_app else 1)
        assert state['saved']==(requested_text if work_inside_app else None)
        assert len(posted)==(5 if work_inside_app else 2)


@pytest.mark.parametrize('replacement', ['Replacement text', ''])
def test_set_text_fallback_replaces_existing_value_and_observes_result(controls, monkeypatch, replacement):
    desktop, state, window, _, editor = controls
    no_pattern = sys.modules['pywinauto.uia_defines'].NoPatternInterfaceError
    class TextOnly(Control):
        @property
        def iface_value(self): raise no_pattern('No ValuePattern')
        @property
        def iface_text(self):
            return types.SimpleNamespace(DocumentRange=types.SimpleNamespace(GetText=lambda limit:state['text'][:limit]))
    editor.__class__ = TextOnly
    sent = []
    def send_keys(text, **kwargs):
        sent.append(text)
        if text=='^a': state['selected']=True
        elif text=='{BACKSPACE}': state['text']=''
        else:
            state['text'] = text if state.pop('selected',False) else state['text'] + text
    keyboard = types.ModuleType('pywinauto.keyboard'); keyboard.send_keys=send_keys
    monkeypatch.setitem(sys.modules,'pywinauto.keyboard',keyboard)
    monkeypatch.setitem(sys.modules,'pyautogui',types.SimpleNamespace(FAILSAFE=False,failSafeCheck=lambda:None))
    observed = desktop.inspect(window['id'])
    target = next(item for item in observed['elements'] if item['type']=='Edit')
    args = {'action':'set_text','window_id':window['id'],'element_id':target['id'],'text':replacement}
    result = desktop.act(args,desktop.preflight(args))
    assert sent[0]=='^a'
    assert state['text']==replacement
    assert next(item for item in result['observation']['elements'] if item['type']=='Edit')['text']==replacement


def test_inaccessible_sibling_does_not_block_an_observed_control(controls):
    desktop, state, window, wrapper, _ = controls
    class Inaccessible:
        def is_visible(self): return True
        @property
        def element_info(self): raise OSError('Element no longer available')
    wrapper.children.insert(0, Inaccessible())
    observed = desktop.inspect(window['id'])
    assert observed['inaccessible_controls']==1
    target = next(item for item in observed['elements'] if item['type']=='Edit')
    args = {'action':'set_text','window_id':window['id'],'element_id':target['id'],'text':'Still editable'}
    assert desktop.act(args,desktop.preflight(args))['performed']=='set_text'
    assert state['text']=='Still editable'


def test_accessibility_import_failure_explains_missing_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules,'pywinauto',None)
    with pytest.raises(ValueError, match='ModuleNotFoundError') as error:
        DesktopControl.automation()
    assert 'py -3 start.py' in str(error.value)


@pytest.mark.parametrize('password_state', ['password','unknown'])
def test_inspection_never_reads_password_or_unknown_password_state(controls, password_state):
    desktop, _, window, _, editor = controls
    reads=[]
    if password_state=='password': editor.element_info.element.CurrentIsPassword=True
    else:
        class Unknown:
            @property
            def CurrentIsPassword(self): raise OSError('Not available')
        editor.element_info.element=Unknown()
    class NeverRead(Control):
        @property
        def iface_value(self):
            reads.append('value')
            raise AssertionError('Private field value must not be read')
        @property
        def iface_text(self):
            reads.append('text')
            raise AssertionError('Private field text must not be read')
    editor.__class__=NeverRead
    observed=desktop.inspect(window['id'])
    field=next(item for item in observed['elements'] if item['type']=='Edit')
    assert reads==[]
    assert 'text' not in field
    if password_state=='password': assert field['name']=='[password field]'

"""Bounded interaction tests; Windows adapters are mocked, browser input is real."""
import shutil
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.browser import BrowserControl
from jarvis.config import Settings
from jarvis.desktop import DesktopControl, Images
from jarvis.execution import Cancelled
from jarvis.tools import ToolRunner


@pytest.mark.parametrize('tool,args', [
    ('desktop_action', {'action':'scroll','window_id':1,'direction':'left','amount':20}),
    ('browser_action', {'action':'scroll','direction':'down','amount':1}),
    ('desktop_action', {'action':'double_click','window_id':1,'element_id':'observed'}),
    ('browser_action', {'action':'right_click','element_id':'observed'}),
    ('desktop_pointer', {'window_id':1,'x':1,'y':2,'action':'double_click'}),
])
def test_interaction_schema_accepts_bounded_actions(tool, args):
    ToolRunner.validate(None, tool, args)


@pytest.mark.parametrize('tool,args', [
    ('desktop_action', {'action':'scroll','window_id':1,'amount':0}),
    ('browser_action', {'action':'scroll','amount':21}),
    ('browser_action', {'action':'scroll','amount':True}),
    ('desktop_action', {'action':'scroll','window_id':1,'direction':'diagonal'}),
    ('desktop_pointer', {'window_id':1,'x':-1,'y':0}),
    ('browser_action', {'action':'evaluate','text':'document.cookie'}),
])
def test_interaction_schema_rejects_unbounded_or_unsupported_input(tool, args):
    with pytest.raises(ValueError): ToolRunner.validate(None, tool, args)


class Rectangle:
    left, top, right, bottom = 100, 200, 600, 600
    def width(self): return self.right - self.left
    def height(self): return self.bottom - self.top


class FakeControl:
    def __init__(self): self.clicks, self.scrolls = [], []
    def click_input(self, **args): self.clicks.append(args)
    def rectangle(self): return Rectangle()
    @property
    def iface_scroll(self): return self
    def Scroll(self, horizontal, vertical): self.scrolls.append((horizontal,vertical))


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    # Importing native UIA is deferred until an actual Windows operation.
    uia = types.ModuleType('pywinauto.uia_defines')
    uia.NoPatternInterfaceError = type('NoPatternInterfaceError', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'pywinauto.uia_defines', uia)
    mouse = types.SimpleNamespace(FAILSAFE=False, events=[], failSafeCheck=lambda:None)
    mouse.moveTo = lambda *args, **kwargs: mouse.events.append(('move',args,kwargs))
    mouse.click = lambda *args, **kwargs: mouse.events.append(('click',args,kwargs))
    mouse.scroll = lambda *args: mouse.events.append(('scroll',args,{}))
    mouse.hscroll = lambda *args: mouse.events.append(('hscroll',args,{}))
    monkeypatch.setitem(sys.modules, 'pyautogui', mouse)
    desktop = DesktopControl(Images(tmp_path)); control = FakeControl()
    window = {'id':7,'pid':101,'title':'Editor','process':'editor.exe'}
    desktop.target = lambda *args:(window,control,control,{'name':'Document'})
    desktop.window = lambda *args:(window,control)
    desktop.native_window = lambda *args:{**window,'bounds':[100,200,600,600]}
    desktop.ensure_pointer_target = lambda *args:None
    desktop.preflight = lambda args:{'signature':'observed'}
    desktop.ensure_focus = lambda identity:None
    desktop.inspect = lambda identity,cancel=None:{'window':window,'elements':[]}
    return desktop, control, mouse


def test_observed_control_double_and_right_click(desktop):
    adapter, control, mouse = desktop
    for action in ['double_click','right_click']:
        result = adapter.act({'action':action,'window_id':7,'element_id':'observed'}, {'signature':'observed'})
        assert result['performed'] == action and result['observation']['window']['id'] == 7
    assert control.clicks == [{'button':'left','double':True},{'button':'right','double':False}]
    assert mouse.FAILSAFE is True


def test_uia_scroll_uses_bounded_accessibility_pattern(desktop):
    adapter, control, mouse = desktop
    result = adapter.act({'action':'scroll','window_id':7,'element_id':'observed','direction':'down','amount':3}, {'signature':'observed'})
    assert control.scrolls == [(2,4)] * 3
    assert not mouse.events and result['performed'] == 'scroll'


def test_scroll_fallback_targets_the_requested_window(desktop):
    adapter, control, mouse = desktop
    adapter.target = lambda *args:({'id':7},control,None,{'name':'Editor'})
    adapter.act({'action':'scroll','window_id':7,'direction':'left','amount':2}, {'signature':'observed'})
    assert mouse.events == [('move',(350,400),{'duration':.1}),('hscroll',(-1,),{}),('hscroll',(-1,),{})]
    assert mouse.FAILSAFE is True


def test_windows_horizontal_scroll_uses_native_wheel(desktop, monkeypatch):
    adapter, control, mouse = desktop
    adapter.target = lambda *args:({'id':7},control,None,{'name':'Editor'})
    class Wheel:
        def __init__(self): self.calls = []
        def __call__(self, *args): self.calls.append(args)
    wheel = Wheel()
    monkeypatch.setattr('jarvis.desktop.platform.system', lambda:'Windows')
    monkeypatch.setattr('jarvis.desktop.ctypes.WinDLL', lambda *args,**kwargs:types.SimpleNamespace(mouse_event=wheel), raising=False)
    adapter.act({'action':'scroll','window_id':7,'direction':'left','amount':2}, {'signature':'observed'})
    assert wheel.calls == [(0x1000,0,0,(-120) & 0xffffffff,0)] * 2
    assert not any(event[0]=='hscroll' for event in mouse.events)


def test_scroll_checks_cancellation_between_inputs(desktop):
    adapter, control, mouse = desktop
    class Cancellation:
        def __init__(self): self.calls = 0
        def check(self):
            self.calls += 1
            if self.calls == 4: raise Cancelled('Stopped')
    with pytest.raises(Cancelled):
        adapter.act({'action':'scroll','window_id':7,'amount':20}, {'signature':'observed'}, Cancellation())
    assert control.scrolls == [(2,4)]


def test_pointer_coordinates_are_window_relative_and_bounded(desktop):
    adapter, control, mouse = desktop
    result = adapter.pointer(7,20,30,action='right_click')
    assert mouse.events == [('click',(120,230),{'button':'right','clicks':1,'interval':0})]
    assert result['performed'] == 'right_click'
    with pytest.raises(ValueError,match='inside'): adapter.pointer(7,500,0)
    assert len(mouse.events) == 1


def test_real_browser_scroll_double_and_right_click(tmp_path, monkeypatch):
    executable = shutil.which('chromium')
    if not executable: pytest.skip('System Chromium is unavailable')
    monkeypatch.setenv('JARVIS_BROWSER_EXECUTABLE', executable)
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers()
            self.wfile.write(b'''<html><body style="margin:0;height:4000px">
                <button ondblclick="document.getElementById('result').textContent='Double clicked'"
                        oncontextmenu="event.preventDefault();document.getElementById('result').textContent='Right clicked'">Action target</button>
                <p id="result">Ready</p>
                <div role="button" aria-label="Scrollable panel" id="panel" style="height:220px;width:500px;overflow:auto">
                  <div style="height:1600px">Inside panel</div>
                </div></body></html>''')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    settings = Settings(tmp_path/'private'); browser = BrowserControl(settings, Images(settings.data_dir))
    def act(args): return browser.run('act',args,browser.run('preflight',args))
    try:
        result = act({'action':'navigate','url':f'http://127.0.0.1:{server.server_port}'})
        for action, expected in [('double_click','Double clicked'), ('right_click','Right clicked')]:
            target = next(item for item in result['observation']['elements'] if item['name']=='Action target')
            result = act({'action':action,'element_id':target['id']})
            assert expected in result['observation']['text']
        panel = next(item for item in result['observation']['elements'] if item['name']=='Scrollable panel')
        result = act({'action':'scroll','element_id':panel['id'],'direction':'down','amount':3})
        panel_offset = browser.executor.submit(lambda:browser.page.locator('#panel').evaluate('(el)=>el.scrollTop')).result()
        assert panel_offset > 0
        result = act({'action':'scroll','direction':'down','amount':4})
        assert result['observation']['viewport']['y'] > 0
        assert result['observation']['viewport']['document_height'] >= 4000
        with pytest.raises(ValueError,match='1 to 20'):
            act({'action':'scroll','amount':21})
    finally:
        browser.close(); server.shutdown(); server.server_close()

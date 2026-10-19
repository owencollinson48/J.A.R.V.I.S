"""Current-browser routing preserves the active browser/session and existing tab."""
import types
import pytest
from jarvis.applications import WindowsApplications
from jarvis.browser import BrowserControl
from jarvis.config import Settings
from jarvis.desktop import Images
from jarvis.existing_browser import ExistingBrowser

@pytest.fixture
def opened(monkeypatch):
    windows=[{'id':8,'pid':80,'title':'JARVIS - Google Chrome','process':'chrome.exe'},
             {'id':9,'pid':90,'title':'Signed in - Microsoft Edge','process':'msedge.exe'}]
    foreground=[9];keys=[];typed=[];focused=[];actual=[None];native=[]
    class Desktop:
        def ensure_focus(self,identity): focused.append(identity)
        def inspect(self,identity,cancel=None): return {'window':next(item for item in windows if item['id']==identity),'elements':[{'id':'observed','name':'Page button','type':'Button'}]}
        def preflight(self,args): return {'signature':'observed','target_name':'Page button'}
        def act(self,args,preview,cancel=None): native.append(args);return {'performed':args['action']}
        def screenshot(self,identity,cancel=None): return {'image_id':'a'*32,'window_id':identity}
    monkeypatch.setattr(WindowsApplications,'windows',lambda:windows[:])
    monkeypatch.setattr(WindowsApplications,'foreground_window',lambda:foreground[0])
    browser=ExistingBrowser(Desktop());browser.address=lambda identity:actual[0]
    browser.type_url=lambda url,*args:typed.append(url)
    def key(*value):
        keys.append(value)
        if value==('enter',): actual[0]=typed[-1]
    browser.keys=key
    return types.SimpleNamespace(browser=browser,windows=windows,foreground=foreground,keys=keys,typed=typed,focused=focused,native=native)

def test_uses_foreground_browser_and_new_tab_without_overwriting_current_session(opened):
    args={'action':'navigate','url':'https://example.com/app?value=100%25'}
    preview=opened.browser.preflight(args)
    result=opened.browser.act(args,preview)
    assert preview['window_id']==9 and result['window_id']==9
    assert opened.keys==[('ctrl','t'),('ctrl','l'),('enter',)]
    assert opened.typed==[args['url']] and opened.focused==[9]
    assert result['verified'] and result['mode']=='existing'

def test_dashboard_tab_is_preserved_by_new_tab_navigation(opened):
    opened.foreground[0]=8
    args={'action':'navigate','url':'https://example.com'}
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['window_id']==8 and opened.keys[0]==('ctrl','t')

def test_observed_controls_are_routed_to_same_browser_native_accessibility(opened):
    args={'action':'fill','element_id':'observed','text':'Requested text'}
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert opened.native==[{'action':'set_text','element_id':'observed','text':'Requested text','window_id':9}]
    assert result['mode']=='existing' and result['observation']['elements'][0]['id']=='observed'

@pytest.mark.parametrize('url',['javascript:alert(1)','file:///C:/secret','https://user:password@example.com','https://example.com/\nrun'])
def test_current_browser_rejects_executable_or_embedded_credential_urls(opened,url):
    with pytest.raises(ValueError): opened.browser.preflight({'action':'navigate','url':url})
    assert not opened.keys and not opened.typed

def test_closed_browser_between_preflight_and_navigation_is_not_replaced_silently(opened):
    args={'action':'navigate','url':'https://example.com'};preview=opened.browser.preflight(args)
    opened.windows[:]=[]
    with pytest.raises(ValueError,match='closed'):opened.browser.act(args,preview)
    assert not opened.keys

def test_windows_current_mode_never_starts_playwright_profile(tmp_path,opened,monkeypatch):
    settings=Settings(tmp_path/'private');browser=BrowserControl(settings,Images(settings.data_dir))
    browser.existing=opened.browser
    monkeypatch.setattr('jarvis.browser.platform.system',lambda:'Windows')
    browser._open=lambda:pytest.fail('Current-browser mode must not start a separate managed browser')
    args={'action':'navigate','url':'https://example.com'}
    try:
        result=browser.run('act',args,browser.run('preflight',args))
        assert result['mode']=='existing' and result['verified']
        assert browser.playwright is None and browser.context is None
    finally: browser.close()


def test_no_open_browser_uses_windows_default_without_creating_managed_profile(opened,monkeypatch):
    opened.windows.clear();launched=[]
    url='https://example.com/new'
    def open_default(value,new=0):
        launched.append((value,new))
        opened.windows.append({'id':9,'pid':90,'title':'New browser','process':'msedge.exe'})
        return True
    monkeypatch.setattr('webbrowser.open',open_default)
    monkeypatch.setattr(WindowsApplications,'focus',lambda identity:opened.focused.append(identity))
    opened.browser.address=lambda identity:url
    args={'action':'navigate','url':url}
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert launched==[(url,2)] and result['verified'] and result['window_id']==9
    assert not opened.keys


def test_degraded_browser_inspection_preserves_accessibility_diagnostic(opened):
    diagnostic = {'available':False,'error':{'stage':'initialize','type':'COMError','hresult':'0x80010106'}}
    def unavailable(identity,cancel=None):
        return {'window':next(item for item in opened.windows if item['id']==identity),
                'elements':[],'limited':True,'accessibility':diagnostic,
                'note':'Native window controls and screenshot remain available.'}
    opened.browser.desktop.inspect = unavailable
    opened.browser.window_id = 9
    opened.browser.address = lambda identity:pytest.fail('Degraded inspection must not retry broken UIA address lookup')
    observed = opened.browser.inspect()
    assert observed['accessibility'] == diagnostic
    assert observed['limited'] and observed['elements'] == [] and observed['url'] is None
    assert 'desktop_screenshot' in observed['note'] and 'desktop_pointer' in observed['note']
    assert 'press_keys/scroll' in observed['note'] and 'reinstall' not in observed['note'].lower()


def test_navigation_with_unavailable_accessibility_remains_unverified_and_can_recover_visually(opened,monkeypatch):
    diagnostic = {'available':False,'error':'Accessibility initialization failed'}
    opened.browser.desktop.inspect = lambda identity,cancel=None:{
        'window':next(item for item in opened.windows if item['id']==identity),
        'elements':[],'limited':True,'accessibility':diagnostic,'note':'Use native window controls.'}
    opened.browser.address = lambda identity:None
    monkeypatch.setattr('jarvis.existing_browser.time.sleep',lambda duration:pytest.fail('Do not poll broken UIA repeatedly'))
    args = {'action':'navigate','url':'https://example.com/report'}
    result = opened.browser.act(args,opened.browser.preflight(args))
    assert opened.typed == [args['url']]
    assert opened.keys == [('ctrl','t'),('ctrl','l'),('enter',)]
    assert result['performed'] == 'navigate' and result['verified'] is False
    assert result['observation']['accessibility'] == diagnostic
    assert 'Navigation input delivered' in result['message']
    shot = {'action':'screenshot'}
    assert opened.browser.act(shot,opened.browser.preflight(shot))['window_id'] == 9
    scroll = {'action':'scroll','direction':'down','amount':2}
    opened.browser.act(scroll,opened.browser.preflight(scroll))
    assert opened.native[-1] == {**scroll,'window_id':9}


def native_keyboard(monkeypatch, send=None):
    import ctypes
    import sys
    monkeypatch.setitem(sys.modules,'pyautogui',types.SimpleNamespace(FAILSAFE=False,failSafeCheck=lambda:None))
    captured = []
    def sender(count,inputs,size):
        captured.append({'count':count,'size':size,'events':[
            (event.type,event.ki.wVk,event.ki.wScan,event.ki.dwFlags,event.ki.time,event.ki.dwExtraInfo)
            for event in inputs]})
        return send(count,inputs,size) if send else count
    monkeypatch.setattr(ctypes,'WinDLL',lambda name,**kwargs:types.SimpleNamespace(SendInput=sender),raising=False)
    return captured


def test_url_entry_uses_literal_unicode_input_without_uia_or_clipboard(monkeypatch):
    import builtins
    import ctypes
    from jarvis.existing_browser import _Input
    actual_import = builtins.__import__
    def no_uia_import(name,*args,**kwargs):
        if name.startswith(('pywinauto','comtypes','pyperclip')):
            pytest.fail('URL entry must work without UIA imports or changing the clipboard')
        return actual_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',no_uia_import)
    captured = native_keyboard(monkeypatch)
    url = 'https://example.com/café/🚀?value=100%25+{x}^()~'
    ExistingBrowser.type_url(url)
    events = [event for batch in captured for event in batch['events']]
    assert ctypes.sizeof(_Input) == (40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)
    assert all(batch['size']==ctypes.sizeof(_Input) for batch in captured)
    assert len(events) == len(url.encode('utf-16-le'))
    for down,up in zip(events[::2],events[1::2]):
        assert down[0] == up[0] == 1 and down[1] == up[1] == 0
        assert down[2] == up[2] and down[3] == 4 and up[3] == 6
        assert down[4:] == up[4:] == (0,0)
    typed = b''.join(event[2].to_bytes(2,'little') for event in events[::2]).decode('utf-16-le')
    assert typed == url


def test_native_url_entry_stops_if_foreground_changes_between_batches(monkeypatch):
    from jarvis.existing_browser import send_unicode_text
    captured = native_keyboard(monkeypatch)
    foreground = iter([9,8])
    monkeypatch.setattr(WindowsApplications,'foreground_window',lambda:next(foreground))
    with pytest.raises(ValueError,match='lost foreground focus'):
        send_unicode_text('https://example.com/'+'x'*100,window_id=9)
    assert len(captured) == 1 and captured[0]['count'] == 64


def test_native_url_entry_stops_on_cancellation_between_batches(monkeypatch):
    from jarvis.existing_browser import send_unicode_text
    from jarvis.execution import Cancelled
    stopped = {'value':False}
    def sent(count,inputs,size):
        stopped['value'] = True
        return count
    captured = native_keyboard(monkeypatch,sent)
    class Cancellation:
        def check(self):
            if stopped['value']: raise Cancelled('Task cancelled')
    with pytest.raises(Cancelled):
        send_unicode_text('https://example.com/'+'x'*100,cancel=Cancellation())
    assert len(captured) == 1


def test_native_url_entry_reports_incomplete_input_and_does_not_continue(monkeypatch):
    from jarvis.existing_browser import send_unicode_text
    captured = native_keyboard(monkeypatch,lambda count,inputs,size:0)
    with pytest.raises(OSError,match='Navigation was not submitted'):
        send_unicode_text('https://example.com/'+'x'*100)
    assert len(captured) == 1



def test_native_text_checks_emergency_corner_before_every_batch(monkeypatch):
    from jarvis.existing_browser import send_unicode_text
    captured = native_keyboard(monkeypatch)
    checks = []
    class FailSafeException(Exception): pass
    def fail_safe():
        checks.append(True)
        if len(checks) == 2: raise FailSafeException('Emergency corner')
    with pytest.raises(FailSafeException,match='Emergency corner'):
        send_unicode_text('https://example.com/'+'x'*100,safety_check=fail_safe)
    assert len(checks) == 2 and len(captured) == 1


def test_existing_browser_url_entry_enables_and_uses_emergency_corner(monkeypatch):
    import sys
    captured = native_keyboard(monkeypatch)
    checks = []
    module = sys.modules['pyautogui']
    module.failSafeCheck = lambda:checks.append(module.FAILSAFE)
    ExistingBrowser.type_url('https://example.com/'+'x'*100)
    assert checks == [True]*len(captured) and len(checks)>1

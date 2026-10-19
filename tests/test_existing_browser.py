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
    inspections=[]
    def unavailable(identity,cancel=None):
        inspections.append(identity)
        return {'window':next(item for item in opened.windows if item['id']==identity),
                'elements':[],'limited':True,'accessibility':diagnostic,'note':'Use native window controls.'}
    opened.browser.desktop.inspect = unavailable
    opened.browser.address = lambda identity:None
    monkeypatch.setattr('jarvis.existing_browser.time.sleep',lambda duration:pytest.fail('Do not poll broken UIA repeatedly'))
    args = {'action':'navigate','url':'https://example.com/report'}
    result = opened.browser.act(args,opened.browser.preflight(args))
    assert opened.typed == [args['url']]
    assert opened.keys == [('ctrl','t'),('ctrl','l'),('enter',)]
    assert result['performed'] == 'navigate' and result['verified'] is False
    assert result['observation']['accessibility'] == diagnostic
    assert 'Navigation input delivered' in result['message']
    assert inspections == [9]  # Do not repeat known-broken accessibility initialization.
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


@pytest.fixture
def browser_clock(monkeypatch):
    clock=[0.0]
    monkeypatch.setattr('jarvis.existing_browser.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('jarvis.existing_browser.time.sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    return clock


def test_read_recovers_replaced_window_in_same_browser_and_invalidates_old_controls(opened):
    opened.foreground[0]=8
    args={'action':'read'}
    opened.browser.act(args,opened.browser.preflight(args))
    preview=opened.browser.preflight(args)
    opened.browser.desktop.elements={'old':{'window_id':8},'other-app':{'window_id':70}}
    opened.windows[0]={'id':18,'pid':180,'title':'Changed page title','process':'chrome.exe'}
    opened.foreground[0]=9  # An unrelated browser must not steal the recovered read.
    result=opened.browser.act(args,preview)
    assert result['window_id']==18 and result['observation']['window']['id']==18
    assert result['observation']['window_recovered'] and result['observation']['previous_window_id']==8
    assert opened.browser.desktop.elements=={'other-app':{'window_id':70}}
    assert not opened.keys and not opened.native


def test_control_action_never_follows_replacement_browser_with_stale_element(opened):
    args={'action':'click','element_id':'observed'}
    preview=opened.browser.preflight(args)
    opened.windows[1]={'id':19,'pid':190,'title':'Replacement window','process':'msedge.exe'}
    with pytest.raises(ValueError,match='window changed'):
        opened.browser.act(args,preview)
    assert not opened.native and not opened.keys


def test_navigation_rejects_reused_window_handle_before_sending_input(opened):
    args={'action':'navigate','url':'https://example.com'}
    preview=opened.browser.preflight(args)
    opened.windows[1]['pid']=190
    with pytest.raises(ValueError,match='changed identity'):
        opened.browser.act(args,preview)
    assert not opened.keys and not opened.typed


@pytest.mark.parametrize('replace_original',[False,True])
def test_navigation_observes_new_same_browser_window_without_replaying_input(opened,replace_original):
    args={'action':'navigate','url':'https://example.com/account'}
    preview=opened.browser.preflight(args)
    opened.browser.desktop.elements={'old-tab':{'window_id':9}}
    original_keys=opened.browser.keys
    def keys(*value):
        original_keys(*value)
        if value==('enter',):
            if replace_original: opened.windows[:]=[item for item in opened.windows if item['id']!=9]
            opened.windows.append({'id':19,'pid':90,'title':'Account - a new title','process':'msedge.exe'})
            opened.foreground[0]=19
    opened.browser.keys=keys
    result=opened.browser.act(args,preview)
    assert result['verified'] and result['actual_url']==args['url']
    assert result['window_id']==19 and result['recovered_from_window_id']==9
    assert result['observation']['window']['title']=='Account - a new title'
    assert opened.keys.count(('enter',))==1 and opened.typed==[args['url']]
    assert opened.browser.desktop.elements=={}


def test_title_change_does_not_invalidate_browser_identity(opened):
    args={'action':'navigate','url':'https://example.com/inbox'}
    preview=opened.browser.preflight(args)
    opened.windows[1]['title']='Inbox (2) - a changing page title'
    result=opened.browser.act(args,preview)
    assert result['verified'] and result['window_id']==9 and result['recovered_from_window_id'] is None


@pytest.mark.parametrize('actual',[
    'https://login.example.net/sign-in',
    'https://example.com/report?account=other',
    'https://example.com:8443/report',
    'http://example.com/report',
])
def test_redirect_reports_actual_url_and_stays_unverified(opened,browser_clock,actual):
    args={'action':'navigate','url':'https://example.com/report'}
    opened.browser.address=lambda identity:actual
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['verified'] is False and result['redirected']
    assert result['navigation_status']=='redirected' and result['actual_url']==actual
    assert result['observation']['url']==actual and result['requested_url']==args['url']
    assert opened.typed==[args['url']] and opened.keys.count(('enter',))==1
    assert browser_clock[0]<6.2


def test_slow_navigation_waits_for_observed_url_and_only_submits_once(opened,browser_clock):
    url='https://example.com/slow'
    opened.browser.address=lambda identity:url if browser_clock[0]>=.6 else None
    args={'action':'navigate','url':url}
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['verified'] and .6<=browser_clock[0]<6
    assert opened.keys.count(('enter',))==1


def test_failed_final_observation_retains_delivered_input_without_false_success(opened):
    args={'action':'navigate','url':'https://example.com/report'}
    inspect=opened.browser.desktop.inspect
    calls=[]
    def final_inspection_fails(identity,cancel=None):
        calls.append(identity)
        if len(calls)>1: raise ValueError('Window disappeared during inspection')
        return inspect(identity,cancel)
    opened.browser.desktop.inspect=final_inspection_fails
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['performed']=='navigate' and result['verified'] is False
    assert result['actual_url'] is None and result['observation'] is None
    assert 'Window disappeared' in result['observation_error']
    assert opened.keys.count(('enter',))==1 and opened.typed==[args['url']]


def test_navigation_does_not_substitute_unrelated_browser_after_selected_one_closes(opened,browser_clock):
    args={'action':'navigate','url':'https://example.com/report'}
    original_keys=opened.browser.keys
    def keys(*value):
        original_keys(*value)
        if value==('enter',):
            opened.windows[:]=[item for item in opened.windows if item['id']!=9]
            opened.foreground[0]=8
    opened.browser.keys=keys
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['verified'] is False and result['observation'] is None
    assert result['actual_url'] is None and result['window_id']==9
    assert 'selected browser' in result['observation_error']
    assert opened.keys.count(('enter',))==1


def test_cancellation_during_navigation_observation_never_replays_input(opened,browser_clock):
    from jarvis.execution import Cancelled
    opened.browser.address=lambda identity:None
    class Cancellation:
        def check(self):
            if browser_clock[0]>=.3: raise Cancelled('Task cancelled')
    args={'action':'navigate','url':'https://example.com/slow'}
    with pytest.raises(Cancelled): opened.browser.act(args,opened.browser.preflight(args),Cancellation())
    assert opened.keys.count(('enter',))==1 and opened.typed==[args['url']]


def test_delivered_native_action_with_closed_window_returns_observation_failure(opened):
    args={'action':'scroll','direction':'down','amount':2}
    def scroll_then_close(native,preview,cancel=None):
        opened.native.append(native)
        opened.windows.clear()
        return {'performed':'scroll'}
    opened.browser.desktop.act=scroll_then_close
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['performed']=='scroll' and result['verified'] is False
    assert 'browser window changed' in result['observation_error']
    assert 'do not repeat the input' in result['note'] and len(opened.native)==1


def test_browser_click_observes_new_foreground_window_and_drops_old_controls(opened):
    args={'action':'click','element_id':'observed'}
    opened.browser.desktop.elements={'old-tab':{'window_id':9}}
    def opens_window(native,preview,cancel=None):
        opened.native.append(native)
        opened.windows.append({'id':19,'pid':90,'title':'Sign-in popup','process':'msedge.exe'})
        opened.foreground[0]=19
        return {'performed':'click','window_id':9}
    opened.browser.desktop.act=opens_window
    result=opened.browser.act(args,opened.browser.preflight(args))
    assert result['window_id']==9  # The receipt identifies where input was sent.
    assert result['observation']['window']['id']==19
    assert result['observation']['window_recovered'] and result['observation']['previous_window_id']==9
    assert opened.browser.window_id==19 and opened.browser.desktop.elements=={}
    assert len(opened.native)==1

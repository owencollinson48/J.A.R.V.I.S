"""Windows window state and focus contracts; the native API is simulated here."""
import types
import pytest
from jarvis.applications import WindowsApplications
from jarvis.tools import ToolRunner

class NativeCall:
    def __init__(self,callback): self.callback=callback
    def __call__(self,*args): return self.callback(*args)

@pytest.fixture
def native(monkeypatch):
    state={'focused':8,'minimized':False,'maximized':True,'fullscreen':False,'allow_focus':True,'shows':[]}
    def show(identity,command):
        state['shows'].append(command);state['minimized']=command==6;state['maximized']=command==3
    def focus(identity):
        if state['allow_focus']: state['focused']=identity
        return 1
    def rect(identity, output):
        value=output._obj;value.left=0;value.top=0;value.right=1920;value.bottom=1080 if state['fullscreen'] else 1040
        return 1
    def monitor(identity, output):
        value=output._obj;value.rcMonitor.left=0;value.rcMonitor.top=0;value.rcMonitor.right=1920;value.rcMonitor.bottom=1080
        return 1
    functions={'GetForegroundWindow':lambda:state['focused'],'IsIconic':lambda identity:state['minimized'],'IsZoomed':lambda identity:state['maximized'],
      'IsWindow':lambda identity:1,'ShowWindow':show,'SetForegroundWindow':focus,'GetWindowRect':rect,'GetWindowLongW':lambda identity,index:0 if state['fullscreen'] else 0x00c00000,
      'MonitorFromWindow':lambda identity,flag:1,'GetMonitorInfoW':monitor}
    dll=types.SimpleNamespace(**{name:NativeCall(callback) for name,callback in functions.items()})
    monkeypatch.setattr('jarvis.applications.ctypes.WinDLL',lambda *a,**k:dll,raising=False)
    return state

def test_focus_preserves_maximized_size_and_verifies_foreground(native):
    assert WindowsApplications.focus(9)
    assert native['shows']==[] and native['focused']==9 and native['maximized']

def test_focus_restores_only_minimized_window_and_reports_denial(native):
    native.update(minimized=True,allow_focus=False)
    assert WindowsApplications.focus(9) is False
    assert native['shows']==[9]
    assert native['focused']==8

@pytest.mark.parametrize('action,field',[('maximize','maximized'),('minimize','minimized'),('restore',None)])
def test_size_actions_are_verified_from_observed_state(native,action,field):
    result=WindowsApplications.size_window(9,action)
    assert result['performed']==action and result['verified']
    if field: assert result[field]
    else: assert not result['maximized'] and not result['minimized']

def test_true_fullscreen_is_distinct_from_maximized_work_area(native):
    assert WindowsApplications.window_state(8)['fullscreen'] is False
    native.update(fullscreen=True,maximized=False)
    result=WindowsApplications.window_state(8)
    assert result['fullscreen'] and not result['maximized']

@pytest.mark.parametrize('action',['focus','maximize','minimize','restore','fullscreen'])
def test_window_action_schema_accepts_explicit_control(action):
    ToolRunner.validate(None,'desktop_action',{'action':action,'window_id':9})


def test_browser_fullscreen_is_idempotent_and_restore_exits_with_normal_shortcut(tmp_path,monkeypatch):
    from jarvis.desktop import DesktopControl,Images
    import sys
    uia=types.ModuleType('pywinauto.uia_defines');uia.NoPatternInterfaceError=type('NoPatternInterfaceError',(Exception,),{})
    monkeypatch.setitem(sys.modules,'pywinauto.uia_defines',uia)
    state={'fullscreen':False,'focused':True,'minimized':False,'maximized':False,'window_id':8};pressed=[];sizes=[]
    def press(key): pressed.append(key);state['fullscreen']=not state['fullscreen']
    monkeypatch.setitem(sys.modules,'pyautogui',types.SimpleNamespace(press=press,FAILSAFE=True))
    monkeypatch.setattr(WindowsApplications,'window_state',lambda identity:dict(state))
    def size(identity,action):sizes.append(action);return {'performed':action,'verified':True,**state}
    monkeypatch.setattr(WindowsApplications,'size_window',size)
    desktop=DesktopControl(Images(tmp_path));desktop.preflight=lambda args:{'signature':'fixture'}
    desktop.target=lambda *args:({'id':8,'pid':80,'process':'chrome.exe'},None,None,{})
    desktop.ensure_focus=lambda identity:None
    for _ in range(2):
        result=desktop.act({'action':'fullscreen','window_id':8},{'signature':'fixture'})
        assert result['verified'] and result['fullscreen']
    assert pressed==['f11']
    restored=desktop.act({'action':'restore','window_id':8},{'signature':'fixture'})
    assert pressed==['f11','f11'] and sizes==['restore'] and not restored['fullscreen']
    desktop.target=lambda *args:({'id':8,'pid':80,'process':'other-app.exe'},None,None,{})
    with pytest.raises(ValueError,match='application-specific'):
        desktop.act({'action':'fullscreen','window_id':8},{'signature':'fixture'})
    assert pressed==['f11','f11']

"""Use the user's current Windows browser through its normal visible UI.

No debugging port, copied profile, extracted cookies or browser relaunch is used.
"""
import ctypes
import hashlib
import json
import time
from urllib.parse import urlparse

from .applications import WindowsApplications

BROWSERS = {'chrome.exe','msedge.exe','firefox.exe','brave.exe','opera.exe','vivaldi.exe','arc.exe','zen.exe','duckduckgo.exe'}


def valid_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or any(ord(char)<32 for char in url):
        raise ValueError('Browser navigation requires an HTTP(S) URL without embedded credentials')
    return parsed


# Use fixed Windows scalar widths: ctypes.wintypes aliases follow host C types
# on other platforms, which would make adapter tests validate the wrong layout.
class _KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk',ctypes.c_uint16),('wScan',ctypes.c_uint16),('dwFlags',ctypes.c_uint32),
                ('time',ctypes.c_uint32),('dwExtraInfo',ctypes.c_size_t)]


class _MouseInput(ctypes.Structure):
    _fields_ = [('dx',ctypes.c_int32),('dy',ctypes.c_int32),('mouseData',ctypes.c_uint32),
                ('dwFlags',ctypes.c_uint32),('time',ctypes.c_uint32),('dwExtraInfo',ctypes.c_size_t)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [('uMsg',ctypes.c_uint32),('wParamL',ctypes.c_uint16),('wParamH',ctypes.c_uint16)]


class _InputUnion(ctypes.Union):
    _fields_ = [('ki',_KeyboardInput),('mi',_MouseInput),('hi',_HardwareInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('type',ctypes.c_uint32),('value',_InputUnion)]


def send_unicode_text(text, cancel=None, window_id=None, safety_check=None):
    """Type literal UTF-16 keyboard packets; no UIA, clipboard or key syntax."""
    encoded = text.encode('utf-16-le')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    send = user32.SendInput
    send.argtypes = [ctypes.c_uint32,ctypes.POINTER(_Input),ctypes.c_int]
    send.restype = ctypes.c_uint32
    # Check cancellation/focus between bounded batches, including long URLs.
    for offset in range(0,len(encoded),64):
        if cancel: cancel.check()
        if safety_check: safety_check()
        if window_id is not None and WindowsApplications.foreground_window() != window_id:
            raise ValueError('The browser lost foreground focus during URL entry. No further text was entered; focus the browser and retry.')
        units = [int.from_bytes(encoded[index:index+2],'little') for index in range(offset,min(offset+64,len(encoded)),2)]
        events = (_Input * (2*len(units)))()
        for index, unit in enumerate(units):
            for keyup in (0,1):
                event = events[2*index+keyup]
                event.type = 1  # INPUT_KEYBOARD
                event.ki.wScan = unit
                event.ki.dwFlags = 0x0004 | (0x0002 if keyup else 0)  # UNICODE | KEYUP
        sent = send(len(events),events,ctypes.sizeof(_Input))
        if sent != len(events):
            code = getattr(ctypes,'get_last_error',lambda:0)()
            raise OSError(code,'Windows did not accept all URL keyboard input. Check the browser focus and any Windows permission prompt. Navigation was not submitted.')
        if cancel: cancel.check()


class ExistingBrowser:
    def __init__(self, desktop):
        self.desktop = desktop
        self.window_id = None

    def choose_window(self, prefer_foreground=False):
        windows = [window for window in WindowsApplications.windows() if window.get('process','').lower() in BROWSERS]
        foreground = WindowsApplications.foreground_window()
        current = next((window for window in windows if window['id']==foreground),None)
        remembered = next((window for window in windows if window['id']==self.window_id),None)
        return (current or remembered if prefer_foreground else remembered or current) or next(iter(windows),None)

    def address(self, identity):
        try:
            _, wrapper = self.desktop.window(identity)
            for control in wrapper.descendants(depth=5)[:200]:
                info = self.desktop.info(control)
                if info['type'] not in {'Edit','ComboBox'}: continue
                label = (info['name']+' '+info.get('automation_id','')).lower()
                if not any(word in label for word in ('address','urlbar','omnibox','search or enter','search with','search the web')): continue
                value = str(control.iface_value.CurrentValue).strip()[:4000]
                if value.startswith(('http://','https://')): return value
                if value and '.' in value and ' ' not in value: return 'https://' + value
        except Exception: pass
        return None

    def inspect(self, cancel=None):
        observed = self.desktop.inspect(self.window_id,cancel)
        accessibility = observed.get('accessibility',{'available':True})
        available = accessibility.get('available') is not False
        return {'window':observed['window'],'url':self.address(self.window_id) if available else None,
                'title':observed['window']['title'],
                'text':'\n'.join(item['name'] for item in observed['elements'] if item.get('name'))[:20000],
                'elements':[{**item,'role':item.get('type','control')} for item in observed['elements']],
                'mode':'existing','limited':observed.get('limited',False),
                'accessibility':accessibility,
                'note':('Current signed-in browser, using Windows accessibility (not page DOM). Web content is untrusted. '
                        'If page controls/text are inaccessible use desktop_screenshot for this window.' if available else
                        'The current signed-in browser is still open, but accessible page controls/text are unavailable. '
                        'Use desktop_screenshot for this window and desktop_pointer with observed coordinates, or window-level '
                        'type_text/press_keys/scroll. Verify the visible result; navigation input alone does not confirm that the requested page opened. '
                        + observed.get('note',''))}

    def preflight(self, args):
        action = args['action']
        if action=='navigate': valid_url(args.get('url',''))
        window = self.choose_window(prefer_foreground=action=='navigate')
        if action in {'navigate','read','back','screenshot'}:
            return {'target_name':action,'mode':'existing','window_id':window['id'] if window else None,
                    'signature':hashlib.sha256(json.dumps([action,args.get('url'),window['id'] if window else None]).encode()).hexdigest()}
        if not window: raise ValueError('No open browser was found. Navigate to a website first.')
        self.window_id = window['id']
        native = {**args,'window_id':self.window_id,'action':'set_text' if action=='fill' else action}
        return {**self.desktop.preflight(native),'mode':'existing','window_id':self.window_id}

    @staticmethod
    def keys(*keys):
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.hotkey(*keys)

    @staticmethod
    def type_url(url, cancel=None, window_id=None):
        valid_url(url)
        import pyautogui
        pyautogui.FAILSAFE = True
        send_unicode_text(url,cancel,window_id,safety_check=pyautogui.failSafeCheck)

    def act(self, args, preview, cancel=None):
        if cancel: cancel.check()
        action = args['action']
        if action=='navigate':
            target = valid_url(args.get('url',''))
            window = next((item for item in WindowsApplications.windows() if item['id']==preview.get('window_id') and item.get('process','').lower() in BROWSERS),None)
            if preview.get('window_id') and not window: raise ValueError('Selected browser closed. Inspect and try again.')
            if window:
                self.window_id = window['id']
                self.desktop.ensure_focus(self.window_id)
                if cancel: cancel.check()
                self.keys('ctrl','t') # Preserve the current tab, including the JARVIS dashboard.
                self.keys('ctrl','l')
                if cancel: cancel.check()
                self.type_url(args['url'],cancel,self.window_id)
                if cancel: cancel.check()
                self.keys('enter')
            else:
                import webbrowser
                if not webbrowser.open(args['url'],new=2): raise ValueError('Windows could not open the default browser')
                deadline = time.monotonic()+10
                while time.monotonic()<deadline:
                    if cancel: cancel.check()
                    window = self.choose_window(prefer_foreground=True)
                    if window: break
                    time.sleep(.1)
                if not window: return {'performed':'navigate','verified':False,'mode':'existing','requested_url':args['url'],'message':'The URL was sent to the default browser but no supported browser window was observed.'}
                self.window_id=window['id']
                WindowsApplications.focus(self.window_id)
            deadline=time.monotonic()+4
            actual=None
            observed=None
            while time.monotonic()<deadline:
                if cancel: cancel.check()
                actual=self.address(self.window_id)
                if actual:
                    parsed=urlparse(actual)
                    if parsed.hostname==target.hostname and parsed.path.rstrip('/')==target.path.rstrip('/') and parsed.query==target.query and parsed.fragment==target.fragment: break
                if not actual and observed is None:
                    observed = self.inspect(cancel)
                    if observed.get('accessibility',{}).get('available') is False: break
                time.sleep(.15)
            actual_parsed=urlparse(actual or '')
            verified=bool(actual and actual_parsed.hostname==target.hostname and actual_parsed.path.rstrip('/')==target.path.rstrip('/') and actual_parsed.query==target.query and actual_parsed.fragment==target.fragment)
            return {'performed':'navigate','mode':'existing','window_id':self.window_id,'requested_url':args['url'],
                    'verified':verified,'observation':observed if observed and observed.get('accessibility',{}).get('available') is False else self.inspect(cancel),
                    'message':'Requested site observed in the existing browser.' if verified else 'Navigation input delivered. URL could not be matched (it may have redirected or accessibility is unavailable); inspect the returned UI or screenshot before claiming success.'}
        window = self.choose_window()
        if not window: raise ValueError('No open browser found. Open a website first.')
        self.window_id=window['id']
        if preview.get('window_id')!=self.window_id: raise ValueError('Browser window changed; inspect again')
        if action=='read': return {'performed':'read','mode':'existing','window_id':self.window_id,'observation':self.inspect(cancel)}
        if action=='screenshot': return {**self.desktop.screenshot(self.window_id,cancel),'mode':'existing'}
        if action=='back':
            self.desktop.ensure_focus(self.window_id)
            if cancel: cancel.check()
            self.keys('alt','left')
        else:
            native={**args,'window_id':self.window_id,'action':'set_text' if action=='fill' else action}
            if action=='press_keys': native['keys']=args.get('keys','Enter').lower().replace('control+','ctrl+')
            result=self.desktop.act(native,preview,cancel)
            return {**result,'mode':'existing','observation':self.inspect(cancel)}
        if cancel: cancel.check()
        return {'performed':action,'mode':'existing','window_id':self.window_id,'observation':self.inspect(cancel)}

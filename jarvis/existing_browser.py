"""Use the user's current Windows browser through its normal visible UI.

No debugging port, copied profile, extracted cookies or browser relaunch is used.
"""
import ctypes
import hashlib
import json
import time
from urllib.parse import urlparse

from .applications import WindowsApplications
from .execution import Cancelled

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
        self.window_identity = None

    @staticmethod
    def identity(window):
        return (window['id'],window.get('pid'),window.get('process','').lower())

    def invalidate_elements(self, window_id):
        # A new tab or replacement window must never inherit old page controls.
        invalidate_captures = getattr(self.desktop,'invalidate_captures',None)
        if invalidate_captures: invalidate_captures(window_id)
        elements = getattr(self.desktop,'elements',None)
        if isinstance(elements,dict):
            for token in list(elements):
                if elements[token].get('window_id')==window_id: elements.pop(token,None)

    def remember(self, window):
        identity = self.identity(window)
        if self.window_identity and self.window_identity!=identity:
            self.invalidate_elements(self.window_identity[0])
        self.window_id, self.window_identity = window['id'],identity

    def choose_window(self, prefer_foreground=False):
        windows = [window for window in WindowsApplications.windows() if window.get('process','').lower() in BROWSERS]
        foreground = WindowsApplications.foreground_window()
        current = next((window for window in windows if window['id']==foreground),None)
        remembered = next((window for window in windows if window['id']==self.window_id and
                           (not self.window_identity or self.identity(window)==self.window_identity)),None)
        if prefer_foreground: return current or remembered or next(iter(windows),None)
        if remembered: return remembered
        # A browser may replace its top-level window during an update, redirect
        # or sign-in. Recover reads in that browser before choosing another app.
        family = self.window_identity[2] if self.window_identity else None
        related = [window for window in windows if window.get('process','').lower()==family]
        return next((window for window in related if window['id']==foreground),None) or next(iter(related),None) or current or next(iter(windows),None)

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

    def inspect(self, cancel=None, window=None):
        if cancel: cancel.check()
        previous = self.window_id
        if window is not None:
            if not any(self.identity(item)==self.identity(window) for item in WindowsApplications.windows()):
                raise ValueError('The browser window changed before it could be inspected.')
        else: window = self.choose_window()
        if not window: raise ValueError('No supported browser window is currently visible. Navigation input is not evidence that a page opened.')
        self.remember(window)
        observed = self.desktop.inspect(self.window_id,cancel)
        accessibility = observed.get('accessibility',{'available':True})
        available = accessibility.get('available') is not False
        address = self.address(self.window_id) if available else None
        if cancel: cancel.check()
        return {'window':observed['window'],'url':address,
                'window_recovered':previous is not None and previous!=self.window_id,
                'previous_window_id':previous if previous!=self.window_id else None,
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
                    'window_identity':self.identity(window) if window else None,
                    'signature':hashlib.sha256(json.dumps([action,args.get('url'),self.identity(window) if window else None]).encode()).hexdigest()}
        if not window: raise ValueError('No open browser was found. Navigate to a website first.')
        self.remember(window)
        native = {**args,'window_id':self.window_id,'action':'set_text' if action=='fill' else action}
        return {**self.desktop.preflight(native),'mode':'existing','window_id':self.window_id}

    @staticmethod
    def url_matches(actual, target):
        if not actual: return False
        try:
            parsed = urlparse(actual)
            def port(value): return value.port or (443 if value.scheme=='https' else 80)
            return (parsed.scheme,parsed.hostname,port(parsed),parsed.path.rstrip('/'),parsed.query,parsed.fragment)==(
                target.scheme,target.hostname,port(target),target.path.rstrip('/'),target.query,target.fragment)
        except ValueError: return False

    def navigation_window(self, original, before):
        windows = [window for window in WindowsApplications.windows() if window.get('process','').lower() in BROWSERS]
        foreground = WindowsApplications.foreground_window()
        family = original.get('process','').lower() if original else None
        related = [window for window in windows if not family or window.get('process','').lower()==family]
        current = next((window for window in related if window['id']==foreground),None)
        # Only follow a newly created foreground window in the same browser;
        # never silently observe another unrelated existing browser after input.
        if current and self.identity(current) not in before: return current
        remembered = next((window for window in related if original and self.identity(window)==self.identity(original)),None)
        return remembered or current or next(iter(related),None)

    def post_observation(self, cancel=None, window=None):
        try: return {'observation':self.inspect(cancel,window)}
        except Cancelled: raise
        except Exception as exc:
            return {'observation_error':f'{type(exc).__name__}: {str(exc)[:600]}',
                    'verified':False,
                    'note':'The input was delivered, but its result could not be observed. Re-inspect the current browser or capture its window; do not repeat the input or claim completion from this result.'}

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
            initial_windows = [item for item in WindowsApplications.windows() if item.get('process','').lower() in BROWSERS]
            before = {self.identity(item) for item in initial_windows}
            window = next((item for item in initial_windows if item['id']==preview.get('window_id')),None)
            if preview.get('window_id') and not window: raise ValueError('Selected browser closed. Inspect and try again.')
            if window and preview.get('window_identity') and self.identity(window)!=tuple(preview['window_identity']):
                raise ValueError('Selected browser window changed identity. Inspect again before navigating.')
            if window:
                self.remember(window)
                self.desktop.ensure_focus(self.window_id)
                if cancel: cancel.check()
                try:
                    self.keys('ctrl','t') # Preserve the current tab, including the JARVIS dashboard.
                    self.keys('ctrl','l')
                    if cancel: cancel.check()
                    self.type_url(args['url'],cancel,self.window_id)
                    if cancel: cancel.check()
                    self.keys('enter')
                finally:
                    self.invalidate_elements(self.window_id)
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
                self.remember(window)
                WindowsApplications.focus(self.window_id)
            original = window
            deadline=time.monotonic()+6
            actual=None
            observed=None
            observation_error=None
            while time.monotonic()<deadline:
                if cancel: cancel.check()
                candidate = self.navigation_window(original,before)
                if candidate:
                    changed = self.window_identity!=self.identity(candidate)
                    self.remember(candidate)
                    try:
                        if observed is None or changed:
                            observed = self.inspect(cancel,candidate)
                            actual = observed.get('url')
                            if observed.get('accessibility',{}).get('available') is False: break
                        else: actual=self.address(self.window_id)
                    except Cancelled: raise
                    except Exception as exc:
                        observation_error=f'{type(exc).__name__}: {str(exc)[:600]}'
                        # The window may have been replaced between enumeration
                        # and inspection. Retry observation only, never navigation.
                        observed=None
                        actual=None
                    if self.url_matches(actual,target): break
                time.sleep(.15)
            candidate = self.navigation_window(original,before)
            if candidate:
                self.remember(candidate)
                if not (observed and observed.get('window',{}).get('id')==candidate['id'] and
                        observed.get('accessibility',{}).get('available') is False):
                    fresh = self.post_observation(cancel,candidate)
                    observed = fresh.get('observation')
                    observation_error = fresh.get('observation_error')
                actual = observed.get('url') if observed else None
            else:
                observed=None
                actual=None
                observation_error='No window in the selected browser is currently visible.'
            verified=bool(observed and self.url_matches(actual,target))
            status='verified' if verified else 'redirected' if actual else 'unverified'
            return {'performed':'navigate','mode':'existing','window_id':self.window_id,'requested_url':args['url'],
                    'actual_url':actual,'navigation_status':status,'redirected':bool(actual and not verified),
                    'verified':verified,'observation':observed,'observation_error':observation_error,
                    'recovered_from_window_id':original['id'] if original and self.window_id!=original['id'] else None,
                    'message':'Requested site observed in the existing browser.' if verified else
                              'Navigation input delivered. A different URL is visible; inspect the returned page before deciding whether the requested work completed.' if actual else
                              'Navigation input delivered. The requested page could not be verified; inspect the returned UI or screenshot before claiming success. Do not repeat navigation just because observation was unavailable.'}
        window = self.choose_window()
        if not window: raise ValueError('No open browser found. Open a website first.')
        previous = self.window_id
        self.remember(window)
        if action not in {'read','screenshot'} and preview.get('window_id')!=self.window_id: raise ValueError('Browser window changed; inspect again')
        if action=='read':
            observation=self.inspect(cancel)
            if preview.get('window_id')!=self.window_id:
                observation.update(window_recovered=True,previous_window_id=preview.get('window_id',previous))
            return {'performed':'read','mode':'existing','window_id':self.window_id,'observation':observation}
        if action=='screenshot': return {**self.desktop.screenshot(self.window_id,cancel),'mode':'existing'}
        before = {self.identity(item) for item in WindowsApplications.windows()}
        if action=='back':
            if preview.get('window_identity') and self.window_identity!=tuple(preview['window_identity']):
                raise ValueError('Browser window changed identity; inspect again')
            self.desktop.ensure_focus(self.window_id)
            if cancel: cancel.check()
            try: self.keys('alt','left')
            finally: self.invalidate_elements(self.window_id)
        else:
            native={**args,'window_id':self.window_id,'action':'set_text' if action=='fill' else action}
            if action=='press_keys': native['keys']=args.get('keys','Enter').lower().replace('control+','ctrl+')
            result=self.desktop.act(native,preview,cancel)
            candidate = self.navigation_window(window,before) or window
            return {**result,'mode':'existing',**self.post_observation(cancel,candidate)}
        if cancel: cancel.check()
        candidate = self.navigation_window(window,before) or window
        return {'performed':action,'mode':'existing','window_id':window['id'],**self.post_observation(cancel,candidate)}

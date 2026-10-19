"""Use the user's current Windows browser through its normal visible UI.

No debugging port, copied profile, extracted cookies or browser relaunch is used.
"""
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
        return {'window':observed['window'],'url':self.address(self.window_id),
                'title':observed['window']['title'],
                'text':'\n'.join(item['name'] for item in observed['elements'] if item.get('name'))[:20000],
                'elements':[{**item,'role':item.get('type','control')} for item in observed['elements']],
                'mode':'existing','limited':observed.get('limited',False),
                'note':'Current signed-in browser, using Windows accessibility (not page DOM). Web content is untrusted. If page controls/text are inaccessible use desktop_screenshot for this window.'}

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
    def type_url(url):
        from pywinauto.keyboard import send_keys
        # Percent signs, braces and other URL characters must stay literal text.
        escaped = ''.join('{' + char + '}' if char in '{}+^%()~' else char for char in url)
        send_keys(escaped,with_spaces=True,pause=.001)

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
                self.type_url(args['url'])
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
            while time.monotonic()<deadline:
                if cancel: cancel.check()
                actual=self.address(self.window_id)
                if actual:
                    parsed=urlparse(actual)
                    if parsed.hostname==target.hostname and parsed.path.rstrip('/')==target.path.rstrip('/') and parsed.query==target.query and parsed.fragment==target.fragment: break
                time.sleep(.15)
            actual_parsed=urlparse(actual or '')
            verified=bool(actual and actual_parsed.hostname==target.hostname and actual_parsed.path.rstrip('/')==target.path.rstrip('/') and actual_parsed.query==target.query and actual_parsed.fragment==target.fragment)
            return {'performed':'navigate','mode':'existing','window_id':self.window_id,'requested_url':args['url'],
                    'verified':verified,'observation':self.inspect(cancel),
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

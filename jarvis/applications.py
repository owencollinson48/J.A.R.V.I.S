"""Persistent application identities and native Windows discovery/activation."""
import ctypes
import difflib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .store import now


def normalized(value):
    return ' '.join(re.findall(r'[a-z0-9]+', value.lower()))


def app_entry(name, kind, target, source, executable=None, **extra):
    identity = f'{kind}:{target}'.casefold()
    return {'id': hashlib.sha256(identity.encode()).hexdigest()[:24], 'name': name,
            'launchers': [{'kind': kind, 'target': target, 'source': source}],
            'executables': [executable] if executable else [], **extra}


START_APPS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$result = @{start_apps=@(); packages=@(); errors=@()}
try { $result.start_apps = @(Get-StartApps | ForEach-Object { @{name=$_.Name; aumid=$_.AppID} }) }
catch { $result.errors += 'Get-StartApps unavailable' }
try {
  foreach ($package in Get-AppxPackage) {
    try {
      $manifest = Get-AppxPackageManifest -Package $package.PackageFullName
      foreach ($app in $manifest.Package.Applications.Application) {
        $result.packages += @{name=$package.Name; aumid=($package.PackageFamilyName+'!'+$app.Id);
          executable=[string]$app.Executable; location=$package.InstallLocation;
          protocols=@($app.SelectNodes('.//*[local-name()="Protocol"]') | ForEach-Object { [string]$_.Name })}
      }
    } catch { }
  }
} catch { $result.errors += 'Windows package enumeration unavailable' }
$result | ConvertTo-Json -Depth 6 -Compress
'''


class WindowsApplications:
    """All Windows-only imports are lazy so the rest of JARVIS stays portable."""
    supported = platform.system() == 'Windows'

    @staticmethod
    def powershell(script, timeout=30):
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script],
                                capture_output=True, timeout=timeout, encoding='utf-8', errors='replace',
                                creationflags=0x08000000 if os.name == 'nt' else 0)
        if result.returncode:
            raise ValueError('Windows discovery command failed; PowerShell may be restricted by your PC')
        return json.loads(result.stdout.lstrip('\ufeff'))

    def registry_entries(self):
        import winreg
        entries, errors, steam_paths = [], [], []
        views = [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]
        def subkeys(key):
            index = 0
            while True:
                try: yield winreg.EnumKey(key, index)
                except OSError: break
                index += 1
        def value(key, name=''):
            try: return winreg.QueryValueEx(key, name)[0]
            except OSError: return ''
        for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            for view in views:
                for base in [r'Software\Microsoft\Windows\CurrentVersion\App Paths',
                             r'Software\Microsoft\Windows\CurrentVersion\Uninstall']:
                    try:
                        with winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view) as parent:
                            for key_name in subkeys(parent):
                                try:
                                    with winreg.OpenKey(parent, key_name) as key:
                                        if base.endswith('App Paths'):
                                            path = os.path.expandvars(str(value(key))).strip('"')
                                            label = Path(key_name).stem
                                        else:
                                            label = str(value(key, 'DisplayName'))
                                            icon = str(value(key, 'DisplayIcon'))
                                            match = re.match(r'"([^"]+\.exe)"|(.+?\.exe)(?:,|$)', icon, re.I)
                                            path = os.path.expandvars(next((g for g in match.groups() if g), '')) if match else ''
                                        if path and Path(path).is_file():
                                            entries.append(app_entry('Outlook (classic)' if Path(path).name.lower() == 'outlook.exe' else label or Path(path).stem, 'exe', path, 'registry', path))
                                except OSError: continue
                    except FileNotFoundError: pass
                    except PermissionError: errors.append('Some application registry registrations are inaccessible')
                try:
                    with winreg.OpenKey(hive, r'Software\Classes', 0, winreg.KEY_READ | view) as parent:
                        for scheme in subkeys(parent):
                            if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9+.-]{1,63}', scheme): continue
                            if scheme.lower() in {'http','https','file','mailto','tel','data','javascript','ms-settings','windowsdefender'}: continue
                            try:
                                with winreg.OpenKey(parent, scheme) as key:
                                    try: winreg.QueryValueEx(key, 'URL Protocol')
                                    except OSError: continue
                                    label = {'outlookforwindows':'Outlook (new)', 'steam':'Steam', 'spotify':'Spotify'}.get(scheme.lower()) or str(value(key)).removeprefix('URL:') or scheme
                                    entry = app_entry(label, 'uri', scheme + ':', 'registered protocol')
                                    try:
                                        with winreg.OpenKey(key, r'shell\open\command') as command_key:
                                            command = str(value(command_key))
                                            match = re.match(r'"([^"\n]+\.exe)"|([^"\n]+?\.exe)(?:\s|$)',command,re.I)
                                            target = os.path.expandvars(next((part for part in match.groups() if part),'')) if match else ''
                                            if target and Path(target).is_file(): entry['executables'].append(target)
                                    except OSError: pass
                                    entries.append(entry)
                            except OSError: continue
                except FileNotFoundError: pass
                except PermissionError: errors.append('Some URI registrations are inaccessible')
                try:
                    with winreg.OpenKey(hive, r'Software\Valve\Steam', 0, winreg.KEY_READ | view) as key:
                        path = str(value(key, 'SteamPath') or value(key, 'InstallPath'))
                        if path: steam_paths.append(Path(path))
                except OSError: pass
        return entries, errors, steam_paths

    @staticmethod
    def steam_entries(roots):
        libraries = set(roots)
        for root in list(libraries):
            file = root / 'steamapps' / 'libraryfolders.vdf'
            try:
                if file.is_file():
                    for path in re.findall(r'"path"\s+"([^"]+)"', file.read_text(encoding='utf-8', errors='replace')):
                        libraries.add(Path(path.replace('\\\\', '\\')))
            except OSError: continue
        entries = []
        for library in libraries:
            for manifest in (library / 'steamapps').glob('appmanifest_*.acf'):
                try:
                    text = manifest.read_text(encoding='utf-8')
                    name, app_id = re.search(r'"name"\s+"([^"]+)"', text), re.search(r'"appid"\s+"(\d+)"', text)
                    if name and app_id:
                        entries.append(app_entry(name[1], 'uri', 'steam://rungameid/' + app_id[1], 'Steam library'))
                except OSError: continue
        return entries

    def discover(self, directories):
        if not self.supported:
            return [], ['Windows application discovery requires Windows']
        entries, errors, steam_paths = self.registry_entries()
        try:
            data = self.powershell(START_APPS_SCRIPT)
            errors.extend(data.get('errors', []))
            for app in data.get('start_apps', []):
                target = app['aumid']
                if target.lower().endswith('.exe') and Path(target).is_file():
                    entries.append(app_entry(app['name'], 'exe', target, 'Get-StartApps', target))
                else: entries.append(app_entry(app['name'], 'aumid', target, 'Get-StartApps'))
            for app in data.get('packages', []):
                executable = str(Path(app['location']) / app['executable']) if app.get('executable') else None
                entry = app_entry('Outlook (new)' if app['name'].lower() == 'microsoft.outlookforwindows' else app['name'], 'aumid', app['aumid'], 'Windows package', executable)
                for protocol in app.get('protocols',[]):
                    if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9+.-]{1,63}',protocol) and protocol.lower() not in {'http','https','file','mailto','tel','data','javascript'}:
                        entry['launchers'].append({'kind':'uri','target':protocol + ':','source':'Windows package protocol'})
                entries.append(entry)
        except (OSError, ValueError, subprocess.TimeoutExpired): errors.append('Start apps/package discovery did not complete')
        menu_dirs = [Path(os.getenv('APPDATA', '')) / 'Microsoft/Windows/Start Menu/Programs',
                     Path(os.getenv('PROGRAMDATA', r'C:\ProgramData')) / 'Microsoft/Windows/Start Menu/Programs']
        shell = None
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            shell = win32com.client.Dispatch('WScript.Shell')
        except Exception: errors.append('Shortcut target metadata unavailable; shortcuts can still be activated')
        for folder in menu_dirs:
            try:
                for shortcut in folder.rglob('*.lnk'):
                    entry = app_entry(shortcut.stem, 'shortcut', str(shortcut), 'Start Menu')
                    if shell:
                        try:
                            details = shell.CreateShortCut(str(shortcut))
                            target = os.path.expandvars(str(details.TargetPath))
                            if target:
                                entry['executables'].append(target)
                                entry['launchers'][0]['executable'] = target
                                entry['launchers'][0]['has_arguments'] = bool(details.Arguments)
                        except Exception: pass
                    entries.append(entry)
            except OSError: errors.append('A Start Menu folder could not be read')
        if shell: pythoncom.CoUninitialize()
        for folder in os.get_exec_path():
            try:
                for executable in Path(folder).glob('*.exe'):
                    entries.append(app_entry(executable.stem, 'exe', str(executable), 'PATH', str(executable)))
            except OSError: continue
        deadline = time.monotonic() + 15
        for folder in directories:
            count = 0
            for base, subdirs, files in os.walk(folder, followlinks=False, onerror=lambda error: errors.append('An application folder could not be read: ' + str(error.filename))):
                depth = len(Path(base).relative_to(folder).parts)
                if depth >= 4: subdirs.clear()
                subdirs[:] = [d for d in subdirs if not d.startswith('.') and not Path(base, d).is_symlink()]
                for name in files:
                    if name.lower().endswith(('.exe', '.lnk')):
                        path = Path(base, name).resolve()
                        if not path.is_relative_to(Path(folder).resolve()): continue
                        kind = 'shortcut' if path.suffix.lower() == '.lnk' else 'exe'
                        entries.append(app_entry(path.stem, kind, str(path), 'approved application folder', str(path) if kind == 'exe' else None))
                    count += 1
                if count > 10000 or time.monotonic() > deadline:
                    errors.append('Application folder scan reached its time/file limit; narrow the configured folders')
                    break
        entries.extend(self.steam_entries(steam_paths))
        epic = Path(os.getenv('PROGRAMDATA', r'C:\ProgramData')) / 'Epic/EpicGamesLauncher/Data/Manifests'
        for manifest in epic.glob('*.item'):
            try:
                app = json.loads(manifest.read_text(encoding='utf-8'))
                identity = app.get('AppName', '')
                if re.fullmatch(r'[\w:-]+', identity) and app.get('DisplayName'):
                    entries.append(app_entry(app['DisplayName'], 'uri', f'com.epicgames.launcher://apps/{identity}?action=launch&silent=true', 'Epic library'))
            except (OSError, ValueError): continue
        return entries, list(dict.fromkeys(errors))

    @staticmethod
    def windows():
        if platform.system() != 'Windows': return []
        import psutil
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        result = []
        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd): return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0: return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                process = psutil.Process(pid.value)
                executable, name = process.exe(), process.name()
            except (psutil.Error, OSError): executable, name = '', ''
            result.append({'id': int(hwnd), 'title': title.value, 'pid': pid.value, 'executable': executable, 'process': name})
            return True
        user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        user32.EnumWindows(callback, 0)
        return result

    @staticmethod
    def foreground_window():
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        return int(user32.GetForegroundWindow() or 0)

    @staticmethod
    def window_state(window_id):
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        for name in ('IsIconic','IsZoomed','IsWindow'):
            getattr(user32,name).argtypes = [wintypes.HWND]
        user32.GetWindowRect.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowLongW.argtypes = [wintypes.HWND,ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.MonitorFromWindow.argtypes = [wintypes.HWND,wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
        if not user32.IsWindow(window_id): raise ValueError('Window no longer exists')
        rect = wintypes.RECT()
        if not user32.GetWindowRect(window_id,ctypes.byref(rect)): raise ValueError('Window bounds are unavailable')
        monitor = MonitorInfo(); monitor.cbSize = ctypes.sizeof(MonitorInfo)
        has_monitor = user32.GetMonitorInfoW(user32.MonitorFromWindow(window_id,2),ctypes.byref(monitor))
        bounds = [rect.left,rect.top,rect.right,rect.bottom]
        fills_monitor = bool(has_monitor and all(abs(a-b)<=3 for a,b in zip(bounds,[monitor.rcMonitor.left,monitor.rcMonitor.top,monitor.rcMonitor.right,monitor.rcMonitor.bottom])))
        caption = bool(user32.GetWindowLongW(window_id,-16) & 0x00c00000)
        return {'window_id':window_id,'focused':WindowsApplications.foreground_window()==window_id,
                'minimized':bool(user32.IsIconic(window_id)),'maximized':bool(user32.IsZoomed(window_id)),
                'fullscreen':fills_monitor and not caption,'bounds':bounds}

    @staticmethod
    def focus(window_id):
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        # Restore only a minimized window; bringing a maximized app forward must
        # preserve its size. Windows still decides whether foreground focus is allowed.
        if user32.IsIconic(window_id): user32.ShowWindow(window_id,9)
        user32.SetForegroundWindow(window_id)
        return WindowsApplications.foreground_window() == window_id

    @staticmethod
    def size_window(window_id, action):
        from ctypes import wintypes
        commands = {'maximize':3,'minimize':6,'restore':9}
        if action not in commands: raise ValueError('Unsupported window size action')
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND,ctypes.c_int]
        user32.ShowWindow(window_id,commands[action])
        if action != 'minimize': WindowsApplications.focus(window_id)
        deadline = time.monotonic()+2
        while True:
            state = WindowsApplications.window_state(window_id)
            verified = state['maximized'] if action=='maximize' else state['minimized'] if action=='minimize' else not state['minimized'] and not state['maximized']
            if verified or time.monotonic()>=deadline: return {'performed':action,'verified':verified,**state}
            time.sleep(.05)

    @staticmethod
    def activate(launcher):
        target, kind = launcher['target'], launcher['kind']
        if not target or any(ord(c) < 32 for c in target): raise ValueError('Invalid indexed launch target')
        if kind == 'exe':
            if not Path(target).is_file(): raise FileNotFoundError('Indexed executable no longer exists')
            return subprocess.Popen([target], cwd=str(Path(target).parent), shell=False).pid
        if kind == 'shortcut':
            if not Path(target).is_file(): raise FileNotFoundError('Indexed shortcut no longer exists')
            os.startfile(target)
        elif kind == 'aumid':
            # Explorer's AppsFolder activation supports packaged and registered Win32 AUMIDs.
            explorer = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'explorer.exe'
            subprocess.Popen([str(explorer), 'shell:AppsFolder\\' + target], shell=False)
        elif kind == 'uri':
            if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target): raise ValueError('Invalid registered protocol')
            os.startfile(target)
        else: raise ValueError('Unsupported launch method')
        return None


class ApplicationIndex:
    def __init__(self, store, settings, adapter=None):
        self.store, self.settings = store, settings
        self.adapter = adapter or WindowsApplications()
        self.lock = threading.RLock()
        self.last_refresh = 0
        store.execute('CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY,name TEXT,payload TEXT,updated_at TEXT)')
        store.execute('CREATE TABLE IF NOT EXISTS application_aliases(alias TEXT PRIMARY KEY,application_id TEXT)')
        store.execute('CREATE TABLE IF NOT EXISTS application_launches(id INTEGER PRIMARY KEY,application_id TEXT,method TEXT,verified INTEGER,created_at TEXT)')

    def refresh(self, force=False):
        with self.lock:
            if not force and time.time() - self.last_refresh < 900:
                return {'count': self.count(), 'cached': True, 'warnings': []}
            entries, errors = self.adapter.discover(self.settings.values.get('application_directories', []))
            merged, name_map, identity_map = {}, {}, {}
            for entry in entries:
                identities = list(dict.fromkeys(identity_map[(m['kind'],m['target'].casefold())] for m in entry['launchers'] if (m['kind'],m['target'].casefold()) in identity_map))
                named = name_map.get(normalized(entry['name']))
                if named and named not in identities: identities.append(named)
                identity = identities[0] if identities else entry['id']
                for other in identities[1:]:
                    old = merged.pop(other)
                    merged[identity]['launchers'].extend(method for method in old['launchers'] if method not in merged[identity]['launchers'])
                    merged[identity]['executables'] = list(dict.fromkeys(merged[identity]['executables'] + old['executables']))
                    identity_map = {key:identity if value==other else value for key,value in identity_map.items()}
                    name_map = {key:identity if value==other else value for key,value in name_map.items()}
                app = merged.setdefault(identity, {**entry, 'id': identity, 'launchers': [], 'executables': []})
                for method in entry['launchers']:
                    if method not in app['launchers']: app['launchers'].append(method)
                    identity_map[(method['kind'], method['target'].casefold())] = identity
                app['executables'] = list(dict.fromkeys(app['executables'] + entry.get('executables', [])))
                name_map[normalized(entry['name'])] = identity
            with self.store.lock, self.store.db:
                for app in merged.values():
                    previous = self.store.row('SELECT payload FROM applications WHERE id=?', (app['id'],))
                    if previous:
                        preferred = json.loads(previous['payload']).get('preferred')
                        if preferred in app['launchers']: app['preferred'] = preferred
                    self.store.db.execute('INSERT INTO applications VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload=excluded.payload,updated_at=excluded.updated_at',
                                          (app['id'], app['name'], json.dumps(app), now()))
                if not errors:
                    keep = set(merged)
                    for old in self.store.rows('SELECT id FROM applications'):
                        if old['id'] not in keep:
                            self.store.db.execute('DELETE FROM applications WHERE id=?', (old['id'],))
                            self.store.db.execute('DELETE FROM application_aliases WHERE application_id=?', (old['id'],))
            self.last_refresh = time.time()
            return {'count': self.count(), 'cached': False, 'warnings': errors}

    def count(self):
        return self.store.row('SELECT count(*) AS n FROM applications')['n']

    def search(self, query='', refresh=False):
        if refresh or not self.count(): self.refresh(force=refresh)
        query = normalized(query)
        alias = self.store.row('SELECT application_id FROM application_aliases WHERE alias=?', (query,))
        terms = set(query.split())
        semantic = {'email':'outlook', 'emails':'outlook', 'my emails':'outlook', 'mail':'outlook', 'music':'spotify', 'file explorer':'explorer', 'files':'explorer'}
        search = semantic.get(query, query)
        terms = set(search.split())
        result = []
        launches = {row['application_id']:row['last_used'] for row in self.store.rows('SELECT application_id,max(created_at) AS last_used FROM application_launches WHERE verified=1 GROUP BY application_id')}
        aliases = {}
        for row in self.store.rows('SELECT * FROM application_aliases'): aliases.setdefault(row['application_id'],[]).append(row['alias'])
        for row in self.store.rows('SELECT payload FROM applications ORDER BY name'):
            app = json.loads(row['payload'])
            name = normalized(app['name'])
            score = 2 if alias and alias['application_id'] == app['id'] else 1 if name == search else 0
            if not score and search:
                overlap = len(terms & set(name.split())) / max(1, len(terms))
                score = max(overlap * .85, difflib.SequenceMatcher(None, search, name).ratio() * .7)
            if not query: score = 1
            if score >= .42:
                result.append({**app, 'score': round(score, 3), 'last_used':launches.get(app['id'],''), 'aliases':aliases.get(app['id'],[])})
        return sorted(result, key=lambda app: (app['score'],app['last_used']), reverse=True)[:50]

    def resolve(self, name):
        exact = self.store.row('SELECT payload FROM applications WHERE id=?', (name,))
        if exact: return json.loads(exact['payload'])
        matches = self.search(name)
        if not matches:
            self.refresh(force=True)
            matches = self.search(name)
        if not matches: raise ValueError('Application not found. Refresh the app index or add its install folder in Settings.')
        if len(matches) > 1 and abs(matches[0]['score'] - matches[1]['score']) < .05:
            return {'ambiguous': True, 'matches': matches[:8]}
        return matches[0]

    def alias(self, alias, application_id):
        app = self.resolve(application_id)
        if app.get('ambiguous'): raise ValueError('Choose a specific indexed application')
        value = normalized(alias)
        if not value or len(value) > 120: raise ValueError('Use an alias of 1 to 120 characters')
        self.store.execute('INSERT INTO application_aliases VALUES(?,?) ON CONFLICT(alias) DO UPDATE SET application_id=excluded.application_id', (value, app['id']))
        return {'alias': value, 'application_id': app['id'], 'name': app['name']}

    @staticmethod
    def matches(app, window, pid=None):
        executable = window.get('executable', '').casefold()
        if executable and executable in {value.casefold() for value in app.get('executables', [])}: return True
        if pid and window.get('pid') == pid: return True
        names = {Path(value.replace('\\', '/')).name.casefold() for value in app.get('executables', [])}
        if window.get('process', '').casefold() in names: return True
        app_name, title = normalized(app['name']), normalized(window.get('title', ''))
        return bool(app_name and title and (app_name == title or title.endswith(' ' + app_name)))

    def launch(self, name, cancel=None, timeout=12):
        if not self.adapter.supported: raise ValueError('Native application launching requires running JARVIS on Windows')
        app = self.resolve(name)
        if app.get('ambiguous'): return {**app, 'verified':False, 'message':'Choose one installed application before launching.'}
        for window in self.adapter.windows():
            if self.matches(app, window):
                if name != app['id']: self.alias(name,app['id'])
                self.store.execute('INSERT INTO application_launches(application_id,method,verified,created_at) VALUES(?,?,1,?)',(app['id'],json.dumps({'kind':'existing_window'}),now()))
                return {'name': app['name'], 'application_id': app['id'], 'verified': True, 'already_running': True,
                        'window_id': window['id'], 'focused': self.adapter.focus(window['id'])}
        launchers = app['launchers'][:]
        if app.get('preferred') in launchers:
            launchers.remove(app['preferred']); launchers.insert(0, app['preferred'])
        failures = []
        for launcher in launchers:
            if cancel: cancel.check()
            previous_windows = {window['id'] for window in self.adapter.windows()}
            try: pid = self.adapter.activate(launcher)
            except (OSError, ValueError) as exc:
                failures.append({'kind': launcher['kind'], 'error': str(exc)})
                continue
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancel: cancel.check()
                for window in self.adapter.windows():
                    if self.matches(app, window) or (pid and window.get('pid') == pid and window['id'] not in previous_windows):
                        app['preferred'] = launcher
                        self.store.execute('UPDATE applications SET payload=? WHERE id=?', (json.dumps(app), app['id']))
                        if name != app['id']: self.alias(name, app['id'])
                        self.store.execute('INSERT INTO application_launches(application_id,method,verified,created_at) VALUES(?,?,1,?)', (app['id'], json.dumps(launcher), now()))
                        return {'name': app['name'], 'application_id': app['id'], 'verified': True, 'already_running': False,
                                'window_id': window['id'], 'focused': self.adapter.focus(window['id']), 'method': launcher['kind']}
                if cancel: cancel.event.wait(.2)
                else: time.sleep(.2)
            self.store.execute('INSERT INTO application_launches(application_id,method,verified,created_at) VALUES(?,?,0,?)', (app['id'], json.dumps(launcher), now()))
            return {'name': app['name'], 'application_id': app['id'], 'verified': False, 'method': launcher['kind'],
                    'message': 'Activation was requested, but no matching visible window appeared. Inspect the desktop; do not claim it opened.'}
        self.refresh(force=True)
        return {'name': app['name'], 'verified': False, 'errors': failures, 'message': 'All indexed activation methods failed; the index has been refreshed.'}

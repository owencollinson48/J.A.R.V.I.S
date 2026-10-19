"""Read-only observations of Windows conditions that can block desktop work.

These observations explain a failed attempt. They do not prove that a task was
completed, and unavailable information is kept unknown rather than guessed.
"""
import ctypes
import os
import platform
from datetime import datetime, timezone


# Fixed-width Windows integers also keep the native boundary testable on Linux.
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
PDWORD = ctypes.POINTER(DWORD)


class _SIDAndAttributes(ctypes.Structure):
    _fields_ = [('sid', ctypes.c_void_p), ('attributes', DWORD)]


def _diagnostic(exc):
    code = getattr(exc, 'winerror', None)
    return f'{type(exc).__name__}: {str(exc)[:300]}' + (f' (WinError {code})' if code is not None else '')


class _NativeWindows:
    """Small Win32 adapter. Every owned process, token and desktop is closed."""

    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        signatures = [
            (self.kernel, 'GetCurrentThreadId', [], DWORD),
            (self.kernel, 'ProcessIdToSessionId', [DWORD, PDWORD], BOOL),
            (self.kernel, 'OpenProcess', [DWORD, BOOL, DWORD], HANDLE),
            (self.kernel, 'CloseHandle', [HANDLE], BOOL),
            (self.advapi, 'OpenProcessToken', [HANDLE, DWORD, ctypes.POINTER(HANDLE)], BOOL),
            (self.advapi, 'GetTokenInformation', [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, PDWORD], BOOL),
            (self.advapi, 'GetSidSubAuthorityCount', [ctypes.c_void_p], ctypes.POINTER(ctypes.c_ubyte)),
            (self.advapi, 'GetSidSubAuthority', [ctypes.c_void_p, DWORD], PDWORD),
            (self.user, 'OpenInputDesktop', [DWORD, BOOL, DWORD], HANDLE),
            (self.user, 'CloseDesktop', [HANDLE], BOOL),
            (self.user, 'GetThreadDesktop', [DWORD], HANDLE),
            (self.user, 'GetUserObjectInformationW', [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, PDWORD], BOOL),
            (self.user, 'GetSystemMetrics', [ctypes.c_int], ctypes.c_int),
            (self.user, 'IsWindow', [HANDLE], BOOL),
            (self.user, 'IsWindowVisible', [HANDLE], BOOL),
            (self.user, 'IsIconic', [HANDLE], BOOL),
            (self.user, 'IsHungAppWindow', [HANDLE], BOOL),
            (self.user, 'GetForegroundWindow', [], HANDLE),
            (self.user, 'GetWindowThreadProcessId', [HANDLE, PDWORD], DWORD),
        ]
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result

    @staticmethod
    def _error(operation):
        code = ctypes.get_last_error()
        return OSError(code, f'{operation} failed (WinError {code})')

    def session(self, pid):
        session = DWORD()
        if not self.kernel.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise self._error('ProcessIdToSessionId')
        return session.value

    def _token_information(self, token, kind):
        needed = DWORD()
        self.advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(needed))
        if not needed.value or needed.value > 1024 * 1024:
            raise self._error('GetTokenInformation size')
        buffer = ctypes.create_string_buffer(needed.value)
        if not self.advapi.GetTokenInformation(token, kind, buffer, needed.value, ctypes.byref(needed)):
            raise self._error('GetTokenInformation')
        return buffer

    def integrity(self, pid):
        process = self.kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not process: raise self._error('OpenProcess')
        token = HANDLE()
        try:
            if not self.advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
                raise self._error('OpenProcessToken')
            result = {'rid': None, 'level': None, 'elevated': None}
            diagnostics = []
            try:
                buffer = self._token_information(token, 25)  # TokenIntegrityLevel
                label = ctypes.cast(buffer, ctypes.POINTER(_SIDAndAttributes)).contents
                count = self.advapi.GetSidSubAuthorityCount(label.sid)
                if not count or not count.contents.value:
                    raise ValueError('Token integrity SID has no subauthorities')
                authority = self.advapi.GetSidSubAuthority(label.sid, count.contents.value - 1)
                if not authority: raise ValueError('Token integrity SID authority is unavailable')
                rid = authority.contents.value
                level = ('untrusted' if rid < 0x1000 else 'low' if rid < 0x2000 else
                         'medium' if rid < 0x3000 else 'high' if rid < 0x4000 else
                         'system' if rid < 0x5000 else 'protected')
                result.update(rid=rid, level=level)
            except (OSError, ValueError) as exc:
                diagnostics.append(_diagnostic(exc))
            try:
                buffer = self._token_information(token, 20)  # TokenElevation
                result['elevated'] = bool(ctypes.cast(buffer, PDWORD).contents.value)
            except (OSError, ValueError) as exc:
                diagnostics.append(_diagnostic(exc))
            if diagnostics: result['diagnostics'] = diagnostics
            return result
        finally:
            if token.value: self.kernel.CloseHandle(token)
            self.kernel.CloseHandle(process)

    def _desktop_name(self, handle):
        needed = DWORD()
        self.user.GetUserObjectInformationW(handle, 2, None, 0, ctypes.byref(needed))  # UOI_NAME
        if not needed.value or needed.value > 65536:
            raise self._error('GetUserObjectInformationW size')
        buffer = ctypes.create_unicode_buffer((needed.value + ctypes.sizeof(ctypes.c_wchar) - 1) // ctypes.sizeof(ctypes.c_wchar))
        if not self.user.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
            raise self._error('GetUserObjectInformationW')
        return buffer.value

    def input_desktop(self):
        result = {'available': None, 'name': None, 'thread_desktop_name': None, 'same_as_thread': None}
        diagnostics = []
        # GetThreadDesktop returns a borrowed handle: it must not be closed.
        thread = self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId())
        if thread:
            try: result['thread_desktop_name'] = self._desktop_name(thread)
            except (OSError, ValueError) as exc: diagnostics.append(_diagnostic(exc))
        else:
            diagnostics.append(_diagnostic(self._error('GetThreadDesktop')))
        desktop = self.user.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS, no switching/writing.
        result['available'] = bool(desktop)
        if desktop:
            try:
                result['name'] = self._desktop_name(desktop)
            except (OSError, ValueError) as exc:
                diagnostics.append(_diagnostic(exc))
            finally:
                self.user.CloseDesktop(desktop)
        else:
            diagnostics.append(_diagnostic(self._error('OpenInputDesktop')))
        if result['name'] is not None and result['thread_desktop_name'] is not None:
            result['same_as_thread'] = result['name'].casefold() == result['thread_desktop_name'].casefold()
        if diagnostics: result['diagnostics'] = diagnostics
        return result

    def remote_session(self):
        return bool(self.user.GetSystemMetrics(0x1000))  # SM_REMOTESESSION; not itself a blocker.

    def window(self, window_id):
        if not self.user.IsWindow(window_id):
            return {'window_id': window_id, 'exists': False}
        pid = DWORD()
        if not self.user.GetWindowThreadProcessId(window_id, ctypes.byref(pid)) or not pid.value:
            raise self._error('GetWindowThreadProcessId')
        return {'window_id': window_id, 'exists': True, 'pid': pid.value,
                'visible': bool(self.user.IsWindowVisible(window_id)),
                'minimized': bool(self.user.IsIconic(window_id)),
                'foreground': self.user.GetForegroundWindow() == window_id,
                'responding': not bool(self.user.IsHungAppWindow(window_id))}


def _process(probe, pid):
    result = {'pid': pid, 'session_id': None, 'integrity': None}
    diagnostics = []
    try: result['session_id'] = probe.session(pid)
    except (OSError, ValueError) as exc: diagnostics.append(_diagnostic(exc))
    try: result['integrity'] = probe.integrity(pid)
    except (OSError, ValueError) as exc: diagnostics.append(_diagnostic(exc))
    if diagnostics: result['diagnostics'] = diagnostics
    return result


def _memory():
    import psutil
    memory = psutil.virtual_memory()
    return {'percent': float(memory.percent), 'available_bytes': int(memory.available),
            'total_bytes': int(memory.total),
            'pressure': memory.percent >= 95 or memory.available < 128 * 1024 * 1024}


def desktop_diagnostics(window_id=None):
    """Observe access, session, window responsiveness and memory without changing anything."""
    system = platform.system()
    result = {'supported': system == 'Windows', 'platform': system,
              'observed_at': datetime.now(timezone.utc).isoformat(), 'blockers': []}
    if system != 'Windows':
        result['note'] = 'Windows desktop diagnostics must run on the Windows PC being controlled.'
        return result
    if window_id is not None and (isinstance(window_id, bool) or not isinstance(window_id, int) or window_id <= 0):
        raise ValueError('window_id must be a positive integer window handle')
    try:
        probe = _NativeWindows()
    except (OSError, AttributeError) as exc:
        result['diagnostics'] = [_diagnostic(exc)]
        result['note'] = 'Windows desktop conditions could not be measured; the cause is unknown.'
        return result
    result['current_process'] = current = _process(probe, os.getpid())
    for name, getter in [('input_desktop', probe.input_desktop), ('remote_session', probe.remote_session), ('memory', _memory)]:
        try: result[name] = getter()
        except (OSError, ValueError, ImportError) as exc:
            result[name] = None
            result.setdefault('diagnostics', []).append(f'{name}: {_diagnostic(exc)}')
    target = None
    if window_id is not None:
        try:
            target = probe.window(window_id)
            if target.get('exists'):
                target.update(_process(probe, target['pid']))
                a, b = current['session_id'], target['session_id']
                target['same_session'] = a == b if a is not None and b is not None else None
            result['target_window'] = target
        except (OSError, ValueError) as exc:
            result['target_window'] = {'window_id': window_id, 'exists': None, 'diagnostics': [_diagnostic(exc)]}

    def blocker(code, detail, recovery):
        result['blockers'].append({'code': code, 'detail': detail, 'recovery': recovery})

    desktop = result.get('input_desktop') or {}
    if desktop.get('available') is False:
        blocker('input_desktop_unavailable', 'OpenInputDesktop did not grant read access to the current input desktop.',
                'Keep JARVIS and the target in the same signed-in interactive desktop. Handle any Windows secure prompt locally, then retry. The exact reason for denied access is unknown.')
    if desktop.get('same_as_thread') is False:
        blocker('input_desktop_mismatch', f"JARVIS is on desktop {desktop['thread_desktop_name']!r}, but input is on {desktop['name']!r}.",
                'Return to the desktop where JARVIS is running before sending desktop input. Handle secure Windows prompts locally.')
    if target:
        if target.get('exists') is False:
            blocker('window_missing', f'Window handle {window_id} no longer exists.',
                    'List windows again and locate the current app window; do not reuse the stale handle or assume the app crashed.')
        if target.get('exists'):
            if target.get('same_session') is False:
                blocker('session_mismatch', f"JARVIS is in session {current['session_id']}; the target process is in session {target['session_id']}.",
                        'Run JARVIS and the target application in the same interactive Windows user session.')
            if target.get('visible') is False:
                blocker('window_hidden', 'Windows reports that the target window is not visible.',
                        'Find the current visible application window or restore the app, then inspect it again.')
            if target.get('minimized') is True:
                blocker('window_minimized', 'Windows reports that the target window is minimized.',
                        'Restore and focus the target window, then inspect its current state before interacting.')
            if target.get('foreground') is False:
                blocker('window_not_foreground', 'The target window is not the foreground window.',
                        'Bring the target window to the front and verify focus before sending input.')
            if target.get('responding') is False:
                blocker('window_not_responding', 'Windows IsHungAppWindow reports that this window is not responding.',
                        'Wait briefly and recheck the window. Do not replay inputs whose delivery is uncertain.')
            own_rid = (current.get('integrity') or {}).get('rid')
            target_rid = (target.get('integrity') or {}).get('rid')
            if own_rid is not None and target_rid is not None and target_rid > own_rid:
                blocker('higher_integrity_target', f'The target integrity RID ({target_rid}) exceeds JARVIS integrity RID ({own_rid}); Windows can restrict cross-integrity input.',
                        'Use a normal, non-elevated application window where possible. Protected or elevated Windows prompts may need local user action.')
    memory = result.get('memory') or {}
    if memory.get('pressure') is True:
        blocker('memory_pressure', f"System memory is {memory['percent']:g}% used with {memory['available_bytes']} bytes available.",
                'Allow the system time to recover or close unused applications, then inspect again; a slow launch is not proof of failure.')
    result['note'] = 'These are current observations, not proof of task completion. Unknown fields do not establish a permission problem; remote access alone is not a failure.'
    return result

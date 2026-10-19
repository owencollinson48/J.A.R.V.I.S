import ctypes
from types import SimpleNamespace

import pytest

from jarvis import windows_health as health


class Probe:
    def __init__(self):
        self.current = 1
        self.target = 2
        self.sessions = {1: 7, 2: 7}
        self.integrities = {1: {'rid': 0x2000, 'level': 'medium', 'elevated': False},
                            2: {'rid': 0x2000, 'level': 'medium', 'elevated': False}}
        self.desktop = {'available': True, 'name': 'Default', 'thread_desktop_name': 'Default', 'same_as_thread': True}
        self.target_window = {'window_id': 0x123456789, 'pid': 2, 'exists': True,
                              'visible': True, 'minimized': False, 'foreground': True, 'responding': True}

    def session(self, pid):
        value = self.sessions[pid]
        if isinstance(value, Exception): raise value
        return value

    def integrity(self, pid):
        value = self.integrities[pid]
        if isinstance(value, Exception): raise value
        return value

    def input_desktop(self): return self.desktop.copy()
    def remote_session(self): return True
    def window(self, _): return self.target_window.copy()


@pytest.fixture
def observed(monkeypatch):
    probe = Probe()
    monkeypatch.setattr(health.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(health.os, 'getpid', lambda: probe.current)
    monkeypatch.setattr(health, '_NativeWindows', lambda: probe)
    monkeypatch.setattr(health, '_memory', lambda: {'percent': 55., 'available_bytes': 8 * 1024**3,
                                                  'total_bytes': 16 * 1024**3, 'pressure': False})
    return probe


def codes(result):
    return {item['code'] for item in result['blockers']}


def test_non_windows_reports_unsupported_without_native_calls(monkeypatch):
    monkeypatch.setattr(health.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(health, '_NativeWindows', lambda: pytest.fail('Native calls on Linux'))
    result = health.desktop_diagnostics()
    assert result['supported'] is False
    assert result['platform'] == 'Linux'
    assert result['blockers'] == []


def test_healthy_remote_session_is_an_observation_not_a_blocker(observed):
    result = health.desktop_diagnostics(0x123456789)
    assert result['supported'] is True
    assert result['remote_session'] is True
    assert result['target_window']['same_session'] is True
    assert result['current_process']['integrity']['elevated'] is False
    assert result['blockers'] == []
    assert 'not proof of task completion' in result['note']


def test_missing_window_does_not_claim_crash_or_check_unrelated_process(observed):
    observed.target_window = {'exists': False, 'window_id': 0x123456789}
    observed.sessions.pop(2)
    observed.integrities.pop(2)
    result = health.desktop_diagnostics(0x123456789)
    assert codes(result) == {'window_missing'}
    assert 'do not reuse the stale handle' in result['blockers'][0]['recovery']


def test_multiple_actual_window_conditions_are_reported(observed):
    observed.target_window.update(visible=False, minimized=True, foreground=False, responding=False)
    result = health.desktop_diagnostics(0x123456789)
    assert codes(result) == {'window_hidden', 'window_minimized', 'window_not_foreground', 'window_not_responding'}
    assert all(item['detail'] and item['recovery'] for item in result['blockers'])


def test_session_mismatch_requires_two_known_session_ids(observed):
    observed.sessions[2] = 8
    result = health.desktop_diagnostics(0x123456789)
    assert codes(result) == {'session_mismatch'}
    assert result['target_window']['same_session'] is False
    assert 'session 7' in result['blockers'][0]['detail']
    observed.sessions[1] = PermissionError('Session query denied')
    result = health.desktop_diagnostics(0x123456789)
    assert 'session_mismatch' not in codes(result)
    assert result['target_window']['same_session'] is None
    assert 'Session query denied' in result['current_process']['diagnostics'][0]


def test_only_measured_higher_integrity_is_reported(observed):
    observed.integrities[2] = {'rid': 0x3000, 'level': 'high', 'elevated': True}
    result = health.desktop_diagnostics(0x123456789)
    assert codes(result) == {'higher_integrity_target'}
    observed.integrities[1] = PermissionError('Token access denied')
    result = health.desktop_diagnostics(0x123456789)
    assert codes(result) == set()
    assert result['current_process']['integrity'] is None
    assert 'Token access denied' in result['current_process']['diagnostics'][0]


def test_elevation_alone_does_not_prove_higher_integrity(observed):
    observed.integrities[2] = {'rid': None, 'level': None, 'elevated': True, 'diagnostics': ['Integrity SID unavailable']}
    assert 'higher_integrity_target' not in codes(health.desktop_diagnostics(0x123456789))


def test_input_desktop_denial_does_not_guess_locked_screen_or_admin(observed):
    observed.desktop.update(available=False, name=None, same_as_thread=None, diagnostics=['OpenInputDesktop failed (WinError 5)'])
    result = health.desktop_diagnostics()
    assert codes(result) == {'input_desktop_unavailable'}
    assert 'WinError 5' in result['input_desktop']['diagnostics'][0]
    assert 'locked' not in result['blockers'][0]['detail'].lower()
    assert 'admin' not in result['blockers'][0]['detail'].lower()
    assert 'unknown' in result['blockers'][0]['recovery']


def test_desktop_mismatch_is_proven_by_names(observed):
    observed.desktop.update(name='Winlogon', same_as_thread=False)
    result = health.desktop_diagnostics()
    assert codes(result) == {'input_desktop_mismatch'}
    assert 'Default' in result['blockers'][0]['detail']
    assert 'Winlogon' in result['blockers'][0]['detail']


def test_memory_pressure_contains_measured_values(observed, monkeypatch):
    monkeypatch.setattr(health, '_memory', lambda: {'percent': 97., 'available_bytes': 123,
                                                  'total_bytes': 10000, 'pressure': True})
    result = health.desktop_diagnostics()
    assert codes(result) == {'memory_pressure'}
    assert '97%' in result['blockers'][0]['detail']
    assert '123 bytes' in result['blockers'][0]['detail']


def test_native_probe_failure_is_unknown_not_assumed_permissions(observed, monkeypatch):
    def fail(): raise OSError('API unavailable')
    monkeypatch.setattr(health, '_NativeWindows', fail)
    result = health.desktop_diagnostics()
    assert result['blockers'] == []
    assert 'API unavailable' in result['diagnostics'][0]
    assert 'unknown' in result['note']


def test_unreadable_window_is_unknown_not_missing(observed):
    def fail(_): raise PermissionError('Query denied')
    observed.window = fail
    result = health.desktop_diagnostics(0x123456789)
    assert result['target_window']['exists'] is None
    assert codes(result) == set()


@pytest.mark.parametrize('invalid', [0, -1, True, '123'])
def test_invalid_window_handles_are_rejected(observed, invalid):
    with pytest.raises(ValueError, match='positive integer'):
        health.desktop_diagnostics(invalid)


class Function:
    def __init__(self, callback=lambda *args: 1):
        self.callback, self.calls = callback, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class Library:
    def __getattr__(self, name):
        function = Function()
        setattr(self, name, function)
        return function


@pytest.fixture
def native(monkeypatch):
    libraries = {name: Library() for name in ['user32', 'kernel32', 'advapi32']}
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **_: libraries[name], raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5, raising=False)
    probe = health._NativeWindows()
    return SimpleNamespace(probe=probe, user=libraries['user32'], kernel=libraries['kernel32'], advapi=libraries['advapi32'])


def set_dword(pointer, value):
    ctypes.cast(pointer, health.PDWORD).contents.value = value


def test_native_handles_and_session_ids_keep_windows_abi(native):
    assert ctypes.sizeof(native.kernel.OpenProcess.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert native.user.OpenInputDesktop.restype is ctypes.c_void_p
    assert native.user.GetForegroundWindow.restype is ctypes.c_void_p
    assert native.kernel.ProcessIdToSessionId.argtypes[0] is ctypes.c_uint32
    assert ctypes.sizeof(health.DWORD) == 4
    native.kernel.ProcessIdToSessionId.callback = lambda pid, out: set_dword(out, 42) or 1
    assert native.probe.session(123) == 42
    hwnd = 0x123456789
    native.user.GetWindowThreadProcessId.callback = lambda handle, out: set_dword(out, 987) or 1
    native.user.GetForegroundWindow.callback = lambda: hwnd
    native.user.IsHungAppWindow.callback = lambda _: 0
    result = native.probe.window(hwnd)
    assert result['pid'] == 987 and result['foreground'] and result['responding']
    assert native.user.IsWindow.calls == [(hwnd,)]


def configure_token(native, fail_token=False, fail_information=False):
    process, token = 0x123456789, 0x234567890
    native.kernel.OpenProcess.callback = lambda *args: process
    def open_token(handle, access, output):
        assert handle == process and access == 0x8
        if fail_token: return 0
        ctypes.cast(output, ctypes.POINTER(health.HANDLE)).contents.value = token
        return 1
    native.advapi.OpenProcessToken.callback = open_token
    def information(handle, kind, output, size, needed):
        assert handle.value == token
        if fail_information: return 0
        length = ctypes.sizeof(health._SIDAndAttributes) if kind == 25 else 4
        set_dword(needed, length)
        if not output: return 0
        if kind == 25:
            ctypes.cast(output, ctypes.POINTER(health._SIDAndAttributes)).contents.sid = 0x55
        else: set_dword(output, 1)
        return 1
    native.advapi.GetTokenInformation.callback = information
    count, rid = ctypes.c_ubyte(1), health.DWORD(0x3000)
    native.advapi.GetSidSubAuthorityCount.callback = lambda sid: ctypes.pointer(count)
    native.advapi.GetSidSubAuthority.callback = lambda sid, index: ctypes.pointer(rid)
    return process, token


def handles_closed(native):
    return [handle.value if isinstance(handle, ctypes.c_void_p) else handle for handle, in native.kernel.CloseHandle.calls]


def test_token_queries_are_read_only_and_owned_64_bit_handles_are_closed(native):
    process, token = configure_token(native)
    assert native.probe.integrity(987) == {'rid': 0x3000, 'level': 'high', 'elevated': True}
    assert native.kernel.OpenProcess.calls == [(0x1000, False, 987)]
    assert handles_closed(native) == [token, process]


def test_failed_token_query_closes_process_and_does_not_close_unowned_token(native):
    process, _ = configure_token(native, fail_token=True)
    with pytest.raises(OSError, match='OpenProcessToken'):
        native.probe.integrity(987)
    assert handles_closed(native) == [process]


def test_process_query_denial_keeps_integrity_unknown_without_closing_invalid_handle(native):
    native.kernel.OpenProcess.callback = lambda *args: 0
    with pytest.raises(OSError, match='OpenProcess failed'):
        native.probe.integrity(987)
    assert native.kernel.CloseHandle.calls == []
    assert native.advapi.OpenProcessToken.calls == []


def test_failed_integrity_and_elevation_queries_remain_unknown_and_close_handles(native):
    process, token = configure_token(native, fail_information=True)
    result = native.probe.integrity(987)
    assert result['rid'] is None and result['elevated'] is None
    assert len(result['diagnostics']) == 2
    assert handles_closed(native) == [token, process]


@pytest.mark.parametrize('fail_name', [False, True])
def test_input_desktop_owned_handle_closed_even_when_name_query_fails(native, fail_name):
    thread, desktop = 0x345678901, 0x456789012
    native.user.GetThreadDesktop.callback = lambda _: thread
    native.user.OpenInputDesktop.callback = lambda *args: desktop
    def name(handle, kind, output, size, needed):
        assert kind == 2
        if handle == desktop and fail_name: return 0
        value = ctypes.create_unicode_buffer('Default' if handle == thread else 'Winlogon')
        set_dword(needed, ctypes.sizeof(value))
        if not output: return 0
        ctypes.memmove(output, value, ctypes.sizeof(value))
        return 1
    native.user.GetUserObjectInformationW.callback = name
    result = native.probe.input_desktop()
    assert result['available'] is True
    assert result['same_as_thread'] is (None if fail_name else False)
    assert native.user.OpenInputDesktop.calls == [(0, False, 0x1)]
    assert native.user.CloseDesktop.calls == [(desktop,)]
    assert native.kernel.CloseHandle.calls == []
    if fail_name: assert 'GetUserObjectInformationW' in result['diagnostics'][0]


def test_input_desktop_denial_preserves_numeric_error_and_never_closes_borrowed_handle(native):
    native.user.GetThreadDesktop.callback = lambda _: 0
    native.user.OpenInputDesktop.callback = lambda *args: 0
    result = native.probe.input_desktop()
    assert result['available'] is False
    assert result['name'] is None and result['same_as_thread'] is None
    assert any('OpenInputDesktop failed (WinError 5)' in item for item in result['diagnostics'])
    assert native.user.CloseDesktop.calls == []
    assert native.kernel.CloseHandle.calls == []

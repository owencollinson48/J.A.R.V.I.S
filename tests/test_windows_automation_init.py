"""COM adapter regressions; these fakes do not claim native Windows execution."""
import builtins
import ctypes
import sys
import types

import pytest

from jarvis.desktop import AccessibilityError, DesktopControl


def automation_environment(monkeypatch, *, apartment=None, selected=0, query_error=None, initialize_error=None):
    state = {'apartment':apartment,'queries':0,'initialized':[],'uninitialized':[],'desktops':[]}
    def get_apartment(apartment_pointer, qualifier_pointer):
        state['queries'] += 1
        if query_error is not None:
            return query_error
        if state['apartment'] is None:
            return -2147221008  # CO_E_NOTINITIALIZED 0x800401F0
        apartment_pointer._obj.value = state['apartment']
        qualifier_pointer._obj.value = 0
        return 0  # S_OK
    def initialize(flags):
        state['initialized'].append(flags)
        if initialize_error:
            raise initialize_error
        assert state['apartment'] is None, 'Never initialize an apartment another library already owns'
        state['apartment'] = 1 if flags == 0 else 0
    def uninitialize():
        state['uninitialized'].append(True)
        pytest.fail('Automation must not uninitialize COM owned by another library')
    def desktop(*,backend):
        assert backend == 'uia'
        instance = object()
        state['desktops'].append(instance)
        return instance
    comtypes = types.ModuleType('comtypes')
    comtypes.CoInitializeEx = initialize
    comtypes.CoUninitialize = uninitialize
    pywinauto = types.ModuleType('pywinauto')
    pywinauto.Desktop = desktop
    pywinauto.timings = types.SimpleNamespace(Timings=types.SimpleNamespace(window_find_timeout=30))
    monkeypatch.setitem(sys.modules,'comtypes',comtypes)
    monkeypatch.setitem(sys.modules,'pywinauto',pywinauto)
    monkeypatch.setattr(sys,'coinit_flags',selected,raising=False)
    def win_dll(name,**kwargs):
        assert name.lower() in {'ole32','ole32.dll'}
        return types.SimpleNamespace(CoGetApartmentType=get_apartment)
    monkeypatch.setattr(ctypes,'WinDLL',win_dll,raising=False)
    return state


@pytest.mark.parametrize('existing_apartment,selected',[(0,0),(3,0),(1,2)])
def test_existing_sta_main_sta_or_mta_is_preserved_without_reinitialization(monkeypatch,existing_apartment,selected):
    state = automation_environment(monkeypatch,apartment=existing_apartment,selected=selected)
    assert DesktopControl.automation() is state['desktops'][0]
    assert state['queries'] == 1
    assert state['initialized'] == [] and state['uninitialized'] == []


@pytest.mark.parametrize('selected',[0,2])
def test_fresh_worker_initializes_selected_apartment_once(monkeypatch,selected):
    state = automation_environment(monkeypatch,selected=selected)
    first = DesktopControl.automation()
    second = DesktopControl.automation()
    assert first is state['desktops'][0] and second is state['desktops'][1]
    assert state['queries'] == 2
    assert state['initialized'] == [selected]
    assert state['uninitialized'] == []


def test_failed_com_initialization_keeps_hresult_and_does_not_uninitialize(monkeypatch):
    class ComFailure(Exception):
        hresult = -2147417850  # RPC_E_CHANGED_MODE 0x80010106
    state = automation_environment(monkeypatch,initialize_error=ComFailure('Cannot change thread mode after it is set.'))
    with pytest.raises(AccessibilityError) as error:
        DesktopControl.automation()
    assert '0x80010106' in str(error.value)
    assert 'Cannot change thread mode' in str(error.value)
    assert 'Run py -3 start.py' not in str(error.value)
    assert state['initialized'] == [0] and state['uninitialized'] == []
    assert state['desktops'] == []


def test_unexpected_apartment_query_hresult_is_not_ignored(monkeypatch):
    state = automation_environment(monkeypatch,query_error=-2147024891)  # E_ACCESSDENIED 0x80070005
    with pytest.raises(AccessibilityError) as error:
        DesktopControl.automation()
    assert '0x80070005' in str(error.value)
    assert state['initialized'] == [] and state['uninitialized'] == []
    assert state['desktops'] == []


def test_only_missing_import_diagnostic_recommends_dependency_repair(monkeypatch):
    automation_environment(monkeypatch)
    original_import = builtins.__import__
    def missing_pywinauto(name,*args,**kwargs):
        if name == 'pywinauto':
            raise ModuleNotFoundError("No module named 'pywinauto'")
        return original_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',missing_pywinauto)
    with pytest.raises(AccessibilityError) as error:
        DesktopControl.automation()
    assert 'ModuleNotFoundError' in str(error.value)
    assert 'Run py -3 start.py' in str(error.value)

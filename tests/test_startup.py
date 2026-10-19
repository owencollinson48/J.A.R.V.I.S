"""Exercise actual child-process output, exit status and startup diagnostics."""
import sys

import pytest

from start import run_application


def test_server_traceback_is_saved_and_shown_at_failure(tmp_path):
    (tmp_path/'run.py').write_text("raise RuntimeError('synthetic startup failure')\n")
    with pytest.raises(RuntimeError,match='synthetic startup failure') as error:
        run_application(sys.executable,tmp_path,no_browser=True)
    log = (tmp_path/'.jarvis-data/startup.log').read_text()
    assert 'Traceback' in log
    assert 'RuntimeError: synthetic startup failure' in log
    assert 'code 1' in str(error.value)
    assert str(tmp_path/'.jarvis-data/startup.log') in str(error.value)


def test_busy_port_produces_specific_next_step_and_preserves_server_error(tmp_path):
    (tmp_path/'run.py').write_text("import sys; print('ERROR: [WinError 10048] address already in use',file=sys.stderr); sys.exit(1)\n")
    with pytest.raises(RuntimeError,match='Port 8765 is already in use') as error:
        run_application(sys.executable,tmp_path)
    assert 'py -3 start.py --port 8766' in str(error.value)
    assert '[WinError 10048]' in (tmp_path/'.jarvis-data/startup.log').read_text()


def test_successful_server_exit_retains_utf8_output_and_arguments(tmp_path):
    (tmp_path/'run.py').write_text("import sys;print('JARVIS ready \\u2713');print(repr(sys.argv[1:]))\n")
    assert run_application(sys.executable,tmp_path,port=8877,no_browser=True)==0
    log=(tmp_path/'.jarvis-data/startup.log').read_text()
    assert 'JARVIS ready ✓' in log
    assert "['--port', '8877', '--no-browser']" in log


def test_silent_failure_reports_original_exit_code(tmp_path):
    (tmp_path/'run.py').write_text('raise SystemExit(7)\n')
    with pytest.raises(RuntimeError,match='code 7') as error:
        run_application(sys.executable,tmp_path)
    assert 'No server output was produced' in str(error.value)

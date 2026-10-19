"""Set up and start JARVIS using an installed Python, without a batch file."""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path


def dependency_digest(root):
    digest = hashlib.sha256()
    for name in ("requirements.txt", "constraints.txt"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def prepare(root):
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required. Install it from python.org.")
    for name in ("run.py", "requirements.txt", "constraints.txt", "jarvis"):
        if not (root / name).exists():
            raise RuntimeError("Extract the entire JARVIS ZIP before starting it. Missing: " + name)
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating the JARVIS Python environment...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(environment)], cwd=root, check=True)
    version = subprocess.run([str(python), "-c", "import sys; sys.exit(sys.version_info < (3, 11))"], cwd=root)
    if version.returncode:
        raise RuntimeError("The .venv Python cannot run. Rename .venv to .venv-old, then try again.")
    marker = environment / ".jarvis-dependencies.sha256"
    fingerprint = dependency_digest(root)
    probe = "import fastapi, uvicorn, httpx, docx, psutil, PIL, playwright"
    if os.name == 'nt': probe += "; import pywinauto, pyautogui, win32com.client"
    imports = [str(python), "-c", probe]
    if marker.exists() and marker.read_text().strip() == fingerprint:
        if subprocess.run(imports, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            print("JARVIS dependencies are already installed.", flush=True)
            return python
    logs = root / ".jarvis-data"
    logs.mkdir(exist_ok=True)
    log_path = logs / "setup.log"
    print("Installing JARVIS dependencies. The first run requires Internet access.", flush=True)
    print("Setup log: " + str(log_path), flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        with subprocess.Popen([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"],
                              cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
            code = process.wait()
        if code:
            raise RuntimeError("Dependency installation failed. See " + str(log_path))
        log.flush()
        probe = subprocess.run(imports, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        if probe.returncode:
            raise RuntimeError("A Python dependency could not load. Check the last lines of " + str(log_path))
    marker.write_text(fingerprint, encoding="utf-8")
    return python


def main():
    parser = argparse.ArgumentParser(description="Install and start JARVIS with Python")
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parent
        python = prepare(root)
        if args.setup_only:
            print("JARVIS setup completed.")
            return 0
        print("Starting JARVIS. Keep this window open for reminders. Ctrl+C stops it.", flush=True)
        command = [str(python), str(root / "run.py"), "--port", str(args.port)]
        if args.no_browser:
            command.append("--no-browser")
        result = subprocess.run(command, cwd=root)
        if result.returncode:
            raise RuntimeError("JARVIS could not start. Copy the error above for troubleshooting.")
        return 0
    except KeyboardInterrupt:
        print("\nJARVIS stopped.")
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("\nJARVIS startup failed: " + str(exc), file=sys.stderr, flush=True)
        if os.name == "nt" and sys.stdin.isatty() and not os.getenv("JARVIS_NO_PAUSE"):
            input("Press Enter to close this window...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

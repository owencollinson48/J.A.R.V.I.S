"""Create a source distribution with a Windows launcher; excludes all local data."""
import hashlib
import zipfile
from pathlib import Path


root = Path(__file__).resolve().parent.parent
output = root / "dist" / "JARVIS-Windows.zip"
output.parent.mkdir(exist_ok=True)
files = [root / name for name in ("README.md", "Start JARVIS.bat", "requirements.txt", "requirements-dev.txt", "constraints.txt", "run.py", "start.py", "pytest.ini", ".gitignore")]
files += [path for folder in ("jarvis", "tests", "scripts") for path in (root / folder).rglob("*")
          if path.is_file() and "__pycache__" not in path.parts and path.suffix in {".py", ".js", ".cjs", ".css", ".html", ".svg", ".sh"}]
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        data = path.read_bytes()
        if path.suffix == ".bat":
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        archive.writestr("JARVIS/" + path.relative_to(root).as_posix(), data)
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
    assert not any(".jarvis-data" in name or ".venv/" in name for name in archive.namelist())
output.with_suffix(".zip.sha256").write_text(hashlib.sha256(output.read_bytes()).hexdigest() + "  " + output.name + "\n")
print(f"Created {output.name}: {len(files)} files, {output.stat().st_size:,} bytes")

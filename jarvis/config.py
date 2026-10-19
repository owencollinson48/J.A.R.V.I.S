import json
import os
import platform
import threading
from pathlib import Path
from urllib.parse import urlparse


class Settings:
    def __init__(self, data_dir=None):
        default = Path(os.getenv("LOCALAPPDATA", Path.home() / ".local" / "share")) / "JARVIS"
        self.data_dir = Path(data_dir or os.getenv("JARVIS_DATA_DIR", default)).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "settings.json"
        self._lock = threading.RLock()
        self.values = {
            "provider_url": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini",
            "api_key": "",
            "roots": [str(Path.home())],
            "name": "",
            "native_notifications": True,
            "permissions_mode": "routine",
            "application_directories": [],
            "workflow_timeout": 300,
        }
        if self.path.exists():
            self.values.update(json.loads(self.path.read_text(encoding="utf-8")))

    @property
    def api_key(self):
        return self.values.get("api_key") or os.getenv("JARVIS_API_KEY") or os.getenv("OPENAI_API_KEY", "")

    @property
    def local_provider(self):
        return urlparse(self.values["provider_url"]).hostname in {"localhost", "127.0.0.1", "::1"}

    def public(self):
        return {**{k: v for k, v in self.values.items() if k != "api_key"},
                "has_api_key": bool(self.api_key), "platform": platform.system(),
                "data_dir": str(self.data_dir), "home": str(Path.home()),
                "provider_ready": bool(self.api_key) or self.local_provider}

    def update(self, changes):
        allowed = set(self.values)
        if set(changes) - allowed:
            raise ValueError("Unknown settings field")
        with self._lock:
            candidate = {**self.values, **changes}
            url = urlparse(candidate["provider_url"])
            if not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("Enter a provider base URL without credentials or query parameters")
            if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
                raise ValueError("Use HTTPS, or HTTP for a local model")
            candidate["provider_url"] = candidate["provider_url"].rstrip("/")
            if not isinstance(candidate["model"], str) or not candidate["model"].strip():
                raise ValueError("A model name is required")
            roots = candidate["roots"]
            if not isinstance(roots, list) or not roots:
                raise ValueError("Add at least one folder for computer access")
            resolved = []
            for root in roots:
                path = Path(root).expanduser().resolve(strict=True)
                if not path.is_dir():
                    raise ValueError(f"Not a folder: {root}")
                resolved.append(str(path))
            candidate["roots"] = list(dict.fromkeys(resolved))
            if candidate['permissions_mode'] not in {'routine', 'review'}:
                raise ValueError('Choose routine autonomy or review mode')
            if not isinstance(candidate['workflow_timeout'], int) or not 30 <= candidate['workflow_timeout'] <= 900:
                raise ValueError('Workflow timeout must be between 30 and 900 seconds')
            if not isinstance(candidate['application_directories'], list):
                raise ValueError('Application directories must be a list of folders')
            folders = []
            for value in candidate['application_directories']:
                folder = Path(value).expanduser().resolve(strict=True)
                if not folder.is_dir(): raise ValueError('Application discovery paths must be folders')
                folders.append(str(folder))
            candidate['application_directories'] = list(dict.fromkeys(folders))
            tmp = self.path.with_suffix(".tmp")
            # Credentials are local to this OS account; never returned to the browser.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(candidate, handle, indent=2)
            os.replace(tmp, self.path)
            self.values = candidate
        return self.public()

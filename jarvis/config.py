import json
import os
import platform
import threading
from pathlib import Path
from urllib.parse import urlparse


class Settings:
    ACCESS_POLICY_VERSION = 1
    ASSISTANT_UPGRADE_VERSION = 1
    DEFAULT_MODEL = "gpt-6.1-sol"

    def __init__(self, data_dir=None):
        default = Path(os.getenv("LOCALAPPDATA", Path.home() / ".local" / "share")) / "JARVIS"
        self.data_dir = Path(data_dir or os.getenv("JARVIS_DATA_DIR", default)).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "settings.json"
        self._lock = threading.RLock()
        self.values = {
            "provider_url": "https://api.openai.com/v1",
            "model": self.DEFAULT_MODEL,
            "assistant_upgrade_version": self.ASSISTANT_UPGRADE_VERSION,
            "browser_mode": "existing",
            "speech_mode": "auto",
            "speech_voice": "cedar",
            "speech_api_key": "",
            "background_voice": True,
            "api_key": "",
            "roots": [str(Path.home())],
            "name": "",
            "native_notifications": True,
            "permissions_mode": "autonomous",
            "computer_access": "full",
            "access_policy_version": self.ACCESS_POLICY_VERSION,
            "application_directories": [],
            "workflow_timeout": 300,
        }
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.values.update(saved)
            # Apply the user's new access defaults once to installations created
            # before this setting existed. Later choices in Settings persist.
            if saved.get("access_policy_version", 0) < self.ACCESS_POLICY_VERSION:
                self.values.update(permissions_mode="autonomous", computer_access="full",
                                   access_policy_version=self.ACCESS_POLICY_VERSION)
                self._save(self.values)
            if saved.get("assistant_upgrade_version",0) < self.ASSISTANT_UPGRADE_VERSION:
                url = urlparse(self.values['provider_url'])
                if url.hostname=='api.openai.com' and self.values['model'] in {'gpt-4.1-mini','gpt-4.1-nano','gpt-4o-mini','gpt-4.1','gpt-4o'}:
                    self.values['model'] = self.DEFAULT_MODEL
                self.values.update(browser_mode='existing',speech_mode='auto',speech_voice='cedar',assistant_upgrade_version=self.ASSISTANT_UPGRADE_VERSION)
                self._save(self.values)

    def _save(self, values):
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(values, handle, indent=2)
        os.replace(tmp, self.path)

    @property
    def api_key(self):
        return self.values.get("api_key") or os.getenv("JARVIS_API_KEY") or os.getenv("OPENAI_API_KEY", "")

    @property
    def local_provider(self):
        return urlparse(self.values["provider_url"]).hostname in {"localhost", "127.0.0.1", "::1"}

    def public(self):
        return {**{k: v for k, v in self.values.items() if k not in {"api_key", "speech_api_key", "access_policy_version", "assistant_upgrade_version"}},
                "has_api_key": bool(self.api_key), "platform": platform.system(),
                "data_dir": str(self.data_dir), "home": str(Path.home()),
                "provider_ready": bool(self.api_key) or self.local_provider,
                "has_speech_api_key": bool(self.values.get('speech_api_key')),
                **self._speech_status()}

    def _speech_status(self):
        from .speech import Speech
        return Speech(self).public()

    def update(self, changes):
        allowed = set(self.values) - {"access_policy_version", "assistant_upgrade_version"}
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
            if candidate['permissions_mode'] not in {'autonomous', 'routine', 'review'}:
                raise ValueError('Choose autonomous, routine, or review mode')
            if candidate['computer_access'] not in {'full', 'folders'}:
                raise ValueError('Choose full computer access or selected folders')
            if candidate['browser_mode'] not in {'existing','managed'}:
                raise ValueError('Choose the current browser or managed browser')
            if candidate['speech_mode'] not in {'auto','neural','browser'}:
                raise ValueError('Choose automatic, natural, or browser speech')
            if not isinstance(candidate['background_voice'],bool): raise ValueError('Background voice must be on or off')
            if candidate['speech_voice'] not in {'cedar','marin'}:
                raise ValueError('Choose Cedar or Marin for the natural voice')
            if not isinstance(candidate['speech_api_key'],str): raise ValueError('Speech API key must be text')
            full_access = candidate['computer_access'] == 'full'
            def folder_values(values, label):
                if not isinstance(values, list): raise ValueError(f'{label} must be a list of folders')
                folders = []
                for value in values:
                    if not isinstance(value, str) or not value.strip() or '\x00' in value:
                        raise ValueError(f'{label} must contain nonempty folder paths')
                    if full_access:
                        # Removable/network locations may currently be offline.
                        # Remember them without blocking unrelated settings saves.
                        folders.append(value)
                    else:
                        folder = Path(value).expanduser().resolve(strict=True)
                        if not folder.is_dir(): raise ValueError(f'Not a folder: {value}')
                        folders.append(str(folder))
                return list(dict.fromkeys(folders))
            roots = candidate["roots"]
            if not isinstance(roots, list) or not roots:
                raise ValueError("Add at least one folder for computer access")
            candidate["roots"] = folder_values(roots, 'Access folders')
            if not isinstance(candidate['workflow_timeout'], int) or not 30 <= candidate['workflow_timeout'] <= 900:
                raise ValueError('Workflow timeout must be between 30 and 900 seconds')
            candidate['application_directories'] = folder_values(candidate['application_directories'], 'Application directories')
            # Credentials are local to this OS account; never returned to the browser.
            self._save(candidate)
            self.values = candidate
        return self.public()

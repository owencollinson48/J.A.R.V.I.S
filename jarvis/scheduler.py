import asyncio
import os
import platform
import subprocess

from .store import now


def native_notify(title):
    if platform.system() != "Windows":
        return
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.BalloonTipTitle = 'JARVIS reminder'
$n.BalloonTipText = $env:JARVIS_NOTIFICATION_TEXT
$n.ShowBalloonTip(8000)
Start-Sleep -Seconds 10
$n.Dispose()
"""
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                     env={**os.environ, "JARVIS_NOTIFICATION_TEXT": title[:250]},
                     creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Scheduler:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def tick(self):
        stamp = now()
        with self.store.lock, self.store.db:
            due = [dict(row) for row in self.store.db.execute("SELECT * FROM reminders WHERE due_at<=? AND fired_at IS NULL", (stamp,))]
            for item in due:
                self.store.db.execute("UPDATE reminders SET fired_at=? WHERE id=? AND fired_at IS NULL", (stamp, item["id"]))
        for item in due:
            if self.settings.values["native_notifications"]:
                try:
                    native_notify(item["title"])
                except OSError:
                    pass  # The reminder remains visible in the dashboard.
        return due

    async def loop(self):
        while True:
            await asyncio.to_thread(self.tick)
            await asyncio.sleep(5)

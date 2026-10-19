# JARVIS

A personal assistant that runs on your Windows computer: natural chat, persistent memory, goals, tasks, reminders, and tools for files and applications. The interface is served locally in your browser; the Python process does the computer work.

## Start on Windows

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/windows/). Enable **Add Python to PATH**. The launcher accepts `py`, `python`, or `python3`.
2. Download or clone this repository onto your Windows computer. For a ZIP download, right-click it and choose **Extract All** first; running the batch file inside the ZIP will not work.
3. Double-click **`Start JARVIS.bat`**. It creates a virtual environment, installs dependencies, and opens the dashboard. Keep its window open for reminders; press Ctrl+C to stop it.
4. Open **Settings & connections** and choose a model:
   - **OpenAI:** enter an API key, retain `https://api.openai.com/v1`, and choose a model that supports Chat Completions, streaming, and tools. The default is `gpt-4.1-mini`. API usage may incur provider charges.
   - **Ollama:** install [Ollama for Windows](https://ollama.com/download/windows), run `ollama pull qwen3:8b`, select **Ollama · local**, and test the connection. Ollama must stay running. Use a tool-capable model; local model quality and speed depend on your computer.
   - Other providers work if their API supports OpenAI-style streaming Chat Completions and tool calls. Supply the provider's base URL, model, and key.
5. Click **Test connection**. This saves your settings and makes a small real model request. No model, account, or credentials are bundled with JARVIS.
6. Set your name and computer access folders. Your user folder is included initially. Add `C:\` or another drive in Settings for broader access.

### If Windows blocks the batch launcher

This is a source-code distribution, not a signed Windows installer. If Smart App Control appears when opening **`Start JARVIS.bat`**, try starting the source through your installed Python:

1. Extract the entire ZIP and open the folder containing `start.py`.
2. Click File Explorer's address bar, type `cmd`, and press Enter.
3. Run `py -3 start.py`. If you installed Python on PATH without its launcher, run `python start.py` instead.

`start.py` creates the environment, installs dependencies with pip's normal verification enabled, and opens the dashboard. It reuses dependencies on later starts without requiring another download. This path does not change Windows security settings. It requires a Python installation Windows permits to run; install Python from python.org if necessary.

If Smart App Control blocks **`python.exe`**, a **`.dll`**, or a **`.pyd`** dependency, the source launcher may also fail. Record the exact blocked filename if available so that component can be identified; a batch-file change cannot resolve a blocked binary.

Examples to try:

- “Help me plan my day around my current tasks.”
- “Remember that I prefer focused work in the morning.”
- “Create a goal to learn Spanish and give me five concrete tasks.”
- “Remind me to leave for the dentist tomorrow at 2:30 pm.”
- “Find my project proposal in Documents and summarize it.”
- “Update the introduction of that document.”
- “Open Calculator.”

Enter sends a typed message; Shift+Enter adds a new line. **Voice on** reads typed-chat replies aloud.

## Talk to the JARVIS circle

Use Chrome or Microsoft Edge. Connect a model, then click the **JARVIS circle** (or **Hands-free**) once and allow microphone access. This initial click is needed for browser microphone/audio permissions; after that, speak naturally. A short pause sends your words automatically, JARVIS speaks the reply, and listening resumes without another button press. The circle stays visible with Listening, Thinking, and Speaking states. Say **“stop listening”** to end the session, or use **End conversation**. The microphone is paused while replies play to avoid hearing its own voice. **Skip spoken reply** moves back to listening sooner.

Voice pauses while you use another app page, an item dialog, or a background browser tab. It resumes when you return to the Assistant. Requested routine actions run automatically; significant consequences pause for on-screen approval. Say **“cancel task”** while JARVIS is working to stop further steps. Permission, model, and speech-service errors stop the voice loop with an explanation. This is a hands-free browser session, not a background wake-word service; keep the dashboard open, and start a new voice session after reloading it. Browser recognition may use an online speech service and requires working microphone permissions.

### British accent

Open **Settings & connections → Voice & conversation**. JARVIS automatically prefers an available **English (United Kingdom)** voice. Choose another voice from the list, adjust the speaking speed, and click **Hear a sample**. Voice preferences save automatically in that browser. No voice recording, upload, or extra API key is required.

Available voices depend on Windows and your browser. If no British voice appears, add an English (United Kingdom) speech voice under **Windows Settings → Time & language → Speech**, then restart the browser. The app explains when it must fall back to the browser voice. This uses stock speech voices; it does not clone a person's voice.

## Blue display and live task flow

The front page uses an animated cyan/blue SVG instrument with concentric rings, fine ticks, radial segments and dots. Rings gently rotate while idle. When you speak, an optional local Web Audio microphone meter drives the radial bars; when JARVIS replies, browser speech word events pulse the display. Browsers without those events still show a speaking animation. Reduced-motion preferences disable the movement. Microphone metering starts only in an active listening session and stops when listening ends or the tab is hidden.

The **Task flow** panel creates connected boxes from real tool events: your request → JARVIS → the actions it performs. Boxes show running, completed, failed, cancelled and awaiting-approval states. A completed input means the tool delivered it; app results and the action log show what was verified. Plans remain separate from executed actions. Switch to **Your day** for the planner sidebar. On small screens, open **Activity** to see the full graph and log. Reloading a conversation restores its latest task and actions.

## Computer access and autonomy

A direct request authorizes ordinary steps: finding/opening an app, reading allowed files, requested edits and saves, normal browser navigation and accessible UI interaction. Multistep work is planned, executed and checked under a time/action budget. **Settings → Autonomy & confirmation** can instead require review of every computer action. Unrestricted commands, credential access, executable scripts/installers, coordinate clicks and consequential controls such as Send, Delete, Pay or security settings require a final approval. JARVIS does not bypass Windows authentication, UAC or security controls. It runs with your Windows account’s permissions.

**Applications** indexes Start Menu entries and shortcuts, Get-StartApps, installed packages/AUMIDs and protocols, registry App Paths and uninstall display registrations, PATH, Steam/Epic libraries and bounded scans of application folders you approve. It never executes uninstall registry strings. Ask “Open Outlook” or “Show my emails”; ambiguity between installed editions is reported. Use application preferences to map “my emails” to a particular edition. A running matching window is focused first; new activations use the indexed executable, shortcut, URI or AUMID, wait for a matching visible window, then remember successful methods. An activation without a matching window is reported as unverified. **Check launch** tests an indexed ordinary app without an AI connection.

Desktop tools inspect Windows accessibility elements, enter text, navigate, close windows normally and capture a specific window for a vision-capable model. Structured UI Automation is preferred; coordinate fallback requires review. Applications with inaccessible/custom controls, elevated windows or unusual launchers may need your help. Classic Outlook inbox search uses an existing signed-in MAPI session; New Outlook uses accessible UI or the managed browser. No login credentials are extracted. The managed Playwright browser opens installed Microsoft Edge on Windows in a separate persistent profile. Sign in there yourself when needed; JARVIS does not copy your personal browser cookies.

File tools list, search, read, create, edit, move and open files in allowed folders. Text and Word `.docx` are supported. Exact-text and font/spacing edits preserve existing Word tables, images, styles and unaffected runs. Full `.docx` replacement still removes rich content and requires review for an existing file. Files are backed up before replacement, and fingerprints prevent overwriting a file changed during the operation. **Activity → Undo this file change** can restore a backup, reverse a move, or archive a file JARVIS created; it refuses if that file has since changed. PDF and Excel editing are not implemented.

**Stop task**, **Escape** in the dashboard, **Ctrl+Alt+F8** globally on Windows (when registration succeeds), or **“cancel task”** during the task’s listening phase cancels further steps. Cancellation stops owned command processes and interrupts waiting for model output. Native operations already delivered to an app may finish; completed effects remain in Activity and are not automatically reversed. Speech cancellation is paused while JARVIS is speaking to prevent self-echo; use the visible stop button or hotkey then. Moving the mouse to PyAutoGUI’s fail-safe corner also stops pointer automation. Windows may reserve the global hotkey; Activity reports its availability.

File, email, browser and screenshot contents are treated as untrusted task data in model instructions. They cannot supply user authorization. The execution policy derives UI controls from inspected metadata rather than a label supplied by the model. Review important results: a model can still make mistakes interpreting a request or document.

Folder limits apply to file tools. Applications and approved commands use your account’s wider access. This cloud workspace cannot control your Windows PC: run JARVIS on the computer you want it to access.

## Memory and reminders

- Conversations, tasks, goals, reminders, approval outcomes, and curated memories are stored in SQLite. Conversations automatically carry into later sessions; relevant excerpts from other conversations and saved facts can be included in model context.
- Review, edit, or delete facts on the **Memory** page. Delete a conversation using its × button in the sidebar. Saved facts and planner entries are independent: deleting a conversation does not erase them. Deleting a saved memory does not remove mentions from conversation history.
- Reminders are one-time reminders. The background scheduler checks every five seconds while the server runs. Overdue reminders fire on the next startup. You can dismiss or delete them in the planner.
- Windows reminders use a native notification-area balloon. Notification visibility depends on Windows notification settings and your interactive desktop session. Browser notifications are optional; reminders also remain visible in the dashboard. Browser notifications need an open dashboard.
- The assistant uses your computer's local time zone. Reminder timestamps are stored in UTC and displayed in the browser's local time.

## Where data lives

On Windows the default directory is `%LOCALAPPDATA%\JARVIS`. On Linux/macOS it is `~/.local/share/JARVIS`. Set `JARVIS_DATA_DIR` to override it. This directory contains `jarvis.db`, `settings.json`, `backups/`, private screenshots and the separate managed-browser profile. Copy the entire directory while JARVIS is stopped to back up your assistant.

An API key entered in Settings is stored in your local settings file, not the repository. Treat this directory as private. You can instead supply `JARVIS_API_KEY` or `OPENAI_API_KEY` in the process environment. Keys are never returned by the settings API. Local Ollama requests do not receive a saved cloud API key.

The server binds to `127.0.0.1` only and checks the local session, Host, Origin, and a custom request header before accepting actions. Do not expose it through a public tunnel. Your selected model receives conversation context, relevant saved memory, and tool results used for the current task. With Ollama, inference can stay local; with a hosted provider, those requests go to that provider.

## Development

```bash
python -m venv .venv
# Windows:
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe run.py
.venv\Scripts\python.exe -m pytest -q

# Linux/macOS (with python3):
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python run.py --no-browser
.venv/bin/python -m pytest -q
```

Optional: `python run.py --port 8766` chooses another local port. The frontend is plain HTML/CSS/JavaScript with no build step or external CDN dependencies.

With Node.js 18+ installed, run `node --test tests/voice.test.js` for speech turn-taking, duplicate-result handling, approval pauses, British voice selection, and error recovery tests. These use simulated speech APIs; a real microphone, installed Windows voices, and browser speech-service access still need a check on the target PC.

For this cloud development workspace, run `bash scripts/setup-cloud.sh`, then `JARVIS_DATA_DIR="$PWD/.jarvis-data" .venv/bin/python run.py --no-browser`. Run `.venv/bin/python scripts/check-ready.py` from another terminal to check the dashboard and authenticated planner API. Cloud validation exercises the app on Linux; native Windows app launches, microphone permissions, and Windows notifications still need testing on your PC.

`jarvis/assistant.py` manages streaming chat and request-scoped execution; `execution.py` handles policy and logs; `applications.py`, `desktop.py` and `browser.py` implement the Windows and browser adapters; `provider.py` implements the model protocol; `computer.py` implements local tools; `store.py` provides persistence; `scheduler.py` delivers reminders; `app.py` serves the local API and interface. Tests use a deterministic model fixture; they do not establish live model quality or Windows-native notification behavior.

If startup fails, the Windows launcher keeps the error window open. Dependency installation output is saved to `.jarvis-data\setup.log` in the extracted app folder. Share the exact error text when asking for help. If a ZIP was not fully extracted, extract it first. If Python was updated and the old environment no longer works, rename the app folder's `.venv` to `.venv-old` and restart; this leaves your separate JARVIS memory/settings directory untouched. If port 8765 is already in use, close any other JARVIS window, or run `"Start JARVIS.bat" --port 8766` from Command Prompt. `"Start JARVIS.bat" --setup-only` installs dependencies without opening the dashboard.

A missing model key affects chat, while the planner, file browser, and memory editor work independently. If a provider rejects tools, choose a tool-capable model or check its API compatibility. Work continues if the dashboard connection drops until completion, cancellation, or a task limit; reload the conversation to see the saved outcome. Interrupted commands are not automatically rerun on restart.

### Validation and native Windows checks

Run `.venv/bin/python -m pytest -q` and `node --test tests/voice.test.js`. Browser coverage additionally uses Node Playwright with a Chromium executable: with the local server running, run `node tests/dashboard.browser.cjs`. Set `JARVIS_TEST_BROWSER` to the browser executable on another machine. It tests live-event graph rendering, HUD animation, reduced motion, mobile layout and simulated hands-free turn-taking. Python browser integration tests use real local Chromium when installed; model/desktop tests use deterministic adapters. They do not establish live provider quality, microphone recognition accuracy or native Windows behavior.

Native checks are deliberately opt-in because they open installed apps. In an interactive Windows Command Prompt:

```bat
set JARVIS_WINDOWS_LIVE=1
.venv\Scripts\python.exe -m pytest tests/test_windows_live.py -v -s
```

These enumerate real apps and check visible launches for installed Notepad, Store Calculator and Outlook editions. Missing apps are skipped. Existing windows are never killed; close any test windows yourself. On Linux these checks are skipped. You can also use **Applications → Check launch** on your Windows PC and inspect **Activity** without enabling the native test suite.

Windows ARM64 wheel availability was checked for the pinned native dependencies, including Python 3.15 wheels for pywin32, Pillow and greenlet and stable-ABI psutil. This is a packaging check, not a native Windows execution test. Installer or Smart App Control failures still need the exact error from that computer; no security checks are disabled.

# JARVIS

A personal assistant that runs on your Windows computer: natural chat, persistent memory, goals, tasks, reminders, and tools for files and applications. The interface is served locally in your browser; the Python process does the computer work.

## Start on Windows

1. Install **Python 3.11 or newer** from [python.org](https://www.python.org/downloads/windows/). Enable **Add Python to PATH**. The launcher accepts `py`, `python`, or `python3`.
2. Download or clone this repository onto your Windows computer. For a ZIP download, right-click it and choose **Extract All** first; running the batch file inside the ZIP will not work.
3. Double-click **`Start JARVIS.bat`**. It creates a virtual environment, installs dependencies, and opens the dashboard. Keep its window open for reminders; press Ctrl+C to stop it.
4. Open **Settings & connections** and choose a model:
   - **OpenAI:** enter an API key, retain `https://api.openai.com/v1`, and choose a model that supports Chat Completions, streaming, and tools. The default is **`gpt-6.1-sol`**, replacing the previous `gpt-4.1-mini` setup. Existing OpenAI mini-model settings upgrade automatically; custom providers/models are preserved. Your API account must have access to the selected model; use Test connection to verify it. No paid request is silently downgraded to a smaller model. API usage may incur provider charges.
   - **Ollama:** install [Ollama for Windows](https://ollama.com/download/windows), run `ollama pull qwen3:8b`, select **Ollama · local**, and test the connection. Ollama must stay running. Use a tool-capable model; local model quality and speed depend on your computer.
   - Other providers work if their API supports OpenAI-style streaming Chat Completions and tool calls. Supply the provider's base URL, model, and key.
5. Click **Test connection**. This saves your settings and checks the actual JARVIS tool schemas, a streamed function call, and a response after receiving its result. If your model fails, **Find compatible model** checks models listed by your provider and saves a replacement only after it passes the same check. These small requests may incur API charges; the checks run no computer actions. No model, account, or credentials are bundled with JARVIS.
6. Set your name. **Automatic execution** and **Full computer access** are enabled by default, including on upgrades. Your user folder is the starting location for relative paths; absolute paths on other drives also work when your Windows account permits them.

### If Windows blocks the batch launcher

This is a source-code distribution, not a signed Windows installer. If Smart App Control appears when opening **`Start JARVIS.bat`**, try starting the source through your installed Python:

1. Extract the entire ZIP and open the folder containing `start.py`.
2. Click File Explorer's address bar, type `cmd`, and press Enter.
3. Run `py -3 start.py`. If you installed Python on PATH without its launcher, run `python start.py` instead.

`start.py` creates the environment, installs dependencies with pip's normal verification enabled, and opens the dashboard. It reuses dependencies on later starts without requiring another download. This path does not change Windows security settings. It requires a Python installation Windows permits to run; install Python from python.org if necessary.

If the server fails after setup, `start.py` saves its full output in `.jarvis-data\startup.log` in the extracted app folder and repeats the last lines before closing. A busy port gets a specific explanation. For an older launcher that only says “JARVIS could not start”, open Command Prompt in that same folder and run `.venv\Scripts\python.exe run.py --no-browser`, then copy the traceback or ERROR lines it prints. This exposes the underlying error without reinstalling dependencies or changing saved data.

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

Voice pauses while you use another JARVIS page or an item dialog. By default the conversation can continue while you use other browser tabs, including sites JARVIS opens for you. Turn off **Keep talking while I use other browser tabs** in Voice settings to pause when the dashboard is hidden. Browser microphone/speech-service restrictions can still affect background recognition. In the default automatic mode, your request authorizes the available actions needed to complete it, with no JARVIS approval cards. Say **“cancel task”** while JARVIS is working to stop further steps. Permission, model, and speech-service errors stop the voice loop with an explanation. This is a hands-free browser session, not a background wake-word service; keep the dashboard open, and start a new voice session after reloading it. Browser recognition may use an online speech service and requires working microphone permissions.

### British accent

Open **Settings → Voice & conversation**. The default **Automatic** quality uses OpenAI’s `gpt-4o-mini-tts` with Cedar and natural British accent instructions whenever an OpenAI speech key is available. Marin is another natural voice option. Reply text is generated as complete passages, with following passages prefetched during playback for smoother cadence. These are AI-generated voices; reply text is sent to OpenAI and speech API charges apply. No voice sample or upload is needed.

An OpenAI chat key is reused automatically. For Ollama or a different chat provider, enter a separate **OpenAI speech API key**, or set `JARVIS_SPEECH_API_KEY`. A third-party provider key is never sent to OpenAI. Keys remain on the local server and generated audio is not saved. Choose **Hear a sample** to save/test the voice settings, or select **Installed browser voice** for speech without an OpenAI synthesis request.

Browser fallback prefers available British Natural/Neural voices such as Microsoft Ryan Natural over older voices such as George, while preserving an explicitly selected browser voice. Voice availability depends on Windows and the browser. Add an English (United Kingdom) speech voice under **Windows Settings → Time & language → Speech** if needed, then restart the browser. If natural generation fails, the app explains its fallback and continues using the installed voice. Skip/end/navigation abort pending speech and prevent late audio from restarting the microphone.

Natural speech now uses an original professional British delivery: precise diction, composed confidence, restrained warmth and brief natural pauses. Long replies generate a short opening passage and the following passage concurrently, so playback can start while later audio is still being generated. Completed recognition results use a 650 ms pause before sending; unfinished speech retains the longer pause and further words reset the timer. Use **Settings → Voice & conversation → Natural British voice → Cedar** and **Hear a sample** to try the delivery.

The stronger model ID and TTS voice/input contracts were checked against the [official OpenAI SDK model list](https://github.com/openai/openai-python/blob/main/src/openai/types/shared/chat_model.py) and [speech request schema](https://github.com/openai/openai-python/blob/main/src/openai/types/audio/speech_create_params.py). Configuration and mocked protocol tests do not establish this account’s access or the quality of a live synthesized voice.

## Blue display and live task flow

The front page shows only the voice circle and the live task flow, with minimal controls. Conversation text, typing and detailed plans are hidden in the optional conversation panel, opened with the speech-bubble button at the top. Saved conversation memory is retained. The animated cyan/blue SVG instrument uses concentric rings, fine ticks, radial segments and dots. Rings gently rotate while idle. When you speak, an optional local Web Audio microphone meter drives the radial bars; when JARVIS replies, natural audio energy or browser speech word events pulse the display. Browsers without those events still show a speaking animation. Reduced-motion preferences disable the movement. Microphone metering starts only in an active listening session and stops when listening ends or the tab is hidden.

The **Task flow** panel creates connected boxes from real tool events: your request → JARVIS → the actions it performs. Boxes show running, completed, failed, cancelled and awaiting-approval states. A completed input means the tool delivered it; app results and the action log show what was verified. Plans remain separate from executed actions. Navigation and settings are tucked behind the menu and gear buttons. The graph remains visible below the circle on small screens; **Activity** provides the detailed log. Reloading a conversation restores its latest task and actions.

## Computer access and autonomy

The default **Automatic** execution mode treats your request as advance authorization for the actions needed to fulfill it. JARVIS does not ask for a second approval before commands, file changes, app launches, clicks, scrolling or explicitly requested consequential actions. It records and executes the plan, observes results and continues. It may ask for missing details such as which document or recipient you mean. Time/action budgets, cancellation, backups and file-change checks remain active. Windows authentication, UAC, sign-ins, administrator boundaries and security controls still apply; JARVIS does not bypass them.

**Full computer access** allows file tools to resolve any path accessible to your Windows account, including other drives, attached locations, hidden/system folders and the local JARVIS data directory. Relative paths start in the configured default folder. A search without a directory considers default folders and available drives under a bounded time/file budget and reports when it was limited, so JARVIS can narrow the next search. Access does not change filesystem permissions or grant administrator rights.

Existing installations migrate once to these automatic/full defaults. Model connections, names and stored memory are preserved. Later choices remain saved. **Settings → Execution** offers optional routine/review modes if you choose them, and **Computer access → Only selected folders** can restore folder restrictions. Old queued approvals are retired without executing stale effects; make a fresh request to continue that work.

**Applications** indexes Start Menu entries and shortcuts, Get-StartApps, installed packages/AUMIDs and protocols, registry App Paths and uninstall display registrations, PATH, Steam/Epic libraries and bounded scans of application folders you approve. It never executes uninstall registry strings. Ask “Open Outlook” or “Show my emails”; ambiguity between installed editions is reported. Use application preferences to map “my emails” to a particular edition. A running matching window is focused first; new activations use the indexed executable, shortcut, URI or AUMID, wait for a matching visible window, then remember successful methods. An activation without a matching window is reported as unverified. **Check launch** tests an indexed ordinary app without an AI connection.

Before answering, JARVIS checks the current task's results and re-observes application windows. Launch confirmation requires application identity and observed foreground focus; a browser tab/document title mentioning Spotify or a launcher PID alone is insufficient. Speculative tool-call narration is withheld, and an unsupported success claim is replaced with the observed limitation before it reaches speech or saved conversation text. Ordinary replies do not require an extra model evaluation request. Shared browser apps without a distinct observable identity can remain unverified.

To reduce response delays, the model context retires old screen/control payloads, includes images only from the current request, and reuses memory/planner snapshots during tool continuations. Saved history, files and memory remain intact; full facts can still be retrieved through tools. Windows drive listing uses the configured drive-letter mask without probing offline disks. Verified reply playback also starts without waiting for sidebar/history refresh requests. These changes preserve the selected model; live response time still depends on the provider, connection and application startup.

Desktop tools inspect Windows accessibility elements, click, double-click, right-click, scroll, enter text, navigate, close windows normally and capture a specific window for a vision-capable model. Structured UI Automation is preferred; window-relative coordinates are available when needed. Scroll actions are bounded and observe the resulting window/page state. Browser-native context menus can require desktop control because they are outside the page DOM. Applications with inaccessible/custom controls, elevated windows or unusual launchers may need your help. Classic Outlook inbox search uses an existing signed-in MAPI session; New Outlook uses accessible UI or the configured browser.

If accessibility is unavailable, JARVIS can continue in the same browser using native window screenshots, coordinate clicks, literal Unicode typing, keys and scrolling. These paths do not import UI Automation. Screenshot metadata accounts for preview scaling and the client area's offset inside the outer window. Inputs check the window identity, foreground focus and cancellation; clicks also reject points covered by another window. A delivered input is reported separately from verification, and a failed observation does not replay it. Visual recovery requires a vision-capable model and may still be blocked by Windows permissions or an application that cannot be captured.

The error **`WinError -2147417850` / `0x80010106` / “Cannot change thread mode after it is set”** identifies a COM initialization conflict, not missing packages. JARVIS preserves a worker's existing STA/MTA apartment and initializes only uninitialized workers, avoiding the previous conflicting initialization. Other accessibility failures retain their technical message and HRESULT in Activity; reinstall guidance is limited to import/dependency errors.

**Current browser** is the default on Windows: JARVIS selects a foreground supported browser (or an already open browser), focuses it and opens websites/web apps in a new tab. Your existing tabs and sign-ins remain in that browser; cookies/passwords are not extracted or copied. Chrome, Edge, Firefox, Brave, Opera and Vivaldi are supported browser families. If no browser is open, Windows opens its default browser. Native accessibility controls provide page interaction; when a page is inaccessible, window screenshots and native controls can be used. A delivered address-bar input is not claimed verified unless the observed URL matches; redirects/sign-in pages may require another observation.

Select **Settings → Execution → Websites & online applications → separate managed Edge profile** only when you want Playwright DOM controls and a separate session. It never attaches a debugging port to your personal browser or relaunches it with security-altering flags. On Linux/cloud, browser integration tests use managed Chromium. Native browser profile reuse still needs validation on the Windows PC.

Window controls explicitly support **maximize**, **restore**, **minimize** and verified **focus/bring to front**. Focusing preserves a maximized window’s size and restores only a minimized window. **Fullscreen** uses F11 for supported browser windows and checks their observed geometry/chrome; other apps support maximize, with app-specific full-screen controls when available. If Windows denies foreground focus, that result is reported rather than claimed successful.

File tools list, search, read, create, edit, move and open files across the selected access scope. Text and Word `.docx` are supported. Exact-text and font/spacing edits preserve existing Word tables, images, styles and unaffected runs. Full `.docx` replacement still removes rich content, so the assistant is instructed to prefer preserving edits and report formatting loss. Files are backed up before replacement, and fingerprints prevent overwriting a file changed during the operation. **Activity → Undo this file change** can restore a backup, reverse a move, or archive a file JARVIS created; it refuses if that file has since changed. PDF and Excel editing are not implemented.

**Stop task**, **Escape** in the dashboard, **Ctrl+Alt+F8** globally on Windows (when registration succeeds), or **“cancel task”** during the task’s listening phase cancels further steps. Cancellation stops owned command processes and interrupts waiting for model output. Native operations already delivered to an app may finish; completed effects remain in Activity and are not automatically reversed. Speech cancellation is paused while JARVIS is speaking to prevent self-echo; use the visible stop button or hotkey then. Moving the mouse to PyAutoGUI’s fail-safe corner also stops pointer automation. Windows may reserve the global hotkey; Activity reports its availability.

File, email, browser and screenshot contents are treated as untrusted task data in model instructions. They cannot supply user authorization. The execution policy derives UI controls from inspected metadata rather than a label supplied by the model, and logs the consequence classification even when automatic mode runs the action. Check important results: a model can still make mistakes interpreting a request or document.

Full access is the default; optional folder limits apply to file tools. Applications and commands run with your account’s permissions. This cloud workspace cannot control your Windows PC: run JARVIS on the computer you want it to access.

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

With Node.js 18+ installed, run `node --test tests/voice.test.js tests/speech.test.js` for speech turn-taking, duplicate-result handling, approval pauses, British voice selection, and error recovery tests. These use simulated speech APIs; a real microphone, installed Windows voices, and browser speech-service access still need a check on the target PC.

For this cloud development workspace, run `bash scripts/setup-cloud.sh`, then `JARVIS_DATA_DIR="$PWD/.jarvis-data" .venv/bin/python run.py --no-browser`. Run `.venv/bin/python scripts/check-ready.py` from another terminal to check the dashboard and authenticated planner API. Cloud validation exercises the app on Linux; native Windows app launches, microphone permissions, and Windows notifications still need testing on your PC.

`jarvis/assistant.py` manages streaming chat and request-scoped execution; `execution.py` handles policy and logs; `applications.py`, `desktop.py` and `browser.py` implement the Windows and browser adapters; `provider.py` implements the model protocol; `computer.py` implements local tools; `store.py` provides persistence; `scheduler.py` delivers reminders; `app.py` serves the local API and interface. Tests use a deterministic model fixture; they do not establish live model quality or Windows-native notification behavior.

If startup fails, the Windows launcher keeps the error window open. Dependency installation output is saved to `.jarvis-data\setup.log` in the extracted app folder. Share the exact error text when asking for help. If a ZIP was not fully extracted, extract it first. If Python was updated and the old environment no longer works, rename the app folder's `.venv` to `.venv-old` and restart; this leaves your separate JARVIS memory/settings directory untouched. If port 8765 is already in use, close any other JARVIS window, or run `"Start JARVIS.bat" --port 8766` from Command Prompt. `"Start JARVIS.bat" --setup-only` installs dependencies without opening the dashboard.

A missing model key affects chat, while the planner, file browser, and memory editor work independently. An HTTP 400 now includes the provider's bounded, redacted explanation and any error code, field or request ID. If an application opens and work then stops, the provider can have rejected the next model request containing the tool result; an app launch alone does not verify that the remaining task completed. The failed task's flow box and Activity show that error.

In **Settings → Intelligence**, run **Test connection**, then **Find compatible model** if needed. Discovery first checks the selected model, then tries up to three other suitable model IDs returned by your provider. It may take about 100 seconds. It never silently changes models during a task, replays computer actions, or saves a candidate that failed the check. Authentication and billing errors require fixing the provider account; if model listing is unavailable, select a tool-capable Chat Completions model supported by that provider manually. A listed model or an SDK model name alone does not prove tool support or account access.

For OpenAI's `gpt-6.1-sol`, JARVIS sends `reasoning_effort: "none"` whenever function tools are included. This model's default reasoning mode rejects tools on `/v1/chat/completions`; the explicit setting follows the provider's compatibility guidance. It keeps the selected model and disables its separate reasoning mode for these requests. The same setting applies to connection checks and every tool-result follow-up. Other model IDs and other providers retain their existing request format. This fix does not retry failed requests or repeat earlier computer actions.

Work continues if the dashboard connection drops until completion, cancellation, or a task limit; reload the conversation to see the saved outcome. Interrupted commands are not automatically rerun on restart.

### Validation and native Windows checks

Run `.venv/bin/python -m pytest -q` and `node --test tests/voice.test.js tests/speech.test.js`. Browser coverage additionally uses Node Playwright with a Chromium executable: with the local server running, run `node tests/dashboard.browser.cjs`. Set `JARVIS_TEST_BROWSER` to the browser executable on another machine. It tests the clean front page, optional transcript/typing panel, no-approval automatic turns, live-event graph rendering, HUD animation, reduced motion, mobile layout, connection diagnostics/repair and simulated hands-free turn-taking; optional routine-mode approval behavior is checked separately. Python browser integration tests use real local Chromium when installed; model/desktop tests use deterministic adapters. They do not establish live provider quality, microphone recognition accuracy or native Windows behavior.

Native checks are deliberately opt-in because they open installed apps. In an interactive Windows Command Prompt:

```bat
set JARVIS_WINDOWS_LIVE=1
.venv\Scripts\python.exe -m pytest tests/test_windows_live.py -v -s
```

These enumerate real apps and check visible launches for installed Notepad, Store Calculator and Outlook editions. Missing apps are skipped. Existing windows are never killed; close any test windows yourself. On Linux these checks are skipped. You can also use **Applications → Check launch** on your Windows PC and inspect **Activity** without enabling the native test suite.

Windows ARM64 wheel availability was checked for the pinned native dependencies, including Python 3.15 wheels for pywin32, Pillow and greenlet and stable-ABI psutil. This is a packaging check, not a native Windows execution test. Installer or Smart App Control failures still need the exact error from that computer; no security checks are disabled.

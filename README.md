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

Voice pauses while you use another app page, an item dialog, or a background browser tab. It resumes when you return to the Assistant. Computer actions still wait for on-screen approval. Permission, model, and speech-service errors stop the voice loop with an explanation. This is a hands-free browser session, not a background wake-word service; keep the dashboard open, and start a new voice session after reloading it. Browser recognition may use an online speech service and requires working microphone permissions.

### British accent

Open **Settings & connections → Voice & conversation**. JARVIS automatically prefers an available **English (United Kingdom)** voice. Choose another voice from the list, adjust the speaking speed, and click **Hear a sample**. Voice preferences save automatically in that browser. No voice recording, upload, or extra API key is required.

Available voices depend on Windows and your browser. If no British voice appears, add an English (United Kingdom) speech voice under **Windows Settings → Time & language → Speech**, then restart the browser. The app explains when it must fall back to the browser voice. This uses stock speech voices; it does not clone a person's voice.

## Computer access

File tools can list, search, read, create, replace, move, and open files in the folders you select. Text files and Word `.docx` are supported. JARVIS can open Windows apps and execute commands with your account's permissions. App launches, command execution, and file changes show an explicit approval card first. Declining sends the decision back to the assistant.

Existing files are copied into the local `backups` directory before replacement. A changed-file fingerprint prevents an edit if the file changed after approval was requested. `.docx` writes rebuild paragraphs and **replace existing formatting, tables, and embedded content**; the original is backed up. PDF, Excel, rich Word editing, mouse/keyboard automation, and an always-listening wake word are not implemented.

Folder limits apply to JARVIS file tools. Programs and commands you approve can use your Windows account's wider access. JARVIS does not elevate itself to administrator. This cloud workspace cannot control your Windows PC: run the application on the computer you want it to access.

## Memory and reminders

- Conversations, tasks, goals, reminders, approval outcomes, and curated memories are stored in SQLite. Conversations automatically carry into later sessions; relevant excerpts from other conversations and saved facts can be included in model context.
- Review, edit, or delete facts on the **Memory** page. Delete a conversation using its × button in the sidebar. Saved facts and planner entries are independent: deleting a conversation does not erase them. Deleting a saved memory does not remove mentions from conversation history.
- Reminders are one-time reminders. The background scheduler checks every five seconds while the server runs. Overdue reminders fire on the next startup. You can dismiss or delete them in the planner.
- Windows reminders use a native notification-area balloon. Notification visibility depends on Windows notification settings and your interactive desktop session. Browser notifications are optional; reminders also remain visible in the dashboard. Browser notifications need an open dashboard.
- The assistant uses your computer's local time zone. Reminder timestamps are stored in UTC and displayed in the browser's local time.

## Where data lives

On Windows the default directory is `%LOCALAPPDATA%\JARVIS`. On Linux/macOS it is `~/.local/share/JARVIS`. Set `JARVIS_DATA_DIR` to override it. This directory contains `jarvis.db`, `settings.json`, and `backups/`. Copy the entire directory while JARVIS is stopped to back up your assistant.

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

`jarvis/assistant.py` manages streaming chat and approved tool execution; `provider.py` implements the model protocol; `computer.py` implements local tools; `store.py` provides persistence; `scheduler.py` delivers reminders; `app.py` serves the local API and interface. Tests use a deterministic model fixture; they do not establish live model quality or Windows-native notification behavior.

If startup fails, the Windows launcher keeps the error window open. Dependency installation output is saved to `.jarvis-data\setup.log` in the extracted app folder. Share the exact error text when asking for help. If a ZIP was not fully extracted, extract it first. If Python was updated and the old environment no longer works, rename the app folder's `.venv` to `.venv-old` and restart; this leaves your separate JARVIS memory/settings directory untouched. If port 8765 is already in use, close any other JARVIS window, or run `"Start JARVIS.bat" --port 8766` from Command Prompt. `"Start JARVIS.bat" --setup-only` installs dependencies without opening the dashboard.

A missing model key affects chat, while the planner, file browser, and memory editor work independently. If a provider rejects tools, choose a tool-capable model or check its API compatibility. Actions continue to finish if a browser connection drops; reload the conversation to see the saved outcome. Interrupted commands are not automatically rerun on restart.

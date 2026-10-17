# Jarvis

A small AI assistant that runs on this laptop. It talks to a local model through Ollama, needs no pip packages, and keeps everything on your own machine unless you add a cloud key.

## Start

Double-click `start_jarvis.bat`. It starts Ollama if it is not running, then opens http://127.0.0.1:8765 in your browser. Closing the window stops Jarvis.

Other ways to use it, from this folder:

- `python main.py` chats in the terminal.
- `python main.py ask "your question"` asks one question and prints the answer.
- `python main.py doctor` checks that everything is set up.
- `python main.py --help` lists every command.

To use Jarvis from your phone, see "Phone" below.

If the terminal was opened before the setup, set `JARVIS_HOME` to your Jarvis data folder in that terminal first. Otherwise Jarvis writes its data to `C:\Users\User\.jarvis`.

## Phone

Double-click `start_jarvis_phone.bat` instead of `start_jarvis.bat`. The console prints a link for your phone, and the web page on the PC gets a Phone button that shows the same link with a Copy button. Send the link to yourself, open it on the phone while it is on the same Wi-Fi as this PC, then add it to the phone's home screen from the browser menu. The link stays the same on every start.

- The link carries a secret token, so anyone who has it can chat with Jarvis. Treat it like a password. To get a new link, set a new token with `python main.py config set server.token NEW_SECRET`, using long random text such as the output of `python -c "import secrets; print(secrets.token_urlsafe(18))"`. The old link stops working.
- Actions that change files or run commands can only be approved on the PC. Asked from the phone, they are refused. The default lite preset has none of them.
- Typing works on the phone. The microphone does not, because browsers allow it only on https pages and this link is plain http.
- The PC has to be on, awake and running Jarvis. The link is the PC's address on your router (shown in the console and behind the Phone button), which can change after a restart. The console and the Phone button always show the current one.
- Windows lists this Wi-Fi as a Public network. `python.exe` already has an inbound allow rule for Public networks, so Windows did not need a change. If a phone cannot connect, check Windows Defender Firewall first.
- I checked this through the PC's own network address with the real model. I have not tried it from a phone.

## What you need

Python 3.9 or newer, and Ollama with two models:

- `qwen3:4b-instruct` answers questions.
- `nomic-embed-text` is optional. It lets memory and document search match by meaning, not only by words.

Use `qwen3:4b-instruct`, not plain `qwen3:4b`. The plain tag is a thinking model that writes out its reasoning before every answer, and that reasoning shows up in replies.

## Where things are

- The program: the project folder (this folder).
- Ollama: its install folder, with its models in your Ollama models folder.
- Jarvis data (database, settings, the workspace folder, saved reports): your Jarvis data folder (set by `JARVIS_HOME`).
- Earlier versions of the project: `backup\`.

## Speed on this laptop

This laptop has no GPU, so Ollama runs the model on the CPU, and the size of the prompt decides how fast an answer starts. Measured on 2026-10-17 with `qwen3:4b-instruct`:

| Situation | Time |
|---|---|
| Plain question, lite preset | 1 to 2 s |
| Question that uses a tool, lite preset | 5 to 7 s |
| Question that uses a tool, jarvis preset (25 tools) | about 14 s once its prompt is cached; the first one is slower (a minute is my estimate) |
| First question after Jarvis starts | about 4 s, because a warm-up runs at start |
| First question after 30 minutes without use | 25 to 30 s, while the model loads again |

What makes it fast:

- The default preset is `lite`: 8 tools and a short persona, about 1,000 tokens (Ollama counted 1,031) instead of an estimated 3,000 for the `jarvis` preset.
- The date and time travel with your question instead of sitting in the system prompt. A prompt that changes every minute forces Ollama to read it again.
- Ollama uses all 12 CPU threads (`engine.num_thread`). That read the prompt about 45% faster than its default in a test on this machine.
- The model stays in memory for 30 minutes after the last question (`engine.keep_alive`), which costs about 4 GB of RAM.

Settings live in `config.json` in your Jarvis data folder. Show them with `python main.py config show` and change one with `python main.py config set engine.num_thread 8`.

## Presets

Choose one with `-p NAME` in the terminal or the menu at the top of the web page. Set the default with `python main.py config set agent.preset NAME`.

- `lite` (default): calculator, clock, web search, page fetch, remember and recall, and protocols.
- `chat`: no tools. The model can get arithmetic wrong without the calculator.
- `jarvis`: all 25 tools, including files, shell and Python. These ask before they act.
- `code` and `research`: smaller tool sets for programming and for looking things up.

## What was left out

Telegram, Home Assistant and terminal voice mode were removed because this setup has no use for them. The phone link above replaces Telegram. Voice input and output still work in the web page. Cloud models are off until you set `ANTHROPIC_API_KEY`, and the energy figures are not shown because this laptop has no power counters.

## Checking and undoing

- `python -m unittest discover tests` runs 20 checks in about three seconds. They do not need Ollama.
- `backup\jarvis_single_file.py` is the original single-file version. `backup\before-trim.zip` and `backup\before-removals.zip` hold the project at two earlier points. `PLAN.md` records what was changed and why.

## Code layout

`main.py` reads the command line and calls into the `jarvis` folder:

- `agent.py`, `router.py`, `engines.py` and `presets.py` talk to the model.
- `tools/` holds the tools. The order of the imports in `tools/__init__.py` is the order the model sees them.
- `memory.py`, `documents.py` and `database.py` store facts and documents.
- `server.py` and `gui.html` are the web page and its API.
- `cli.py` is the terminal chat, `doctor.py` the setup check, and `security.py` the folder and command limits.

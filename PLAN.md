# Jarvis plan for this laptop

## Goal

A small local assistant that starts with one double-click, answers quickly on this laptop, and does a short list of useful things well. Everything is installed on D:.

The laptop is an Intel Core 7 150U with 16 GB of RAM, no GPU (Intel Graphics with 128 MB) and Windows 11. C: has about 41 GB free and D: about 225 GB. Ollama runs the model on the CPU only, so the size of the prompt sent to the model is what decides how fast Jarvis feels.

## What "simple" means here

- One way to start: double-click `start_jarvis.bat`, or run `python main.py`.
- No pip packages. Only the Python standard library and Ollama.
- A plain question is answered in under 5 seconds. A question that uses a tool is answered in under 30 seconds.
- Features that need hardware or accounts this laptop does not have are removed. Everything else stays.
- A short README and one small test file, so later changes do not break it.

## Baseline (measured on 2026-10-17, model qwen3:4b-instruct)

| Preset | Tools | Prompt size (estimated tokens) | Plain question | Question with a tool |
|---|---|---|---|---|
| chat | 0 | 50 | 3.9 s | not available |
| lite (default) | 8 | 930 | 3.4 s | 36.5 s |
| jarvis | 27 | 3,000 | not measured | 114 to 131 s |

The model writes 6 to 14 tokens per second. A question with a tool makes two model calls, and each call first reads the whole prompt.

## What stays and what goes

Removed, because they need something this laptop or its owner does not have:

- The Telegram bot. It needs a bot token and sends messages out of the machine.
- Home Assistant. It needs a Home Assistant server. This covers the smart home tools, the `home` command, the `ha_state` watcher and the home steps in protocols.
- Terminal voice mode (the `voice` command). It needs numpy, sounddevice and faster-whisper. Voice in the web app stays.

Changed:

- Energy numbers are no longer shown on this machine. They are 25 watts times the elapsed time, which is a guess. Real counters are still used on machines that have them.

Kept as is:

- Cloud engines and routing stay switched off because there is no API key.
- Document search, memory, the scheduler, the morning brief, the battery and disk watchers, and the `jarvis` preset with every tool (27 at the start, 25 after Home Assistant was removed) for the rare case where speed does not matter.

## Steps

Each step is done when its check passes. Boxes are ticked as the work is finished.

### 1. Safety copy

- [x] Zip `main.py` and `jarvis/` into `backup\before-trim.zip`. Check: the zip exists and lists the files. The original single file is already in `backup\jarvis_single_file.py`.

### 2. Speed

- [x] 2a. Move the clock out of the system prompt and into the message that is sent. The clock changes every minute and sits before the tool list, so Ollama probably cannot reuse its cached prompt for a follow-up question. Check: a follow-up asked more than a minute after the first is faster than before. If it is not, revert. Result: the first question in a new minute went from 27 s to 2.8 s.
- [x] 2b. Try 4, 6, 8 and 12 CPU threads on a fixed prompt. Add an `engine.num_thread` setting only if one value is at least 15% faster than the default. Result: 12 threads read the prompt at 51 tokens per second against 35 for the default (+46%), and generate at 10.0 against 8.5 (+18%), over 3 to 4 runs each. The setting is added and set to 12 in this laptop's config. The code defaults are now the lite preset, a context of 8192 and the model `qwen3:4b-instruct`.
- [x] 2e. Added during step 2: a warm-up that loads the model and caches the default prompt when the web app or terminal chat starts. Result: with the model unloaded, the first real question took 4.1 s instead of 27 to 39 s.
- [x] 2c. Point the two benchmark questions that use tools at the lite preset, run `python main.py bench`, and write accuracy and speed in the results table below. Result: 83% on the first run. The model skipped the calculator on a large multiplication and guessed a wrong number, so the lite persona now says to use the calculator for any calculation with more than two digits. After that: 92% (11 of 12), 2.1 s per answer. The one miss is a no-tools chat question.
- [x] 2d. Only if a question with a tool still takes over 30 seconds: pull `qwen3:1.7b`, run the same benchmark and choose the model from the numbers. Skipped: a question with a tool now takes about 6.5 s.

### 3. Windows fixes

- [x] Read battery with `GetSystemPowerStatus`, memory with `GlobalMemoryStatusEx` and CPU with `GetSystemTimes`, all through the built-in `ctypes`. Today the battery check starts a PowerShell process every time, and with the HUD on the web page that happens every 5 seconds. Memory and CPU readings are missing on Windows. Check: `system_info`, the RAM watcher and the HUD gauges show values. Result: the battery reading takes 2 ms; memory (15.7 GB total), CPU and battery now show in `system_info` and in the HUD data. The optional `psutil` code is gone.
- [x] Make the reasoning filter handle a closing `</think>` that has no opening tag, so a thinking model cannot show its reasoning in replies. Check: a unit test with the exact text the model returned earlier. Result: the saved and final answer is clean ("OK"). Text streamed live to the terminal still shows the reasoning from a thinking build, so keep using `qwen3:4b-instruct`. The unit test comes in step 6.
- [x] Stop showing energy numbers when the machine has no power counters. Check: the line under a reply no longer ends with a joule figure. Result: done, and the 60 old estimated rows in the stats database were cleared.

### 4. Remove what this laptop cannot use

- [x] Telegram bot, then Home Assistant, then terminal voice mode, one at a time. Check after each: no leftover imports or undefined names, `python main.py doctor` runs cleanly, and the command list and help no longer mention it. Result: all three removed (`telegram.py`, `home.py`, `tools/home_tools.py` and `voice.py` are deleted). The static check reports no undefined names or unused imports, `doctor` and the protocols still run, and the command list no longer has `telegram`, `home` or `voice`. A second snapshot taken just before this step is in `backup\before-removals.zip`. The long description at the top of `main.py` still mentions them and is rewritten in step 6.

### 5. Laptop defaults and launcher

- [x] Pull `nomic-embed-text` (about 270 MB) so memory and document search also match by meaning. Result: installed on D:. "Favorite drink" now finds "likes green tea", which shares no words with it. The similarity cut-off was 0.35, which let unrelated queries match (unrelated scores were 0.37 to 0.41, related 0.53 to 0.77), so it is now a setting, `engine.embed_min_similarity`, set to 0.47.
- [x] Keep the model in memory between questions. Changed from the plan: instead of an Ollama environment variable and a restart, Jarvis sends `keep_alive` with each request (setting `engine.keep_alive`, default 30m). The memory cost is about 4 GB while the model is loaded.
- [x] Add `start_jarvis.bat`. It starts Ollama if it is not running, then starts the web app and opens the browser. Check: double-clicking it gives a working chat page. Result: tested with Ollama running (the page is served in under a second) and with Ollama stopped (Ollama is back after 11 s, the models on D: are visible, Jarvis answers). Ollama also has its own startup shortcut, so it normally starts at login.
- [x] Scheduled tasks and the `schedule` command now default to the lite preset instead of the 27-tool preset.

### 6. Tests and README

- [x] `tests/test_basics.py`, using only `unittest`. It covers the calculator, the security rules, the tool list, the agent loop with a fake model, the reasoning filter and the web endpoints. Check: `python -m unittest discover tests` passes. Result: 16 checks pass in about a second, with no Ollama needed. They also guard the two speed fixes (no clock in the system prompt, `num_thread` and `keep_alive` sent to Ollama) and the removed features. The test run cleans up its own temporary folder.
- [x] `README.md`: how to start, where the files live, how to change the model and the preset, and how to undo a change. Result: written, with the measured times.
- [x] Also done in this step: the long description at the top of `main.py` was rewritten to match the trimmed project, and the unused `assistant` preset (the same tools as `jarvis`) was removed.

### 7. Final check

- [x] Run the static checks, the tests and `doctor`. Time a plain question and a question with a tool. Fill in the results table. Result: no undefined names or unused imports, 16 of 16 tests pass, `doctor` shows no errors (the only warning is the optional cloud key), and the JavaScript in the web page passes `node --check`.

## Results

| Measure | Before | After |
|---|---|---|
| Plain question | 3.4 s | 1.0 to 1.6 s |
| Question with a tool | 36.5 s | 5.4 to 7.2 s |
| Question with a tool, jarvis preset (25 tools) | 114 to 131 s | about 14 s once its prompt is cached |
| First question after the model was idle | 27 to 39 s | about 4 s after a restart (warm-up), 25 to 30 s after 30 idle minutes |
| Benchmark (12 questions) | 83% | 92%, 2.1 s per answer |
| Lines of Python | 4,993 in 42 files (after the split) | 4,550 in 39 files, largest file 438 lines |
| Pip packages needed | 0 | 0 |
| Features removed | none | Telegram, Home Assistant, terminal voice mode |

The after column was measured on 2026-10-17 with `qwen3:4b-instruct` and 12 CPU threads.

## Phone access (added later the same day)

Telegram was already removed in step 4. In its place, Jarvis can be opened from your phone on the same Wi-Fi through a private link. No account and no bot are involved.

Decisions:

- A second launcher, `start_jarvis_phone.bat`, turns this on. The normal launcher stays PC-only.
- The link carries a secret token that is generated once and saved in the settings. Anyone who has the link can chat with Jarvis, so it is shared like a password.
- Actions that change files or run commands can only be approved on the PC. A request from the phone that needs approval is refused. The default lite preset has no such actions.
- The phone link is shown in the console and behind a Phone button in the web page on the PC.
- Microphone input will not work on the phone, because browsers only allow the microphone on https pages and this link is plain http. Typing works.
- Windows marks this Wi-Fi network as Public, and `python.exe` already has inbound allow rules for Public networks, so no firewall change is needed.

Steps:

- [x] P1. Find this PC's address on the network and print the phone link. Open the PC browser on the local address. Result: done (`lan_ip()` in `sysutils.py`). The console prints the PC link and the phone link. The token is now used only when Jarvis listens on the network, so the normal PC-only launcher and plain bookmarks keep working.
- [x] P2. Refuse risky-action approvals that come from another device. Result: done. A request from another device gets its risky actions refused at once, and `/api/approve` returns 403 for it.
- [x] P3. Add a Phone button and a link window to the web page. Result: done. The button shows only on the PC and only when phone mode is on. The page script passes `node --check`.
- [x] P4. Add `start_jarvis_phone.bat`. Result: done. It starts Ollama if needed, then `python main.py gui --host 0.0.0.0`.
- [x] P5. Tests for the token and the approval rule, then a live check through this PC's network address. Result: 4 new tests (20 in total) pass. The live check ran the real server through 192.168.18.246: the page loads, the API gives 401 without the token and 200 with it, approval from that address gives 403, and a chat question was answered in 0.9 s. It was not tried from a real phone.
- [x] P6. Update the README. Result: done, with a Phone section and its limits.

## Undo

The state right before step 4 is in `backup\before-removals.zip`, and the state before any change in this plan is in `backup\before-trim.zip`. The original single file is in `backup\jarvis_single_file.py`.

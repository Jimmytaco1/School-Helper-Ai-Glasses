# Troubleshooting

Find the message you see, then try the fix. Your notes are safe in every case below.

## Ollama (the AI)

| What you see | What to do |
|---|---|
| **"Ollama kept failing (error 503)"** or "busy or low on memory" | 1. Close other programs, especially browsers with many tabs. 2. Quit Ollama from the system tray (bottom right) and open it again. 3. In PowerShell run `ollama ps`: it should list `qwen2.5vl:3b`. 4. If it still fails, open `notebook_config.py` and change `OLLAMA_CONTEXT = 4096` to `2048`. 5. Still failing? Run `Get-Content $env:LOCALAPPDATA\Ollama\server.log -Tail 30` and read the last lines: they usually say why. |
| **"Can't reach Ollama"** | Ollama is not running. Open the Ollama app and look for its icon in the system tray. If you use a VPN or proxy, restart it after Ollama is running. |
| **"Ollama does not have the model qwen2.5vl:3b"** | `ollama pull qwen2.5vl:3b` |
| Everything is very slow | The model may be running on the CPU. Run `ollama ps`: the PROCESSOR column should say `100% GPU`. Close memory-hungry programs. |
| Replies are cut off or forget earlier parts of a long tutor session | The model reads limited text at once. Raise `OLLAMA_CONTEXT` to 8192 if you have spare memory. |

## The window

| What you see | What to do |
|---|---|
| **`No module named tkinter`** | Re-run the Python installer, choose **Modify**, and tick **tcl/tk and IDLE**. |
| `No module named ...` (anything else) | `pip install -r requirements.txt` |
| `python` is not recognised | Python was installed without "Add python.exe to PATH". Re-run the installer and tick it. |
| The window says the inbox watcher did not start | Read the message in the **Activity** tab. Usually the inbox and notebook folders are the same or one is inside the other: fix `INBOX` / `NOTEBOOK_ROOT` in `notebook_config.py`. |
| **"No notes found"** | Notes are found by subject, term, week and day. Ask with a subject, for example `summarise physics this week`. Check the **Storage** tab shows the notebook folder you expect. |

## Filing and dates

| What you see | What to do |
|---|---|
| A page was filed under the wrong day | Write the date at the top of each page, day first (`21/10/2026`, `21 Oct`). For PDFs put it in the file name (`2026-10-21_physics.pdf`). The message after filing says which date it used. |
| A page was filed as "unsorted" | It couldn't tell the subject. Put photos in a subject folder inside the inbox (`NotebookPhotos\physics`) or start the file name with the subject (`physics_page1.jpg`). |
| The handwriting was read badly | Better light, a straight-on photo, dark ink and a thicker pen help most. Measure it with `tools/evaluate_reading.py`. |
| A file I dropped in disappeared | If it was an exact copy of something already stored, the repeat is deleted. Otherwise it is in the notebook folder, under its year / subject / term / week / day. |

## YouTube

| What you see | What to do |
|---|---|
| "Could not get that video" | Check the link. Some videos have no captions, and then the audio is downloaded instead (slow). |
| It fails on the school network or a VPN | School networks and VPNs often block it. Try at home. |
| Nothing happens for a long time | Audio videos are slow: a long lecture can take many minutes. |

## Practice tests

| What you see | What to do |
|---|---|
| "Making Word documents needs one extra package" | `pip install python-docx` |
| "I couldn't find any notes for ..." | Ask for something you have notes on, for example `physics week 4`, or a topic word that appears in them. |
| "The model didn't write any usable questions" | Try again, or choose a smaller part of your notes. Small models sometimes fail. |
| Some answers look wrong | They can be. Check them with your teacher or textbook. |
| Where are the tests saved? | In your notebook folder, under `Practice tests`. |

## Disk space and memory

| What you see | What to do |
|---|---|
| The notebook folder is getting big | Open the **Storage** tab. Preview, then shrink photos older than 60 days, or remove them (the text is kept). |
| Computer is short of memory | Close Chrome tabs. Lower `OLLAMA_KEEP_ALIVE` (for example `"2m"`) so the model leaves memory sooner. |
| Temporary files | The notebook now cleans up after itself. Older versions may have left copies in `%TEMP%`; you can delete the folders there named like `tmpxxxx`. |

## GitHub

| What you see | What to do |
|---|---|
| `Author identity unknown` | `git config --global user.name "yourname"` and `git config --global user.email "your-noreply-address"` (GitHub: Settings > Emails shows a private address). |
| `src refspec main does not match any` | There is no commit yet. Fix the identity, then `git commit -m "Initial commit"` and push again. |
| `Connection was reset` | Your network is blocking Git (school Wi-Fi or a VPN are common causes). Try home Wi-Fi or a phone hotspot. |

# Resume and application material

Draft wording you can adapt. **Only keep what is true of your own project**, and fill in the numbers in [brackets] from your own runs. Specific and honest beats impressive-sounding.

## Short description (2 to 3 sentences)

Notebook Assistant is a local-first study tool I built in Python. It reads photos of handwritten notes, PDFs, lecture videos and YouTube links using a small AI model that runs on my own laptop, files them by school term, week and day, and builds a spaced-repetition review list from the mistakes it finds. I can ask it questions in plain English, even with spelling mistakes, and work through topics with a step-by-step tutor that uses my own notes.

## Resume bullets

- Built a local-first study assistant in Python (SQLite, Ollama, Streamlit, faster-whisper) that ingests handwritten-note photos, PDFs, lecture videos and YouTube links and files them by school year, subject, term, week and day.
- Designed a typo-tolerant question parser using fuzzy string matching, relative dates and number words instead of an AI model, so queries like "sumarise yesterdays phisics" resolve instantly and deterministically.
- Implemented spaced-repetition scheduling (a simplified SM-2) that pulls reviews forward ahead of recorded test dates.
- Wrote 153 automated tests (pytest) using a fake model and a virtual display, covering date logic, filing, de-duplication, storage, retry behaviour, practice-test generation and the desktop interface.
- Built a lightweight Tkinter desktop app (about 32 MB when idle on my Linux test machine) that replaced a browser-based version, with background threads so the window never freezes.
- Added storage efficiency: photos are shrunk when filed (hash of the original kept for de-duplication), temporary files are cleaned up, and old photos can be shrunk or removed on request.
- Built a practice-test generator that turns notes into a Word document with an answer key, validating every question the model writes before including it.
- Made the pipeline fail safely: files are only moved after they are read successfully, repeated files are de-duplicated by SHA-256, and Ollama errors are retried with a smaller memory request.
- Wrote an evaluation tool that measures handwriting-reading accuracy as character error rate; current result on my own pages: **[X]% over [N] pages** *(run `tools/evaluate_reading.py` and fill this in, or delete this bullet)*.

## Being accurate about how it was made

If an AI assistant helped write any of the code, say so plainly (for example: "I specified the features and tested them on my own laptop; an AI assistant wrote much of the code, and I debugged and modified it"). Applications and interviews reward honesty, and a reviewer who finds out later that you overstated your part will trust the rest less. Also make sure you can explain every file you list. You do not need to have typed it all yourself; you do need to understand it.

## Be ready to explain

- Why a hash (SHA-256) is used to detect duplicates, rather than the file name.
- Why the question parser does not use the AI model.
- What a character error rate is and how it is calculated.
- How the review intervals grow, and why a coming test changes them.
- What the model gets wrong, and how you would improve it (better photos, examples in the prompt, a bigger model, fine-tuning).
- One bug you found and how you fixed it (see `docs/DESIGN_NOTES.md`).
- What you would build next.

## Before you publish

- Replace `[YOUR NAME]` in `LICENSE` and `YOUR-USERNAME` in `README.md`.
- Fill in or delete the "My results" line in the README.
- Add real screenshots to `docs/screenshots/` and link them from the README.
- Check `git status` shows no notes, photos, recordings or `.db` files before you commit.
- Commit in small steps with clear messages, so the history shows how the project grew.

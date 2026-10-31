# Design notes

Why it works the way it does, and what went wrong along the way. Written down because the mistakes teach more than the parts that worked first time.

*(Edit this in your own words, and keep only what is true of your project.)*

## Decisions

**Local-first.** The notes are personal and the goal was no subscription, so everything runs through Ollama on the user's own computer. The cost is a small model (3 billion parameters) that is much less capable than a cloud model. Most of the design is about working *around* that: keeping the model's job small, checking its output, and letting the user correct it.

**Don't ask the model what you can compute.** Which term, week and day a date falls in is arithmetic on a calendar, not a language problem. Understanding "sumarise yesterdays phisics" is done with rules and fuzzy string matching (`difflib`), not the model. That makes it fast, free, deterministic, and testable with 10 lines per case. The model is used only where it is needed: reading handwriting, summarising, giving feedback, tutoring.

**One shared config.** Paths, subjects and term dates are in `notebook_config.py` and nowhere else.

**SQLite plus plain files.** One database file and the original pages in a readable folder tree. Nothing to install or run as a server, and the user can always open the folders themselves.

**De-duplicate by content.** Each file's SHA-256 hash is stored, so the same page dropped in twice (or renamed) is only stored once. Summaries are cached by a hash of the notes' contents plus a prompt version, so changing the prompt invalidates old summaries.

**The user can overrule the model.** Mistakes the checker flags go into a review list where a wrong flag can be deleted. The filing message says which date was used and where it came from.

## Things that went wrong, and the fixes

1. **The first script deleted every photo, even when processing failed.** Cleanup ran in a `finally` block, so if Ollama was not running the page was lost. Fix: read first, move the file into the notebook only after the read succeeds, and leave the file in the inbox if anything fails. There is a test for this (`test_a_failed_read_never_loses_the_original`).

2. **Two programs, two databases.** On the first real run, the watcher filed a page but the question tool said "no notes found". The root folder was set through an environment variable that only the watcher set; the question tool fell back to a folder next to the scripts and created a second, empty database. Fix: a shared `notebook_config.py` that both read, a "database:" line in `status` so the path is visible, and a startup check that refuses to run if the inbox and notebook folder are the same or nested.

3. **Ollama answered 503 (busy or out of memory) in the middle of a summary.** The transcript had been saved but the summary failed with an unhelpful message. Fixes: retry with back-off and a smaller context window, translate failures into messages a person can act on (model missing, Ollama not running, out of memory), and make a repeated command re-show the summary instead of saying "already stored".

4. **Dates written on pages are easy to misread, so the rules are strict.** A simple rule finds `21/10/2026` or `21 Oct` near the top of a page. To avoid false matches it needs word boundaries (so `Mark 5` is not March), ignores dates in the future (a worksheet's "due 30 October"), skips fractions like `3/4`, and is only used for photos, not PDFs. Each of these cases has a test. The filing message always says which date was used and where it came from, so a wrong guess is visible.

5. **Fuzzy matching needs guard rails.** Correcting a typo of `week` in front of a number (`wek 4`) is useful, but the same rule could turn the word `weak` into `week`, so `weak` is excluded and the correction only applies when a number follows. Subject typos use a similarity cut-off of 0.8, high enough to catch `phisics` but not unrelated words.

6. **A desktop window instead of a browser.** The first interface was a Streamlit web page, but on a computer that was already short of memory the browser tab and the web server were the heaviest parts of the whole system. The replacement uses Tkinter (included with Python), one process, and no server. The cost is a plainer look; colours and pictures are configurable.

7. **Photos could not be both de-duplicated and shrunk.** De-duplication compares the hash of the file's bytes, but a shrunk photo has different bytes. The hash of the original is stored, and the folder scan skips files by path instead. Both cases have tests.

8. **Temporary files were leaking.** Every page read left a shrunk copy in the system temp folder, and every scanned PDF page left a full-size PNG. Found while working on storage, fixed with `TemporaryDirectory`, and covered by a test that checks the temp folder is empty afterwards.

9. **A test found a real bug.** A generated question with no marks given was being scored 1 mark instead of its sensible default (3 for a calculation). The test for default marks failed on the first run.

10. **Generated questions are checked, not trusted.** The model's JSON is validated question by question; ones that cannot be verified (a multiple-choice answer that matches no option, for example) are dropped rather than printed. The document still says the answers may be wrong, because a valid-looking answer can be incorrect.

## What the tests do and don't prove

The suite replaces the model with a fake and fixes "today" to a known Wednesday, so results never depend on a GPU, the network or the date. That proves the filing, date, parsing, scheduling, retry and interface logic. It proves nothing about how well the real model reads real handwriting; `tools/evaluate_reading.py` exists to measure that separately, and the README leaves the result blank until it has been run.

## Known weaknesses

- Handwriting accuracy with a 3B model is the biggest unknown.
- Question understanding is rule-based, so unusual phrasings can miss. A fallback that asks the model to extract the filters would help.
- Topic search is keyword counting. Meaning-based search (embeddings) would find "Newton's second law" when you ask about "force and acceleration".
- Practice-test answers are written by the same small model that writes the questions; calculation answers could be checked with a computer algebra system.
- Only Monday to Friday folders are pre-created; weekend files make their own folders when needed.
- A page with several subjects on it is filed under one.

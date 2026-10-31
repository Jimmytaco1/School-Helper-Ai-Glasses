# Architecture

## Modules

| File | Job |
|---|---|
| `notebook_config.py` | The one place for settings: the two folders, subjects, nicknames, term dates, and the date-to-term/week/day calculation. Every other file reads it. |
| `notebook_checker.py` | The core library, also usable as a command line. Reading files, filing, the database, understanding questions, summaries, spaced repetition, YouTube, and tutor mode. |
| `notebook_hub.py` | A background watcher on the inbox folder. Decides the subject and date for each arriving file, hands it to the checker, and creates the folder tree (`setup`). |
| `notebook_gui.py` | The desktop window (Tkinter, no browser): Ask, Add notes, Tutor, Practice test, Review, Tests, Storage, Activity, Help. It also runs the inbox watcher in background threads, so one program does everything. |
| `notebook_app.py` | An optional older browser version (Streamlit) with fewer tabs. |
| `notebook_bridge.py` | A few lines that let another program (the glasses prototype) drop a photo into the inbox. |
| `tools/evaluate_reading.py` | Measures handwriting-reading accuracy as character error rate. |
| `tests/` | 153 automated tests with a fake model (15 open the desktop window and need a display). |

## What happens when you drop in a photo

```mermaid
sequenceDiagram
    participant You
    participant Hub as notebook_hub
    participant Core as notebook_checker
    participant LLM as Ollama (qwen2.5vl:3b)
    participant DB as SQLite + folders
    You->>Hub: photo lands in inbox/physics/
    Hub->>Hub: wait until the file stops growing
    Hub->>Core: already stored? (SHA-256 of the bytes)
    Core->>LLM: transcribe the handwriting
    LLM-->>Core: text
    Core->>Core: date from file name, page text, photo, or file date
    Core->>Core: date + subject calendar -> term, week, day
    Core->>DB: copy/move into 2026/physics/term4/week04/day3/ and insert note
    Core->>LLM: check the notes (JSON: summary, feedback, mistakes)
    LLM-->>Core: JSON
    Core->>DB: save summary and feedback; add confident mistakes to the review list
```

The original file is only moved *after* it has been read successfully, so a failed read never loses a page.

## What happens when you ask a question

1. `normalise()` lower-cases and repairs the wording: `wks`/`weeks` become `week`, `week four` and `4th week` become `week 4`, a typo before a number (`wek 4`) is corrected, `yesterdays` becomes `yesterday`.
2. `find_subject_in()` looks for a whole subject name, then nicknames (`maths`, `lit`), then fuzzy matches single words (`phisics`).
3. `relative_filters()` turns `today`, `yesterday`, `last week`, `this term` and weekday names into a term, week and day using the calendar.
4. `detect_intent()` chooses between summary, weak points, show, and tutor.
5. `topic_words()` keeps the remaining content words (`projectile motion`), and `rank_by_topic()` picks the notes that mention them, forgiving small typos.
6. The notes are summarised by the model. Summaries are cached by a hash of the notes' contents, so asking again is instant.

None of steps 1 to 5 call the model.

## Data model (SQLite)

```
notes     id, path, sha (unique), year, subject, term, week, day, text, summary, feedback, added
weak      id, note_id, subject, topic, issue, fix, source, reps, interval_days, ease, due, active
tests     id, subject, name, date
summaries key (hash of note contents + prompt version), text
```

`weak.source` is `checker`, `tutor` or `manual`, so you can see where a review item came from.

## Spaced repetition

A simplified version of the SM-2 idea. Remembering an item moves its next review to 1 day, then 3 days, then `previous interval x ease` (ease rises 0.1 each success, up to 3.0). Forgetting resets it to 1 day and lowers ease (down to 1.3). If a test for that subject is coming up and the next review would land on or after it, the review is pulled forward to the day before the test.

## Folder layout

```
<inbox>/                           where you drop things
    physics/ maths-methods/ ...    a subfolder chooses the subject
    _duplicates/                   exact repeats are moved here
<notebook folder>/
    notebook.db
    <year>/<subject>/term<N>/week<NN>/day<D>/<file>
```

`<inbox>` and `<notebook folder>` must be separate; the watcher refuses to start if one contains the other.

## Practice tests

`make_quiz()` picks the notes the same way a question does (or the review list, for "what I got wrong"), splits long notes into pieces of about 650 words and samples at most five pieces spread across them, and asks the model for a few questions per piece as JSON. `_clean_question()` then rejects anything unusable: a multiple-choice question needs at least three options and an answer that matches one of them; questions with no answer, near-duplicates, and anything that is not valid JSON are dropped; missing marks get a default. The survivors are ordered into sections and written to a Word file by `build_quiz_docx()` (A4, answer boxes, marks, page numbers, then an answer key).

## Saving space

Photos are read at full size, then a shrunk JPEG is what gets stored (`place_file()`). The database keeps the SHA-256 of the *original*, so the same photo dropped in again is still recognised, and `scan` skips files by path so shrunk photos are not filed twice. Reading a page or a PDF uses a temporary folder that is removed straight away. `tidy_photos()` can later shrink or remove old photos without touching the text, summaries or review items.

## The desktop window and background work

Anything slow (reading a page, asking the model) runs on a background thread with its own database connection, so the window never freezes. The code underneath uses `print()`, so `sys.stdout` is replaced by an `OutputRouter`: output from a job goes to that job's buffer and is shown in the right tab, while output from the inbox watcher goes to the Activity tab. Only one job runs at a time, because the small model is the bottleneck.

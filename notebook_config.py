"""notebook_config.py - the ONE place to edit your paths, subjects and term dates.

notebook_hub.py, notebook_checker.py and notebook_bridge.py all read this file,
so they always use the same folders and the same database.
"""
import datetime as dt
from pathlib import Path

D = dt.date

# The in-tray: photos, PDFs, videos and YouTube links arrive here.
# (Default: a folder called NotebookPhotos in your home folder. Change it to suit.)
INBOX = Path.home() / "NotebookPhotos"

# The filing cabinet: organised notes and notebook.db live here.
# It must be a DIFFERENT folder from the inbox, and keep it off OneDrive
# (a database that syncs while it is open can get corrupted).
NOTEBOOK_ROOT = Path.home() / "Notebook"

SUBJECTS = ["physics", "maths-methods", "specialist-maths", "literature", "engineering"]

# (term, first day, last day). Queensland state school dates;
# check them against your own school's calendar.
TERMS = {
    2026: [(1, D(2026, 1, 27), D(2026, 4, 2)), (2, D(2026, 4, 20), D(2026, 6, 26)),
           (3, D(2026, 7, 13), D(2026, 9, 18)), (4, D(2026, 10, 6), D(2026, 12, 11))],
    2027: [(1, D(2027, 1, 27), D(2027, 3, 25)), (2, D(2027, 4, 12), D(2027, 6, 25)),
           (3, D(2027, 7, 12), D(2027, 9, 17)), (4, D(2027, 10, 5), D(2027, 12, 10))],
}

# Optional: give a subject its own calendar, e.g. uni semesters. "term 1" then
# means semester 1 for that subject. Example (remove the # to use it):
CALENDARS = {
    # "uni-calculus": [(1, D(2028, 9, 4), D(2028, 12, 15)),
    #                  (2, D(2029, 2, 12), D(2029, 5, 25))],
}

# If a handwritten page has a date near the top, file it under that date
# instead of the day the photo was taken.
USE_PAGE_DATE = True

# Nicknames you might type in questions. Spelling mistakes are forgiven
# automatically; this is for different words (e.g. "lit" for literature).
ALIASES = {
    "phys": "physics",
    "maths": "maths-methods", "math": "maths-methods", "mathematics": "maths-methods",
    "methods": "maths-methods",
    "specialist": "specialist-maths", "spec": "specialist-maths",
    "lit": "literature", "english": "literature",
    "engineer": "engineering",
}

# Colours for the desktop window (notebook_gui.py). Any you leave out keep their default.
GUI_THEME = {
    "bg": "#0E1117",        # window background
    "panel": "#262730",     # boxes and text areas
    "fg": "#FAFAFA",        # text
    "accent": "#4F8BF9",    # buttons and highlights
    "muted": "#9AA0A6",     # small grey text
}

# How long the model stays in memory after you last used it. A short time frees
# several GB for other programs sooner (the next answer then takes a few seconds
# longer to start). Examples: "2m", "5m", "30m", "0" (unload straight away).
OLLAMA_KEEP_ALIVE = "5m"

# How much text the model reads in one go. Smaller uses less memory and is
# faster; if Ollama keeps failing, try 2048. If you have lots of memory, 8192.
OLLAMA_CONTEXT = 4096

# Saving disk space. Photos are shrunk when they are filed (the text has already
# been read from the full-size photo); the readable text is what matters.
COMPRESS_PHOTOS = True       # False keeps every photo exactly as taken
PHOTO_MAX_SIDE = 1800        # longest side in pixels (1800 is still very readable)
PHOTO_QUALITY = 80           # JPEG quality, 1 to 95
COMPRESS_PDFS = True         # lossless tidy-up only; a PDF is never degraded
KEEP_DUPLICATES = False      # True keeps exact repeats in inbox/_duplicates instead of deleting them

DELETE_VIDEO_AFTER = True    # delete a lecture video once its transcript is saved
WHISPER_SIZE = "base"        # "small" is more accurate but uses more memory


def term_week_day(d, subject=None):
    """Date -> (term, week of term, day 1-7 where Monday is 1)."""
    terms = CALENDARS.get(subject) or TERMS.get(d.year)
    if not terms:
        print(f"[!] No term dates for {d.year}; add them to TERMS. Using term 1 week 1.")
        return 1, 1, d.isoweekday()
    term, start, end = terms[0]
    for t in terms:
        if t[1] <= d:
            term, start, end = t
    monday = start - dt.timedelta(days=start.weekday())
    week = (d - monday).days // 7 + 1
    last = (end - monday).days // 7 + 1
    return term, max(1, min(week, last)), d.isoweekday()


def make_meta(when, subject):
    term, week, day = term_week_day(when.date(), subject)
    return {"year": when.year, "term": term, "week": week, "day": day,
            "subject": subject or "unsorted"}

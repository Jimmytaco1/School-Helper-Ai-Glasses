#!/usr/bin/env python3
"""notebook_hub.py - drop photos, PDFs, lecture videos or YouTube links into the inbox.

Each one is read, filed under  year / subject / term / week / day,  checked,
and summarised. Needs notebook_config.py and notebook_checker.py beside it.

Run:
    python notebook_hub.py                start watching the inbox
    python notebook_hub.py setup          make the folders for this year
    python notebook_hub.py setup 2027     a specific year
    python notebook_hub.py setup all      every year listed in TERMS

Choosing the subject (first match wins):
    1. a subfolder:            inbox/physics/photo.jpg
    2. a filename prefix:      physics_page3.jpg
    3. the word in inbox/current_subject.txt
    4. the model guesses from the text (falls back to "unsorted")

Choosing the day (first match wins):
    1. a date in the file name   (physics_2026-10-21.jpg)
    2. a date written at the top of a handwritten page (21/10/2026, 21 Oct)
    3. the date the photo was taken
    4. the file's own date

YouTube: drag a YouTube link from your browser's address bar into the inbox
(or a subject subfolder). It makes a small .url file, and the hub turns it
into a transcript and summary.

Install:  pip install watchdog ollama pymupdf pillow faster-whisper
          pip install youtube-transcript-api yt-dlp      (only for YouTube)
"""
import datetime as dt
import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import notebook_checker as nc
from notebook_config import (INBOX, SUBJECTS, TERMS, CALENDARS, DELETE_VIDEO_AFTER,
                             term_week_day, make_meta)
try:
    from notebook_config import KEEP_DUPLICATES
except ImportError:
    KEEP_DUPLICATES = False

VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv"}
WANTED = nc.IMAGE_EXT | VIDEO_EXT | {".pdf", ".txt", ".md", ".url"}


# ---------------------------------------------------------------- helpers
def wanted(path):
    p = Path(path)
    if p.suffix.lower() not in WANTED or p.name.startswith(("~", ".")):
        return False
    try:
        rel = p.relative_to(INBOX).parts
    except ValueError:
        return False
    return not any(part.startswith("_") for part in rel[:-1])   # skip _duplicates


def wait_until_stable(path, checks=3, delay=1.5, timeout=900):
    """Wait until the file stops growing (OneDrive and videos write slowly)."""
    last, same, start = -1, 0, time.time()
    while time.time() - start < timeout:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        if size > 0 and size == last:
            same += 1
            if same >= checks:
                return True
        else:
            same, last = 0, size
        time.sleep(delay)
    return False


def find_subject(path):
    rel = path.relative_to(INBOX).parts
    if len(rel) > 1:
        return nc.slug(rel[0])
    name = nc.slug(path.stem)
    for s in SUBJECTS:
        if name.startswith(s):
            return s
    f = INBOX / "current_subject.txt"
    if f.exists():
        return nc.slug(f.read_text(encoding="utf-8")) or None
    return None


def transcribe(path):
    return nc.transcribe_file(path)


def read_url_file(p):
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().lower().startswith("url="):
            return line.strip()[4:]
    return None


# ---------------------------------------------------------------- processing
def handle_link(con, p, subject):
    url = read_url_file(p)
    if not url or not nc.video_id(url):
        print(f"[!] {p.name} is not a YouTube link, so it was left alone.")
        return
    print(f"\n[+] YouTube link detected: {url}")
    if nc.process_youtube(con, url, subject, when=dt.datetime.now()):
        p.unlink()
        print(f"[-] Removed {p.name}")


def handle_video(con, p, when, subject):
    print(f"\n[+] Video lecture detected: {p.name}")
    print("[-] Transcribing speech (this takes a while)...")
    transcript = transcribe(p)
    if not transcript.strip():
        print("[!] No speech found. The video was left in the inbox.")
        return
    subject = subject or nc.guess_subject(transcript)
    meta = make_meta(when, subject)
    dest = nc.note_dir(meta["year"], meta["subject"], meta["term"], meta["week"], meta["day"])
    dest.mkdir(parents=True, exist_ok=True)
    txt = dest / f"{p.stem}.transcript.txt"
    txt.write_text(transcript, encoding="utf-8")
    nc.ingest(con, txt, meta, check=False)
    rows = con.execute("select * from notes where sha=?", (nc.sha256(txt),)).fetchall()
    if rows:
        print("[-] Distilling the lecture into high-yield notes...")
        nc.print_report(nc.summarise(con, rows))
    if DELETE_VIDEO_AFTER:
        p.unlink()                                    # only after the transcript is saved
        print(f"[-] Deleted {p.name} (transcript kept in {dest})")
    else:
        shutil.move(str(p), str(dest / p.name))


def handle(con, p):
    if not wait_until_stable(p):
        print(f"[!] {p.name} never finished writing; skipped")
        return
    subject = find_subject(p)
    ext = p.suffix.lower()

    if ext == ".url":
        return handle_link(con, p, subject)
    if ext in VIDEO_EXT:
        return handle_video(con, p, nc.file_date(p)[0], subject)

    if nc.already_stored(con, p):
        if KEEP_DUPLICATES:
            dup = INBOX / "_duplicates"
            dup.mkdir(exist_ok=True)
            shutil.move(str(p), str(dup / f"{int(time.time())}_{p.name}"))
            print(f"[=] {p.name} is already stored; moved to _duplicates")
        else:
            p.unlink()                                   # an exact repeat: nothing is lost
            print(f"[=] {p.name} is already stored; removed the repeat")
        return

    print(f"\n[+] New file detected: {p.name}")
    print("[-] Reading and checking...")
    text = nc.extract_text(p)
    if not subject:
        subject = nc.guess_subject(text)
        if not subject:
            print("[?] Could not tell the subject; filed as 'unsorted'. Next time use "
                  "inbox/<subject>/ or a name like physics_page1.jpg")
    when, source = nc.file_date(p, text)
    meta = make_meta(when, subject)
    nc.store_file(con, p, meta, check=True, move=True, text=text)
    print(f"[+] Filed: {meta['subject']} / {meta['year']} term {meta['term']} "
          f"week {meta['week']} day {meta['day']}  (date from {source}: {when:%d %b %Y})")


# ---------------------------------------------------------------- folders
def setup_folders(arg=None):
    """Make the inbox subject folders and the whole
    year / subject / term / week / day tree under the notebook folder."""
    years = sorted(TERMS) if arg == "all" else [int(arg) if arg else dt.date.today().year]
    INBOX.mkdir(parents=True, exist_ok=True)
    for s in SUBJECTS:
        (INBOX / s).mkdir(exist_ok=True)
    print(f"Inbox subject folders ready in {INBOX}")
    total = 0
    for y in years:
        if y not in TERMS and any(s not in CALENDARS for s in SUBJECTS):
            print(f"[!] No term dates for {y}; add them to TERMS first.")
        for s in SUBJECTS:
            terms = CALENDARS[s] if s in CALENDARS else TERMS.get(y, [])
            for term, start, end in terms:
                if s in CALENDARS and start.year != y:
                    continue
                monday = start - dt.timedelta(days=start.weekday())
                weeks = (end - monday).days // 7 + 1
                for w in range(1, weeks + 1):
                    for d in range(1, 6):                 # days 1-5 = Monday-Friday
                        nc.note_dir(y, s, term, w, d).mkdir(parents=True, exist_ok=True)
                        total += 1
        print(f"{y}: folders ready")
    print(f"{total} day folders checked or created under {nc.ROOT}")


def check_folders():
    a, b = INBOX.resolve(), Path(nc.ROOT).resolve()
    if a == b or a in b.parents or b in a.parents:
        sys.exit("[!] The inbox and the notebook folder must be two separate folders, "
                 "and neither can be inside the other.\n"
                 f"    inbox    = {INBOX}\n    notebook = {nc.ROOT}\n"
                 "    Fix NOTEBOOK_ROOT in notebook_config.py.")


# ---------------------------------------------------------------- watching
jobs = queue.Queue()
queued = set()


def enqueue(path):
    p = Path(path)
    if wanted(p) and p not in queued:
        queued.add(p)
        jobs.put(p)


class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            enqueue(event.src_path)

    def on_moved(self, event):                        # files renamed into place
        if not event.is_directory:
            enqueue(event.dest_path)


def worker():
    con = nc.connect()            # database connections stay in their own thread
    while True:
        p = jobs.get()
        try:
            if p.exists():
                handle(con, p)
        except Exception as e:
            print(f"[!] Failed on {p.name}; the file was left in the inbox: {e}")
        finally:
            queued.discard(p)
            print("\nSystem ready for the next page, video or link...")


def start_background():
    """Start watching the inbox in background threads (the desktop window uses this
    so that one program does everything). Returns the observer so it can be stopped."""
    check_folders()
    INBOX.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    for f in sorted(INBOX.rglob("*")):                # anything left from last time
        if f.is_file():
            enqueue(f)
    observer = Observer()
    observer.schedule(InboxHandler(), str(INBOX), recursive=True)
    observer.start()
    return observer


def main():
    check_folders()
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_folders(sys.argv[2] if len(sys.argv) > 2 else None)
        return
    print(f"[*] Inbox:    {INBOX}")
    print(f"[*] Notebook: {nc.ROOT}")
    print("\n[*] Reminders")
    nc.cmd_remind(nc.connect())
    observer = start_background()
    print("\n[*] Notebook hub active.")
    print("[*] Drop photos, PDFs, videos or YouTube links in (subfolders set the subject).")
    print("[*] Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()

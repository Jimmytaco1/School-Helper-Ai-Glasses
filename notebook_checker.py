#!/usr/bin/env python3
"""notebook_checker.py - local notebook organiser, checker, tutor and reminder.

Run it from PowerShell:   python notebook_checker.py chat
then type things like:
    summarise day 3 of week 4 term 4 for physics 2026
    what did I get wrong in week 4 term 4 physics
    show physics week 4                  (what the model read from your pages)
    tutor physics projectile motion      (a step-by-step tutor using your notes)
    youtube https://youtu.be/xxxxxxxxxxx physics
    add "Projectile Motion.pdf" --subject physics --date 2026-10-21
    review    remind    status    test physics 2026-11-12 "IA3 exam"    quit

Paths, subjects and term dates are set in notebook_config.py.
Needs: Ollama running with qwen2.5vl:3b, and
       pip install ollama pymupdf pillow faster-whisper
For YouTube links also: pip install youtube-transcript-api yt-dlp
"""
import argparse
import datetime as dt
import difflib
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

# Calls to Ollama are local. If a VPN or proxy is configured on the computer,
# make sure it is not used for them (a proxy can answer "503" instead of Ollama).
os.environ["NO_PROXY"] = ",".join(x for x in (os.environ.get("NO_PROXY", ""), "127.0.0.1,localhost") if x)

try:
    import notebook_config as cfg
except ImportError:                       # still works without it, using ./notebook
    cfg = None

ROOT = Path(os.environ.get("NOTEBOOK_ROOT") or (cfg.NOTEBOOK_ROOT if cfg else "notebook"))
DB_PATH = ROOT / "notebook.db"
MODEL = "qwen2.5vl:3b"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
TEXT_EXT = {".txt", ".md"}
PROMPT_VERSION = "v1"
USE_PAGE_DATE = getattr(cfg, "USE_PAGE_DATE", True)
CTX = int(getattr(cfg, "OLLAMA_CONTEXT", 4096))      # how much text the model reads at once
KEEP_ALIVE = str(getattr(cfg, "OLLAMA_KEEP_ALIVE", "5m"))   # how long the model stays in memory after use
COMPRESS_PHOTOS = getattr(cfg, "COMPRESS_PHOTOS", True)
PHOTO_MAX_SIDE = int(getattr(cfg, "PHOTO_MAX_SIDE", 1800))
PHOTO_QUALITY = int(getattr(cfg, "PHOTO_QUALITY", 80))
COMPRESS_PDFS = getattr(cfg, "COMPRESS_PDFS", True)
WHISPER_SIZE = getattr(cfg, "WHISPER_SIZE", "base")
CONFIG_SUBJECTS = list(getattr(cfg, "SUBJECTS", []))
ALIASES = dict(getattr(cfg, "ALIASES", {}))

SUMMARY_PROMPT = (
    "Summarise these school notes for revision. Give the key ideas, formulas "
    "and laws, and the steps of any worked examples, as short plain bullet "
    "points. Do not add facts that are not in the notes."
)

TUTOR_SYSTEM = (
    "You are a patient, friendly tutor for a high school student. Teach the "
    "topic in small steps using simple language and one worked example. After "
    "each explanation ask ONE short question to check understanding, then "
    "wait for the answer. If the student is wrong, give a hint before the "
    "answer. Keep every reply under 150 words. Use the student's own notes "
    "below when they are relevant, and point out anything in them that looks "
    "wrong. If you are not sure of a fact, say so instead of guessing."
)

SCHEMA = """
create table if not exists notes(
    id integer primary key, path text, sha text unique,
    year int, subject text, term int, week int, day int,
    text text, summary text, added text, feedback text);
create table if not exists weak(
    id integer primary key, note_id int, subject text, topic text,
    issue text, fix text, source text,
    reps int default 0, interval_days int default 1, ease real default 2.5,
    due text, active int default 1);
create table if not exists tests(
    id integer primary key, subject text, name text, date text);
create table if not exists summaries(key text primary key, text text);
"""


# ---------------------------------------------------------------- basics
def connect():
    ROOT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:                                          # older databases lack this column
        con.execute("alter table notes add column feedback text")
    except sqlite3.OperationalError:
        pass
    return con


def today():
    return dt.date.today()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def note_dir(year, subject, term, week, day):
    return (ROOT / str(year) / slug(subject) / f"term{term}"
            / f"week{int(week):02d}" / f"day{day}")


def make_meta(when, subject):
    if cfg:
        return cfg.make_meta(when, slug(subject) if subject else None)
    return {"year": when.year, "term": 1, "week": 1, "day": when.isoweekday(),
            "subject": slug(subject) if subject else "unsorted"}


def parse_path(path):
    """notebook/2026/physics/term4/week01/day1/file.jpg -> metadata dict."""
    try:
        parts = Path(path).resolve().relative_to(ROOT.resolve()).parts
    except ValueError:
        return None
    if len(parts) < 6:
        return None
    y, subject, t, w, d = parts[:5]
    mt, mw, md = (re.fullmatch(p, x) for p, x in
                  ((r"term(\d+)", t), (r"week(\d+)", w), (r"day(\d+)", d)))
    if not (y.isdigit() and mt and mw and md):
        return None
    return {"year": int(y), "subject": subject, "term": int(mt.group(1)),
            "week": int(mw.group(1)), "day": int(md.group(1))}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def chunks(text, words=1200):
    w = text.split()
    return [" ".join(w[i:i + words]) for i in range(0, len(w), words)] or [""]


# ---------------------------------------------------------------- dates
_MON = (r"(january|february|march|april|may|june|july|august|september|october|"
        r"november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b\.?")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def date_from_text(text, ref=None):
    """Find a date written near the top of a page (day first, as in Australia)."""
    ref = ref or today()
    head = text[:300].lower()
    cands = []
    for m in re.finditer(r"\b(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4}|\d{2})\b", head):
        y = int(m.group(3))
        cands.append((y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON +
                         r"(?:,?\s+(\d{4}))?", head):
        cands.append((int(m.group(3)) if m.group(3) else ref.year,
                      _MONTHS[m.group(2)[:3]], int(m.group(1))))
    for m in re.finditer(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s+(\d{4}))?", head):
        cands.append((int(m.group(3)) if m.group(3) else ref.year,
                      _MONTHS[m.group(1)[:3]], int(m.group(2))))
    for y, mo, d in cands:
        try:
            day = dt.date(y, mo, d)
        except ValueError:
            continue
        if ref - dt.timedelta(days=370) <= day <= ref + dt.timedelta(days=1):
            return day
    return None


def exif_date(path):
    try:
        from PIL import Image
        ex = Image.open(path).getexif()
        raw = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        return dt.datetime.strptime(raw, "%Y:%m:%d %H:%M:%S") if raw else None
    except Exception:
        return None


def file_date(path, text=None):
    """Which day does this file belong to? Returns (datetime, where it came from).
    Order: a date in the file name, a date written on the page, the photo's
    own date, then the file's modified date."""
    path = Path(path)
    ref = today()
    m = re.search(r"(20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])", path.stem)
    if m:
        day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if ref - dt.timedelta(days=370) <= day <= ref + dt.timedelta(days=1):
            return dt.datetime(day.year, day.month, day.day, 12), "the file name"
    is_image = path.suffix.lower() in IMAGE_EXT
    if text and USE_PAGE_DATE and is_image:
        day = date_from_text(text)
        if day:
            return dt.datetime(day.year, day.month, day.day, 12), "the date written on the page"
    if is_image:
        d = exif_date(path)
        if d:
            return d, "the date the photo was taken"
    return dt.datetime.fromtimestamp(path.stat().st_mtime), "the file's date"


# ---------------------------------------------------------------- model
class OllamaProblem(RuntimeError):
    """A problem talking to Ollama, with a message a human can act on."""


def chat_llm(messages, as_json=False, ctx=None):
    import ollama
    ctx = ctx or CTX
    kw = {"format": "json"} if as_json else {}
    wait = 5
    for attempt in range(1, 5):
        try:
            r = ollama.chat(model=MODEL, messages=messages, keep_alive=KEEP_ALIVE,
                            options={"num_ctx": ctx}, **kw)
            return r["message"]["content"]
        except ollama.ResponseError as e:
            code = getattr(e, "status_code", 0)
            if code == 404:
                raise OllamaProblem(f"Ollama does not have the model {MODEL}. "
                                    f"Run:  ollama pull {MODEL}")
            if code in (500, 502, 503, 504) and attempt < 4:
                print(f"  Ollama is busy or low on memory (error {code}); "
                      f"trying again in {wait} seconds...")
                time.sleep(wait)
                wait *= 2
                if attempt >= 2:
                    ctx = max(2048, ctx // 2)     # ask for less memory
                continue
            raise OllamaProblem(
                f"Ollama kept failing (error {code}). Close other programs (Chrome uses a "
                "lot of memory), or quit and reopen Ollama from the system tray, then try "
                "again. To see what Ollama is doing, run:  ollama ps  . Your notes are safe.")
        except Exception as e:
            name = (type(e).__name__ + str(e)).lower()
            if "connect" in name:
                raise OllamaProblem("Can't reach Ollama. Is it running? Look for its icon "
                                    "in the system tray, or open the Ollama app.")
            raise


def llm(prompt, images=None, as_json=False, ctx=None):
    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = [str(i) for i in images]
    return chat_llm([msg], as_json=as_json, ctx=ctx)


def _flatten(im):
    """Photos as plain RGB, with the phone's rotation applied."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        white = Image.new("RGB", im.size, "white")
        white.paste(im, mask=im.split()[-1])
        return white
    return im.convert("RGB")


def shrink(path, limit=1600, out_dir=None):
    """A smaller copy for the model to read (fast, and fine for handwriting).
    Written into out_dir, which the caller removes afterwards."""
    from PIL import Image
    with Image.open(path) as im:
        im = _flatten(im)
        im.thumbnail((limit, limit))
        out = Path(out_dir or tempfile.mkdtemp()) / "page.jpg"
        im.save(out, quality=90)
    return out


def compress_image(src, dest, max_side=None, quality=None):
    """Save a smaller JPEG of a photo (rotation fixed, camera data dropped)."""
    from PIL import Image
    with Image.open(src) as im:
        im = _flatten(im)
        im.thumbnail((max_side or PHOTO_MAX_SIDE,) * 2)
        im.save(dest, "JPEG", quality=quality or PHOTO_QUALITY, optimize=True)
    return Path(dest)


def read_image(path, limit=1600):
    with tempfile.TemporaryDirectory() as tmp:           # removed as soon as we are done
        small = shrink(path, limit, out_dir=tmp)
        return llm("Transcribe all the handwriting and printed text on this page "
                   "exactly. Write equations in plain text. Output only the "
                   "transcription.", images=[small])


def read_pdf(path):
    try:
        import pymupdf as fitz
    except ImportError:                           # older pymupdf versions
        import fitz
    parts = []
    with fitz.open(path) as doc, tempfile.TemporaryDirectory() as tmp:
        for i, page in enumerate(doc, 1):
            text = page.get_text().strip()
            if len(text) < 20:                    # scanned page: use the model
                png = Path(tmp) / f"p{i}.png"
                page.get_pixmap(dpi=150).save(png)
                text = read_image(png)
                png.unlink()
            parts.append(f"--- page {i} ---\n{text}")
    return "\n\n".join(parts)


_whisper = None


def transcribe_file(path):
    """Speech to text. Reads the audio straight from audio or video files."""
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        print("[-] Loading Whisper (first time only)...")
        _whisper = WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(str(path))
    return " ".join(s.text.strip() for s in segments)


def guess_subject(text):
    if not CONFIG_SUBJECTS:
        return None
    reply = slug(llm("Which school subject are these notes from? Choose exactly one of: "
                     + ", ".join(CONFIG_SUBJECTS) + ". Reply with that one word only.\n\n"
                     + text[:2000]))
    for s in CONFIG_SUBJECTS:
        if reply == s or reply.startswith(s):
            return s
    return None


# ---------------------------------------------------------------- weak points
def add_weak(con, subject, topic, issue, fix, note_id=None, source="manual"):
    subject = slug(subject)
    dup = con.execute("select 1 from weak where subject=? and lower(topic)=? "
                      "and active=1", (subject, topic.lower())).fetchone()
    if dup:
        return False
    due = (today() + dt.timedelta(days=1)).isoformat()
    con.execute("insert into weak(note_id, subject, topic, issue, fix, source, due) "
                "values (?,?,?,?,?,?,?)",
                (note_id, subject, topic, issue, fix, source, due))
    con.commit()
    return True


def next_test_date(con, subject):
    r = con.execute("select min(date) from tests where subject=? and date>=?",
                    (slug(subject), today().isoformat())).fetchone()
    return dt.date.fromisoformat(r[0]) if r and r[0] else None


def reschedule(con, w, remembered):
    """Simple spaced repetition: 1 day, 3 days, then grow by the ease factor.
    A coming test pulls the next review in to the day before it."""
    if remembered:
        reps = w["reps"] + 1
        interval = 1 if reps == 1 else 3 if reps == 2 else round(w["interval_days"] * w["ease"])
        ease = min(w["ease"] + 0.1, 3.0)
    else:
        reps, interval, ease = 0, 1, max(w["ease"] - 0.2, 1.3)
    due = today() + dt.timedelta(days=interval)
    test = next_test_date(con, w["subject"])
    if test and due >= test:
        due = max(today() + dt.timedelta(days=1), test - dt.timedelta(days=1))
    con.execute("update weak set reps=?, interval_days=?, ease=?, due=? where id=?",
                (reps, interval, ease, due.isoformat(), w["id"]))
    con.commit()
    return due


# ---------------------------------------------------------------- notes
def check_note(con, note_id):
    n = con.execute("select * from notes where id=?", (note_id,)).fetchone()
    prompt = (
        f"These are a student's {n['subject']} notes.\n"
        "Return JSON with keys: summary (max 100 words), formulas (list of "
        "strings), feedback (string), mistakes (list of objects with topic, "
        "issue, fix).\n"
        "feedback: for maths or physics, go through the working step by step "
        "and say where the first error is and which rule was broken; for "
        "English or literature, comment on grammar, spelling, punctuation and "
        "structure. Be direct and keep it under 120 words.\n"
        "mistakes: only list a mistake if you are confident it is wrong; "
        "otherwise use an empty list.\n\nNOTES:\n" + n["text"][:6000])
    try:
        data = json.loads(llm(prompt, as_json=True))
        assert isinstance(data, dict)
    except (json.JSONDecodeError, TypeError, AssertionError):
        print("  checker gave unreadable output; skipped this note")
        return
    feedback = str(data.get("feedback", "")).strip()
    con.execute("update notes set summary=?, feedback=? where id=?",
                (str(data.get("summary", "")), feedback, note_id))
    added = 0
    for m in data.get("mistakes") or []:
        if isinstance(m, dict) and m.get("topic") and m.get("fix"):
            added += add_weak(con, n["subject"], str(m["topic"]),
                              str(m.get("issue", "")), str(m["fix"]),
                              note_id, "checker")
    con.commit()
    print("\n" + "=" * 50 + "\nTUTOR FEEDBACK:\n" + "=" * 50)
    print(feedback or "(no feedback)")
    print("=" * 50)
    print(f"{added} possible mistake(s) added to your review list")


def already_stored(con, path):
    return con.execute("select 1 from notes where sha=?", (sha256(path),)).fetchone() is not None


def extract_text(path):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return read_pdf(path)
    if ext in IMAGE_EXT:
        return read_image(path)
    return path.read_text(encoding="utf-8", errors="ignore")


def save_note(con, path, meta, text, sha, check=True):
    cur = con.execute(
        "insert into notes(path, sha, year, subject, term, week, day, text, added) "
        "values (?,?,?,?,?,?,?,?,?)",
        (str(path), sha, meta["year"], slug(meta["subject"]), meta["term"],
         meta["week"], meta["day"], text,
         dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    if check and text.strip():
        check_note(con, cur.lastrowid)
    return cur.lastrowid


def ingest(con, path, meta, check=True):
    """For a file that is already sitting in its folder (used by scan)."""
    path = Path(path)
    if path.suffix.lower() not in IMAGE_EXT | TEXT_EXT | {".pdf"}:
        return None
    if con.execute("select 1 from notes where path=?", (str(path),)).fetchone():
        return None                                # already filed (e.g. a shrunk photo)
    sha = sha256(path)
    if con.execute("select 1 from notes where sha=?", (sha,)).fetchone():
        print("skip (already stored):", path.name)
        return None
    print("reading:", path.name)
    return save_note(con, path, meta, extract_text(path), sha, check)


def _same_times(src, dst):
    st = Path(src).stat()
    os.utime(dst, (st.st_atime, st.st_mtime))


def _unique(target):
    target = Path(target)
    if target.exists():
        target = target.with_name(f"{target.stem}_{dt.datetime.now():%H%M%S}{target.suffix}")
    return target


def _smaller_pdf(src, target):
    """A lossless tidy-up of a PDF. Returns True only if it saved space."""
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        with fitz.open(src) as doc:
            doc.save(str(target), garbage=3, deflate=True)
        if Path(target).stat().st_size < 0.9 * Path(src).stat().st_size:
            return True
    except Exception:
        pass
    Path(target).unlink(missing_ok=True)
    return False


def place_file(src, target, move):
    """Put a file into the notebook. Photos are shrunk to save space (the text was
    already read from the full-size original), PDFs get a lossless tidy-up, and
    anything else is kept as it is. Returns where the file ended up."""
    src, target = Path(src), Path(target)
    ext = src.suffix.lower()
    done = None
    if COMPRESS_PHOTOS and ext in IMAGE_EXT:
        small = _unique(target.with_suffix(".jpg"))
        try:
            compress_image(src, small)
            if small.stat().st_size < src.stat().st_size:
                done = small
            else:
                small.unlink()                        # no saving: keep the original
        except Exception:
            small.unlink(missing_ok=True)
    elif COMPRESS_PDFS and ext == ".pdf":
        small = _unique(target)
        if _smaller_pdf(src, small):
            done = small
    if done:
        _same_times(src, done)
        if move:
            src.unlink()
        return done
    target = _unique(target)
    (shutil.move if move else shutil.copy2)(str(src), str(target))
    return target


def store_file(con, src, meta, check=True, move=False, text=None):
    """Read a file, then file it in its year/subject/term/week/day folder (photos
    are shrunk on the way). The original is only moved or removed after it has
    been read successfully, so a failure never loses a page.
    Returns the note id, or None if it was already stored."""
    src = Path(src)
    sha = sha256(src)                  # of the original, so a repeat is recognised
    if con.execute("select 1 from notes where sha=?", (sha,)).fetchone():
        print("skip (already stored):", src.name)
        return None
    if text is None:
        print("reading:", src.name)
        text = extract_text(src)
    dest = note_dir(meta["year"], meta["subject"], meta["term"], meta["week"], meta["day"])
    dest.mkdir(parents=True, exist_ok=True)
    target = place_file(src, dest / src.name, move)
    return save_note(con, target, meta, text, sha, check)


def print_report(text):
    print("\n" + "=" * 50 + "\nLECTURE COMPRESSION REPORT:\n" + "=" * 50)
    print(text)
    print("=" * 50)


def file_page(con, path, subject=None, when=None, check=True, move=False):
    """Read one photo, PDF or text file, work out where it belongs, and file it.
    Returns a dict describing what happened."""
    path = Path(path)
    if already_stored(con, path):
        return {"status": "duplicate"}
    text = extract_text(path)
    subject = slug(subject) if subject else (guess_subject(text) or "unsorted")
    if when:
        source = "the date you chose"
    else:
        when, source = file_date(path, text)
    meta = make_meta(when, subject)
    nid = store_file(con, path, meta, check=check, move=move, text=text)
    row = con.execute("select summary, feedback from notes where id=?", (nid,)).fetchone()
    return {"status": "filed", "meta": meta, "source": source, "note_id": nid,
            "summary": row[0] or "", "feedback": row[1] or ""}


# ---------------------------------------------------------------- YouTube
def video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def youtube_text(url):
    """Returns (video id, transcript). Tries YouTube's own captions first
    (fast, no download); if there are none, downloads only the audio."""
    vid = video_id(url)
    if not vid:
        raise ValueError("That doesn't look like a YouTube link.")
    langs = ["en", "en-AU", "en-GB", "en-US"]
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            parts = YouTubeTranscriptApi().fetch(vid, languages=langs)
            text = " ".join(s.text for s in parts)
        except AttributeError:                    # older library version
            parts = YouTubeTranscriptApi.get_transcript(vid, languages=langs)
            text = " ".join(p["text"] for p in parts)
        if text.strip():
            print("  used YouTube's captions")
            return vid, text
    except ImportError:
        print("  (youtube-transcript-api is not installed; trying an audio download)")
    except Exception as e:
        print(f"  no captions available ({type(e).__name__}); trying an audio download")
    import yt_dlp
    with tempfile.TemporaryDirectory() as tmp:           # the audio is deleted afterwards
        opts = {"format": "bestaudio/best", "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
                "quiet": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            audio = ydl.prepare_filename(info)
        print("  transcribing the audio (slow)...")
        text = transcribe_file(audio)
    return vid, text


def process_youtube(con, url, subject=None, when=None):
    """Turn a YouTube link into a filed transcript and a summary. True on success.
    A link that was done before is not fetched or filed again: its stored
    transcript is simply summarised again (useful if the summary failed)."""
    vid = video_id(url)
    if not vid:
        print("[!] Could not get that video: that doesn't look like a YouTube link.")
        return False
    old = con.execute("select * from notes where path like ?",
                      (f"%youtube_{vid}.transcript.txt",)).fetchone()
    if old:
        print("[=] This video is already stored; showing its summary again.")
        meta = {k: old[k] for k in ("year", "subject", "term", "week", "day")}
        rows = [old]
    else:
        try:
            vid, text = youtube_text(url)
        except Exception as e:
            print(f"[!] Could not get that video: {e}")
            return False
        if not text.strip():
            print("[!] No transcript could be made for that video.")
            return False
        subject = slug(subject) if subject else (guess_subject(text) or "unsorted")
        meta = make_meta(when or dt.datetime.now(), subject)
        dest = note_dir(meta["year"], meta["subject"], meta["term"], meta["week"], meta["day"])
        dest.mkdir(parents=True, exist_ok=True)
        txt = dest / f"youtube_{vid}.transcript.txt"
        txt.write_text(f"Source: {url}\n\n{text}", encoding="utf-8")
        ingest(con, txt, meta, check=False)
        rows = con.execute("select * from notes where sha=?", (sha256(txt),)).fetchall()
    if rows:
        print("[-] Distilling the video into notes...")
        print_report(summarise(con, rows))
    print(f"[+] Filed: {meta['subject']} / {meta['year']} term {meta['term']} "
          f"week {meta['week']} day {meta['day']}")
    return True


# ---------------------------------------------------------------- storage
def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def kind_of(path):
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "photos"
    if ext == ".pdf":
        return "PDFs"
    if ext in TEXT_EXT:
        return "text and transcripts"
    return "other"


def storage_report():
    by_kind, by_subject, files = {}, {}, []
    total = 0
    for f in ROOT.rglob("*"):
        if not f.is_file() or f == DB_PATH:
            continue
        size = f.stat().st_size
        total += size
        k = kind_of(f)
        by_kind[k] = by_kind.get(k, 0) + size
        rel = f.relative_to(ROOT).parts
        subject = rel[1] if len(rel) > 2 and rel[0].isdigit() else f"({rel[0].lower()})"
        by_subject[subject] = by_subject.get(subject, 0) + size
        files.append((size, f))
    db = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    lines = [f"Notes folder: {ROOT}",
             f"Files: {human(total)} in {len(files)} file(s).  Database: {human(db)}.", "", "By type:"]
    lines += [f"  {k}: {human(v)}" for k, v in sorted(by_kind.items(), key=lambda x: -x[1])]
    lines += ["", "By subject:"]
    lines += [f"  {k}: {human(v)}" for k, v in sorted(by_subject.items(), key=lambda x: -x[1])]
    if files:
        lines += ["", "Biggest files:"]
        lines += [f"  {human(sz)}  {f.relative_to(ROOT)}" for sz, f in sorted(files, reverse=True)[:5]]
    return "\n".join(lines)


def tidy_photos(con, days=60, side=1200, quality=70, delete=False, apply=False):
    """Shrink (or remove) photos filed more than `days` days ago. The text, summaries
    and review items are never touched. Without apply=True nothing changes."""
    cutoff = (today() - dt.timedelta(days=days)).isoformat()
    rows = con.execute("select id, path from notes where substr(added, 1, 10) <= ?",
                       (cutoff,)).fetchall()
    saved = count = 0
    for r in rows:
        p = Path(r["path"])
        if p.suffix.lower() not in IMAGE_EXT or not p.exists():
            continue
        size = p.stat().st_size
        if delete:
            gain = size
            if apply:
                p.unlink()
        else:
            if size < 80 * 1024:
                continue                                  # already small
            with tempfile.TemporaryDirectory() as tmp:
                small = compress_image(p, Path(tmp) / "x.jpg", side, quality)
                new = small.stat().st_size
                if new > 0.8 * size:
                    continue
                gain = size - new
                if apply:
                    final = p.with_suffix(".jpg")
                    shutil.copyfile(small, final)
                    _same_times(p, final)
                    if final != p:
                        p.unlink()
                        con.execute("update notes set path=? where id=?", (str(final), r["id"]))
        saved += gain
        count += 1
    if apply:
        con.commit()
        con.execute("vacuum")
    verb = ("removed" if delete else "shrunk") if apply else ("would be removed" if delete else "would be shrunk")
    msg = f"{count} photo(s) older than {days} days {verb}, freeing {human(saved)}."
    if not apply and count:
        msg += "  Nothing was changed yet: add --apply to do it."
    return msg


def cmd_storage(con, a=None):
    print(storage_report())


def cmd_tidy(con, a):
    if a.delete_photos and a.apply:
        print("Removing the old photos themselves (your text, summaries and review items stay).")
    print(tidy_photos(con, a.days, delete=a.delete_photos, apply=a.apply))


# ---------------------------------------------------------------- practice tests
QUIZ_DIR_NAME = "Practice tests"
QUIZ_TYPES = ("multiple_choice", "short_answer", "calculation")
QUIZ_STYLES = {
    "mixed": "a mix of multiple choice, short answer and (for maths or science) calculation questions",
    "multiple choice": "only multiple choice questions, each with four options",
    "short answer": "only short answer questions",
    "calculations": "only calculation questions that need working out",
}
QUIZ_PROMPT = (
    "You write practice questions for a high school student, using ONLY the notes below. "
    "Do not use facts that are not in the notes. Write {n} questions: {style}. "
    "Make them clear and different from each other. Return JSON like "
    '{{"questions": [{{"type": "multiple_choice" or "short_answer" or "calculation", '
    '"question": "...", "options": ["...", "...", "...", "..."] (multiple choice only), '
    '"answer": "the correct answer (for multiple choice, the letter A, B, C or D)", '
    '"explanation": "a short worked solution or reason", "marks": 1 to 4}}]}}.\n\nNOTES:\n{notes}')


def _clean_question(raw):
    """Check one question from the model. Returns a tidy dict, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question", "")).strip()
    answer = str(raw.get("answer", "")).strip()
    if len(question) < 8 or not answer:
        return None
    kind = str(raw.get("type", "")).lower()
    kind = ("multiple_choice" if "multi" in kind or "mcq" in kind
            else "calculation" if "calc" in kind else "short_answer")
    options = [re.sub(r"^\s*[A-Da-d][\).:]\s*", "", str(o)).strip() for o in (raw.get("options") or [])]
    options = [o for o in options if o][:4]
    if kind == "multiple_choice":
        if len(options) < 3:
            return None
        letter = answer[:1].upper()
        if len(answer) <= 2 and letter in "ABCD"[:len(options)]:
            index = "ABCD".index(letter)
        else:                                          # the model wrote the option text instead
            matches = [i for i, o in enumerate(options) if o.lower() == answer.lower()]
            if not matches:
                return None
            index = matches[0]
        answer = "ABCD"[index] + ". " + options[index]
    try:
        marks = int(raw.get("marks") or 0)
    except (TypeError, ValueError):
        marks = 0
    if marks < 1:                                   # missing or nonsense: use a sensible default
        marks = {"multiple_choice": 1, "short_answer": 2, "calculation": 3}[kind]
    marks = min(marks, 5)
    return {"type": kind, "question": question, "options": options if kind == "multiple_choice" else [],
            "answer": answer, "explanation": str(raw.get("explanation", "")).strip(), "marks": marks}


def make_questions(text, total=10, style="mixed", ask=None):
    """Write `total` questions from some notes. Long notes are split up so each
    request fits the small model, and the pieces are spread across the notes."""
    ask = ask or (lambda prompt: llm(prompt, as_json=True))
    pieces = [c for c in chunks(text, words=650) if c.strip()]
    if len(pieces) > 5:                                           # spread evenly across the notes
        pieces = [pieces[round(i * (len(pieces) - 1) / 4)] for i in range(5)]
    per = max(2, -(-total // max(1, len(pieces))) + 1)             # a spare or two for rejects
    found, seen = [], set()
    for piece in pieces:
        try:
            data = json.loads(ask(QUIZ_PROMPT.format(n=per, style=QUIZ_STYLES.get(style, QUIZ_STYLES["mixed"]),
                                                      notes=piece)))
        except (json.JSONDecodeError, TypeError):
            continue
        items = data.get("questions") if isinstance(data, dict) else data
        for raw in items or []:
            q = _clean_question(raw)
            key = re.sub(r"\W+", " ", q["question"].lower()).strip() if q else None
            if q and key not in seen:
                seen.add(key)
                found.append(q)
    found = found[:total]
    order = {t: i for i, t in enumerate(QUIZ_TYPES)}
    return sorted(found, key=lambda q: order[q["type"]])           # sections in a fixed order


def _page_number(paragraph, size_pt=8):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    run = paragraph.add_run()
    run.font.size = Pt(size_pt)
    for kind, text in (("begin", None), (None, " PAGE "), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def build_quiz_docx(path, title, subtitle, questions, answers_in_file=True, answers_only=False):
    """Write a practice test (and/or its answer key) as a Word document."""
    try:
        from docx import Document
        from docx.enum.table import WD_ROW_HEIGHT_RULE
        from docx.enum.text import WD_BREAK, WD_TAB_ALIGNMENT
        from docx.shared import Cm, Pt, RGBColor
    except ImportError:
        raise RuntimeError("Making Word documents needs one extra package. In PowerShell run:  "
                           "pip install python-docx")
    doc = Document()
    from docx.oxml.ns import qn
    zoom = doc.settings.element.find(qn("w:zoom"))        # python-docx's template omits this
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)               # A4
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(2)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(11)
    total = sum(q["marks"] for q in questions)
    grey = RGBColor(0x59, 0x59, 0x59)

    foot = sec.footer.paragraphs[0]
    note = foot.add_run("Written by a small AI model from your own notes. Answers can be wrong: "
                        "check them with your teacher or textbook.   Page ")
    note.font.size, note.font.color.rgb = Pt(8), grey
    _page_number(foot)

    def numbered(q_index, q, with_answer):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.tab_stops.add_tab_stop(Cm(17), WD_TAB_ALIGNMENT.RIGHT)
        p.add_run(f"{q_index}.  ").bold = True
        p.add_run(q["question"])
        if not with_answer:
            r = p.add_run(f"\t[{q['marks']} mark{'s' if q['marks'] != 1 else ''}]")
            r.font.size, r.font.color.rgb = Pt(9), grey

    if not answers_only:
        doc.add_heading(title, level=1)
        meta = doc.add_paragraph(subtitle)
        meta.runs[0].font.color.rgb = grey
        doc.add_paragraph(f"Name: ______________________     Date: ______________     "
                          f"Time: ________     Total marks: {total}")
        doc.add_paragraph("Answer every question. Show your working for calculations.")
        index = 0
        sections = (("multiple_choice", "Multiple choice"), ("short_answer", "Short answer"),
                    ("calculation", "Calculations"))
        for kind, heading in sections:
            group = [q for q in questions if q["type"] == kind]
            if not group:
                continue
            doc.add_heading(heading, level=2)
            for q in group:
                index += 1
                numbered(index, q, with_answer=False)
                for i, opt in enumerate(q["options"]):
                    op = doc.add_paragraph(f"{'ABCD'[i]}.  {opt}")
                    op.paragraph_format.left_indent = Cm(1.2)
                    op.paragraph_format.space_after = Pt(1)
                if kind != "multiple_choice":
                    box = doc.add_table(rows=1, cols=1, style="Table Grid")
                    row = box.rows[0]
                    row.height = Cm(2.2 if kind == "short_answer" else 4.5)
                    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
    if answers_in_file or answers_only:
        if not answers_only:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        doc.add_heading("Answer key and worked solutions" if not answers_only else f"Answer key: {title}", level=1)
        if answers_only:
            doc.add_paragraph(subtitle).runs[0].font.color.rgb = grey
        index = 0
        for kind in QUIZ_TYPES:
            for q in (x for x in questions if x["type"] == kind):
                index += 1
                numbered(index, q, with_answer=True)
                a = doc.add_paragraph()
                a.paragraph_format.left_indent = Cm(1.2)
                a.add_run("Answer: ").bold = True
                a.add_run(q["answer"])
                if q["explanation"]:
                    e = doc.add_paragraph(q["explanation"])
                    e.paragraph_format.left_indent = Cm(1.2)
                    e.runs[0].italic = True
    doc.save(str(path))
    return Path(path)


def quiz_request(text):
    """Pick the number of questions and the style out of a request like
    'make me a 5 question multiple choice test on week 4 physics'."""
    low = text.lower()
    m = re.search(r"\b(\d{1,2})\s*(?:-\s*)?(?:questions?|qs?|mcqs?)\b", low)
    count = int(m.group(1)) if m else None
    style = ("multiple choice" if re.search(r"multiple[- ]choice|mcq", low)
             else "short answer" if re.search(r"short[- ]answer", low)
             else "calculations" if re.search(r"calculation|calculate|problems?", low) else None)
    return count, style


def make_quiz(con, request, count=None, style=None, separate_answers=False, out_dir=None):
    """Make a practice test as a Word document. Returns (path, questions, answers_path)."""
    asked_count, asked_style = quiz_request(request)
    count = max(1, min(30, count or asked_count or 10))
    style = style or asked_style or "mixed"
    info = understand(con, request)
    ql = normalise(request)
    weak_mode = has_word(re.findall(r"[a-z']+", ql), WEAK_WORDS)
    f = dict(info["filters"])
    if weak_mode:
        where, args = "active=1", []
        if "subject" in f:
            where += " and subject=?"
            args.append(f["subject"])
        weak = con.execute(f"select subject, topic, fix from weak where {where}", args).fetchall()
        if not weak:
            raise RuntimeError("There is nothing on your review list for that yet.")
        text = "\n".join(f"{w['topic']}: {w['fix']}" for w in weak)
        what = "your weak points"
        subject = f.get("subject") or (weak[0]["subject"] if len({w["subject"] for w in weak}) == 1 else "mixed")
        sources = len(weak)
    else:
        rows, f, _ = pick_notes(con, info)
        rows = [r for r in rows if r["text"].strip()]
        if not rows:
            raise RuntimeError(f"I couldn't find any notes for {label(f, info['topic'])}, "
                               "so there is nothing to write questions from.")
        text = "\n\n".join(r["text"] for r in rows)
        what = label(f, info["topic"])
        subject = f.get("subject") or (rows[0]["subject"] if len({r["subject"] for r in rows}) == 1 else "mixed")
        sources = len(rows)
    questions = make_questions(text, count, style)
    if not questions:
        raise RuntimeError("The model didn't write any usable questions this time. Try again, "
                           "or choose fewer notes or a shorter time period.")
    out_dir = Path(out_dir or ROOT / QUIZ_DIR_NAME)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{slug(subject)}_{today().isoformat()}_{slug(what)[:40]}"
    path = _unique(out_dir / f"{stem}.docx")
    title = f"Practice test: {subject.replace('-', ' ').title()}"
    subtitle = f"Made {today():%d %B %Y} from {sources} note{'s' if sources != 1 else ''}: {what}"
    build_quiz_docx(path, title, subtitle, questions, answers_in_file=not separate_answers)
    answers_path = None
    if separate_answers:
        answers_path = _unique(out_dir / f"{stem}_answers.docx")
        build_quiz_docx(answers_path, title, subtitle, questions, answers_only=True)
    return path, questions, answers_path


def cmd_quiz(con, a):
    request = " ".join(a.request or [])
    if not request.strip():
        print("Say what the test should cover, for example:  quiz physics week 4   or   "
              "make me a 5 question multiple choice test on projectile motion")
        return
    try:
        path, questions, answers = make_quiz(con, request, a.questions, a.style, a.separate_answers)
    except RuntimeError as e:
        print(f"[!] {e}")
        return
    marks = sum(q["marks"] for q in questions)
    print(f"Made a practice test with {len(questions)} questions ({marks} marks).")
    print(f"Test: {path}")
    if answers:
        print(f"Answers: {answers}")
    else:
        print("The answer key is on the last pages of the same document.")
    print("It was written by a small AI model, so check the answers before you rely on them.")


# ---------------------------------------------------------------- commands
def cmd_add(con, a):
    explicit = a.term and a.week and a.day
    for pattern in a.files:                       # expands *.jpg on Windows too
        for f in glob.glob(pattern) or [pattern]:
            f = Path(f)
            if not f.is_file():
                print("not found:", f)
                continue
            if already_stored(con, f):
                print("skip (already stored):", f.name)
                continue
            print("reading:", f.name)
            text = extract_text(f)
            if a.date:
                when = dt.datetime.combine(dt.date.fromisoformat(a.date), dt.time(12))
                src = "--date"
            else:
                when, src = file_date(f, text)
            meta = make_meta(when, a.subject)
            if explicit:
                meta.update(year=a.year or meta["year"], term=a.term, week=a.week, day=a.day)
                src = "your term/week/day"
            print(f"  filed as {meta['subject']} {meta['year']} term {meta['term']} "
                  f"week {meta['week']} day {meta['day']} (from {src})")
            store_file(con, f, meta, check=not a.no_check, text=text)


def cmd_scan(con, a):
    for f in sorted(ROOT.rglob("*")):
        if f.is_file() and f.name != DB_PATH.name:
            meta = parse_path(f)
            if meta:
                ingest(con, f, meta, check=not a.no_check)    # skips anything already filed
            elif f.suffix.lower() in IMAGE_EXT | TEXT_EXT | {".pdf"}:
                print("ignored (not in a year/subject/term/week/day folder):", f)


def cmd_youtube(con, a):
    when = None
    if a.date:
        when = dt.datetime.combine(dt.date.fromisoformat(a.date), dt.time(12))
    process_youtube(con, a.url, a.subject, when)


# ---------------------------------------------------------------- understanding questions
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
            "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

SUMMARY_WORDS = {"summarise", "summarize", "summary", "summarising", "summarizing", "recap",
                 "overview", "outline", "condense", "digest"}
WEAK_WORDS = {"wrong", "mistake", "mistakes", "error", "errors", "weak", "struggle",
              "struggling", "struggled", "forgot", "forget", "trouble", "difficult", "hard", "bad"}
SHOW_WORDS = {"show", "print", "read", "transcript", "transcription", "display"}
TUTOR_RE = (r"\b(teach me|tutor|explain|help me (?:to )?understand|(?:don'?t|do not|dont) "
            r"(?:understand|get)|confus\w+|walk me through|how does|how do)\b")
INTENT_WORDS = SUMMARY_WORDS | WEAK_WORDS | SHOW_WORDS | {"teach", "tutor", "explain", "understand"}

STOPWORDS = set("""a an and are as at be been but by can could did do does for from get got had has
have how i if in into is it its just me my of on or our please so some than that the their them then
there these this those to up was we were what whats when which who why will with would you your about
all any every everything stuff things thing notes note page pages learn learned learnt learning cover
covered study studied lesson lessons class during tell give need want help show pdf pdfs photo photos
picture pictures image images video videos lecture lectures youtube week weeks term day days year
today yesterday tomorrow last next monday tuesday wednesday thursday friday saturday sunday
sum summarise summarize summary recap overview us um like also really very bit more most
revise revision again then them out over under between see look list find point points mine quiz quizzes questions question practice exam worksheet make create write generate multiple choice short answer calculation calculations mcq""".split())


def normalise(q):
    ql = q.lower().replace("\u2019", "'")
    ql = re.sub(r"\b(yesterday|today|tomorrow)'?s\b", r"\1", ql)       # "yesterdays physics"
    ql = re.sub(r"\b(wks?|weeks)\b", "week", ql)
    ql = re.sub(r"\b(semester|sem)\b", "term", ql)

    def fix(m):                                   # "wek 4", "trem 4", "dya 3"
        word, num = m.group(1), m.group(2)
        if word in ("term", "week", "day", "weak"):
            return m.group(0)
        close = difflib.get_close_matches(word, ["term", "week", "day"], n=1, cutoff=0.75)
        return f"{close[0]} {num}" if close else m.group(0)
    ql = re.sub(r"\b([a-z]{3,5})\s*(\d{1,2})\b", fix, ql)
    for w, n in NUMBER_WORDS.items():
        ql = re.sub(rf"\b(term|week|day)\s+{w}\b", rf"\1 {n}", ql)
    for w, n in ORDINALS.items():
        ql = re.sub(rf"\b{w}\s+(term|week|day)\b", rf"\1 {n}", ql)
    ql = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\s+(term|week|day)\b", r"\2 \1", ql)
    ql = re.sub(r"\b(term|week|day)\s*(\d{1,2})\b", r"\1 \2", ql)
    return ql


def subject_vocab(subjects):
    vocab = {}
    for s in subjects:
        for w in s.split("-"):
            if len(w) >= 4:
                vocab.setdefault(w, s)
    for alias, target in ALIASES.items():
        if slug(target) in subjects:
            vocab.setdefault(alias, slug(target))
    return vocab


def find_subject_in(ql, subjects):
    plain = ql.replace("-", " ")
    for s in sorted(subjects, key=len, reverse=True):          # whole names first
        if re.search(rf"\b{re.escape(s.replace('-', ' '))}\b", plain):
            return s
    vocab = subject_vocab(subjects)
    tokens = re.findall(r"[a-z]+", plain)
    for t in tokens:                                           # exact words and nicknames
        if t in vocab:
            return vocab[t]
    for t in tokens:                                           # typos
        if len(t) >= 4 and t not in STOPWORDS:
            close = difflib.get_close_matches(t, list(vocab), n=1, cutoff=0.8)
            if close:
                return vocab[close[0]]
    return None


def relative_filters(ql, subject):
    t = today()
    out = {}

    def tw(d):
        return cfg.term_week_day(d, subject) if cfg else (1, 1, d.isoweekday())

    def fill(d, parts):
        term, week, day = tw(d)
        out["year"] = d.year
        if "term" in parts:
            out["term"] = term
        if "week" in parts:
            out["week"] = week
        if "day" in parts:
            out["day"] = day

    if re.search(r"\byesterday\b", ql):
        fill(t - dt.timedelta(days=1), ("term", "week", "day"))
    elif re.search(r"\btoday\b|\btodays\b", ql):
        fill(t, ("term", "week", "day"))
    elif re.search(r"\blast week\b", ql):
        fill(t - dt.timedelta(days=7), ("term", "week"))
    elif re.search(r"\bthis week\b", ql):
        fill(t, ("term", "week"))
    elif re.search(r"\blast term\b", ql):
        term, _, _ = tw(t)
        out["year"], out["term"] = (t.year, term - 1) if term > 1 else (t.year - 1, 4)
    elif re.search(r"\bthis term\b", ql):
        fill(t, ("term",))
    else:
        for i, name in enumerate(WEEKDAYS):
            m = re.search(rf"\b(last\s+)?{name}\b", ql)
            if m:
                back = (t.weekday() - i) % 7
                if m.group(1):
                    back = back + 7 if back else 7
                fill(t - dt.timedelta(days=back), ("term", "week", "day"))
                break
    return out


def _filters(ql, subjects):
    f = {}
    m = re.search(r"\b(20\d\d)\b", ql)
    if m:
        f["year"] = int(m.group(1))
    for key in ("term", "week", "day"):
        m = re.search(rf"\b{key}\s+(\d+)\b", ql)
        if m:
            f[key] = int(m.group(1))
    subject = find_subject_in(ql, subjects)
    if subject:
        f["subject"] = subject
    for k, v in relative_filters(ql, subject).items():
        f.setdefault(k, v)
    return f


def parse_query(q, subjects):
    return _filters(normalise(q), subjects)


def has_word(tokens, targets, cutoff=0.84):
    for t in tokens:
        if t in targets:
            return True
        if len(t) >= 5 and difflib.get_close_matches(t, list(targets), n=1, cutoff=cutoff):
            return True
    return False


QUIZ_RE = (r"\b(quiz|quizzes|mcq|practice (?:test|exam|paper|questions?)|"
           r"(?:make|create|write|generate|give)(?: me)?(?: an?| some| \d+)? ?(?:\w+ ){0,3}(?:test|exam|worksheet|quiz|questions?)|"
           r"test me|quiz me|questions? (?:on|about|for))\b")


def detect_intent(ql):
    if re.search(QUIZ_RE, ql):
        return "quiz"
    tokens = re.findall(r"[a-z']+", ql)
    summary = has_word(tokens, SUMMARY_WORDS)
    if re.search(TUTOR_RE, ql) and not summary:
        return "tutor"
    if has_word(tokens, WEAK_WORDS):
        return "weak"
    if summary:
        return "summary"
    if has_word(tokens, SHOW_WORDS):
        return "show"
    return "summary"


def detect_kind(ql):
    if re.search(r"\bpdfs?\b", ql):
        return "pdf"
    if re.search(r"\b(photos?|pictures?|images?)\b", ql):
        return "photo"
    if re.search(r"\b(videos?|lectures?|youtube|transcripts?)\b", ql):
        return "video"
    return None


def topic_words(ql, subjects):
    vocab = subject_vocab(subjects)
    out = []
    for t in re.findall(r"[a-z][a-z0-9']+", ql):
        if len(t) < 3 or t in STOPWORDS or t in INTENT_WORDS or t in vocab:
            continue
        if len(t) >= 5 and (difflib.get_close_matches(t, list(INTENT_WORDS), n=1, cutoff=0.84)
                            or difflib.get_close_matches(t, list(vocab), n=1, cutoff=0.8)):
            continue
        if t in {"term", "week", "day"} or re.fullmatch(r"[a-z]*\d+[a-z]*", t):
            continue
        out.append(t)
    return out


def understand(con, q):
    ql = normalise(q)
    subjects = known_subjects(con)
    return {"intent": detect_intent(ql), "filters": _filters(ql, subjects),
            "kind": detect_kind(ql), "topic": topic_words(ql, subjects)}


def known_subjects(con):
    stored = [r[0] for r in con.execute("select distinct subject from notes")]
    return sorted(set(stored) | {slug(s) for s in CONFIG_SUBJECTS})


def select_notes(con, f):
    where, args = [], []
    for k in ("year", "subject", "term", "week", "day"):     # fixed names only
        if k in f:
            where.append(f"{k}=?")
            args.append(f[k])
    sql = "select * from notes" + (" where " + " and ".join(where) if where else "")
    return con.execute(sql + " order by year, subject, term, week, day, id", args).fetchall()


def filter_kind(rows, kind):
    if not kind:
        return rows
    def ok(r):
        p = r["path"].lower()
        if kind == "pdf":
            return p.endswith(".pdf")
        if kind == "photo":
            return any(p.endswith(e) for e in IMAGE_EXT)
        return "transcript" in p
    return [r for r in rows if ok(r)]


def rank_by_topic(rows, topic, limit=8):
    """Keep the notes that mention the topic words (forgiving of typos)."""
    vocab = set()
    for r in rows:
        vocab.update(w for w in re.findall(r"[a-z]{4,}", r["text"].lower()))
    words = []
    for t in topic:
        if t in vocab or len(t) < 4:
            words.append(t)
        else:
            close = difflib.get_close_matches(t, list(vocab), n=1, cutoff=0.8)
            words.append(close[0] if close else t)
    scored = [(sum(r["text"].lower().count(w) for w in words), r) for r in rows]
    keep = sorted([x for x in scored if x[0] > 0], key=lambda x: -x[0])[:limit]
    return sorted((r for _, r in keep), key=lambda r: r["id"])


def label(f, topic=None):
    bits = [f.get("subject", "all subjects"), str(f.get("year", ""))]
    for k in ("term", "week", "day"):
        if k in f:
            bits.append(f"{k} {f[k]}")
    if topic:
        bits.append("about " + " ".join(topic))
    return " ".join(b for b in bits if b)


def summarise(con, rows):
    key = hashlib.sha256((PROMPT_VERSION + "|".join(sorted(r["sha"] for r in rows)))
                         .encode()).hexdigest()
    hit = con.execute("select text from summaries where key=?", (key,)).fetchone()
    if hit:
        return hit[0]
    text = "\n\n".join(r["text"] for r in rows)
    parts = [llm(SUMMARY_PROMPT + "\n\n" + c) for c in chunks(text)]
    final = parts[0] if len(parts) == 1 else llm(SUMMARY_PROMPT + "\n\n" + "\n".join(parts))
    con.execute("insert or replace into summaries values (?,?)", (key, final))
    con.commit()
    return final


def show_weak(rows):
    if not rows:
        print("Nothing flagged.")
    for w in rows:
        print(f"- [{w['subject']}] {w['topic']}: {w['fix']}")


def pick_notes(con, info):
    """Turn an understood question into (notes, filters, a note for the user)."""
    f = dict(info["filters"])
    topic = info["topic"]
    time_given = any(k in f for k in ("term", "week", "day"))
    if "year" not in f and (time_given or not topic):
        f["year"] = today().year
    rows = filter_kind(select_notes(con, f), info["kind"])
    note = ""
    if topic and rows:
        ranked = rank_by_topic(rows, topic)
        if ranked:
            rows = ranked
        else:
            note = (f"I couldn't find '{' '.join(topic)}' in those notes, "
                    f"so this covers everything for {label(f)}.\n\n")
    return rows, f, note


def start_tutor_from_question(con, info, interactive):
    subject = info["filters"].get("subject")
    topic = info["topic"]
    if not subject or not topic:
        print("Tutor mode needs a subject and a topic, for example:  "
              "tutor physics projectile motion")
        return
    if not interactive:
        print(f"That sounds like tutor mode. Open the Tutor tab, choose {subject} "
              f"and type: {' '.join(topic)}")
        return
    cmd_tutor(con, argparse.Namespace(subject=subject, topic=topic))


def cmd_ask(con, a):
    q = " ".join(a.question)
    info = understand(con, q)
    if info["intent"] == "quiz":
        return cmd_quiz(con, argparse.Namespace(request=q.split(), questions=None, style=None,
                                                separate_answers=False))
    if info["intent"] == "tutor":
        return start_tutor_from_question(con, info, getattr(a, "interactive", True))
    rows, f, note = pick_notes(con, info)
    topic = info["topic"]
    if not rows:
        print(f"No notes found for {label(f, topic)}.")
        return cmd_status(con, a)
    if "subject" not in f and len({r["subject"] for r in rows}) > 1:
        print("Which subject? I found notes in:", ", ".join(sorted({r["subject"] for r in rows})))
        return
    ids = ",".join(str(r["id"]) for r in rows)
    if info["intent"] == "weak":
        print(f"Possible weak points: {label(f, topic)}\n")
        if any(k in f for k in ("term", "week", "day")) or topic:
            q2 = f"select * from weak where note_id in ({ids}) and active=1"
            show_weak(con.execute(q2).fetchall())
        elif "subject" in f:
            show_weak(con.execute("select * from weak where subject=? and active=1",
                                  (f["subject"],)).fetchall())
        else:
            show_weak(con.execute("select * from weak where active=1").fetchall())
        return
    if info["intent"] == "show":
        print(note, end="")
        for r in rows:
            print(f"\n--- {Path(r['path']).name}  ({r['subject']}, term {r['term']}, "
                  f"week {r['week']}, day {r['day']}) ---")
            print(r["text"])
        return
    print(f"Summary: {label(f, topic)}  ({len(rows)} note(s))\n")
    print(note, end="")
    print(summarise(con, rows))
    weak = con.execute(f"select * from weak where note_id in ({ids}) and active=1").fetchall()
    if weak:
        print("\nShaky points from these notes:")
        show_weak(weak)


def cmd_show(con, a):
    q = " ".join(a.question)
    info = understand(con, q)
    info["intent"] = "show"
    rows, f, note = pick_notes(con, info)
    if not rows:
        print(f"No notes found for {label(f, info['topic'])}.")
        return
    print(note, end="")
    for r in rows:
        print(f"\n--- {Path(r['path']).name}  ({r['subject']}, term {r['term']}, "
              f"week {r['week']}, day {r['day']}) ---")
        print(r["text"])


# ---------------------------------------------------------------- weak points, tests, review
def cmd_weak(con, a):
    ok = add_weak(con, a.subject, a.topic, a.issue or "", a.fix)
    print("Added." if ok else "Already on the list.")


def cmd_test(con, a):
    dt.date.fromisoformat(a.date)                 # validates the date
    con.execute("insert into tests(subject, name, date) values (?,?,?)",
                (slug(a.subject), a.name, a.date))
    con.commit()
    print(f"Test saved: {a.name} ({slug(a.subject)}) on {a.date}")


def cmd_remind(con, a=None, horizon=14):
    t = today()
    due = con.execute("select * from weak where active=1 and due<=? order by subject, due",
                      (t.isoformat(),)).fetchall()
    print(f"Due for review today: {len(due)}")
    show_weak(due)
    soon = con.execute("select * from tests where date>=? and date<=? order by date",
                       (t.isoformat(), (t + dt.timedelta(days=horizon)).isoformat())).fetchall()
    for x in soon:
        days = (dt.date.fromisoformat(x["date"]) - t).days
        n = con.execute("select count(*) from weak where subject=? and active=1",
                        (x["subject"],)).fetchone()[0]
        print(f"\nTEST in {days} day(s): {x['name']} ({x['subject']}) on {x['date']}")
        print(f"  {n} shaky point(s) on the list. Run: review")
        print(f"  Revise with: tutor {x['subject'].replace('-', ' ')} <topic>")
    if not due and not soon:
        print("\nNothing urgent.")


def cmd_review(con, a=None):
    due = con.execute("select * from weak where active=1 and due<=? order by subject, due",
                      (today().isoformat(),)).fetchall()
    if not due:
        print("Nothing due.")
        return
    for w in due:
        print(f"\n[{w['subject']}] {w['topic']}")
        if w["issue"]:
            print("  Watch out:", w["issue"])
        input("  Think of the answer, then press Enter... ")
        print("  ->", w["fix"])
        ans = input("  Remembered? (y)es / (n)o / (d)elete if the checker was wrong / (q)uit: ").strip().lower()
        if ans.startswith("q"):
            break
        if ans.startswith("d"):
            con.execute("update weak set active=0 where id=?", (w["id"],))
            con.commit()
        else:
            print("  next review:", reschedule(con, w, ans.startswith("y")))


def cmd_status(con, a=None):
    rows = con.execute("select year, subject, term, count(*) n from notes "
                       "group by year, subject, term order by year, subject, term").fetchall()
    if not rows:
        print("No notes stored yet. Drop a photo in the inbox, or use: "
              "add <file> --subject physics")
    for r in rows:
        print(f"{r['year']}  {r['subject']}  term {r['term']}: {r['n']} note(s)")
    w = con.execute("select count(*) from weak where active=1").fetchone()[0]
    print(f"Weak points on the list: {w}")
    print(f"(database: {DB_PATH})")


# ---------------------------------------------------------------- tutor mode
def find_context(con, subject, topic, limit=3, chars=2500):
    """Pick the stored notes that mention the topic most."""
    words = [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if len(w) > 2]
    rows = con.execute("select * from notes" + (" where subject=?" if subject else ""),
                       (subject,) if subject else ()).fetchall()
    scored = sorted(((sum(r["text"].lower().count(w) for w in words), r) for r in rows),
                    key=lambda x: -x[0])
    keep = [r for s, r in scored if s > 0][:limit]
    return "\n---\n".join(r["text"][:chars // max(1, len(keep))] for r in keep)


def tutor_start(con, subject, topic):
    """Set up a tutor conversation. Returns (messages, found_notes)."""
    subject = slug(subject)
    notes = find_context(con, subject, topic)
    weak = con.execute("select topic from weak where subject=? and active=1 limit 10",
                       (subject,)).fetchall()
    system = TUTOR_SYSTEM
    if notes:
        system += "\n\nTHE STUDENT'S NOTES:\n" + notes
    if weak:
        system += "\n\nTopics the student has found hard: " + ", ".join(w["topic"] for w in weak)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"I want to understand: {topic}. "
                 "Start with the big idea, then check what I understood."}]
    return messages, bool(notes)


def tutor_wrapup(con, subject, messages):
    """After a session: save the ideas the student found hard. Returns how many."""
    if len(messages) < 4:
        return 0
    messages = messages + [{"role": "user", "content":
                            'The session is over. Return JSON like {"shaky": [{"topic": "...", '
                            '"fix": "..."}]} listing up to 3 ideas I got wrong or was unsure '
                            "about, with the correct idea in fix. Use an empty list if none."}]
    try:
        data = json.loads(chat_llm(messages, as_json=True))
        items = data.get("shaky") or []
    except (json.JSONDecodeError, TypeError, AttributeError):
        items = []
    added = 0
    for m in items:
        if isinstance(m, dict) and m.get("topic") and m.get("fix"):
            added += add_weak(con, slug(subject), str(m["topic"]), "", str(m["fix"]), None, "tutor")
    return added


def cmd_tutor(con, a):
    subject = slug(a.subject)
    topic = " ".join(a.topic)
    messages, found = tutor_start(con, subject, topic)
    print(f"Tutor mode: {subject}, {topic}")
    print("(using your notes)" if found else "(no matching notes found, so this uses general knowledge)")
    print("Answer the tutor's questions. Type 'done' to finish.")
    while True:
        reply = chat_llm(messages)
        print("\nTutor:", reply)
        messages.append({"role": "assistant", "content": reply})
        try:
            ans = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if ans.lower() in ("done", "quit", "exit", "q"):
            break
        if ans:
            messages.append({"role": "user", "content": ans})
    added = tutor_wrapup(con, subject, messages)
    print(f"\n{added} shaky idea(s) added to your review list. "
          "(A small model can be wrong, so check anything important with your teacher or textbook.)")


# ---------------------------------------------------------------- chat + main
CHAT_COMMANDS = {"add", "scan", "show", "weak", "test", "remind", "review",
                 "status", "youtube", "tutor", "storage", "tidy", "quiz"}
NO_ARGS = {"remind", "review", "status", "storage"}


def cmd_chat(con, a=None):
    parser = build_parser()
    print("Ask in your own words, e.g.  summarise yesterdays physics   or   what did I get wrong in week 4")
    print("Other things to type: tutor, youtube, add, review, remind, status, quit")
    while True:
        try:
            q = input("\nask> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        words = q.split()
        first = words[0].lower()
        if len(first) >= 5:                       # forgive typos like "reveiw"
            close = difflib.get_close_matches(
                first, ["remind", "review", "status", "youtube", "tutor", "storage", "quit"], n=1, cutoff=0.8)
            if close:
                first = close[0]
                q = " ".join([first] + words[1:])
        if first in ("quit", "exit", "q"):
            break
        is_command = first in CHAT_COMMANDS and not (first in NO_ARGS and len(words) > 1)
        if first == "test" and not (len(words) >= 4 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", words[2])):
            is_command = False                    # "test me on week 4" is a question, not a test date
        if is_command:
            try:
                tokens = [t.strip("\"'") for t in shlex.split(q, posix=False)]
                args = parser.parse_args(tokens)
            except (SystemExit, ValueError):
                continue                          # argparse already explained the problem
            try:
                args.fn(con, args)
            except Exception as e:
                print(f"[!] That failed: {e}")
        else:
            try:
                cmd_ask(con, argparse.Namespace(question=words))
            except Exception as e:
                print(f"[!] That failed: {e}")


def build_parser():
    ap = argparse.ArgumentParser(prog="notebook_checker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="file photos/PDFs you already have")
    p.add_argument("files", nargs="+")
    p.add_argument("--subject", required=True)
    p.add_argument("--date", help="YYYY-MM-DD; otherwise worked out from the file")
    p.add_argument("--year", type=int)
    p.add_argument("--term", type=int)
    p.add_argument("--week", type=int)
    p.add_argument("--day", type=int)
    p.add_argument("--no-check", action="store_true", help="read only, skip the checker")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("scan")
    p.add_argument("--no-check", action="store_true")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("ask")
    p.add_argument("question", nargs="+")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("show")
    p.add_argument("question", nargs="+")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("youtube")
    p.add_argument("url")
    p.add_argument("subject", nargs="?")
    p.add_argument("--date", help="YYYY-MM-DD; otherwise today")
    p.set_defaults(fn=cmd_youtube)

    p = sub.add_parser("tutor")
    p.add_argument("subject")
    p.add_argument("topic", nargs="+")
    p.set_defaults(fn=cmd_tutor)

    p = sub.add_parser("weak")
    p.add_argument("subject")
    p.add_argument("topic")
    p.add_argument("fix")
    p.add_argument("--issue")
    p.set_defaults(fn=cmd_weak)

    p = sub.add_parser("test")
    p.add_argument("subject")
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("name")
    p.set_defaults(fn=cmd_test)

    p = sub.add_parser("quiz", help="write a practice test as a Word document")
    p.add_argument("request", nargs="*", help="what it should cover, e.g. physics week 4")
    p.add_argument("-n", "--questions", type=int, help="how many questions (default 10)")
    p.add_argument("--style", choices=list(QUIZ_STYLES), help="kind of questions (default mixed)")
    p.add_argument("--separate-answers", action="store_true", help="put the answers in their own document")
    p.set_defaults(fn=cmd_quiz)

    p = sub.add_parser("tidy", help="shrink or remove old photos to save space")
    p.add_argument("--days", type=int, default=60, help="only photos filed more than this many days ago")
    p.add_argument("--delete-photos", action="store_true", help="remove the photos (the text is kept)")
    p.add_argument("--apply", action="store_true", help="actually do it (otherwise just a preview)")
    p.set_defaults(fn=cmd_tidy)

    for name, fn in (("chat", cmd_chat), ("remind", cmd_remind), ("review", cmd_review),
                     ("status", cmd_status), ("storage", cmd_storage)):
        sub.add_parser(name).set_defaults(fn=fn)
    return ap


def main():
    a = build_parser().parse_args()
    a.fn(connect(), a)


if __name__ == "__main__":
    main()

"""notebook_gui.py - the notebook as a small desktop window. No browser needed.

Start it with:   python notebook_gui.py     (or double-click start_notebook.bat)

It uses Tkinter, which comes with Python, so it needs very little memory. It also
watches your inbox folder while it is open, so files you drop in are filed
automatically. Colours are set in notebook_config.py (GUI_THEME) and your own
pictures go in the assets folder (logo.png, banner.png).
"""
import datetime as dt
import io
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from argparse import Namespace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import notebook_checker as nc
import notebook_config as cfg

SUBJECTS = list(cfg.SUBJECTS)
GUESS = "(let the model guess)"
THEME = {"bg": "#0E1117", "panel": "#262730", "fg": "#FAFAFA", "accent": "#4F8BF9", "muted": "#9AA0A6"}
THEME.update(getattr(cfg, "GUI_THEME", {}))
ASSETS = Path(__file__).resolve().parent / "assets"

HELP_TEXT = """Quick fixes

"Ollama kept failing (error 503)"
   Close other programs (browsers use a lot of memory). Quit Ollama from the system tray and open it again.
   In PowerShell, run:  ollama ps   to see whether the model loaded.
   Still failing? In notebook_config.py change OLLAMA_CONTEXT = 4096 to 2048.

"Can't reach Ollama"
   Open the Ollama app and look for its icon in the system tray.

"Ollama does not have the model"
   In PowerShell run:  ollama pull qwen2.5vl:3b

Nothing is found when I ask
   Check the Add notes tab filed the pages (look at the Activity tab), and that the subject and week you
   asked about match. Questions work like:  summarise yesterdays physics  /  what did I get wrong in week 4

A page was filed under the wrong day
   Write the date at the top of the page (day first, like 21/10/2026), or put it in the file name
   (2026-10-21_physics.pdf). The message after filing says which date was used.

Disk space
   Open the Storage tab. Photos are shrunk when they are filed. You can also shrink or remove old photos there.
"""


def tidy(text):
    """Drop the ===== divider lines so the text reads cleanly."""
    return "\n".join(l for l in text.splitlines() if l.strip() and set(l.strip()) != {"="})


def open_folder(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(path))                       # noqa: Windows only
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    else:
        subprocess.run(["xdg-open", str(path)])


class OutputRouter:
    """Replaces sys.stdout. print() output goes to the buffer of whichever
    background job is running on that thread; anything else (such as the inbox
    watcher) goes to the Activity tab."""

    def __init__(self, q, original=None):
        self.local = threading.local()
        self.q = q
        self.original = original

    def set_sink(self, sink):
        self.local.sink = sink

    def clear_sink(self):
        self.local.sink = None

    def write(self, text):
        sink = getattr(self.local, "sink", None)
        if sink is not None:
            sink.write(text)
        elif text:
            self.q.put(("log", text))
            if self.original is not None:
                try:
                    self.original.write(text)
                except Exception:
                    pass
        return len(text)

    def flush(self):
        pass


class App(tk.Tk):
    def __init__(self, watch=True):
        super().__init__()
        self.title("My Notebook")
        self.geometry("1020x700")
        self.minsize(820, 560)
        self.configure(bg=THEME["bg"])
        self.q = queue.Queue()
        self.busy = False
        self.tutor = None
        self.observer = None
        self._orig_stdout, self._orig_stderr = sys.stdout, sys.stderr
        self.router = OutputRouter(self.q, self._orig_stdout)
        sys.stdout = sys.stderr = self.router
        self.con = nc.connect()                       # used on this thread only
        self._style()
        self._header()
        self.status = tk.StringVar(value="Ready")
        self.nb = ttk.Notebook(self)
        for name, build in (("Ask", self._build_ask), ("Add notes", self._build_add),
                            ("Tutor", self._build_tutor), ("Practice test", self._build_quiz),
                            ("Review", self._build_review),
                            ("Tests and weak points", self._build_tests),
                            ("Storage", self._build_storage), ("Activity", self._build_activity),
                            ("Help", self._build_help)):
            frame = ttk.Frame(self.nb, padding=10)
            self.nb.add(frame, text=name)
            build(frame)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        ttk.Label(self, textvariable=self.status, style="Muted.TLabel", anchor="w").pack(
            fill="x", padx=12, pady=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.close)
        if watch:
            self._start_watcher()
        self.refresh_status()
        self.load_review()
        self._poll_id = self.after(100, self._poll)

    # ------------------------------------------------------------ look
    def _style(self):
        t = THEME
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=t["bg"], foreground=t["fg"], fieldbackground=t["panel"],
                     bordercolor=t["panel"], lightcolor=t["panel"], darkcolor=t["panel"],
                     troughcolor=t["panel"], font=("Segoe UI", 10))
        st.configure("TNotebook", background=t["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", background=t["panel"], foreground=t["muted"], padding=(12, 6))
        st.map("TNotebook.Tab", background=[("selected", t["accent"])],
               foreground=[("selected", "#FFFFFF")])
        st.configure("TButton", background=t["panel"], foreground=t["fg"], padding=(10, 5))
        st.map("TButton", background=[("active", t["accent"])])
        st.configure("TEntry", insertcolor=t["fg"], padding=4)
        st.configure("TCombobox", arrowcolor=t["fg"], padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", t["panel"])],
               foreground=[("readonly", t["fg"])])
        st.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
        st.configure("Muted.TLabel", foreground=t["muted"])
        st.configure("Big.TLabel", font=("Segoe UI", 14, "bold"))
        st.configure("Treeview", background=t["panel"], fieldbackground=t["panel"],
                     foreground=t["fg"], rowheight=24)
        st.configure("Treeview.Heading", background=t["bg"], foreground=t["fg"])
        st.map("Treeview", background=[("selected", t["accent"])])
        self.option_add("*TCombobox*Listbox.background", t["panel"])
        self.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["accent"])

    def _picture(self, *names, max_width=None, size=None):
        """Load an optional picture from the assets folder, or None."""
        for name in names:
            path = ASSETS / name
            if path.exists():
                try:
                    from PIL import Image, ImageTk
                    with Image.open(path) as im:
                        im = im.convert("RGBA")
                        if size:
                            im.thumbnail(size)
                        elif max_width and im.width > max_width:
                            im = im.resize((max_width, round(im.height * max_width / im.width)))
                        return ImageTk.PhotoImage(im)
                except Exception:
                    return None
        return None

    def _header(self):
        self._pics = []                               # keep references so they stay visible
        icon = self._picture("logo.png", "logo.jpg", "logo.jpeg", size=(64, 64))
        if icon:
            try:
                self.iconphoto(True, icon)
            except tk.TclError:
                pass
            self._pics.append(icon)
        banner = self._picture("banner.png", "banner.jpg", "banner.jpeg", max_width=1000)
        if banner:
            self._pics.append(banner)
            tk.Label(self, image=banner, bg=THEME["bg"]).pack(fill="x", padx=10, pady=(8, 0))
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(10, 6))
        if icon:
            small = self._picture("logo.png", "logo.jpg", "logo.jpeg", size=(36, 36))
            if small:
                self._pics.append(small)
                tk.Label(top, image=small, bg=THEME["bg"]).pack(side="left", padx=(0, 8))
        ttk.Label(top, text="My Notebook", style="Big.TLabel").pack(side="left")
        self.summary = tk.StringVar()
        ttk.Label(top, textvariable=self.summary, style="Muted.TLabel").pack(side="right")

    def _text(self, parent, height=10, **pack):
        frame = ttk.Frame(parent)
        box = tk.Text(frame, height=height, wrap="word", bg=THEME["panel"], fg=THEME["fg"],
                      insertbackground=THEME["fg"], relief="flat", padx=8, pady=6,
                      font=("Segoe UI", 10), state="disabled")
        bar = ttk.Scrollbar(frame, command=box.yview)
        box.configure(yscrollcommand=bar.set)
        box.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        frame.pack(fill="both", expand=True, **pack)
        return box

    @staticmethod
    def _append(box, text):
        box.configure(state="normal")
        box.insert("end", text)
        box.see("end")
        box.configure(state="disabled")

    @staticmethod
    def _replace(box, text):
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    # ------------------------------------------------------------ background jobs
    def set_status(self, text):
        self.status.set(text)

    def refresh_status(self):
        today = nc.today()
        due = self.con.execute("select count(*) from weak where active=1 and due<=?",
                               (today.isoformat(),)).fetchone()[0]
        notes = self.con.execute("select count(*) from notes").fetchone()[0]
        test = self.con.execute("select name, date from tests where date>=? order by date limit 1",
                                (today.isoformat(),)).fetchone()
        text = f"{notes} notes   |   {due} review(s) due today"
        if test:
            days = (dt.date.fromisoformat(test["date"]) - today).days
            text += f"   |   {test['name']} in {days} day(s)"
        self.summary.set(text)

    def run_job(self, work, done, label="Working... (the first answer is slow while the model loads)"):
        """Run work(connection) on a background thread so the window stays responsive.
        done(result, printed_text, error) is called back on the main thread."""
        if self.busy:
            self.set_status("Still busy with the last request. Please wait a moment.")
            return False
        self.busy = True
        self.set_status(label)
        sys.stdout = sys.stderr = self.router      # make sure print() output is captured

        def target():
            buf = io.StringIO()
            self.router.set_sink(buf)
            result = error = con = None
            try:
                con = nc.connect()
                result = work(con)
            except BaseException as e:                # includes SystemExit from helpers
                error = e
            finally:
                self.router.clear_sink()
                if con is not None:
                    con.close()
            self.q.put(("done", done, result, buf.getvalue(), error))
        threading.Thread(target=target, daemon=True).start()
        return True

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    self._append(self.activity, item[1])
                else:
                    _, done, result, printed, error = item
                    self.busy = False
                    self.set_status("Ready")
                    try:
                        done(result, printed, error)
                    except Exception as e:
                        messagebox.showerror("Something went wrong", str(e))
                    self.refresh_status()
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self._poll)

    @staticmethod
    def describe(error):
        return str(error) or type(error).__name__

    def _start_watcher(self):
        try:
            import notebook_hub as hub
            self.observer = hub.start_background()
            self._append(self.activity, f"Watching your inbox: {hub.INBOX}\n"
                         "Files dropped there are filed automatically while this window is open.\n\n")
        except SystemExit as e:
            self._append(self.activity, f"The inbox watcher did not start:\n{e}\n\n")
            self.set_status("The inbox watcher is not running (see the Activity tab).")
        except Exception as e:
            self._append(self.activity, f"The inbox watcher did not start: {self.describe(e)}\n\n")

    def close(self):
        if self.observer is not None:
            try:
                self.observer.stop()
            except Exception:
                pass
        self.destroy_app()

    def destroy_app(self):
        try:
            self.after_cancel(self._poll_id)
        except Exception:
            pass
        sys.stdout, sys.stderr = self._orig_stdout, self._orig_stderr
        try:
            self.con.close()
        except Exception:
            pass
        self.destroy()

    # ------------------------------------------------------------ Ask
    def _build_ask(self, f):
        ttk.Label(f, text="Ask in your own words. Spelling mistakes are fine.  Examples: "
                  "summarise yesterdays physics  |  what did I get wrong in week 4  |  "
                  "what did we learn about momentum", style="Muted.TLabel", wraplength=940).pack(anchor="w")
        self.ask_out = self._text(f, pady=(6, 6))
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.ask_entry = ttk.Entry(row)
        self.ask_entry.pack(side="left", fill="x", expand=True)
        self.ask_entry.bind("<Return>", lambda e: self.ask())
        ttk.Button(row, text="Ask", command=self.ask).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Clear", command=lambda: self._replace(self.ask_out, "")).pack(side="left", padx=(6, 0))

    def ask(self):
        q = self.ask_entry.get().strip()
        if not q:
            return
        if not self.run_job(lambda con: nc.cmd_ask(con, Namespace(question=q.split(), interactive=False)),
                            lambda r, out, err: self._answer(out, err)):
            return
        self._append(self.ask_out, f"You: {q}\n")
        self.ask_entry.delete(0, "end")

    def _answer(self, out, err):
        text = tidy(out) or "(nothing to show)"
        if err:
            text += f"\nProblem: {self.describe(err)}"
        self._append(self.ask_out, f"\nNotebook:\n{text}\n\n")

    # ------------------------------------------------------------ Add notes
    def _build_add(self, f):
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Subject").pack(side="left")
        self.add_subject = ttk.Combobox(row, values=SUBJECTS + [GUESS], state="readonly", width=22)
        self.add_subject.set(SUBJECTS[0] if SUBJECTS else GUESS)
        self.add_subject.pack(side="left", padx=(6, 18))
        self.auto_date = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Work out the date from the page or photo", variable=self.auto_date).pack(side="left")
        ttk.Label(row, text="  otherwise use").pack(side="left")
        self.add_date = ttk.Entry(row, width=12)
        self.add_date.insert(0, nc.today().isoformat())
        self.add_date.pack(side="left", padx=6)
        buttons = ttk.Frame(f)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Choose photos, PDFs or text files...", command=self.choose_files).pack(side="left")
        ttk.Button(buttons, text="Open inbox folder", command=lambda: open_folder(cfg.INBOX)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Open notebook folder", command=lambda: open_folder(nc.ROOT)).pack(side="left")
        ttk.Label(f, text="You can also drop files into the inbox folder; they are filed automatically while "
                  "this window is open (use a subfolder such as physics to set the subject).",
                  style="Muted.TLabel", wraplength=940).pack(anchor="w")
        yt = ttk.Frame(f)
        yt.pack(fill="x", pady=(10, 4))
        ttk.Label(yt, text="YouTube link").pack(side="left")
        self.yt_entry = ttk.Entry(yt)
        self.yt_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(yt, text="Get transcript and notes", command=self.youtube).pack(side="left")
        self.add_out = self._text(f, pady=(6, 0))

    def _chosen_subject(self):
        s = self.add_subject.get()
        return None if s == GUESS else s

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose your notes",
            filetypes=[("Notes", "*.jpg *.jpeg *.png *.webp *.pdf *.txt *.md"), ("All files", "*.*")])
        if paths:
            self.file_batch(list(paths))

    def file_batch(self, paths):
        subject = self._chosen_subject()
        when = None
        if not self.auto_date.get():
            try:
                when = dt.datetime.combine(dt.date.fromisoformat(self.add_date.get().strip()), dt.time(12))
            except ValueError:
                messagebox.showinfo("Date", "Write the date like 2026-10-21 (year-month-day).")
                return

        def work(con):
            lines = []
            for path in paths:
                name = Path(path).name
                try:
                    res = nc.file_page(con, path, subject, when)
                except Exception as e:
                    lines.append(f"{name}: problem - {self.describe(e)}")
                    continue
                if res["status"] == "duplicate":
                    lines.append(f"{name}: already stored.")
                    continue
                m = res["meta"]
                lines.append(f"{name}: filed under {m['subject']}, {m['year']} term {m['term']} "
                             f"week {m['week']} day {m['day']} (date from {res['source']}).")
                if res["feedback"]:
                    lines.append("   Feedback: " + res["feedback"])
            return "\n".join(lines)

        def done(result, out, err):
            self._append(self.add_out, (result or "") + ("\n" if result else "") +
                         (f"Problem: {self.describe(err)}\n" if err else "") + "\n")
        self.run_job(work, done, "Reading your pages... (the first one is slowest)")

    def youtube(self):
        url = self.yt_entry.get().strip()
        if not url:
            return
        subject = self._chosen_subject()
        ok = self.run_job(lambda con: nc.process_youtube(con, url, subject),
                          lambda r, out, err: self._append(
                              self.add_out, tidy(out) + (f"\nProblem: {self.describe(err)}" if err else "") + "\n\n"),
                          "Getting the video... (captions are quick; audio is slow)")
        if ok:
            self.yt_entry.delete(0, "end")

    # ------------------------------------------------------------ Tutor
    def _build_tutor(self, f):
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Subject").pack(side="left")
        self.t_subject = ttk.Combobox(row, values=SUBJECTS, state="readonly", width=20)
        self.t_subject.set(SUBJECTS[0] if SUBJECTS else "")
        self.t_subject.pack(side="left", padx=6)
        ttk.Label(row, text="What do you want to understand?").pack(side="left", padx=(12, 0))
        self.t_topic = ttk.Entry(row)
        self.t_topic.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Start", command=self.tutor_start).pack(side="left")
        ttk.Label(f, text="A small model, so check anything important with your teacher or textbook.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.t_out = self._text(f, pady=(6, 6))
        row2 = ttk.Frame(f)
        row2.pack(fill="x")
        self.t_answer = ttk.Entry(row2)
        self.t_answer.pack(side="left", fill="x", expand=True)
        self.t_answer.bind("<Return>", lambda e: self.tutor_send())
        ttk.Button(row2, text="Send", command=self.tutor_send).pack(side="left", padx=6)
        ttk.Button(row2, text="Finish and save what I found hard", command=self.tutor_finish).pack(side="left")

    def tutor_start(self):
        subject, topic = self.t_subject.get(), self.t_topic.get().strip()
        if not subject or not topic:
            return

        def work(con):
            messages, found = nc.tutor_start(con, subject, topic)
            messages.append({"role": "assistant", "content": nc.chat_llm(messages)})
            return messages, found

        def done(result, out, err):
            if err:
                self._append(self.t_out, f"Problem: {self.describe(err)}\n")
                return
            messages, found = result
            self.tutor = {"subject": subject, "topic": topic, "messages": messages}
            self._replace(self.t_out, "")
            self._append(self.t_out, f"{subject}: {topic} " +
                         ("(using your notes)" if found else "(no matching notes found)") + "\n\n")
            self._append(self.t_out, "Tutor: " + messages[-1]["content"] + "\n\n")
        self.run_job(work, done, "The tutor is thinking...")

    def tutor_send(self):
        if not self.tutor:
            self._append(self.t_out, "Start a session first.\n")
            return
        ans = self.t_answer.get().strip()
        if not ans:
            return
        messages = self.tutor["messages"] + [{"role": "user", "content": ans}]

        def done(result, out, err):
            if err:
                self._append(self.t_out, f"Problem: {self.describe(err)}\n\n")
                return
            self.tutor["messages"] = messages + [{"role": "assistant", "content": result}]
            self._append(self.t_out, "Tutor: " + result + "\n\n")
        if self.run_job(lambda con: nc.chat_llm(messages), done, "The tutor is thinking..."):
            self._append(self.t_out, f"You: {ans}\n\n")
            self.t_answer.delete(0, "end")

    def tutor_finish(self):
        if not self.tutor:
            return
        session = self.tutor

        def done(result, out, err):
            self.tutor = None
            self._append(self.t_out, f"\nSaved. {result or 0} idea(s) added to your review list.\n" if not err
                         else f"Problem: {self.describe(err)}\n")
            self.load_review()
        self.run_job(lambda con: nc.tutor_wrapup(con, session["subject"], session["messages"]),
                     done, "Saving what you found hard...")

    # ------------------------------------------------------------ Practice test
    def _build_quiz(self, f):
        ttk.Label(f, text="The model writes questions from your own notes and saves them as a Word document, "
                  "with an answer key. Say what it should cover, in your own words.",
                  style="Muted.TLabel", wraplength=940).pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 4))
        ttk.Label(row, text="Cover").pack(side="left")
        self.q_request = ttk.Entry(row)
        self.q_request.insert(0, "physics this week")
        self.q_request.pack(side="left", fill="x", expand=True, padx=6)
        self.q_request.bind("<Return>", lambda e: self.quiz_make())
        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Questions").pack(side="left")
        self.q_count = ttk.Spinbox(row2, from_=1, to=30, width=4)
        self.q_count.set(10)
        self.q_count.pack(side="left", padx=(6, 18))
        ttk.Label(row2, text="Style").pack(side="left")
        self.q_style = ttk.Combobox(row2, values=list(nc.QUIZ_STYLES), state="readonly", width=16)
        self.q_style.set("mixed")
        self.q_style.pack(side="left", padx=(6, 18))
        self.q_separate = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Put the answers in a separate document", variable=self.q_separate).pack(side="left")
        ttk.Label(f, text="Examples:  physics week 4  |  projectile motion  |  what I got wrong in engineering  |  "
                  "specialist maths last week", style="Muted.TLabel").pack(anchor="w")
        buttons = ttk.Frame(f)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Make the test", command=self.quiz_make).pack(side="left")
        self.q_open = ttk.Button(buttons, text="Open the test", command=lambda: self.quiz_open(False))
        self.q_open.pack(side="left", padx=6)
        self.q_open.state(["disabled"])
        ttk.Button(buttons, text="Open the folder", command=lambda: open_folder(nc.ROOT / nc.QUIZ_DIR_NAME)).pack(side="left")
        self.q_out = self._text(f)
        self.q_last = None

    def quiz_make(self):
        request = self.q_request.get().strip()
        if not request:
            return
        try:
            count = max(1, min(30, int(self.q_count.get())))
        except ValueError:
            count = 10
        style, separate = self.q_style.get() or "mixed", self.q_separate.get()

        def work(con):
            return nc.make_quiz(con, request, count, style, separate)

        def done(result, out, err):
            if err:
                self._append(self.q_out, f"{self.describe(err)}\n\n")
                return
            path, questions, answers = result
            self.q_last = path
            self.q_open.state(["!disabled"])
            marks = sum(q["marks"] for q in questions)
            self._append(self.q_out, f"Made a test with {len(questions)} questions ({marks} marks).\n"
                         f"Saved as: {path}\n" + (f"Answers: {answers}\n" if answers else
                                                    "The answer key is on the last pages of the same document.\n") +
                         "It was written by a small AI model, so check the answers before you rely on them.\n\n")
        self.run_job(work, done, "Writing your test... (this can take a minute or two)")

    def quiz_open(self, folder=False):
        if self.q_last and Path(self.q_last).exists():
            try:
                if sys.platform.startswith("win"):
                    os.startfile(str(self.q_last))                # noqa: Windows only
                else:
                    open_folder(Path(self.q_last).parent)
            except OSError as e:
                messagebox.showinfo("Open", f"Could not open it: {e}")

    # ------------------------------------------------------------ Review
    def _build_review(self, f):
        self.rv_count = ttk.Label(f, style="Muted.TLabel")
        self.rv_count.pack(anchor="w")
        self.rv_topic = ttk.Label(f, style="Big.TLabel", wraplength=900)
        self.rv_topic.pack(anchor="w", pady=(12, 4))
        self.rv_issue = ttk.Label(f, style="Muted.TLabel", wraplength=900)
        self.rv_issue.pack(anchor="w")
        self.rv_answer = ttk.Label(f, wraplength=900, foreground=THEME["accent"])
        self.rv_answer.pack(anchor="w", pady=14)
        row = ttk.Frame(f)
        row.pack(anchor="w")
        self.rv_show = ttk.Button(row, text="Show the answer", command=self.review_show)
        self.rv_show.pack(side="left")
        self.rv_buttons = [
            ttk.Button(row, text="I remembered it", command=lambda: self.review_mark(True)),
            ttk.Button(row, text="I forgot it", command=lambda: self.review_mark(False)),
            ttk.Button(row, text="Delete (the checker was wrong)", command=self.review_delete)]
        for b in self.rv_buttons:
            b.pack(side="left", padx=(8, 0))

    def load_review(self):
        due = self.con.execute("select * from weak where active=1 and due<=? order by subject, due",
                               (nc.today().isoformat(),)).fetchall()
        self.rv_current = due[0] if due else None
        self.rv_answer.configure(text="")
        for b in self.rv_buttons:
            b.state(["disabled"])
        if not due:
            self.rv_count.configure(text="")
            self.rv_topic.configure(text="Nothing due for review today.")
            self.rv_issue.configure(text="")
            self.rv_show.state(["disabled"])
            return
        w = self.rv_current
        self.rv_count.configure(text=f"{len(due)} due today")
        self.rv_topic.configure(text=f"[{w['subject']}] {w['topic']}")
        self.rv_issue.configure(text=("Watch out: " + w["issue"]) if w["issue"] else "")
        self.rv_show.state(["!disabled"])

    def review_show(self):
        if self.rv_current is None:
            return
        self.rv_answer.configure(text=self.rv_current["fix"])
        for b in self.rv_buttons:
            b.state(["!disabled"])

    def review_mark(self, remembered):
        if self.rv_current is not None:
            nc.reschedule(self.con, self.rv_current, remembered)
        self.load_review()
        self.refresh_status()

    def review_delete(self):
        if self.rv_current is not None:
            self.con.execute("update weak set active=0 where id=?", (self.rv_current["id"],))
            self.con.commit()
        self.load_review()
        self.refresh_status()

    # ------------------------------------------------------------ Tests and weak points
    def _build_tests(self, f):
        left = ttk.Frame(f)
        left.pack(side="left", fill="y", padx=(0, 16))
        ttk.Label(left, text="Add a test", style="Big.TLabel").pack(anchor="w")
        self.te_subject = ttk.Combobox(left, values=SUBJECTS, state="readonly", width=24)
        self.te_subject.set(SUBJECTS[0] if SUBJECTS else "")
        self.te_subject.pack(anchor="w", pady=2)
        self.te_name = ttk.Entry(left, width=26)
        self.te_name.pack(anchor="w", pady=2)
        ttk.Label(left, text="name, e.g. IA3 exam", style="Muted.TLabel").pack(anchor="w")
        self.te_date = ttk.Entry(left, width=26)
        self.te_date.insert(0, (nc.today() + dt.timedelta(days=14)).isoformat())
        self.te_date.pack(anchor="w", pady=2)
        ttk.Label(left, text="date: year-month-day", style="Muted.TLabel").pack(anchor="w")
        ttk.Button(left, text="Save test", command=self.save_test).pack(anchor="w", pady=6)
        ttk.Label(left, text="Add something you can't remember", style="Big.TLabel").pack(anchor="w", pady=(16, 0))
        self.wk_subject = ttk.Combobox(left, values=SUBJECTS, state="readonly", width=24)
        self.wk_subject.set(SUBJECTS[0] if SUBJECTS else "")
        self.wk_subject.pack(anchor="w", pady=2)
        self.wk_topic = ttk.Entry(left, width=26)
        self.wk_topic.pack(anchor="w", pady=2)
        ttk.Label(left, text="topic", style="Muted.TLabel").pack(anchor="w")
        self.wk_fix = ttk.Entry(left, width=26)
        self.wk_fix.pack(anchor="w", pady=2)
        ttk.Label(left, text="the right answer", style="Muted.TLabel").pack(anchor="w")
        ttk.Button(left, text="Add", command=self.save_weak).pack(anchor="w", pady=6)
        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Everything on your review list", style="Big.TLabel").pack(anchor="w")
        cols = ("subject", "topic", "answer", "due", "from")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=14)
        for c, w in zip(cols, (110, 150, 300, 90, 70)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=6)
        self.refresh_tree()

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.con.execute("select subject, topic, fix, due, source from weak "
                                  "where active=1 order by due"):
            self.tree.insert("", "end", values=tuple(r))

    def save_test(self):
        name = self.te_name.get().strip()
        try:
            day = dt.date.fromisoformat(self.te_date.get().strip())
        except ValueError:
            messagebox.showinfo("Date", "Write the date like 2026-11-12 (year-month-day).")
            return
        if not name:
            return
        self.con.execute("insert into tests(subject, name, date) values (?,?,?)",
                         (nc.slug(self.te_subject.get()), name, day.isoformat()))
        self.con.commit()
        self.te_name.delete(0, "end")
        self.set_status("Test saved. Reviews will be pulled in before it.")
        self.refresh_status()

    def save_weak(self):
        topic, fix = self.wk_topic.get().strip(), self.wk_fix.get().strip()
        if not topic or not fix:
            return
        added = nc.add_weak(self.con, self.wk_subject.get(), topic, "", fix)
        self.set_status("Added." if added else "That topic is already on the list.")
        self.wk_topic.delete(0, "end")
        self.wk_fix.delete(0, "end")
        self.refresh_tree()

    # ------------------------------------------------------------ Storage
    def _build_storage(self, f):
        self.st_out = self._text(f, height=14, pady=(0, 8))
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Photos older than").pack(side="left")
        self.st_days = ttk.Entry(row, width=5)
        self.st_days.insert(0, "60")
        self.st_days.pack(side="left", padx=4)
        ttk.Label(row, text="days:").pack(side="left")
        ttk.Button(row, text="Refresh numbers", command=self.storage_refresh).pack(side="left", padx=(12, 0))
        ttk.Button(row, text="Preview shrinking", command=lambda: self.storage_tidy(False, False)).pack(side="left", padx=6)
        ttk.Button(row, text="Shrink them", command=lambda: self.storage_tidy(True, False)).pack(side="left")
        ttk.Button(row, text="Remove the photos (keep the text)", command=lambda: self.storage_tidy(True, True)).pack(side="left", padx=6)
        ttk.Label(f, text="New photos are already shrunk when they are filed. Your text, summaries and review items "
                  "are never removed.", style="Muted.TLabel", wraplength=940).pack(anchor="w", pady=(6, 0))
        self.storage_refresh()

    def storage_refresh(self):
        self._replace(self.st_out, nc.storage_report())

    def storage_tidy(self, apply, delete):
        try:
            days = int(self.st_days.get())
        except ValueError:
            messagebox.showinfo("Days", "Write a whole number of days, such as 60.")
            return
        if apply and delete and not messagebox.askyesno(
                "Remove photos", f"Remove every photo filed more than {days} days ago?\n\n"
                "The text, summaries and review items stay. The pictures cannot be brought back."):
            return
        if apply and not delete and not messagebox.askyesno(
                "Shrink photos", f"Shrink photos filed more than {days} days ago?"):
            return

        def done(result, out, err):
            self._append(self.st_out, "\n" + (result or self.describe(err)) + "\n")
            if apply:
                self.storage_refresh()
        self.run_job(lambda con: nc.tidy_photos(con, days, delete=delete, apply=apply), done, "Working...")

    # ------------------------------------------------------------ Activity and Help
    def _build_activity(self, f):
        ttk.Label(f, text="What the inbox watcher is doing", style="Muted.TLabel").pack(anchor="w")
        self.activity = self._text(f, pady=(4, 0))

    def _build_help(self, f):
        box = self._text(f)
        self._replace(box, HELP_TEXT)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()

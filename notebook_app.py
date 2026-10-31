"""notebook_app.py - a friendly window for your notebook. Opens in your web browser.

Start it with:   python -m streamlit run notebook_app.py
or just double-click start_notebook.bat

Needs:  pip install streamlit
and notebook_config.py + notebook_checker.py in the same folder.
"""
import contextlib
import datetime as dt
import io
import tempfile
from argparse import Namespace
from pathlib import Path

import streamlit as st

import notebook_checker as nc
from notebook_config import SUBJECTS

# Optional looks: drop assets/logo.png and/or assets/banner.png next to this
# file and they appear automatically. Colours are set in .streamlit/config.toml.
ASSETS = Path(__file__).resolve().parent / "assets"


def find_asset(*names):
    for name in names:
        if (ASSETS / name).exists():
            return ASSETS / name
    return None


LOGO = find_asset("logo.png", "logo.jpg", "logo.jpeg")
BANNER = find_asset("banner.png", "banner.jpg", "banner.jpeg")
try:
    from PIL import Image
    ICON = Image.open(LOGO) if LOGO else None
except Exception:
    ICON = None

st.set_page_config(page_title="My Notebook", page_icon=ICON, layout="wide")
if BANNER:
    st.image(str(BANNER))
con = nc.connect()                      # a fresh connection on every page refresh
GUESS = "(let the model guess)"


def quiet(fn, *args, **kwargs):
    """Run a function, hiding what it prints. Returns (result, printed text, error)."""
    buf = io.StringIO()
    result, error = None, None
    with contextlib.redirect_stdout(buf):
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            error = str(e) or type(e).__name__
    return result, buf.getvalue().strip(), error


def tidy(text):
    """Drop the ===== divider lines so the text displays cleanly."""
    return "\n".join(l for l in text.splitlines() if l.strip() and set(l.strip()) != {"="})


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    if LOGO:
        st.image(str(LOGO))
    st.header("My Notebook")
    today = nc.today()
    due = con.execute("select count(*) from weak where active=1 and due<=?",
                      (today.isoformat(),)).fetchone()[0]
    st.metric("Reviews due today", due)
    tests = con.execute("select * from tests where date>=? order by date limit 5",
                        (today.isoformat(),)).fetchall()
    if tests:
        st.subheader("Coming up")
        for t in tests:
            days = (dt.date.fromisoformat(t["date"]) - today).days
            st.write(f"**{t['name']}** ({t['subject']}): {days} day(s)")
    total = con.execute("select count(*) from notes").fetchone()[0]
    st.caption(f"{total} notes stored in {nc.ROOT}")

tab_ask, tab_add, tab_tutor, tab_review, tab_tests = st.tabs(
    ["Ask", "Add notes", "Tutor", "Review", "Tests and weak points"])

# ---------------------------------------------------------------- ask
with tab_ask:
    st.write("Ask in your own words. Spelling mistakes are fine. Examples: "
             "*summarise yesterdays physics*, *what did I get wrong in week 4*, "
             "*show engineering today*, *what did we learn about momentum*")
    log = st.session_state.setdefault("ask_log", [])
    for role, text in log:
        with st.chat_message(role):
            st.markdown(text)
    with st.form("ask_form", clear_on_submit=True):
        question = st.text_input("Your question", key="ask_text")
        asked = st.form_submit_button("Ask")
    if asked and question.strip():
        with st.spinner("Thinking..."):
            _, printed, error = quiet(nc.cmd_ask, con,
                                      Namespace(question=question.split(), interactive=False))
        answer = tidy(printed) or "(nothing to show)"
        if error:
            answer += f"\n\n**Problem:** {error}"
        st.session_state.ask_log = log + [("user", question), ("assistant", answer)]
        st.rerun()
    if log and st.button("Clear this conversation"):
        st.session_state.ask_log = []
        st.rerun()

# ---------------------------------------------------------------- add notes
with tab_add:
    st.subheader("Photos, PDFs and text files")
    c1, c2 = st.columns(2)
    subject = c1.selectbox("Subject", SUBJECTS + [GUESS], key="add_subject")
    auto = c2.checkbox("Work out the date from the page or photo", value=True, key="add_auto")
    chosen = c2.date_input("Otherwise use this date", value=dt.date.today(), key="add_date")
    files = st.file_uploader("Drop pages here", accept_multiple_files=True,
                             type=["jpg", "jpeg", "png", "webp", "pdf", "txt", "md"],
                             key="add_files")
    if st.button("File these pages", key="add_go") and files:
        for up in files:
            when = None if auto else dt.datetime.combine(chosen, dt.time(12))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / up.name
                path.write_bytes(up.getbuffer())
                with st.spinner(f"Reading {up.name} (the first one is slowest)..."):
                    res, _, error = quiet(nc.file_page, con, path,
                                          None if subject == GUESS else subject, when)
            if error:
                st.error(f"{up.name}: {error}")
            elif res["status"] == "duplicate":
                st.info(f"{up.name} is already stored.")
            else:
                m = res["meta"]
                st.success(f"{up.name} filed under {m['subject']}, {m['year']} term {m['term']} "
                           f"week {m['week']} day {m['day']} (date from {res['source']}).")
                if res["feedback"]:
                    with st.expander(f"Tutor feedback for {up.name}", expanded=True):
                        st.write(res["feedback"])
                if res["summary"]:
                    with st.expander("Summary"):
                        st.write(res["summary"])

    st.divider()
    st.subheader("YouTube link")
    with st.form("yt_form"):
        url = st.text_input("Paste a YouTube link", key="yt_url")
        yt_subject = st.selectbox("Subject", SUBJECTS, key="yt_subject")
        yt_go = st.form_submit_button("Get the transcript and notes")
    if yt_go and url.strip():
        with st.spinner("Getting the video (captions are quick; audio is slow)..."):
            ok, printed, error = quiet(nc.process_youtube, con, url.strip(), yt_subject)
        if error or not ok:
            st.error(error or "That didn't work. " + tidy(printed))
        else:
            st.success("Done.")
            st.markdown(tidy(printed))

# ---------------------------------------------------------------- tutor
with tab_tutor:
    st.write("A step-by-step tutor that uses your own notes. It is a small model, so "
             "check anything important with your teacher or textbook.")
    session = st.session_state.get("tutor")
    if not session:
        with st.form("tutor_start"):
            t_subject = st.selectbox("Subject", SUBJECTS, key="t_subject")
            t_topic = st.text_input("What do you want to understand?", key="t_topic")
            t_go = st.form_submit_button("Start")
        if t_go and t_topic.strip():
            messages, found = nc.tutor_start(con, t_subject, t_topic.strip())
            with st.spinner("The tutor is thinking..."):
                try:
                    reply = nc.chat_llm(messages)
                    messages.append({"role": "assistant", "content": reply})
                    st.session_state.tutor = {"subject": t_subject, "topic": t_topic,
                                              "messages": messages, "found": found}
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.caption(f"{session['subject']}: {session['topic']} "
                   + ("(using your notes)" if session["found"] else "(no matching notes found)"))
        for m in session["messages"][1:]:
            if m["role"] == "user" and m is session["messages"][1]:
                continue                      # hide the automatic first message
            with st.chat_message("assistant" if m["role"] == "assistant" else "user"):
                st.markdown(m["content"])
        with st.form("tutor_reply", clear_on_submit=True):
            answer = st.text_input("Your answer", key="t_answer")
            sent = st.form_submit_button("Send")
        if sent and answer.strip():
            session["messages"].append({"role": "user", "content": answer.strip()})
            with st.spinner("The tutor is thinking..."):
                try:
                    reply = nc.chat_llm(session["messages"])
                    session["messages"].append({"role": "assistant", "content": reply})
                except Exception as e:
                    session["messages"].pop()
                    st.error(str(e))
            st.rerun()
        if st.button("Finish and save what I found hard", key="t_finish"):
            with st.spinner("Saving..."):
                added = nc.tutor_wrapup(con, session["subject"], session["messages"])
            st.session_state.tutor = None
            st.session_state.tutor_done = f"{added} idea(s) added to your review list."
            st.rerun()
    if st.session_state.get("tutor_done"):
        st.success(st.session_state.pop("tutor_done"))

# ---------------------------------------------------------------- review
with tab_review:
    due_rows = con.execute("select * from weak where active=1 and due<=? order by subject, due",
                           (nc.today().isoformat(),)).fetchall()
    if not due_rows:
        st.success("Nothing due for review today.")
    else:
        w = due_rows[0]
        st.caption(f"{len(due_rows)} due today")
        st.subheader(f"[{w['subject']}] {w['topic']}")
        if w["issue"]:
            st.write("Watch out:", w["issue"])
        if st.button("Show the answer", key="rv_show"):
            st.session_state.revealed = w["id"]
        if st.session_state.get("revealed") == w["id"]:
            st.info(w["fix"])
            b1, b2, b3 = st.columns(3)
            if b1.button("I remembered it", key="rv_yes"):
                nc.reschedule(con, w, True)
                st.session_state.revealed = None
                st.rerun()
            if b2.button("I forgot it", key="rv_no"):
                nc.reschedule(con, w, False)
                st.session_state.revealed = None
                st.rerun()
            if b3.button("Delete (the checker was wrong)", key="rv_del"):
                con.execute("update weak set active=0 where id=?", (w["id"],))
                con.commit()
                st.session_state.revealed = None
                st.rerun()

# ---------------------------------------------------------------- tests and weak points
with tab_tests:
    left, right = st.columns(2)
    with left:
        st.subheader("Add a test")
        with st.form("test_form", clear_on_submit=True):
            ts = st.selectbox("Subject", SUBJECTS, key="test_subject")
            tn = st.text_input("Name", key="test_name")
            td = st.date_input("Date", value=dt.date.today() + dt.timedelta(days=14), key="test_date")
            tgo = st.form_submit_button("Save test")
        if tgo and tn.strip():
            con.execute("insert into tests(subject, name, date) values (?,?,?)",
                        (nc.slug(ts), tn.strip(), td.isoformat()))
            con.commit()
            st.success("Saved. Reviews will be pulled in before it.")
            st.rerun()
    with right:
        st.subheader("Add something you can't remember")
        with st.form("weak_form", clear_on_submit=True):
            ws = st.selectbox("Subject", SUBJECTS, key="weak_subject")
            wt = st.text_input("Topic", key="weak_topic")
            wf = st.text_area("The right answer", key="weak_fix")
            wgo = st.form_submit_button("Add")
        if wgo and wt.strip() and wf.strip():
            st.success("Added." if nc.add_weak(con, ws, wt.strip(), "", wf.strip())
                       else "That topic is already on the list.")
    st.subheader("Everything on your review list")
    rows = con.execute("select subject, topic, fix, due, source from weak "
                       "where active=1 order by due").fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows])
    else:
        st.write("Nothing yet.")

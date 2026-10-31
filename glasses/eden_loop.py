# eden_loop.py - tutor mode + lecture mode, ~5 s checks, video kept
# Keys (stand-ins for the glasses button):  m = switch mode,
# r = start/stop recording (lecture mode),  q = quit
import time, queue, threading, textwrap, pathlib
import cv2
import numpy as np
import ollama
import sounddevice as sd
from scipy.io import wavfile

MODEL = "qwen2.5vl:3b"          # one model for reading AND coaching
SOURCE = 0                      # webcam now; later "http://ESP32_IP:81/stream"
CHECK_EVERY = 5.0               # seconds between looks at the page
CHANGE_THRESH = 6.0             # mean pixel change that counts as new writing
RATE = 16000                    # audio sample rate (Whisper likes 16 kHz)
OUT = pathlib.Path("recordings")
OUT.mkdir(exist_ok=True)

PROMPTS = {
    "tutor": ("You are a silent tutor shown on tiny smart glasses. Read the "
              "student's handwritten work. Reply in at most 4 lines of 32 "
              "characters, no markdown. If a line has a mistake, start with "
              "FIX: and say which line and why. Otherwise start with NEXT: "
              "and give only the next step."),
    "lecture": ("You turn lecture material into short revision notes: key "
                "formulas, laws and the logical steps. Use plain bullet points."),
}

jobs = queue.Queue(maxsize=1)   # holds only the newest page photo
mode = "tutor"


# ---------- image and model helpers ----------
def preprocess(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

def changed(prev, cur):
    if prev is None:
        return True
    small = lambda g: cv2.resize(g, (160, 120)).astype("float32")
    return float(np.mean(np.abs(small(prev) - small(cur)))) > CHANGE_THRESH

def ask(image_path, which, extra=""):
    msg = {"role": "user", "content": "Here is the page. " + extra}
    if image_path:
        msg["images"] = [image_path]
    r = ollama.chat(model=MODEL, keep_alive="30m", messages=[
        {"role": "system", "content": PROMPTS[which]}, msg])
    return r["message"]["content"]

def to_glasses(text, width=32, lines=4):
    return "\n".join(textwrap.wrap(text.replace("\n", " "), width)[:lines])

def worker():                   # runs in the background, one page at a time
    while True:
        path = jobs.get()
        print("\n" + to_glasses(ask(path, "tutor")) + "\n" + "-" * 20)
        # later: send to_glasses(...) to the glasses display here


# ---------- audio recording (lecture mode) ----------
chunks, stream = [], None

def _audio_cb(indata, frames, t, status):
    chunks.append(indata.copy())

def audio_start():
    global stream
    chunks.clear()
    stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                            callback=_audio_cb)
    stream.start()

def audio_stop(path):
    stream.stop()
    stream.close()
    wavfile.write(str(path), RATE, np.concatenate(chunks))


# ---------- lecture summary (runs after you stop recording) ----------
def summ(text):
    r = ollama.chat(model=MODEL, options={"num_ctx": 8192}, messages=[
        {"role": "system", "content": PROMPTS["lecture"]},
        {"role": "user", "content": text}])
    return r["message"]["content"]

def chunks_of(text, words=1200):
    w = text.split()
    return [" ".join(w[i:i + words]) for i in range(0, len(w), words)]

def summarise_lecture(video_path, wav_path):
    from faster_whisper import WhisperModel
    stt = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = stt.transcribe(str(wav_path))
    transcript = " ".join(s.text for s in segments)

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    boards, n = [], 0
    while True:                 # look at the board/slide every 30 s of video
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * 30 * fps))
        ok, frame = cap.read()
        if not ok:
            break
        p = OUT / f"board_{n:04d}.jpg"
        cv2.imwrite(str(p), preprocess(frame))
        boards.append(ask(str(p), "lecture", "List what is written or drawn."))
        n += 1
    cap.release()

    parts = [summ(c) for c in chunks_of(transcript)]      # long talks: chunk
    final = summ("Spoken notes:\n" + "\n".join(parts) +
                 "\n\nBoard notes:\n" + "\n".join(boards))
    (OUT / "lecture_notes.txt").write_text(final, encoding="utf-8")
    print("Saved", OUT / "lecture_notes.txt")


# ---------- main loop ----------
def main():
    global mode
    cap = cv2.VideoCapture(SOURCE)
    threading.Thread(target=worker, daemon=True).start()
    writer, prev, last, stamp = None, None, 0.0, ""
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        if writer is not None:
            writer.write(frame)                 # the video is kept
        now = time.time()
        if mode == "tutor" and now - last >= CHECK_EVERY:
            last = now
            g = preprocess(frame)
            if changed(prev, g):
                prev = g
                path = str(OUT / f"page_{int(now)}.jpg")
                cv2.imwrite(path, g)
                try:
                    jobs.get_nowait()           # drop the stale job
                except queue.Empty:
                    pass
                jobs.put(path)
        cv2.imshow("eden", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("m") and writer is None:
            mode = "lecture" if mode == "tutor" else "tutor"
            print("MODE:", mode)
        if key == ord("r") and mode == "lecture":
            if writer is None:                  # start recording
                stamp = time.strftime("%Y%m%d_%H%M%S")
                h, w = frame.shape[:2]
                fps = cap.get(cv2.CAP_PROP_FPS) or 15
                writer = cv2.VideoWriter(str(OUT / f"lecture_{stamp}.mp4"),
                                         cv2.VideoWriter_fourcc(*"mp4v"),
                                         fps, (w, h))
                audio_start()
                print("RECORDING")
            else:                               # stop and summarise
                writer.release()
                writer = None
                audio_stop(OUT / f"lecture_{stamp}.wav")
                print("Stopped. Summarising (this takes a while)...")
                summarise_lecture(OUT / f"lecture_{stamp}.mp4",
                                  OUT / f"lecture_{stamp}.wav")
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

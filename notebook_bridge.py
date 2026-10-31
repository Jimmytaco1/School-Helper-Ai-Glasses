"""notebook_bridge.py - lets the glasses script file a page into the notebook.

The glasses code and the notebook hub only talk through the inbox folder, so
either can run without the other.

In eden_loop.py add:
    from notebook_bridge import send_to_notebook
and inside the key handling:
    if key == ord("n"):                      # 'n' = file this page in my notebook
        send_to_notebook(frame, "physics")   # subject is optional
"""
import time
from pathlib import Path

import cv2

from notebook_config import INBOX               # the same folder the hub watches


def send_to_notebook(frame, subject=None):
    folder = INBOX / subject if subject else INBOX
    folder.mkdir(parents=True, exist_ok=True)
    name = f"glasses_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return None
    tmp = folder / (name + ".part")              # the hub ignores .part files
    tmp.write_bytes(buf.tobytes())
    final = folder / name
    tmp.rename(final)                            # appears complete, never half-written
    print("Filed to notebook inbox:", final.name)
    return final

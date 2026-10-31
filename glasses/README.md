# Smart-glasses concept (design stage)

> **Status: design and software prototype only. No hardware has been built or tested.**

The idea: glasses with a small floating text display, a camera and a microphone. A local vision model reads what you are writing and shows a short correction or hint in your field of view. Everything stays on your own computer.

## What is here

- `Lecture_Coach_Glasses_Guide.docx`: a build guide covering the optics (virtual image from a lens, with worked numbers), hardware options, a feasibility assessment (what is realistic and what is hard), the software pipeline, a camera-resolution calculation for reading handwriting, safety, and a phased plan.
- `eden_loop.py`: a laptop-only prototype of the loop (webcam in, short tutor reply out, with a tutor mode and a lecture-recording mode). It has been syntax-checked and its parts tested separately, but it has **not** been run end to end on the target hardware.

## What is not done

- No display optics have been built or measured.
- No head-mounted camera has been tested for reading handwriting.
- Replies are printed in a terminal, not shown on glasses.
- The wake-word voice trigger is described in the guide but not implemented.

## Safety

If this is ever built: use LED or OLED displays only (never lasers near the eye), keep batteries off the head, check heat, and get permission before recording a lesson. The guide has more detail.

## Running the prototype

```bash
pip install -r requirements.txt
python eden_loop.py        # keys: m = switch mode, r = record (lecture mode), q = quit
```

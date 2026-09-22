# CLAUDE.md — VQA-VN-Traffic-2026

Vietnamese traffic video dataset for VQA. Current phase: **YouTube compilations → clean short
clips**. VQA annotation is out of scope.

Design docs: [`architecture.md`](./architecture.md) · [`TODO.md`](./TODO.md)

---

## CONSTITUTION — Preprocessing Pipeline

**This section is the source of truth for decision-making. If a question isn't explicitly
covered, default to "ship it" and move on.**

### Principles

1. **This is a preprocessing pipeline, NOT an annotation platform.**
   - Goal: produce candidate clips for later review
   - Perfection is not required at this stage
   - Rough cuts are acceptable — human review exists to clean up

2. **Ship it, don't polish it.**
   - If it works 80% of the time on 80% of videos, it's good enough
   - Edge cases are handled by the review tool, not by making the detector smarter
   - Default parameters are fine — don't tune thresholds unless proven necessary

3. **Do not overthink things that don't matter for this phase:**
   - Exact number of videos to crawl → just grab enough, ~50 is fine
   - Boundary P/R/F1 benchmarks → not needed until evaluation phase
   - Optimal parameters → use PySceneDetect defaults
   - Review UI polish → ugly but functional is the goal
   - Multi-machine coordination → not needed (single user, single machine)

4. **Keep the scope tight.**
   - If a feature isn't required to get clips out, defer it
   - If it adds >1 day of work, question whether you really need it
   - The review tool is where quality happens, not the pipeline

5. **Progress over perfection.**
   - A working pipeline that produces clips is better than a perfect pipeline that never ships
   - You can always iterate after seeing real outputs

### Decision Log

| Decision | Choice |
|---|---|
| Tech stack | **Streamlit + Python** (no FastAPI, no JS framework, no separate web server) |
| Review mode | Triage — Approve / Reject / Flag. **No Skip**: a decision is required to advance. |
| Segmentation | **PySceneDetect only**, defaults. Counter/OCR fusion removed 2026-08-29: the real counter is transient, and a mis-detected one made the two signals correlated. All boundaries are `MEDIUM`. |
| Overlay removal | Blur + desaturate + darken (toggleable), over **calibrated regions + always-on fixed bands** |
| Headless mode | Yes, clips marked `UNREVIEWED` |
| Config | Streamlit sidebar widgets (no separate config file editing) |
| Output | Local disk only (no Drive upload) |
| Review order | Clip duration: `>15s` = TOP priority, drains before LOW |
| Reviewer output | `trimmed/` is the finished product. Approve writes it (one file per shot, up to 3); Reject deletes only from there. |
| Cut rule | Impact → impact + 5s (`GUIDE.md` §2.4). Dataset policy, not architecture. |
| Trim UI | **Mark cut** (playhead → `impact ± pad`) + visual timeline. Max 3 shots. Overlaps auto-shrink ±5s→±1s, then refuse; a conflict blocks Approve. |
| Queue removal | Removable = the states the runner is done with: `QUEUED` / `DOWNLOAD_FAILED` / `FAILED` / `STOPPED`. Status checked inside the `DELETE`, since the runner is another process. Go through `review.purge_video()` — a `STOPPED` video can own clips *and* approved files in `trimmed/`. The download itself is never deleted. |
| Stop | Marks in-flight rows `STOPPED`, after `proc.wait()` — a dying runner overwrites the status otherwise. `STOPPED` is both resumable and removable. Rows stranded by a crash get a separate manual button; never reconcile automatically, since `busy` is per-session and another tab may be running. |
| Run monitoring | `st.fragment(run_every=2)`. Never poll at app scope: Streamlit runs every tab's body, so it restarts the reviewer's video player. |
| Crawl target | ~50 videos, ~1200–1500 clips expected |

---

## Working agreements

- **One app, one process.** `streamlit run app.py` is the entire interface.
- **Don't add a layer.** No API tier, no task broker, no ORM, no build step.
- **Don't propose benchmarks, tuning campaigns, or coordination mechanisms** for this phase —
  they are explicitly out of scope per Principle 3.
- **`python -m pytest tests -q` before calling anything done.** 26 tests, ~7s,
  no ffmpeg and no network. They drive the real app through `AppTest`, which is
  the only way the widget-state and stranded-state bugs above were ever found.
- **Run the `ship-feature` skill for any feature or bug fix.** Read the code →
  ask short grounded questions → `feature-conflict-audit` → `tdd`. It exists
  because features here break at the seam with what was already there.
- **Audit new features against old ones before building.** Run the
  `feature-conflict-audit` skill on any proposed feature that touches shared state,
  reviewer outputs, the DB schema, or a recorded decision. Every serious bug here has
  been a new feature colliding with an existing default, owner or invariant — and none
  were found by reading code, only by running it.
- When a design question comes up that the constitution doesn't answer: pick the option that
  ships soonest, note the choice in a comment, and keep going.

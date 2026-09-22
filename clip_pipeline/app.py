"""VQA traffic clip pipeline — the entire interface.

    streamlit run app.py

Ugly but functional, on purpose (CLAUDE.md Principle 2).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from vqa import config, db, media, review, trim, urls, winasyncio

# Must run before the first websocket drops. Windows only; no-op elsewhere.
winasyncio.patch()

st.set_page_config(page_title="VQA Clip Pipeline", page_icon="🎬", layout="wide")

db.init()
APP_DIR = Path(__file__).resolve().parent
LOG_DIR = Path("logs")


# ── run process helpers ───────────────────────────────────────────────────

def running_proc() -> subprocess.Popen | None:
    p = st.session_state.get("proc")
    if p is not None and p.poll() is None:
        return p
    return None


def start_run() -> None:
    python_exe = APP_DIR / "venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)
    proc = subprocess.Popen(
        [str(python_exe), str(APP_DIR / "run_pipeline.py")],
        cwd=APP_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    st.session_state["proc"] = proc


def log_tail(n: int = 40) -> str:
    latest = LOG_DIR / "latest.log"
    if not latest.exists():
        return "(no runs yet)"
    try:
        path = Path(latest.read_text(encoding="utf-8").strip())
        lines = path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-n:]) or "(empty)"
    except OSError:
        return "(log unavailable)"


def report_enqueue(added: list, dupes: list, bad: list) -> None:
    """One place for both paste paths to say what happened to each line."""
    st.success(f"Added {len(added)}, duplicate {len(dupes)}, invalid {len(bad)}")
    if dupes:
        # Naming them is the point: a duplicate sitting at DOWNLOAD_FAILED needs
        # removing and re-adding, one at READY_FOR_REVIEW needs nothing at all.
        st.info("Already in the queue — not downloaded again:\n"
                + "\n".join(f"- `{v}` — {s}" for v, s in dupes))
    for line_no, raw in bad[:10]:
        st.caption(f"line {line_no}: {raw}")


# ── sidebar: all configuration ────────────────────────────────────────────

cfg = config.load()
busy = running_proc() is not None

with st.sidebar:
    st.header("⚙️ Settings")
    if busy:
        st.warning("Run in progress — settings are locked.")
        st.caption(
            "Editing config mid-run would put clips with different parameters "
            "under one config hash."
        )

    st.subheader("Toggles")
    cfg["review_enabled"] = st.checkbox(
        "Review mode", value=cfg["review_enabled"], disabled=busy,
        key="review_enabled",
        help="Off = headless. Clips are written UNREVIEWED, never APPROVED.")
    cfg["blur_enabled"] = st.checkbox(
        "Blur overlays", value=cfg["blur_enabled"], disabled=busy)
    cfg["color_kill_enabled"] = st.checkbox(
        "Desaturate + darken", value=cfg["color_kill_enabled"],
        disabled=busy or not cfg["blur_enabled"],
        help="Kills the brand colour signature. Ignored if blur is off.")

    st.subheader("Overlay removal")
    cfg["blur_sigma"] = st.slider("Blur sigma", 2, 60, int(cfg["blur_sigma"]),
                                  disabled=busy or not cfg["blur_enabled"])
    cfg["desaturate"] = st.slider("Desaturate", 0.0, 1.0, float(cfg["desaturate"]),
                                  0.05, disabled=busy or not cfg["color_kill_enabled"])
    cfg["darken"] = st.slider("Darken", 0.0, 0.5, float(cfg["darken"]), 0.05,
                              disabled=busy or not cfg["color_kill_enabled"])

    st.subheader("Fixed blur bands")
    st.caption(
        "Applied to every video, on top of whatever calibration finds. "
        "Coordinates are fractions of the frame, so one setting works at any "
        "resolution. Defaults cover the burned-in clock, the `#03` counter and "
        "the source-camera name."
    )
    bands = []
    for i, band in enumerate(cfg.get("fixed_blur", [])):
        name = band.get("name", f"band{i}")
        with st.expander(name, expanded=False):
            band = dict(band)
            band["enabled"] = st.checkbox(
                "Enabled", value=band.get("enabled", True),
                key=f"fb_on_{i}", disabled=busy or not cfg["blur_enabled"])
            c1, c2 = st.columns(2)
            band["x"] = c1.number_input("x", 0.0, 1.0, float(band["x"]), 0.01,
                                        key=f"fb_x_{i}", disabled=busy)
            band["y"] = c2.number_input("y", 0.0, 1.0, float(band["y"]), 0.01,
                                        key=f"fb_y_{i}", disabled=busy)
            band["w"] = c1.number_input("w", 0.01, 1.0, float(band["w"]), 0.01,
                                        key=f"fb_w_{i}", disabled=busy)
            band["h"] = c2.number_input("h", 0.01, 1.0, float(band["h"]), 0.01,
                                        key=f"fb_h_{i}", disabled=busy)
        bands.append(band)
    cfg["fixed_blur"] = bands

    st.subheader("Detection")
    cfg["content_threshold"] = st.slider(
        "Content threshold", 5.0, 60.0, float(cfg["content_threshold"]), 1.0,
        disabled=busy, help="PySceneDetect default is 27. Leave it alone.")

    st.subheader("Review")
    cfg["trim_pad_s"] = st.number_input(
        "Mark cut padding (s)", 1.0, 30.0, float(cfg.get("trim_pad_s", 5.0)), 0.5,
        help="Mark cut builds impact +/- this. It shrinks automatically when the "
             "window runs off the clip or into another shot.")

    st.subheader("Clips")
    cfg["min_duration_s"] = st.number_input(
        "Min duration (s)", 1.0, 60.0, float(cfg["min_duration_s"]), 1.0, disabled=busy)
    cfg["max_duration_s"] = st.number_input(
        "Max duration (s)", 5.0, 300.0, float(cfg["max_duration_s"]), 5.0, disabled=busy)

    st.subheader("Encoding")
    cfg["encoder"] = st.selectbox(
        "Encoder", ["h264_nvenc", "libx264"],
        index=0 if cfg["encoder"] == "h264_nvenc" else 1, disabled=busy)
    cfg["nvenc_cq"] = st.slider("NVENC cq", 10, 35, int(cfg["nvenc_cq"]),
                                disabled=busy or cfg["encoder"] != "h264_nvenc",
                                help="NVENC has no CRF; cq is the equivalent.")

    st.subheader("YouTube access")
    cfg["cookies_file"] = st.text_input(
        "cookies.txt path", cfg.get("cookies_file", ""), disabled=busy,
        placeholder="cookies.txt",
        help="Netscape-format cookie export. Needed when YouTube answers "
             "\"Sign in to confirm you're not a bot\".")
    _browsers = ["", "chrome", "edge", "firefox", "brave"]
    _cur = (cfg.get("cookies_browser") or "")
    cfg["cookies_browser"] = st.selectbox(
        "or read cookies from browser", _browsers,
        index=_browsers.index(_cur) if _cur in _browsers else 0,
        disabled=busy or bool(cfg.get("cookies_file", "").strip()),
        help="That browser must be fully closed while the pipeline runs.")

    st.subheader("Paths")
    cfg["work_dir"] = st.text_input("Work dir", cfg["work_dir"], disabled=busy)
    cfg["output_dir"] = st.text_input("Export dir", cfg["output_dir"], disabled=busy)

    if st.button("💾 Save settings", disabled=busy, width='stretch'):
        config.save(cfg)
        st.success(f"Saved — config {config.config_hash(config.load())}")

    st.caption(f"config hash `{config.config_hash(config.load())}`  ·  "
               f"pipeline `{config.PIPELINE_VERSION}`")

if cfg["blur_enabled"] and not cfg["color_kill_enabled"]:
    st.sidebar.info("Blur only: the brand colour survives as a smear.")
if not cfg["review_enabled"]:
    st.sidebar.warning("Headless mode: no human gate. Clips stay UNREVIEWED.")


# ── tabs ──────────────────────────────────────────────────────────────────

tab_queue, tab_run, tab_review, tab_export = st.tabs(
    ["📥 Queue", "▶️ Run", "👀 Review", "📦 Export"]
)


with tab_queue:
    st.subheader("Add videos")
    col_a, col_b = st.columns(2)

    with col_a:
        pasted = st.text_area("Paste URLs (one per line)", height=140, key="paste",
                              placeholder="https://youtu.be/...\nhttps://www.youtube.com/watch?v=...")
        if st.button("Add pasted URLs", key="paste_go"):
            ok, bad = urls.parse_lines(pasted)
            report_enqueue(*db.enqueue_many(ok), bad)

    with col_b:
        up = st.file_uploader("Or upload a .txt (one URL per line)", type=["txt"])
        if up is not None and st.button("Add from file", key="file_go"):
            text = up.read().decode("utf-8", errors="replace")
            ok, bad = urls.parse_lines(text)
            report_enqueue(*db.enqueue_many(ok), bad)

    st.divider()
    st.subheader("Queue")
    rows = db.list_videos()
    if rows:
        frame = pd.DataFrame([{
            "video_id": r["youtube_video_id"],
            "title": (r["title"] or "")[:60],
            "channel": (r["channel_name"] or "")[:24],
            "status": r["status"],
            "stage": r["stage"] or "",
            "min": round((r["duration_s"] or 0) / 60, 1),
            "bounds": r["n_boundaries"] or 0,
            "clips": r["n_clips"] or 0,
            "error": (r["error_code"] or ""),
        } for r in rows])
        st.dataframe(frame, width='stretch', hide_index=True)

        c1, c2, c3 = st.columns(3)
        if c1.button("🔁 Retry DOWNLOAD_FAILED", key="retry_dl", disabled=busy):
            st.info(f"Requeued {db.retry_status(db.DOWNLOAD_FAILED)}")
        if c2.button("🔁 Retry FAILED", key="retry_failed", disabled=busy):
            st.info(f"Requeued {db.retry_status(db.FAILED)}")
        # Resuming a stopped video is cheap: the download and every completed
        # stage are already on disk, so it picks up where it was killed.
        if c3.button("▶️ Resume STOPPED", key="retry_stopped", disabled=busy):
            st.info(f"Requeued {db.retry_status(db.STOPPED)}")

        # A crash, a reboot or a closed browser tab leaves rows at DOWNLOADING
        # or PROCESSING with no runner behind them. Stop cannot reach those --
        # it is disabled when this session has no subprocess -- so they need a
        # way out of their own. Deliberately a button and not automatic: this
        # session's `busy` says nothing about a run started from another tab.
        stranded = [r for r in rows
                    if r["status"] in (db.DOWNLOADING, db.PROCESSING)]
        if stranded and not busy:
            st.warning(
                f"{len(stranded)} video(s) sit at DOWNLOADING/PROCESSING with "
                "no run in progress here — a crash, a reboot, or a run started "
                "from another browser tab.")
            if st.button("🔓 Mark them STOPPED", key="unstick",
                         help="Only do this if nothing is actually running."):
                st.info(f"Marked {db.mark_stopped()} — now resumable "
                        "and removable.")
                st.rerun()

        # Remove a URL from the queue. Only rows that cannot have produced
        # clips are offered: `clips` has no foreign key to `videos`, so
        # dropping a processed video would leave orphans in the review queue.
        # Disabled during a run, like the retry buttons above -- the runner is
        # a separate process reading the same table.
        st.markdown("**Remove from queue**")
        st.caption(
            "Drops the URL and, for a STOPPED video, any clips it managed to "
            "produce — including files already approved into `trimmed/`. The "
            "download itself stays on disk, so re-adding the URL resumes "
            "instead of starting over. A video that finished its run cannot be "
            "removed here."
        )
        labels = {f"{r['youtube_video_id']} ({r['status']})": r["youtube_video_id"]
                  for r in rows if r["status"] in db.REMOVABLE}
        # Streamlit owns widget state under `key=`, and refuses to let the
        # script write it once the widget exists. Bumping a revision renders a
        # *new* multiselect instead, which starts empty — otherwise the URL you
        # just removed stays selected, naming a row that no longer exists.
        rm_rev = st.session_state.get("rm_rev", 0)
        picked = st.multiselect(
            "URLs", sorted(labels), key=f"rm_pick_{rm_rev}",
            disabled=busy or not labels,
            placeholder=("nothing removable" if not labels
                         else "pick one or more URLs"),
            label_visibility="collapsed")
        if st.button("🗑️ Remove from queue", key="rm_go",
                     disabled=busy or not picked):
            # purge_video, not db.remove_video: a STOPPED video can already own
            # APPROVED files in trimmed/, since clips are reviewable while the
            # run is still going.
            results = [review.purge_video(labels[p]) for p in picked]
            gone = sum(rows for rows, _ in results)
            files = sum(n for _, n in results)
            st.session_state["rm_rev"] = rm_rev + 1
            st.session_state["rm_said"] = (
                f"Removed {gone} of {len(picked)}"
                + (f", and {files} file(s) from trimmed/" if files else "")
                + (f" — {len(picked) - gone} had already started processing."
                   if gone < len(picked) else "."))
            st.rerun()
        said = st.session_state.pop("rm_said", None)
        if said:
            st.info(said)
    else:
        st.info("Queue is empty. Add some URLs above.")


with tab_run:
    queued = len(db.list_videos(db.QUEUED))
    st.subheader("Pipeline")

    c1, c2, c3 = st.columns([1, 1, 3])
    if c1.button("▶️ Start", disabled=busy or queued == 0, type="primary"):
        start_run()
        st.rerun()
    if c2.button("⏹ Stop", key="stop_go", disabled=not busy):
        proc = running_proc()
        if proc:
            proc.terminate()
            # Wait for it to actually die before writing status: the runner
            # writes status too, and a dying one would overwrite STOPPED with
            # whatever stage it was in.
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        st.session_state["stop_said"] = (
            f"Stopped. {db.mark_stopped()} video(s) marked STOPPED — "
            "retry to resume from what is already downloaded, or remove them.")
        st.rerun()
    c3.metric("Queued", queued)

    stopped = st.session_state.pop("stop_said", None)
    if stopped:
        st.warning(stopped)

    @st.fragment(run_every=2 if busy else None)
    def run_monitor() -> None:
        """Poll progress without rerunning the app.

        Streamlit executes every tab's body in one script run, so the old
        `time.sleep(2); st.rerun()` at app scope re-created the Review tab's
        `st.video` element every two seconds — you could not watch a clip while
        the pipeline ran. A fragment reruns only itself.
        """
        rows = db.list_videos()
        if rows:
            done = sum(1 for r in rows
                       if r["status"] in (db.READY_FOR_REVIEW, db.DONE))
            st.progress(done / len(rows),
                        text=f"{done}/{len(rows)} videos complete")
        # st.code, not st.text_area: a keyed text_area would pin the first read
        # forever (Streamlit ignores `value=` once the key exists), and an
        # unkeyed one inside a fragment is a widget with no stable identity.
        # The log is output, not input.
        st.markdown("**Log**")
        st.code(log_tail(), language=None, height=320)

        # `busy` was computed once, at the top of the script. Nothing else will
        # recompute it now that the app no longer reruns on a timer, so the
        # finished run would leave the sidebar locked and Start greyed out.
        if busy and running_proc() is None:
            st.rerun(scope="app")

    run_monitor()


with tab_review:
    if not cfg["review_enabled"]:
        st.warning(
            "Headless mode is on. Clips from the last run are UNREVIEWED — "
            "you can still review them here."
        )

    pending = db.list_clips(decision=db.UNREVIEWED)
    top = [c for c in pending if db.priority(c["duration_ms"]) == db.TOP]
    low = [c for c in pending if db.priority(c["duration_ms"]) == db.LOW_PRIORITY]

    counts = db.clip_counts()
    total = sum(counts["by_decision"].values()) or 1
    approved = counts["by_decision"].get(db.APPROVED, 0)
    reviewed = total - counts["by_decision"].get(db.UNREVIEWED, 0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Top priority left", len(top))
    m2.metric("Low priority left", len(low))
    m3.metric("Reviewed", reviewed)
    m4.metric("Approved", approved)

    # Last decision, echoed so the reviewer sees the approve landed.
    last = st.session_state.get("last_decision")
    if last:
        cid_done, decision, detail = last
        icon = {"APPROVED": "✅", "REJECTED": "❌", "FLAGGED": "🚩"}[decision]
        label = {"APPROVED": "Approved", "REJECTED": "Rejected",
                 "FLAGGED": "Flagged"}[decision]
        banner = st.success if decision == "APPROVED" else st.info
        banner(f"{icon} `{cid_done}` — + {label} · {detail}")

    # Top priority drains first, then Low. The handover is automatic: no
    # "Next batch" click, because there is nothing for the reviewer to decide.
    queue = top or low
    band = db.TOP if top else db.LOW_PRIORITY

    if not queue:
        st.success("Nothing left to review.")
    else:
        clip = queue[0]
        cid = clip["clip_id"]
        dur = clip["duration_ms"] / 1000
        flags = json.loads(clip["flags"] or "[]")
        chip = "🔺 TOP PRIORITY" if band == db.TOP else "▪️ Low priority"

        st.markdown(
            f"### {chip} · `{cid}` · {dur:.1f}s"
            + (f" · ⚠️ {', '.join(flags)}" if flags else "")
        )
        st.caption(
            f"Clips longer than {db.PRIORITY_SECONDS}s are reviewed first. "
            "Priority comes from the original duration and does not change "
            "when you trim."
        )

        path = clip["delivered_path"] or clip["master_path"]
        if path and Path(path).exists():
            st.video(path, autoplay=True, loop=True)
        else:
            st.error(f"Missing file: {path}")

        existing = db.trim_segments(clip)
        if existing:
            st.info(
                f"Already materialised as {len(existing)} file(s) in `trimmed/`. "
                "Approving again overwrites them; Reject deletes them."
            )

        # ── trim: define the output shots ─────────────────────
        st.divider()
        pad = float(cfg.get("trim_pad_s", trim.PAD_DEFAULT))
        # One rounded duration for every widget: a slider maxing at dur and a
        # number_input maxing at round(dur,1) disagree, and Streamlit throws.
        dmax = round(dur, 1)
        skey = f"shots_{cid}"
        if skey not in st.session_state:
            # Re-opening an already-approved clip should show what it was cut to.
            st.session_state[skey] = (
                [trim.shot(x["start_ms"] / 1000, x["end_ms"] / 1000)
                 for x in existing] if existing else trim.whole_clip(dmax))
        shots = st.session_state[skey]

        # Streamlit widget state outlives the value= argument: once s_<cid>_0
        # exists, re-rendering it with a new value is ignored and the old number
        # wins. That silently undid Mark cut and, on delete, kept the removed
        # shot's numbers on top of the surviving shot. Versioning the keys makes
        # every programmatic change render *new* widgets, which do take value=.
        rkey = f"rev_{cid}"
        rev = int(st.session_state.get(rkey, 0))

        def _set(new_shots: list[dict]) -> None:
            st.session_state[skey] = new_shots
            st.session_state[rkey] = rev + 1

        st.markdown("**✂️ Trim**")
        st.caption(
            f"Park the playhead on the impact and press **Mark cut**: that "
            f"becomes impact ±{pad:g}s, shrunk automatically if it runs off the "
            f"clip or into another shot. Up to {trim.MAX_SHOTS} shots, one "
            "output file each. Cut only — no crop, rotate or effects."
        )

        mc1, mc2, mc3 = st.columns([5, 1.4, 1.4])
        x = mc1.slider("Playhead (s)", 0.0, dmax, round(dmax / 2, 1), 0.1,
                       key=f"play_{cid}")
        full = len(shots) >= trim.MAX_SHOTS and not trim.is_untouched(shots, dmax)
        if mc2.button("📍 Mark cut", key=f"mark_{cid}", disabled=full,
                      width='stretch'):
            # The first mark replaces the untouched whole-clip default rather
            # than colliding with it.
            base = [] if trim.is_untouched(shots, dmax) else list(shots)
            made = trim.mark_cut(x, dmax, base, pad)
            if made is None:
                st.session_state[f"err_{cid}"] = (
                    "Cannot auto-adjust – "
                    + trim.why_no_room(x, dmax, base, pad)
                    + ". Move the playhead, remove a shot, or type the times in "
                      "by hand.")
            else:
                _set(base + [made])
                st.session_state[f"toast_{cid}"] = (
                    f"Shot at {made['start']:.1f}–{made['end']:.1f}s"
                    + (" (auto-adjusted)" if made["auto"] else ""))
            st.rerun()
        if mc3.button("➕ Add shot", key=f"add_{cid}", disabled=full,
                      width='stretch'):
            base = [] if trim.is_untouched(shots, dmax) else list(shots)
            last = base[-1]["end"] if base else 0.0
            _set(base + [trim.shot(last, min(last + 2 * pad, dmax))])
            st.rerun()

        st.markdown(trim.timeline_html(shots, dmax), unsafe_allow_html=True)

        for c in trim.conflicts(shots):
            k1, k2 = st.columns([4, 1])
            k1.error(
                f"Shots {c['i'] + 1} and {c['j'] + 1} overlap by "
                f"{c['amount']:.1f}s ({c['start']:.1f}–{c['end']:.1f}s).")
            if k2.button("🔧 Resolve conflict", width='stretch',
                         key=f"res_{cid}_{c['i']}_{c['j']}"):
                fixed = list(shots)
                # Shrink the newer shot; the earlier one is the reviewer's
                # settled decision.
                if trim.resolve(fixed, c["j"], dmax):
                    _set(fixed)
                    st.session_state[f"toast_{cid}"] = "Resolved"
                else:
                    st.session_state[f"err_{cid}"] = (
                        "Cannot resolve automatically – please adjust "
                        "Start/End manually.")
                st.rerun()

        edited: list[dict] = []
        for i, s in enumerate(shots):
            r1, r2, r3, r4 = st.columns([0.7, 2, 2, 0.7])
            r1.markdown(
                "<div style='height:38px;display:flex;align-items:center;gap:6px'>"
                f"<span style='width:14px;height:14px;border-radius:3px;"
                f"background:{trim.COLORS[i % len(trim.COLORS)]}'></span>"
                f"<b>{i + 1}</b></div>", unsafe_allow_html=True)
            a = r2.number_input("Start (s)", 0.0, dmax, min(float(s["start"]), dmax),
                                0.1, key=f"s_{cid}_{rev}_{i}")
            b = r3.number_input("End (s)", 0.0, dmax, min(float(s["end"]), dmax),
                                0.1, key=f"e_{cid}_{rev}_{i}")
            if r4.button("✗", key=f"del_{cid}_{rev}_{i}",
                         help="Remove this shot"):
                _set([t for k, t in enumerate(shots) if k != i])
                st.rerun()
            moved = abs(a - s["start"]) > 1e-6 or abs(b - s["end"]) > 1e-6
            edited.append(trim.shot(
                a, b, auto=s.get("auto") and not moved,
                default=s.get("default") and not moved))
        if edited != shots:
            # Typing a number must move the timeline, so take the edit and redraw.
            # No revision bump: these values came *from* the widgets.
            st.session_state[skey] = edited
            st.rerun()

        toast = st.session_state.pop(f"toast_{cid}", None)
        if toast:
            st.toast(toast)
        failed = st.session_state.pop(f"err_{cid}", None)
        if failed:
            st.error(failed)

        errs = trim.errors(shots, dmax)
        segments = trim.segments(shots)
        seg_error = bool(errs)
        for msg in errs:
            st.warning(msg)
        if not errs:
            st.caption(
                f"Approve writes **{len(segments)} file(s)** to `trimmed/`: "
                + ", ".join(f"{media.fmt_time(a)}→{media.fmt_time(b)}"
                            for a, b in segments)
                + ". The master and the delivered clip are never modified."
            )

        # ── decision ──────────────────────────────────────────────────────
        st.divider()
        st.caption(
            "Approve or Reject is required to advance — there is no Skip. "
            "Approve materialises the shots into `trimmed/`; Reject removes "
            "this clip from `trimmed/` and leaves the source untouched. "
            "Approve stays disabled while any conflict or out-of-range shot "
            "remains."
        )
        b1, b2, b3 = st.columns(3)

        def _done(decision: str, detail: str) -> None:
            # Drop this clip's editing state; a re-review reloads from trimmed/.
            for k in [skey, rkey, f"play_{cid}", f"toast_{cid}", f"err_{cid}"]:
                st.session_state.pop(k, None)
            st.session_state["last_decision"] = (cid, decision, detail)

        if b1.button("✅ Approve", width='stretch', type="primary",
                     disabled=bool(seg_error)):
            try:
                with st.spinner("Writing segments…"):
                    written = review.materialize(clip, segments, cfg)
                db.set_decision(cid, db.APPROVED)
                _done(db.APPROVED, f"{len(written)} file(s) → trimmed/")
                st.rerun()
            except media.MediaError as exc:
                st.error(str(exc))

        if b2.button("🗑️ Reject", width='stretch'):
            removed = review.discard(clip)
            db.set_decision(cid, db.REJECTED)
            _done(db.REJECTED, f"removed {removed} file(s) from trimmed/")
            st.rerun()

        if b3.button("🚩 Flag", width='stretch'):
            db.set_decision(cid, db.FLAGGED)
            _done(db.FLAGGED, "needs a second look")
            st.rerun()


with tab_export:
    st.subheader("Export")
    batch = st.text_input("Batch name", "batch01")
    headless_batch = not cfg["review_enabled"]
    wanted = db.UNREVIEWED if headless_batch else db.APPROVED
    clips = db.list_clips(decision=wanted)
    st.write(f"{len(clips)} clip(s) with decision `{wanted}` ready to export.")

    if st.button("📦 Export to disk", disabled=not clips, type="primary"):
        out = Path(cfg["output_dir"]) / batch
        (out / "clips").mkdir(parents=True, exist_ok=True)
        manifest = {
            "batch": batch,
            "pipeline_version": config.PIPELINE_VERSION,
            # The config at EXPORT time. Each clip carries the hash of the
            # config that actually produced it, which may differ.
            "export_config_hash": config.config_hash(cfg),
            "review_mode": "headless" if headless_batch else "interactive",
            "exported_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "clips": [],
        }
        for c in clips:
            # trimmed/ is the finished product: an approved clip is always
            # materialised there, as one file or several.
            segs = db.trim_segments(c)
            sources = ([Path(x["path"]) for x in segs] or
                       [Path(c["delivered_path"] or c["master_path"])])
            dest_dir = out / "clips" / c["youtube_video_id"]
            dest_dir.mkdir(parents=True, exist_ok=True)
            for i, src in enumerate(sources, 1):
                if not src.exists():
                    continue
                name = src.name if segs else f"{c['clip_id']}.mp4"
                shutil.copy2(src, dest_dir / name)
                manifest["clips"].append({
                    "clip_id": c["clip_id"],
                    "file": name,
                    "segment": i,
                    "of_segments": len(sources),
                    "youtube_video_id": c["youtube_video_id"],
                    "start_ms": c["start_ms"], "end_ms": c["end_ms"],
                    "priority": db.priority(c["duration_ms"]),
                    "trim_start_ms": segs[i - 1]["start_ms"] if segs else None,
                    "trim_end_ms": segs[i - 1]["end_ms"] if segs else None,
                    "confidence": c["confidence"],
                    "flags": json.loads(c["flags"] or "[]"),
                    "decision": c["decision"],
                    "pipeline_version": c["pipeline_version"],
                    "config_hash": c["config_hash"],
                })
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        st.success(f"Exported {len(manifest['clips'])} clips to {out}")

    st.divider()
    st.caption("Stats")
    st.json(db.clip_counts())

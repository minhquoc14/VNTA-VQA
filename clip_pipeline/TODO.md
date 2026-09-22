# TODO — Vietnamese Traffic Clip Dataset Pipeline

**Updated:** 2026-08-29 · Governed by [`CLAUDE.md`](./CLAUDE.md) ·
Design: [`architecture.md`](./architecture.md) · Usage: [`GUIDE.md`](./GUIDE.md)

---

## Status

**The pipeline is built and verified end-to-end on real footage.** One real
video (`KdhIs56QWuQ`, *Camera giao thông*, 12 min) went download → segment →
blur → review → export and produced **35 clips**.

**Open P0 items: none.**

### What real footage changed

Testing against actual video invalidated three assumptions from the original
design. All three are fixed; the details are in `architecture.md` §2, §6, §7.

| Assumption | Reality | Fix |
|---|---|---|
| Branding is opaque, so temporal variance finds it | It is **alpha-blended**; variance floor 258 vs a threshold of 18 → **0 regions found** | percentile cutoff (steadiest 1%), absolute value kept as a floor |
| The `#01/#02` counter is in every frame | It is **transient** — a few seconds per segment | counter path removed entirely |
| Counter + PySceneDetect are independent signals | A mis-detected "counter" was the logo, changing **with the scene** (rate 0.244 vs control 0.000) | fusion removed; one signal, all `MEDIUM` |

### Bugs found by testing, not by reading

| Bug | Symptom | Cause |
|---|---|---|
| Only the last overlay was blurred | logo std 91.4 → 91.3 | filter graph reused one label twice; ffmpeg gave no error |
| `boxblur` failed on small regions | `Invalid chroma_param radius` | radius capped by chroma plane size → `gblur` |
| Calibration found nothing | `0 overlay region(s)` | absolute variance threshold vs alpha-blended branding |
| Approve was impossible | `0.0-57.8s falls outside the clip (0-57.8s)` | UI rounds to 0.1s; validation had no tolerance |
| Reviewer trims wiped on re-run | `trimmed_path` became NULL | `INSERT OR REPLACE` drops the whole row → UPSERT |
| Stale trims shipped silently | export used old-blur files | re-cut segments when `delivered/` is re-encoded |
| 3 of 4 clips vanished | no log line | blank filter dropped silently → counted and logged |
| Delivered 6× the master | 905 MB per video | `cq=19` → `cq=23` |
| Mark cut always collided | every new shot hit the whole-clip default | first mark **replaces** the untouched default |
| Deleting shot 1 kept shot 1 | the wrong shot survived, silently | Streamlit widget state outlives `value=`; stale inputs overwrote the new list → versioned widget keys |
| "Cannot auto-adjust" said nothing | reviewer could not tell what was in the way | `trim.why_no_room()` names the blocking shot or the clip edge |
| Stop stranded a video permanently | row stuck at `PROCESSING`; no retry, no remove, no requeue could see it | `terminate()` never touched the DB → `STOPPED` state, set after `proc.wait()` |
| Removing a stopped video orphaned approved .mp4s | files left in `trimmed/` that nothing lists or exports | reviewing-during-a-run means a removable video can own outputs → `review.purge_video()` |
| Could not review while the pipeline ran | `st.video` restarted every 2s; `AppTest` never returned | `time.sleep(2); st.rerun()` at app scope reran *every* tab → `st.fragment(run_every=2)` |
| Removing a URL crashed the Queue tab | `st.session_state.rm_pick cannot be modified after the widget is instantiated` | same widget-ownership trap as the trim rows → versioned key |
| Log would have frozen at its first line | (caught by a test before shipping) | keyed `st.text_area` ignores `value=` on re-render → `st.code` |
| `WinError 10054` traceback on every reload | looked cosmetic; actually leaked a socket + left the transport attached to the server | CPython's unguarded `sock.shutdown()` skips its own teardown → `vqa/winasyncio.py` completes it |

---

# Done

## Phase 0 — Decisions ✅

- [x] Streamlit only — no FastAPI, no JS framework, no separate web server
- [x] All configuration through the UI; no CLI flags, no manual config editing
- [x] Local disk only — no Google Drive, no cloud storage
- [x] Single machine, single user — no locking or coordination
- [x] Constitution written to `CLAUDE.md` as the source of truth

## Phase 1 — Foundations ✅

- [x] `config.py` — defaults, load/save, `config_hash`, `PIPELINE_VERSION`
- [x] `db.py` — 4 tables, WAL, `busy_timeout`, states, decisions, indexes
- [x] `urls.py` — 8 URL shapes → video ID; batch parse reports bad lines by
      number without aborting
- [x] `media.py` — probe, sample, cut, blur, encode, trim, time parsing
- [x] Additive schema migration (`trim_segments`) that preserves existing rows

## Phase 2 — App shell ✅

- [x] Sidebar owns every setting; locked during a run
- [x] Queue tab — paste URLs + `.txt` upload, dedup, retry buttons
- [x] Run tab — subprocess Start/Stop, progress bar, live log tail
- [x] Headless mode — clips written `UNREVIEWED`, never `APPROVED`, nothing in
      `trimmed/`

## Phase 3 — Download ✅

- [x] yt-dlp with resume (skips if `source.mp4` + `metadata.json` exist)
- [x] Cookie support: Netscape `.txt` **and** extension `.json` (auto-converted)
- [x] JS runtime + `ejs:github` solver for YouTube's "n" challenge
- [x] Cookie paths gitignored (`cookies.json`, `cookies.txt`, `cookies.netscape.txt`)

## Phase 4 — Calibration + detection ✅

- [x] Per-pixel temporal variance, cached **per channel**
- [x] Percentile cutoff — verified 97.8% precision against the logo box
- [x] PySceneDetect at defaults with `auto_downscale`
- [x] ~~Counter / OCR detection~~ — **removed entirely**, see Status above

## Phase 5 — Clips + blur ✅

- [x] Boundary → clip policy with safety trim, min/max duration, `TOO_LONG`
- [x] Blank filter (black + frozen), drops counted and logged
- [x] Lossless masters with duration verification and libx264 fallback
- [x] Calibrated regions **+** always-on fixed bands, config-driven fractions
- [x] NVENC with CPU fallback; `cq=23`
- [x] Verified: logo std −76.3%, roadway control −0.2%

## Phase 6 — Review ✅

- [x] Duration-based priority: `>15s` = TOP, drains before LOW automatically
- [x] Priority derived from the **original** duration, unchanged by trimming
- [x] **Mark cut** — playhead + button builds `impact ± pad`, pad configurable
- [x] **Auto-shrink** — ±5s → ±1s when the window overruns the clip or another
      shot; adjusted shots marked yellow, refusal is explicit
- [x] **Visual timeline** — one colour per shot, hover tooltips, overlap in red
- [x] **Resolve conflict** — shrinks the newer shot, or says it cannot
- [x] Up to 3 shots, N shots → N files; per-shot ±0.1s Start/End boxes
- [x] Time input accepts `HH:MM:SS`, `MM:SS` and plain seconds
- [x] Approve materialises into `trimmed/`; re-approve replaces, no orphans
- [x] Approve blocked while any conflict or out-of-range shot remains
- [x] Reject deletes only from `trimmed/`; master, delivered and source survive
- [x] No Skip — a decision is required to advance
- [x] Confirmation banner: `✅ <clip_id> — + Approved · N file(s) → trimmed/`

## Phase 7 — Export ✅

- [x] One manifest entry per **file**, not per clip
- [x] Records segment index, trim points, priority, decision, config hash
- [x] `review_mode` distinguishes headless from interactive batches

## Phase 8 — Docs ✅

- [x] `README.md` — setup, run, layout, three-tier artifacts
- [x] `GUIDE.md` — operator walkthrough + reviewer guideline (cut rule in §2.4)
- [x] `architecture.md` — rewritten against the as-built code
- [x] `TODO.md` — this file

---

# Next

## P1 — Before scaling to 50 videos

- [ ] **Run 2–3 more videos from the same channel.** Calibration is cached per
      channel; confirm the cached regions still land correctly on a different
      upload before committing to a 50-video batch.
- [ ] **Confirm the reviewer cut rule.** `GUIDE.md` §2.4 currently says
      *impact → impact+5s*. Clips then contain **no lead-up**, so causal VQA
      questions ("why did this happen?", "who had right of way?") are
      unanswerable from them. If those are in scope the rule should become
      *impact−3s → impact+5s*. **This is a dataset-policy decision, not an
      engineering one** — and changing it later means re-reviewing everything.
- [ ] **Decide the disk budget.** ~70 GB for 50 videos. Deleting `clips/` after
      a channel's blur is settled roughly halves it, at the cost of a
      re-download if blur ever changes again.

## P2 — Nice to have, not blocking

- [ ] Keyboard shortcuts for A/R/F and Mark cut (`streamlit-shortcuts`)
- [ ] Drag the shot blocks on the timeline instead of typing numbers — needs a
      custom component, which is a build step (`CLAUDE.md`: don't add a layer)
- [ ] Review filters (by video, by flag, by priority band)
- [ ] Manual overlay ROI override in the UI, for a channel where calibration
      picks the wrong box
- [ ] "Re-derive delivered" button, so a blur change does not need a full re-run
- [ ] Disk reclamation UI (drop `clips/` for exported videos)

---

# Deferred, with the trigger that revives it

| Item | Deferred because | Revive when |
|---|---|---|
| **Near-duplicate detection** | not needed to produce clips | **Before drawing any train/test split.** Compilations re-use footage across uploads; the same incident in both splits would invalidate the dataset. This is the one deferral with a hard deadline. |
| Boundary editing in the UI | reviewer trimming covers the real need | a reviewer reports boundaries are wrong more often than they are right |
| Formal boundary P/R/F1 benchmark | explicitly out of scope (Principle 3) | the evaluation phase, if the thesis needs the number |
| Threshold tuning campaign | defaults work | a channel where defaults visibly fail |
| Vendoring the yt-dlp JS solver | `ejs:github` works | the remote fetch becomes unacceptable or unavailable |
| Trimming from masters instead of `delivered` | generation loss negligible at `cq=23` | quality complaints from annotation |
| VQA / QA generation | **explicitly out of scope for this phase** | clips are reviewed and exported |

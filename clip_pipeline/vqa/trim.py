"""Reviewer trim geometry — mark-cut, auto-shrink, conflicts, timeline.

Kept out of `app.py` so the rules can be exercised without starting Streamlit.
A *shot* is a plain dict so it survives `st.session_state` untouched:

    {"start": float, "end": float, "auto": bool, "default": bool}

`auto` means the shot was shrunk to avoid an overlap or the clip edge — the UI
paints those yellow. `default` marks the untouched whole-clip shot the reviewer
is given on arrival; the first Mark cut replaces it instead of colliding with it.
"""

from __future__ import annotations

from html import escape

PAD_DEFAULT = 5.0     # "impact ± 5s" — GUIDE.md 2.4
PAD_MIN = 1.0         # give up below this rather than emit a 1-frame clip
PAD_STEP = 1.0
OVERLAP_TOL = 0.1     # under this is rounding noise, not a real overlap
MAX_SHOTS = 3         # UI limit; three colours, three rows

COLORS = ["#22C55E", "#3B82F6", "#F59E0B"]
CONFLICT_COLOR = "#EF4444"
AUTO_COLOR = "#FACC15"


def shot(start: float, end: float, auto: bool = False, default: bool = False) -> dict:
    return {"start": round(float(start), 1), "end": round(float(end), 1),
            "auto": bool(auto), "default": bool(default)}


def whole_clip(duration: float) -> list[dict]:
    """What the reviewer starts with: keep everything, decide nothing."""
    return [shot(0.0, round(duration, 1), default=True)]


def is_untouched(shots: list[dict], duration: float) -> bool:
    return len(shots) == 1 and shots[0].get("default")


# ── geometry ──────────────────────────────────────────────────────────────

def overlap(a: dict, b: dict) -> float:
    """Seconds of intersection. Negative when the shots are disjoint."""
    return min(a["end"], b["end"]) - max(a["start"], b["start"])


def _pad_ladder(pad: float) -> list[float]:
    """5 -> [5, 4, 3, 2, 1]. Never goes below PAD_MIN."""
    out, p = [], float(pad)
    while p >= PAD_MIN - 1e-9:
        out.append(round(p, 1))
        p -= PAD_STEP
    if not out or out[-1] > PAD_MIN:
        out.append(PAD_MIN)
    return out


def _fits(start: float, end: float, duration: float, others: list[dict]) -> bool:
    if start < 0 or end > duration:
        return False
    cand = {"start": start, "end": end}
    return all(overlap(cand, o) < OVERLAP_TOL for o in others)


def mark_cut(x: float, duration: float, others: list[dict],
             pad: float = PAD_DEFAULT) -> dict | None:
    """Centre a shot on the impact at `x`, shrinking evenly until it fits.

    Tries +/-pad, then pad-1, ... down to +/-1s. Returns None when even +/-1s
    still runs off the clip or into another shot — the reviewer has to fix that
    by hand.
    """
    for p in _pad_ladder(pad):
        a, b = round(x - p, 1), round(x + p, 1)
        if _fits(a, b, duration, others):
            return shot(a, b, auto=p < pad)
    return None


def why_no_room(x: float, duration: float, others: list[dict],
                pad: float = PAD_DEFAULT) -> str:
    """Explain a failed mark_cut in the reviewer's terms, not the algorithm's."""
    for n, o in enumerate(others, 1):
        if o["start"] - PAD_MIN < x < o["end"] + PAD_MIN:
            return (f"the playhead is inside (or within {PAD_MIN:g}s of) shot {n} "
                    f"[{o['start']:.1f}-{o['end']:.1f}s]")
    room = min(x, duration - x)
    if room < PAD_MIN:
        return (f"the playhead is only {room:.1f}s from the edge of the clip, "
                f"and a shot needs at least {PAD_MIN:g}s on each side")
    gaps = []
    for n, o in enumerate(others, 1):
        if o["end"] <= x:
            gaps.append(f"shot {n} ends at {o['end']:.1f}s")
        elif o["start"] >= x:
            gaps.append(f"shot {n} starts at {o['start']:.1f}s")
    where = "; ".join(gaps)
    return (f"there is no clear {PAD_MIN:g}s on both sides of {x:.1f}s"
            + (f" ({where})" if where else ""))


def resolve(shots: list[dict], idx: int, duration: float) -> bool:
    """Shrink `shots[idx]` around its own centre until it clears everything.

    Mutates the list in place. False means it hit PAD_MIN still overlapping.
    """
    target = shots[idx]
    centre = (target["start"] + target["end"]) / 2.0
    half = (target["end"] - target["start"]) / 2.0
    others = [s for i, s in enumerate(shots) if i != idx]
    for p in _pad_ladder(half):
        if p > half:
            continue          # the ladder floor may exceed an already-tiny shot
        a, b = round(centre - p, 1), round(centre + p, 1)
        if _fits(a, b, duration, others):
            shots[idx] = shot(a, b, auto=True)
            return True
    return False


def conflicts(shots: list[dict]) -> list[dict]:
    """Every overlapping pair, with the region they share."""
    out = []
    for i in range(len(shots)):
        for j in range(i + 1, len(shots)):
            amount = overlap(shots[i], shots[j])
            if amount >= OVERLAP_TOL:
                out.append({
                    "i": i, "j": j,
                    "start": max(shots[i]["start"], shots[j]["start"]),
                    "end": min(shots[i]["end"], shots[j]["end"]),
                    "amount": round(amount, 1),
                })
    return out


def errors(shots: list[dict], duration: float) -> list[str]:
    """Everything blocking Approve. Empty list = good to go."""
    msgs: list[str] = []
    if not shots:
        msgs.append("Add at least one shot, or Reject the clip.")
    for n, s in enumerate(shots, 1):
        if s["end"] <= s["start"]:
            msgs.append(f"Shot {n}: End must be after Start.")
        elif s["start"] < -0.05 or s["end"] > duration + 0.2:
            # +0.2 matches review.materialize's tolerance for 0.1s rounding.
            msgs.append(
                f"Shot {n}: {s['start']:.1f}-{s['end']:.1f}s falls outside "
                f"the clip (0-{duration:.1f}s).")
    for c in conflicts(shots):
        msgs.append(
            f"Shots {c['i'] + 1} and {c['j'] + 1} overlap by {c['amount']:.1f}s.")
    return msgs


def segments(shots: list[dict]) -> list[tuple[float, float]]:
    """Shots -> what `review.materialize` wants."""
    return [(s["start"], s["end"]) for s in shots]


# ── timeline ──────────────────────────────────────────────────────────────

_CSS = """
<style>
.vqa-tl{position:relative;height:46px;border-radius:6px;background:#20252c;
        border:1px solid #3a424d;margin:2px 0 6px 0;overflow:hidden}
.vqa-shot{position:absolute;top:5px;height:36px;border-radius:4px;
          border:1px solid rgba(0,0,0,.35);box-sizing:border-box;
          display:flex;align-items:center;justify-content:space-between;
          padding:0 5px;color:#0b0f14;font:600 11px/1 sans-serif;
          cursor:default;overflow:hidden;white-space:nowrap}
.vqa-shot:hover{border:2px solid #000;z-index:5}
.vqa-shot.vqa-auto{border:2px dashed %(auto)s}
.vqa-n{font-size:14px;font-weight:800}
.vqa-conflict{position:absolute;top:5px;height:36px;border-radius:4px;
              background:%(conflict)s;opacity:.85;z-index:4;box-sizing:border-box;
              border:2px solid %(conflict)s;animation:vqablink .8s steps(1) infinite}
@keyframes vqablink{50%%{border-color:#fff}}
.vqa-ticks{display:flex;justify-content:space-between;
           color:#9aa4b2;font:11px/1 sans-serif;margin-bottom:8px}
</style>
""" % {"conflict": CONFLICT_COLOR, "auto": AUTO_COLOR}


def timeline_html(shots: list[dict], duration: float) -> str:
    """The whole clip as a bar, one coloured block per shot, overlaps in red."""
    dur = max(float(duration), 0.001)

    def pct(t: float) -> float:
        return max(0.0, min(100.0, t / dur * 100.0))

    parts = [_CSS, '<div class="vqa-tl">']
    for i, s in enumerate(shots):
        left = pct(s["start"])
        width = max(pct(s["end"]) - left, 1.2)   # keep a sliver clickable/visible
        tip = (f"Shot {i + 1}: {s['start']:.1f}s -> {s['end']:.1f}s "
               f"({s['end'] - s['start']:.1f}s)")
        if s.get("auto"):
            tip += " - auto-adjusted to avoid overlap"
        cls = "vqa-shot vqa-auto" if s.get("auto") else "vqa-shot"
        parts.append(
            f'<div class="{cls}" title="{escape(tip)}" '
            f'style="left:{left:.3f}%;width:{width:.3f}%;'
            f'background:{COLORS[i % len(COLORS)]}">'
            f'<span>{s["start"]:.1f}</span>'
            f'<span class="vqa-n">{i + 1}</span>'
            f'<span>{s["end"]:.1f}</span></div>')
    for c in conflicts(shots):
        left = pct(c["start"])
        width = max(pct(c["end"]) - left, 1.2)
        tip = (f"Shots {c['i'] + 1} and {c['j'] + 1} overlap by "
               f"{c['amount']:.1f}s")
        parts.append(
            f'<div class="vqa-conflict" title="{escape(tip)}" '
            f'style="left:{left:.3f}%;width:{width:.3f}%"></div>')
    parts.append("</div>")
    parts.append(f'<div class="vqa-ticks"><span>0.0s</span>'
                 f'<span>{dur / 2:.1f}s</span><span>{dur:.1f}s</span></div>')
    return "".join(parts)

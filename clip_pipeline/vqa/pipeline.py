"""Per-video pipeline with file-based resume.

Resume is `if the file exists, skip` — not a caching framework. A crash during
encoding re-reads boundaries.json and continues; nothing re-downloads.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config, db, media
from .stages import calibrate, download, fuse, shots


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def process_video(video_id: str, url: str, cfg: dict, log) -> None:
    work = config.work_dir(cfg, video_id)
    cfg_hash = config.config_hash(cfg)
    headless = not cfg.get("review_enabled", True)

    # ── S2 download ───────────────────────────────────────────────────────
    db.set_status(video_id, db.DOWNLOADING, stage="download")
    log(f"[{video_id}] downloading")
    try:
        meta = download.download(url, work, int(cfg.get("max_height", 1080)), cfg)
    except download.DownloadError as exc:
        log(f"[{video_id}] DOWNLOAD FAILED: {exc}")
        db.set_status(video_id, db.DOWNLOAD_FAILED, stage="download",
                      error_code="DOWNLOAD_FAILED", error_detail=str(exc)[:500])
        return

    source = work / "source.mp4"
    db.set_status(video_id, db.PROCESSING, stage="probe")
    db.update_video(video_id, title=meta["title"], channel_id=meta["channel_id"],
                    channel_name=meta["channel_name"],
                    config_snapshot=json.dumps(cfg))

    # ── S3 probe ──────────────────────────────────────────────────────────
    probe_path = work / "probe.json"
    info = _load(probe_path)
    if info is None:
        try:
            info = media.probe(source)
        except media.MediaError as exc:
            log(f"[{video_id}] INVALID VIDEO: {exc}")
            db.set_status(video_id, db.FAILED, stage="probe",
                          error_code="INVALID_VIDEO", error_detail=str(exc)[:500])
            return
        _dump(probe_path, info)

    duration = info["duration_s"] or meta["duration_s"]
    fps = info["fps"] or 25.0
    if duration < 30:
        log(f"[{video_id}] REJECTED: {duration:.0f}s is shorter than 30s "
            f"(not a compilation)")
        db.set_status(video_id, db.FAILED, stage="probe",
                      error_code="INVALID_VIDEO", error_detail="shorter than 30s")
        return
    db.update_video(video_id, duration_s=duration)
    log(f"[{video_id}] {info['width']}x{info['height']} {fps:.1f}fps {duration/60:.1f}min")

    # ── S4 overlay calibration (cached per channel) ───────────────────────
    db.set_status(video_id, db.PROCESSING, stage="calibrate")
    channel_id = meta["channel_id"]
    row = db.get_channel(channel_id)
    if row and row["overlay_regions"]:
        regions = json.loads(row["overlay_regions"])
        log(f"[{video_id}] calibration cached for channel {channel_id}")
    else:
        log(f"[{video_id}] calibrating overlays")
        try:
            regions = calibrate.calibrate(source, duration)
        except Exception as exc:  # calibration must never kill the batch
            log(f"[{video_id}] calibration failed ({exc}) — continuing without blur")
            regions = []
        db.save_channel(channel_id, meta["channel_name"], regions)
        log(f"[{video_id}] found {len(regions)} overlay region(s)")

    # ── S5b visual shot detection ─────────────────────────────────────────
    db.set_status(video_id, db.PROCESSING, stage="shots")
    shots_path = work / "shots.json"
    sdata = _load(shots_path)
    if sdata is None:
        log(f"[{video_id}] running shot detection")
        try:
            cuts = shots.detect_cuts(source, float(cfg["content_threshold"]),
                                     float(cfg["min_duration_s"]))
        except Exception as exc:
            log(f"[{video_id}] SHOT DETECTION FAILED: {exc}")
            db.set_status(video_id, db.FAILED, stage="shots",
                          error_code="DETECTION_FAILED", error_detail=str(exc)[:500])
            return
        sdata = {"cuts": cuts}
        _dump(shots_path, sdata)
    cuts = sdata["cuts"]
    log(f"[{video_id}] visual cuts: {len(cuts)}")

    # ── S6 fusion ─────────────────────────────────────────────────────────
    db.set_status(video_id, db.PROCESSING, stage="fuse")
    bounds_path = work / "boundaries.json"
    bdata = _load(bounds_path)
    if bdata is None:
        boundaries = fuse.boundaries_from_cuts(cuts, duration)
        clips = fuse.to_clips(boundaries, duration, fps, cfg)
        bdata = {"boundaries": boundaries, "clips": clips}
        _dump(bounds_path, bdata)
    boundaries, clips = bdata["boundaries"], bdata["clips"]

    if not boundaries and not clips:
        log(f"[{video_id}] NO BOUNDARIES DETECTED")
        db.set_status(video_id, db.FAILED, stage="fuse",
                      error_code="NO_SHOTS_DETECTED", error_detail="no boundaries")
        return

    log(f"[{video_id}] {len(boundaries)} boundaries -> {len(clips)} clips")
    db.update_video(video_id, n_boundaries=len(boundaries))

    # ── S7/S8 cut + blur + encode ─────────────────────────────────────────
    db.set_status(video_id, db.PROCESSING, stage="encode")
    # Detected static overlays + the always-on fixed bands (clock, counter,
    # camera name) — the latter are intermittent, so calibration cannot see them.
    blur_regions = []
    if cfg.get("blur_enabled"):
        blur_regions = list(regions) + media.fixed_regions(
            cfg, int(info["width"]), int(info["height"]))
    log(f"[{video_id}] blur regions: {len(blur_regions)}")
    kept = 0
    dropped = 0
    for c in clips:
        start_ms = int(c["start_s"] * 1000)
        end_ms = int(c["end_s"] * 1000)
        clip_id = f"{video_id}_{start_ms:06d}_{end_ms:06d}"
        master = work / "clips" / f"{clip_id}.mp4"
        delivered = work / "delivered" / f"{clip_id}.mp4"

        try:
            if not master.exists():
                media.cut_master(source, master, c["start_s"], c["duration_s"])

            # Drop blank/frozen segments — intro cards, outros, bumpers.
            probe_frames, _ = media.sample_window_gray(master, 0.0,
                                                       min(c["duration_s"], 4.0))
            reason = fuse.blank_reason(probe_frames)
            if reason:
                log(f"[{video_id}] drop {clip_id}: {reason}")
                dropped += 1
                master.unlink(missing_ok=True)
                continue

            if not delivered.exists():
                media.encode_delivered(master, delivered, blur_regions, cfg)
                # A reviewer's trim was cut from the OLD delivered file. Now that
                # delivered has been re-encoded (different blur settings, say),
                # that trim is stale — re-cut it from the new one rather than
                # letting export ship the old blur.
                prev = db.get_clip(clip_id)
                segs = db.trim_segments(prev) if prev else []
                if segs:
                    from . import review
                    review.materialize(
                        prev, [(x["start_ms"] / 1000, x["end_ms"] / 1000)
                               for x in segs], cfg)
                    log(f"[{video_id}] re-cut {len(segs)} segment(s) for {clip_id}")
        except media.MediaError as exc:
            log(f"[{video_id}] clip {clip_id} encode failed: {exc}")
            continue

        db.insert_clip({
            "clip_id": clip_id,
            "youtube_video_id": video_id,
            "seq": c["seq"],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "confidence": c["confidence"],
            "flags": json.dumps(c["flags"]),
            "master_path": str(master),
            "delivered_path": str(delivered),
            "pipeline_version": config.PIPELINE_VERSION,
            "config_hash": cfg_hash,
        })
        if headless:
            db.set_decision(clip_id, db.UNREVIEWED)
        kept += 1

    db.update_video(video_id, n_clips=kept)
    # Headless runs never mark clips APPROVED — a headless batch must not be able
    # to pass as a reviewed one.
    db.set_status(video_id, db.DONE if headless else db.READY_FOR_REVIEW, stage="done")
    log(f"[{video_id}] DONE - {kept} clips kept, {dropped} dropped"
        f"{' (headless, UNREVIEWED)' if headless else ''}")

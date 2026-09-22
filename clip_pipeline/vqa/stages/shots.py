"""Visual shot detection — PySceneDetect ContentDetector at defaults.

Hard cuts between unrelated fixed-camera CCTV scenes produce an enormous content
delta, so defaults work. Per CLAUDE.md Principle 2, we do not tune thresholds.
"""

from __future__ import annotations

from scenedetect import ContentDetector, SceneManager, open_video


def detect_cuts(video_path, threshold: float, min_scene_len_s: float) -> list[float]:
    """Return cut times in seconds (the start of each scene after the first)."""
    video = open_video(str(video_path))
    fps = video.frame_rate or 25.0
    min_len_frames = max(1, int(min_scene_len_s * fps))

    manager = SceneManager()
    manager.auto_downscale = True
    manager.add_detector(
        ContentDetector(threshold=threshold, min_scene_len=min_len_frames)
    )
    manager.detect_scenes(video, show_progress=False)

    scenes = manager.get_scene_list()
    return [s[0].get_seconds() for s in scenes[1:]]

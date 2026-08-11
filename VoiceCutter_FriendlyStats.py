#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VoiceCutter
Standalone DaVinci Resolve script for macOS.

Requires FFmpeg:
    brew install ffmpeg

Expected source timeline:
    V1 + A1 only
    linked/aligned normal clips
    no Compound Clips
"""

import math
import os
import re
import shutil
import subprocess
import sys
import time

SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def round_frame(value):
    return int(math.floor(float(value) + 0.5))


def clamp(value, low, high):
    return max(low, min(high, value))


def parse_fps(value, fallback=24.0):
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else float(fallback)


def format_duration(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    seconds -= hours * 3600
    minutes = int(seconds // 60)
    seconds -= minutes * 60

    if hours:
        return "{:02d}:{:02d}:{:02d}".format(
            hours,
            minutes,
            int(round(seconds)),
        )

    return "{:02d}:{:02d}".format(
        minutes,
        int(round(seconds)),
    )


def format_frames(frames, fps):
    return format_duration(float(frames) / float(fps))


def get_resolve():
    instance = globals().get("resolve")
    if instance:
        return instance
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        sys.path.append(
            "/Library/Application Support/Blackmagic Design/"
            "DaVinci Resolve/Developer/Scripting/Modules"
        )
        import DaVinciResolveScript as dvr
    return dvr.scriptapp("Resolve")


def locate_ffmpeg():
    for path in (
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ):
        if path and os.path.isfile(path):
            return path
    raise RuntimeError("FFmpeg not found. Install with: brew install ffmpeg")


def merge_intervals(intervals, duration):
    cleaned = []
    for start, end in intervals:
        start = clamp(float(start), 0.0, float(duration))
        end = clamp(float(end), 0.0, float(duration))
        if end > start:
            cleaned.append((start, end))

    cleaned.sort()
    merged = []
    for start, end in cleaned:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(a, b) for a, b in merged]


def complement_intervals(intervals, duration):
    result = []
    cursor = 0.0
    for start, end in merge_intervals(intervals, duration):
        if start > cursor:
            result.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        result.append((cursor, duration))
    return result


def voice_from_silence(silence, duration, padding):
    raw_voice = complement_intervals(silence, duration)
    padded = [
        (start - padding, end + padding)
        for start, end in raw_voice
    ]
    return merge_intervals(padded, duration)


def parse_silence(stderr_text, duration):
    intervals = []
    open_start = None
    for line in stderr_text.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            open_start = float(start_match.group(1))

        end_match = SILENCE_END_RE.search(line)
        if end_match:
            end = float(end_match.group(1))
            intervals.append((0.0 if open_start is None else open_start, end))
            open_start = None

    if open_start is not None:
        intervals.append((open_start, duration))

    return merge_intervals(intervals, duration)


def analyze(ffmpeg, clip, threshold, minimum_silence):
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel", "info",
        "-ss", "{:.9f}".format(clip["source_start_time"]),
        "-i", clip["media_path"],
        "-t", "{:.9f}".format(clip["duration"]),
        "-map", "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        "asetpts=PTS-STARTPTS,silencedetect=noise={}dB:d={}".format(
            threshold,
            minimum_silence,
        ),
        "-f", "null",
        "-",
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return parse_silence(result.stderr, clip["duration"])


def quantize_interval(interval, duration, span_frames):
    start, end = interval
    a = round_frame((float(start) / float(duration)) * span_frames)
    b = round_frame((float(end) / float(duration)) * span_frames)
    a = int(clamp(a, 0, span_frames))
    b = int(clamp(b, 0, span_frames))
    if b <= a:
        return None
    return a, b


def collect_clips(timeline, fps):
    items = timeline.GetItemListInTrack("audio", 1) or []
    items = sorted(items, key=lambda x: float(x.GetStart()))
    clips = []

    for item in items:
        media = item.GetMediaPoolItem()
        props = media.GetClipProperty()
        media_path = props["File Path"]

        record_start = round_frame(float(item.GetStart()))
        record_end = round_frame(float(item.GetEnd()))
        record_span = record_end - record_start

        source_start = int(item.GetSourceStartFrame())
        source_end = int(item.GetSourceEndFrame())
        source_span = source_end - source_start + 1
        source_fps = parse_fps(props.get("FPS", ""), fps)

        try:
            source_start_time = float(item.GetSourceStartTime())
            source_end_time = float(item.GetSourceEndTime())
            duration = source_end_time - source_start_time
        except Exception:
            source_start_time = source_start / source_fps
            duration = source_span / source_fps

        if duration <= 0:
            source_start_time = source_start / source_fps
            duration = source_span / source_fps

        clips.append({
            "media": media,
            "media_path": media_path,
            "record_start": record_start,
            "record_end": record_end,
            "record_span": record_span,
            "source_start": source_start,
            "source_span": source_span,
            "source_start_time": source_start_time,
            "duration": duration,
        })

    return clips


def unique_timeline_name(project, name):
    names = set()
    for i in range(1, int(project.GetTimelineCount()) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            names.add(tl.GetName())

    if name not in names:
        return name

    i = 2
    while "{} {}".format(name, i) in names:
        i += 1
    return "{} {}".format(name, i)


def ask_settings():
    script = r'''
set msg to "VoiceCutter" & return & return & ¬
"THRESHOLD dB, MIN SILENCE sec, PADDING sec" & return & return & ¬
"Threshold: Recommended -25" & return & ¬
"Min Silence: Recommended 0.30" & return & ¬
"Padding: Recommended 0.12"

text returned of (display dialog msg default answer "-25, 0.30, 0.12" buttons {"Cancel", "Run"} default button "Run" with title "VoiceCutter")
'''

    try:
        value = subprocess.check_output(
            ["osascript", "-e", script],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError:
        return None

    threshold, minimum_silence, padding = [
        float(x.strip())
        for x in value.split(",")
    ]
    return threshold, minimum_silence, padding


def items_on(timeline, track_type, track):
    return sorted(
        timeline.GetItemListInTrack(track_type, track) or [],
        key=lambda x: (int(x.GetStart()), int(x.GetEnd())),
    )


def clipped_intervals(items, start, end):
    intervals = []
    for item in items:
        a = max(start, int(item.GetStart()))
        b = min(end, int(item.GetEnd()))
        if b > a:
            intervals.append((a, b))

    intervals.sort()
    merged = []
    for a, b in intervals:
        if not merged or a > merged[-1][1]:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    return [(a, b) for a, b in merged]


def gaps_between(intervals, start, end):
    gaps = []
    cursor = start
    for a, b in intervals:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def link_matching(timeline, video_track, audio_track):
    videos = items_on(timeline, "video", video_track)
    audios = items_on(timeline, "audio", audio_track)
    audio_map = {}

    for audio in audios:
        key = (int(audio.GetStart()), int(audio.GetEnd()))
        audio_map.setdefault(key, []).append(audio)

    for video in videos:
        key = (int(video.GetStart()), int(video.GetEnd()))
        matches = audio_map.get(key, [])
        if matches:
            timeline.SetClipsLinked([video, matches.pop(0)], True)


def find_item(timeline, track_type, track, start):
    matches = [
        item
        for item in (timeline.GetItemListInTrack(track_type, track) or [])
        if int(item.GetStart()) == int(start)
    ]
    if not matches:
        return None
    return max(matches, key=lambda x: int(x.GetEnd()))


def append_exact_nonvoice(timeline, media_pool, clip, track, target_start, target_end):
    rel_start = target_start - clip["record_start"]
    rel_end = target_end - clip["record_start"]

    source_start_offset = int(round(
        float(rel_start) * float(clip["source_span"]) / float(clip["record_span"])
    ))
    source_end_offset = int(round(
        float(rel_end) * float(clip["source_span"]) / float(clip["record_span"])
    ))

    source_start_offset = max(
        0,
        min(source_start_offset, clip["source_span"] - 1),
    )
    source_end_offset = max(
        source_start_offset + 1,
        min(source_end_offset, clip["source_span"]),
    )

    for _ in range(40):
        info = {
            "mediaPoolItem": clip["media"],
            "startFrame": clip["source_start"] + source_start_offset,
            "endFrame": clip["source_start"] + source_end_offset - 1,
            "trackIndex": track,
            "recordFrame": target_start,
        }

        result = media_pool.AppendToTimeline([info])
        if not result:
            source_end_offset += 1
            continue

        video = find_item(timeline, "video", track, target_start)
        audio = find_item(timeline, "audio", track, target_start)

        if video and int(video.GetEnd()) == target_end:
            if audio:
                timeline.SetClipsLinked([video, audio], True)
            return

        delete_items = [x for x in (video, audio) if x is not None]
        actual_end = int(video.GetEnd()) if video else target_start

        if delete_items:
            timeline.DeleteClips(delete_items, False)

        error = target_end - actual_end
        step = max(
            1,
            int(round(
                float(abs(error)) * float(clip["source_span"]) / float(clip["record_span"])
            )),
        )

        if error > 0:
            source_end_offset += step
        else:
            source_end_offset -= step

        source_end_offset = max(source_start_offset + 1, source_end_offset)
        source_end_offset = min(source_end_offset, clip["source_span"] + 4)


def main():
    started = time.perf_counter()

    settings = ask_settings()
    if settings is None:
        return

    threshold, minimum_silence, padding = settings

    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    original = project.GetCurrentTimeline()

    fps = parse_fps(
        original.GetSetting("timelineFrameRate"),
        24.0,
    )

    clips = collect_clips(
        original,
        fps,
    )

    ffmpeg = locate_ffmpeg()

    total_frames = sum(
        clip["record_span"]
        for clip in clips
    )

    print("")
    print("VoiceCutter")
    print("Timeline: {}".format(original.GetName()))
    print("Clips: {}".format(len(clips)))
    print("Duration: {}".format(format_frames(total_frames, fps)))
    print(
        "Settings: {:.0f} dB · {:.2f} s · {:.2f} s".format(
            threshold,
            minimum_silence,
            padding,
        )
    )
    print("")
    print("Analyzing audio...")

    for index, clip in enumerate(clips, 1):
        silence = analyze(
            ffmpeg,
            clip,
            threshold,
            minimum_silence,
        )

        clip["voice"] = voice_from_silence(
            silence,
            clip["duration"],
            padding,
        )

        print(
            "[{}/{}] {} · {} · {} silence regions".format(
                index,
                len(clips),
                os.path.basename(clip["media_path"]),
                format_frames(clip["record_span"], fps),
                len(silence),
            )
        )

    timeline_name = "{}_{}".format(
        original.GetName(),
        time.strftime("%Y%m%d%H%M%S"),
    )

    timeline = original.DuplicateTimeline(
        timeline_name
    )

    project.SetCurrentTimeline(
        timeline
    )

    subtype = str(
        timeline.GetTrackSubType(
            "audio",
            1,
        )
    ).lower()

    timeline.AddTrack(
        "audio",
        subtype,
    )
    a_voice = int(
        timeline.GetTrackCount("audio")
    )

    timeline.AddTrack(
        "audio",
        subtype,
    )
    a_silence = int(
        timeline.GetTrackCount("audio")
    )

    timeline.AddTrack("video")
    v_voice = int(
        timeline.GetTrackCount("video")
    )

    timeline.AddTrack("video")
    v_silence = int(
        timeline.GetTrackCount("video")
    )

    timeline.SetTrackName(
        "audio",
        a_voice,
        "VOICE",
    )
    timeline.SetTrackName(
        "video",
        v_voice,
        "VOICE",
    )
    timeline.SetTrackName(
        "audio",
        a_silence,
        "SILENCE",
    )
    timeline.SetTrackName(
        "video",
        v_silence,
        "SILENCE",
    )

    media_pool = project.GetMediaPool()

    print("")
    print("Building VOICE...")

    for clip in clips:
        for interval in clip["voice"]:
            record_bounds = quantize_interval(
                interval,
                clip["duration"],
                clip["record_span"],
            )

            source_bounds = quantize_interval(
                interval,
                clip["duration"],
                clip["source_span"],
            )

            if not record_bounds or not source_bounds:
                continue

            r0, r1 = record_bounds
            s0, s1 = source_bounds

            media_pool.AppendToTimeline([{
                "mediaPoolItem": clip["media"],
                "startFrame": clip["source_start"] + s0,
                "endFrame": clip["source_start"] + s1 - 1,
                "trackIndex": v_voice,
                "recordFrame": clip["record_start"] + r0,
            }])

    link_matching(
        timeline,
        v_voice,
        a_voice,
    )

    voice_items = items_on(
        timeline,
        "video",
        v_voice,
    )

    voice_frames = sum(
        int(item.GetEnd()) - int(item.GetStart())
        for item in voice_items
    )

    print(
        "{} segments · {}".format(
            len(voice_items),
            format_frames(voice_frames, fps),
        )
    )

    print("")
    print("Building SILENCE...")

    for clip in clips:
        voice_parts = clipped_intervals(
            voice_items,
            clip["record_start"],
            clip["record_end"],
        )

        for gap_start, gap_end in gaps_between(
            voice_parts,
            clip["record_start"],
            clip["record_end"],
        ):
            append_exact_nonvoice(
                timeline,
                media_pool,
                clip,
                v_silence,
                gap_start,
                gap_end,
            )

    link_matching(
        timeline,
        v_voice,
        a_voice,
    )

    link_matching(
        timeline,
        v_silence,
        a_silence,
    )

    silence_items = items_on(
        timeline,
        "video",
        v_silence,
    )

    silence_frames = sum(
        int(item.GetEnd()) - int(item.GetStart())
        for item in silence_items
    )

    print(
        "{} segments · {}".format(
            len(silence_items),
            format_frames(silence_frames, fps),
        )
    )

    print("")
    print("Cleaning timeline...")

    source_items = (
        (timeline.GetItemListInTrack("video", 1) or [])
        + (timeline.GetItemListInTrack("audio", 1) or [])
    )

    if source_items:
        timeline.DeleteClips(
            source_items,
            False,
        )

    timeline.DeleteTrack(
        "video",
        1,
    )

    timeline.DeleteTrack(
        "audio",
        1,
    )

    timeline.SetTrackName(
        "video",
        1,
        "VOICE",
    )

    timeline.SetTrackName(
        "audio",
        1,
        "VOICE",
    )

    timeline.SetTrackName(
        "video",
        2,
        "SILENCE",
    )

    timeline.SetTrackName(
        "audio",
        2,
        "SILENCE",
    )

    generated_frames = (
        voice_frames
        + silence_frames
    )

    voice_percent = (
        100.0 * voice_frames / generated_frames
        if generated_frames
        else 0.0
    )

    silence_percent = (
        100.0 * silence_frames / generated_frames
        if generated_frames
        else 0.0
    )

    elapsed = (
        time.perf_counter()
        - started
    )

    print("")
    print(
        "Done · {} total · {} voice ({:.0f}%) · {} silence ({:.0f}%) · "
        "{} voice segments · {} silence segments · {} clips · {:.1f}s processing".format(
            format_frames(generated_frames, fps),
            format_frames(voice_frames, fps),
            voice_percent,
            format_frames(silence_frames, fps),
            silence_percent,
            len(voice_items),
            len(silence_items),
            len(clips),
            elapsed,
        )
    )


if __name__ == "__main__":
    main()

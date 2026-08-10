exec(r"""
import os
import subprocess
import time

HELPER = "/Users/tutorials/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Ripple Silence/Ripple Silence.py"


def run():

    if not os.path.isfile(HELPER):
        raise RuntimeError("Ripple Silence helper script not found: " + HELPER)

    ns = {"__name__": "ripple_lib", "resolve": resolve}
    exec(open(HELPER, encoding="utf-8").read(), ns)
    RS = ns


    # ---------------------------------------------------------
    # SETTINGS
    # ---------------------------------------------------------

    apple_script = r'''
set msg to "Ripple Silence Settings" & return & return & ¬
"THRESHOLD dB, MIN SILENCE sec, PADDING sec" & return & return & ¬
"THRESHOLD: quieter than this counts as silence. Recommended: -35" & return & ¬
"MIN SILENCE: shortest quiet section. Recommended: 0.35" & return & ¬
"PADDING: extra material kept with VOICE. Recommended: 0.12" & return & return & ¬
"NON-VOICE is created only from the actual holes Resolve leaves in VOICE." & return & ¬
"Track 2 + Track 3 therefore use the same boundaries."

text returned of (display dialog msg default answer "-35, 0.35, 0.12" buttons {"Cancel", "Run"} default button "Run" with title "Ripple Silence")
'''

    try:
        result = subprocess.check_output(
            ["osascript", "-e", apple_script],
            text=True,
            stderr=subprocess.STDOUT
        ).strip()

    except subprocess.CalledProcessError:
        print("Cancelled.")
        return


    try:
        values = [
            float(x.strip())
            for x in result.split(",")
        ]

        if len(values) != 3:
            raise ValueError

        threshold_db, minimum_silence, padding = values

    except Exception:
        raise RuntimeError(
            "Use exactly 3 numbers, e.g. -35, 0.35, 0.12"
        )


    if threshold_db >= 0:
        raise RuntimeError("Threshold must be negative.")

    if minimum_silence <= 0:
        raise RuntimeError("Minimum silence must be > 0.")

    if padding < 0:
        raise RuntimeError("Padding cannot be negative.")


    # ---------------------------------------------------------
    # PROJECT
    # ---------------------------------------------------------

    pm = resolve.GetProjectManager()

    project = (
        pm.GetCurrentProject()
        if pm else None
    )

    original = (
        project.GetCurrentTimeline()
        if project else None
    )


    if not project or not original:
        raise RuntimeError(
            "Open a project and timeline first."
        )


    if (
        int(original.GetTrackCount("audio")) != 1
        or
        int(original.GetTrackCount("video")) != 1
    ):
        raise RuntimeError(
            "Run this from a clean timeline containing only V1 + A1."
        )


    fps = RS["_parse_fps"](
        original.GetSetting("timelineFrameRate"),
        24.0
    )


    subtype = str(
        original.GetTrackSubType(
            "audio",
            1
        )
    ).lower()


    clips, problems = RS["collect_clip_info"](
        original,
        1,
        fps
    )


    if problems:
        raise RuntimeError(
            "; ".join(problems)
            +
            ". If this is a Compound Clip, use Render in Place first."
        )


    # IMPORTANT:
    # use actual timeline start/end.
    # Do not reconstruct end from rounded duration.

    for clip in clips:

        clip["record_start_exact"] = RS["_round_frame"](
            float(
                clip["item"].GetStart()
            )
        )

        clip["record_end_exact"] = RS["_round_frame"](
            float(
                clip["item"].GetEnd()
            )
        )


    ffmpeg = RS["locate_ffmpeg"]()


    print(
        "Settings: %.1f dB | %.3f s | %.3f s"
        %
        (
            threshold_db,
            minimum_silence,
            padding
        )
    )


    # ---------------------------------------------------------
    # 1. ANALYSE + CREATE VOICE DEFINITION
    # ---------------------------------------------------------

    for i, clip in enumerate(
        clips,
        1
    ):

        print(
            "Analysing %d/%d: %s"
            %
            (
                i,
                len(clips),
                clip["name"]
            )
        )


        silence = RS["analyze_with_ffmpeg"](
            ffmpeg,
            clip["media_path"],
            clip["source_start_time"],
            clip["duration"],
            threshold_db,
            minimum_silence,
            0
        )


        voice, ignored = RS["classify_intervals"](
            silence,
            clip["duration"],
            padding
        )


        clip["voice_intervals"] = voice


    # ---------------------------------------------------------
    # 2. DUPLICATE
    # ---------------------------------------------------------

    name = RS["_unique_timeline_name"](
        project,
        original.GetName()
        +
        " - VOICE SPLIT"
    )


    tl = original.DuplicateTimeline(
        name
    )


    if not tl:
        raise RuntimeError(
            "Could not duplicate timeline."
        )


    if not project.SetCurrentTimeline(
        tl
    ):
        raise RuntimeError(
            "Could not switch to duplicated timeline."
        )


    time.sleep(0.4)


    # ---------------------------------------------------------
    # 3. TRACKS
    # ---------------------------------------------------------

    if not tl.AddTrack(
        "audio",
        subtype
    ):
        raise RuntimeError(
            "Could not create A2 VOICE."
        )

    a_voice = int(
        tl.GetTrackCount("audio")
    )


    if not tl.AddTrack(
        "audio",
        subtype
    ):
        raise RuntimeError(
            "Could not create A3 NON-VOICE."
        )

    a_non = int(
        tl.GetTrackCount("audio")
    )


    if not tl.AddTrack("video"):
        raise RuntimeError(
            "Could not create V2 VOICE."
        )

    v_voice = int(
        tl.GetTrackCount("video")
    )


    if not tl.AddTrack("video"):
        raise RuntimeError(
            "Could not create V3 NON-VOICE."
        )

    v_non = int(
        tl.GetTrackCount("video")
    )


    if (
        a_voice != v_voice
        or
        a_non != v_non
    ):
        raise RuntimeError(
            "Generated audio/video track numbers do not match."
        )


    tl.SetTrackName(
        "audio",
        a_voice,
        "VOICE"
    )

    tl.SetTrackName(
        "video",
        v_voice,
        "VOICE"
    )

    tl.SetTrackName(
        "audio",
        a_non,
        "NON-VOICE"
    )

    tl.SetTrackName(
        "video",
        v_non,
        "NON-VOICE"
    )


    for track_type, track_index in [

        ("audio", a_voice),
        ("video", v_voice),

        ("audio", a_non),
        ("video", v_non)

    ]:

        try:
            tl.SetTrackLock(
                track_type,
                track_index,
                False
            )
        except Exception:
            pass


        try:
            tl.SetTrackEnable(
                track_type,
                track_index,
                True
            )
        except Exception:
            pass


    time.sleep(0.3)


    media_pool = project.GetMediaPool()


    # ---------------------------------------------------------
    # HELPERS
    # ---------------------------------------------------------

    def items_on(
        track_type,
        index
    ):

        return sorted(

            tl.GetItemListInTrack(
                track_type,
                index
            )
            or
            [],

            key=lambda x:
                (
                    int(x.GetStart()),
                    int(x.GetEnd())
                )

        )


    def find_item_at(
        track_type,
        track_index,
        start_frame
    ):

        matches = [

            x

            for x in (
                tl.GetItemListInTrack(
                    track_type,
                    track_index
                )
                or
                []
            )

            if int(
                x.GetStart()
            )
            ==
            int(
                start_frame
            )

        ]


        if not matches:
            return None


        return max(
            matches,
            key=lambda x:
                int(
                    x.GetEnd()
                )
        )


    def clipped_intervals(
        items,
        start,
        end
    ):

        out = []


        for item in items:

            a = max(
                start,
                int(item.GetStart())
            )

            b = min(
                end,
                int(item.GetEnd())
            )


            if b > a:

                out.append(
                    (
                        a,
                        b
                    )
                )


        out.sort()


        merged = []


        for a, b in out:

            if (
                not merged
                or
                a > merged[-1][1]
            ):

                merged.append(
                    [
                        a,
                        b
                    ]
                )

            else:

                merged[-1][1] = max(
                    merged[-1][1],
                    b
                )


        return [
            (
                a,
                b
            )
            for a, b in merged
        ]


    def complement_absolute(
        intervals,
        start,
        end
    ):

        gaps = []

        cursor = start


        for a, b in intervals:

            if a > cursor:

                gaps.append(
                    (
                        cursor,
                        a
                    )
                )


            cursor = max(
                cursor,
                b
            )


        if cursor < end:

            gaps.append(
                (
                    cursor,
                    end
                )
            )


        return gaps


    def link_matching(
        v_track,
        a_track
    ):

        videos = items_on(
            "video",
            v_track
        )

        audios = items_on(
            "audio",
            a_track
        )


        audio_map = {}


        for a in audios:

            key = (
                int(a.GetStart()),
                int(a.GetEnd())
            )

            audio_map.setdefault(
                key,
                []
            ).append(
                a
            )


        linked = 0


        for v in videos:

            key = (
                int(v.GetStart()),
                int(v.GetEnd())
            )


            matches = audio_map.get(
                key,
                []
            )


            if matches:

                a = matches.pop(
                    0
                )


                if tl.SetClipsLinked(
                    [
                        v,
                        a
                    ],
                    True
                ):

                    linked += 1


        return linked


    def delete_generated(
        v_item,
        a_item
    ):

        items = [

            x

            for x in (
                v_item,
                a_item
            )

            if x is not None

        ]


        if not items:
            return


        if not tl.DeleteClips(
            items,
            False
        ):

            raise RuntimeError(
                "Could not remove temporary generated segment."
            )


        time.sleep(0.01)


    # ---------------------------------------------------------
    # 4. BUILD TRACK 2 FIRST
    # ---------------------------------------------------------

    print(
        "Building VOICE first..."
    )


    for clip in clips:

        for interval in clip[
            "voice_intervals"
        ]:


            rec = RS[
                "quantize_interval"
            ](
                interval,
                clip["duration"],
                clip["record_span"]
            )


            src = RS[
                "quantize_interval"
            ](
                interval,
                clip["duration"],
                clip["source_span"]
            )


            if not rec or not src:
                continue


            r0, r1 = rec
            s0, s1 = src


            info = {

                "mediaPoolItem":
                    clip[
                        "media_pool_item"
                    ],

                "startFrame":
                    int(
                        clip[
                            "source_start_frame"
                        ]
                        +
                        s0
                    ),

                "endFrame":
                    int(
                        clip[
                            "source_start_frame"
                        ]
                        +
                        s1
                        -
                        1
                    ),

                "trackIndex":
                    int(
                        v_voice
                    ),

                "recordFrame":
                    int(
                        clip[
                            "record_start"
                        ]
                        +
                        r0
                    )
            }


            result = media_pool.AppendToTimeline(
                [
                    info
                ]
            )


            if not result:

                raise RuntimeError(
                    "Resolve rejected a VOICE segment."
                )


    time.sleep(
        0.15
    )


    link_matching(
        v_voice,
        a_voice
    )


    # ---------------------------------------------------------
    # 5. READ ACTUAL HOLES IN TRACK 2
    # ---------------------------------------------------------

    voice_items = items_on(
        "video",
        v_voice
    )


    gaps_to_build = []


    for clip in clips:

        # IMPORTANT:
        # GetStart/GetEnd from original itself.

        record_start = int(
            clip[
                "record_start_exact"
            ]
        )

        record_end = int(
            clip[
                "record_end_exact"
            ]
        )


        actual_voice = clipped_intervals(
            voice_items,
            record_start,
            record_end
        )


        gaps = complement_absolute(
            actual_voice,
            record_start,
            record_end
        )


        for gap_start, gap_end in gaps:

            gaps_to_build.append(
                (
                    clip,
                    gap_start,
                    gap_end
                )
            )


    # ---------------------------------------------------------
    # 6. TRACK 3 = ACTUAL HOLES OF TRACK 2
    # ---------------------------------------------------------

    def append_exact_gap(
        clip,
        target_start,
        target_end
    ):

        target_start = int(
            target_start
        )

        target_end = int(
            target_end
        )


        if target_end <= target_start:
            return


        rel_start = (
            target_start
            -
            int(
                clip[
                    "record_start_exact"
                ]
            )
        )


        rel_end = (
            target_end
            -
            int(
                clip[
                    "record_start_exact"
                ]
            )
        )


        source_start_offset = int(
            round(
                float(rel_start)
                *
                float(
                    clip[
                        "source_span"
                    ]
                )
                /
                float(
                    clip[
                        "record_span"
                    ]
                )
            )
        )


        source_end_offset = int(
            round(
                float(rel_end)
                *
                float(
                    clip[
                        "source_span"
                    ]
                )
                /
                float(
                    clip[
                        "record_span"
                    ]
                )
            )
        )


        source_start_offset = max(

            0,

            min(
                source_start_offset,
                int(
                    clip[
                        "source_span"
                    ]
                )
                -
                1
            )

        )


        source_end_offset = max(

            source_start_offset
            +
            1,

            min(
                source_end_offset,
                int(
                    clip[
                        "source_span"
                    ]
                )
            )

        )


        attempted = set()


        for attempt in range(
            1,
            40
        ):


            key = (
                source_start_offset,
                source_end_offset
            )


            if key in attempted:

                source_end_offset += 1


            attempted.add(
                (
                    source_start_offset,
                    source_end_offset
                )
            )


            info = {

                "mediaPoolItem":
                    clip[
                        "media_pool_item"
                    ],

                "startFrame":
                    int(
                        clip[
                            "source_start_frame"
                        ]
                        +
                        source_start_offset
                    ),

                "endFrame":
                    int(
                        clip[
                            "source_start_frame"
                        ]
                        +
                        source_end_offset
                        -
                        1
                    ),

                "trackIndex":
                    int(
                        v_non
                    ),

                "recordFrame":
                    int(
                        target_start
                    )

            }


            result = media_pool.AppendToTimeline(
                [
                    info
                ]
            )


            # IMPORTANT:
            #
            # For a one-frame hole Resolve may reject the first
            # source endpoint representation.
            #
            # Instead of crashing, move the endpoint one frame
            # further and try again.

            if not result:

                source_end_offset += 1

                continue


            time.sleep(
                0.01
            )


            v_item = find_item_at(
                "video",
                v_non,
                target_start
            )


            a_item = find_item_at(
                "audio",
                a_non,
                target_start
            )


            if not v_item:

                raise RuntimeError(
                    "NON-VOICE video item was not created."
                )


            actual_end = int(
                v_item.GetEnd()
            )


            # EXACT MATCH

            if actual_end == target_end:

                if a_item:

                    tl.SetClipsLinked(
                        [
                            v_item,
                            a_item
                        ],
                        True
                    )


                return


            # Wrong length.
            # Delete and adjust source endpoint.

            delete_generated(
                v_item,
                a_item
            )


            error = (
                target_end
                -
                actual_end
            )


            if error > 0:

                step = max(

                    1,

                    int(
                        round(
                            float(error)
                            *
                            float(
                                clip[
                                    "source_span"
                                ]
                            )
                            /
                            float(
                                clip[
                                    "record_span"
                                ]
                            )
                        )
                    )

                )


                source_end_offset += (
                    step
                )


            else:

                step = max(

                    1,

                    int(
                        round(
                            float(
                                abs(error)
                            )
                            *
                            float(
                                clip[
                                    "source_span"
                                ]
                            )
                            /
                            float(
                                clip[
                                    "record_span"
                                ]
                            )
                        )
                    )

                )


                source_end_offset -= (
                    step
                )


            source_end_offset = max(

                source_start_offset
                +
                1,

                source_end_offset

            )


            # Allow a few endpoint representations beyond
            # source_span because Resolve's endFrame convention
            # can otherwise make the final 1-frame tail impossible.

            source_end_offset = min(

                source_end_offset,

                int(
                    clip[
                        "source_span"
                    ]
                )
                +
                4

            )


        raise RuntimeError(
            "Could not make NON-VOICE exactly fill Track-2 gap %d-%d."
            %
            (
                target_start,
                target_end
            )
        )


    print(
        "Building NON-VOICE from actual VOICE cuts..."
    )


    for (
        clip,
        gap_start,
        gap_end
    ) in gaps_to_build:


        append_exact_gap(
            clip,
            gap_start,
            gap_end
        )


    # ---------------------------------------------------------
    # 7. LINK
    # ---------------------------------------------------------

    original_links = link_matching(
        1,
        1
    )


    voice_links = link_matching(
        v_voice,
        a_voice
    )


    non_links = link_matching(
        v_non,
        a_non
    )


    # ---------------------------------------------------------
    # 8. FINAL HARD CHECK
    # ---------------------------------------------------------

    voice_items = items_on(
        "video",
        v_voice
    )


    non_items = items_on(
        "video",
        v_non
    )


    for clip in clips:


        start = int(
            clip[
                "record_start_exact"
            ]
        )


        end = int(
            clip[
                "record_end_exact"
            ]
        )


        voice_parts = clipped_intervals(
            voice_items,
            start,
            end
        )


        non_parts = clipped_intervals(
            non_items,
            start,
            end
        )


        combined = [

            (
                a,
                b,
                "VOICE"
            )

            for a, b
            in voice_parts

        ] + [

            (
                a,
                b,
                "NON-VOICE"
            )

            for a, b
            in non_parts

        ]


        combined.sort(
            key=lambda x:
                (
                    x[0],
                    x[1]
                )
        )


        cursor = start


        for a, b, label in combined:


            if a > cursor:

                raise RuntimeError(
                    "FINAL CHECK FAILED: gap %d-%d."
                    %
                    (
                        cursor,
                        a
                    )
                )


            if a < cursor:

                raise RuntimeError(
                    "FINAL CHECK FAILED: overlap at frame %d (%s)."
                    %
                    (
                        a,
                        label
                    )
                )


            cursor = b


        if cursor != end:

            raise RuntimeError(
                "FINAL CHECK FAILED: ends at %d, original ends at %d."
                %
                (
                    cursor,
                    end
                )
            )


    print("")
    print("DONE")

    print(
        "V1 <-> A1 ORIGINAL:",
        original_links
    )

    print(
        "V%d <-> A%d VOICE: %d"
        %
        (
            v_voice,
            a_voice,
            voice_links
        )
    )

    print(
        "V%d <-> A%d NON-VOICE: %d"
        %
        (
            v_non,
            a_non,
            non_links
        )
    )

    print(
        "FINAL CHECK: no gap, no overlap."
    )


run()
""")
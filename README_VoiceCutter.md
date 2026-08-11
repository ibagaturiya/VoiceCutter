# VoiceCutter

VoiceCutter is a DaVinci Resolve script for macOS that separates speech into a **VOICE** track and everything else into a **NON-VOICE** track.

## Install

1. Install FFmpeg once:
   ```bash
   brew install ffmpeg
   ```
2. Put `VoiceCutter.py` in:
   ```text
   /Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Comp/
   ```
3. Restart DaVinci Resolve.

## Use

Open your timeline, then run **Workspace → Scripts → Comp → VoiceCutter**.

The timeline must contain exactly one linked video/audio track pair, no empty extra tracks, and no Compound Clips; multiple normal clips are supported.

## Settings

**Threshold** — audio quieter than this is treated as silence.

**Min Silence** — silence must last at least this long before it is removed from VOICE.

**Padding** — keeps extra audio/video around detected speech so cuts are not too tight.

Recommended defaults:

```text
-25, 0.30, 0.12
```

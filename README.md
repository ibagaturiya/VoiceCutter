# VoiceCutter
Davinci
# VoiceCutter

VoiceCutter is a DaVinci Resolve script that automatically separates spoken sections from silent / non-voice sections while keeping the original timing intact.

It creates:

- **Original** — untouched source
- **VOICE** — only detected speech
- **NON-VOICE** — the exact remaining parts

Video and audio stay linked.

## Compatibility

- Tested on **macOS only**
- DaVinci Resolve
- Requires **FFmpeg**

## Installation

1. Download `VoiceCutter.py`.
2. Copy it into:

```text
/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/
```

3. Restart DaVinci Resolve.
4. Open:

```text
Workspace → Scripts → Comp → VoiceCutter
```

## Timeline requirements

Before running VoiceCutter:

- Use exactly **one video track and one linked audio track**
- Video and audio must be linked
- No empty extra tracks
- No Compound Clips
- Compound Clips must be **Render in Place** first
- Multiple normal clips on the same V1/A1 tracks are supported

Recommended starting layout:

```text
V1  Video
A1  Linked Audio
```

## Settings

VoiceCutter asks for three values:

### Threshold
Audio below this level is considered silence.

Recommended:

```text
-35 dB
```

### Minimum Silence
Minimum duration that audio must remain quiet before it is treated as silence.

Recommended:

```text
0.35 s
```

### Padding
Keeps additional material around spoken sections so words are not cut too tightly.

Recommended:

```text
0.12 s
```

## Output

VoiceCutter duplicates the timeline and creates:

```text
V3  NON-VOICE
V2  VOICE
V1  ORIGINAL

A3  NON-VOICE
A2  VOICE
A1  ORIGINAL
```

The generated VOICE and NON-VOICE tracks use matching boundaries:

```text
VOICE + NON-VOICE = ORIGINAL
```

There should be no overlap and no missing gap between the generated tracks.

## FFmpeg

VoiceCutter uses FFmpeg for silence detection.

With Homebrew:

```bash
brew install ffmpeg
```

If Homebrew is not installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then:

```bash
brew install ffmpeg
```

## Notes

- Keep the original V1/A1 tracks as a reference.
- Render Compound Clips in Place before running VoiceCutter.
- For best results, start with the recommended detection settings and adjust only if necessary.

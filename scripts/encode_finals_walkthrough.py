"""Encode recorded frames with true per-frame timing (no dropped-frame speedup).

Usage (inside `revguard-recorder:20260918` on 10.10.10.202):

    SPEED=1.43 python scripts/encode_finals_walkthrough.py \
        /frames /out/finals.mp4 /out/title-in.png /out/title-out.png

Reads `frame-times.json` (seconds of wall clock at each grab) and builds an
ffmpeg concat list whose `duration` is the real gap to the next frame, so the
rendered mp4 lasts as long as the recording really took.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

frames_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/frames")
out = Path(sys.argv[2] if len(sys.argv) > 2 else "/out/finals.mp4")
speed = float(os.environ.get("SPEED", "1.0"))
title_in = Path(sys.argv[3]) if len(sys.argv) > 3 else None
title_out = Path(sys.argv[4]) if len(sys.argv) > 4 else None

times = json.loads((frames_dir / "frame-times.json").read_text())
count = len(times)
if count < 2:
    raise SystemExit("need at least two frames")
durations = [max(0.05, times[i + 1] - times[i]) for i in range(count - 1)]
tail = statistics.median(durations)
durations.append(tail)

lines = ["ffconcat version 1.0"]
if title_in:
    lines.append(f"file '{title_in}'")
    lines.append("duration 3.000")
total = 3.0 if title_in else 0.0
for index, duration in enumerate(durations, start=1):
    name = frames_dir / f"frame-{index:06d}.png"
    if not name.exists():
        raise SystemExit(f"missing frame {name}")
    lines.append(f"file '{name}'")
    lines.append(f"duration {duration:.3f}")
    total += duration
# concat demuxer needs the last file repeated for the final duration to apply
lines.append(f"file '{frames_dir / f'frame-{count:06d}.png'}'")
if title_out:
    lines.append(f"file '{title_out}'")
    lines.append("duration 4.000")
    lines.append(f"file '{title_out}'")
    total += 4.0

list_path = frames_dir / "concat.txt"
list_path.write_text("\n".join(lines) + "\n")
print(
    f"frames={count} timeline={total:.1f}s speed={speed:g}x final={total / speed:.1f}s -> {out}",
    flush=True,
)

subprocess.run([
    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
    "-f", "concat", "-safe", "0", "-i", str(list_path),
    "-vf", f"format=yuv420p,setpts=PTS/{speed:g}", "-r", "30",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
    "-movflags", "+faststart", str(out),
], check=True)
print("encoded", out, flush=True)

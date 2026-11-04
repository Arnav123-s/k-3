from pathlib import Path
import os
import subprocess

os.chdir(Path(__file__).resolve().parent)
ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
cards = [
    ["Hard development suite | W4-o", "Glimmer alone       8 / 12 after rescoring",
     "Glimmer + Kuttaka  12 / 12", "53 model calls | 79 / 79 private cases"],
    ["Graded and growth development suites", "Graded   18 / 18 with Kuttaka | 16 / 18 alone",
     "Growth   12 / 12 with memory", "Later growth run   12 / 12 in 59 calls"],
]
filters = ["[0:v]trim=end=110,setpts=PTS-STARTPTS[demo]"]
for index, rows in enumerate(cards, 1):
    overlays = []
    for row, line in enumerate(rows):
        start = [0, 0.25, 0.85, 1.45][row]
        color = "0x236647" if row == 2 else "0x282421"
        overlays.append(
            f"drawtext=fontfile=font.ttf:text='{line}':fontsize={36 if row == 0 else 31}:"
            f"fontcolor={color}:x=80:y={126 + 88 * row}:enable='gte(t,{start})'"
        )
    filters.append(f"[{index}:v]{','.join(overlays)}[card{index}]")
filters += [
    "[3:v]scale=1280:720,fps=24,setsar=1,setpts=PTS-STARTPTS[ending]",
    "[demo][card1][card2][ending]concat=n=4:v=1:a=0[v]",
]
card_input = ["-f", "lavfi", "-i", "color=c=0xf3ecdf:s=1280x720:r=24:d=6"]
subprocess.run([
    ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", "kuttaka3-capture-cut.mp4",
    *card_input, *card_input, "-i", "part_c.mp4", "-filter_complex", ";".join(filters),
    "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
    "-movflags", "+faststart", "kuttaka3-tight-cut.mp4",
], check=True)

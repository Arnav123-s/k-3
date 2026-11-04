from pathlib import Path
import os, subprocess
root=Path(__file__).resolve().parent
os.chdir(root)
ffmpeg=os.environ.get('FFMPEG','ffmpeg')
def ff(*args):
 subprocess.run([ffmpeg,'-nostdin','-y','-loglevel','error',*args],check=True)
def card(name,seconds,title,lines):
 filters = []
 for index, line in enumerate([title] + [line for line in lines if line]):
  path = Path(f'{name}-{index}.txt')
  path.write_text(line, encoding='utf-8')
  size = 32 if index == 0 else 27
  filters.append(f'drawtext=fontfile=font.ttf:textfile={path.name}:fontcolor=0x282421:fontsize={size}:x=80:y={112 + 65 * index}')
 ff('-f','lavfi','-i',f'color=c=0xf3ecdf:s=1280x720:r=24:d={seconds}',
    '-vf',','.join(filters),
    '-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p',name+'.mp4')
ff('-i','sqrtmod-terminal-replay.mp4','-an','-vf',
   "setpts=PTS*0.478,scale=1160:652,pad=1280:720:60:44,fps=24,setsar=1,tpad=stop_mode=clone:stop_duration=2,drawtext=fontfile=font.ttf:text='01  Modular square roots | Accelerated terminal replay':fontsize=23:fontcolor=white:box=1:boxcolor=black@0.85:x=60:y=9",
   '-t','22','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','demo-sqrt.mp4')
card('demo-drag-plan',6,'02  Physics | Drag repair trace',[
 'W4-o / h-r1-drag / Glimmer + Kuttaka',
 'The reference and a blind re-derivation',
 'both rejected the first method.',
 'Kuttaka refuted that method and tried again.'])
card('demo-drag-score',7,'02  Physics | Verified result',[
 'The repaired method passed 5 of 5 private cases.',
 '15 model calls for this task.',
 'Source: W4-o console log and per-task report.'])
card('demo-spark',13,'03  LiveCodeBench | Recorded Spark checkpoint',[
 '15:52:22 UTC  |  November 4, 2026',
 '',
 'Spark alone             9 / 12   |   498 calls',
 'Spark + Kuttaka        10 / 12   |   559 calls',
 '',
 'Snapshot of an in-progress run. Calls dispatched.',
 'Separate from the Glimmer development suite.'])
ff('-i','kuttaka3-film.mp4','-i','demo-sqrt.mp4','-i','demo-drag-plan.mp4','-i','demo-drag-score.mp4','-i','demo-spark.mp4',
   '-filter_complex','[0:v]trim=end=22,setpts=PTS-STARTPTS[a];[0:v]trim=start=70,setpts=PTS-STARTPTS[b];[a][1:v][2:v][3:v][4:v][b]concat=n=6:v=1:a=0[v]',
   '-map','[v]','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-movflags','+faststart','kuttaka3-demo-expanded.mp4')


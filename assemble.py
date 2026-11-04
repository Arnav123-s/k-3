from pathlib import Path
import os, subprocess, sys
here=Path(__file__).resolve().parent
os.chdir(here)
ff=os.environ.get('FFMPEG','ffmpeg')
if not Path('font.ttf').exists():
 import shutil
 font = os.environ.get('KUTTAKA_VIDEO_FONT') or next((str(p) for p in [
     Path('C:/Windows/Fonts/consola.ttf'),
     Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')
 ] if p.exists()), '')
 if not font:
  raise SystemExit('Set KUTTAKA_VIDEO_FONT to a local TrueType font.')
 shutil.copyfile(font,'font.ttf')
def run(*args):
 subprocess.run([ff,'-nostdin','-y','-loglevel','error',*args],check=True)
def clip(src,dst,length,extra=''):
 filt='scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1,tpad=stop_mode=clone:stop_duration=48'
 if extra: filt+=','+extra
 run('-i',src,'-an','-vf',filt,'-t',str(length),'-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p',dst)
def card(name,title,body,secs):
 Path(name+'.txt').write_text(title+'\n\n'+body,encoding='utf-8')
 run('-f','lavfi','-i',f'color=c=0xf3ecdf:s=1280x720:r=24:d={secs}','-vf',f'drawtext=fontfile=font.ttf:textfile={name}.txt:fontcolor=0x282421:fontsize=34:line_spacing=20:x=(w-tw)/2:y=(h-th)/2','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p',name+'.mp4')
clip('part_a.mp4','intro.mp4',22)
live=sys.argv[1] if len(sys.argv)>1 else 'sqrtmod-terminal-replay.mp4'
if len(sys.argv)>1:
 result=subprocess.run([ff,'-hide_banner','-i',live],capture_output=True,text=True)
 import re
 h,m,s=map(float,re.search(r'Duration: (\d+):(\d+):([\d.]+)',result.stderr).groups())
 speed=max(1,(h*3600+m*60+s)/48)
 clip(live,'live.mp4',48,f'setpts=PTS/{speed},fps=24,tpad=stop_mode=clone:stop_duration=48,subtitles=live.srt')
else:
 clip(live,'live.mp4',48,"drawtext=fontfile=font.ttf:text='Terminal replay | Muse Code recording pending':fontsize=23:fontcolor=white:box=1:boxcolor=black@0.8:x=22:y=22")
card('hard-suite','Best Glimmer run | W4-o','Hard development suite\n\n8 / 12 alone after rescoring\n12 / 12 with Kuttaka\n\n53 model calls | 79 / 79 private cases',25)
card('result-summary','Verified project results','18 / 18 on the graded suite | 16 / 18 alone\n\n12 / 12 on the growth suite with memory\n12 / 12 in the later GROWTH-s run | 59 calls\n\nDevelopment results. Full evidence in the repository.',20)
clip('part_c.mp4','outro.mp4',65)
Path('parts.txt').write_text(''.join("file '"+n+".mp4'\n" for n in ('intro','live','hard-suite','result-summary','outro')))
args=['-f','concat','-safe','0','-i','parts.txt']
if len(sys.argv)>2:args+=['-i',sys.argv[2],'-map','0:v','-map','1:a','-af','apad','-shortest','-c:a','aac']
run(*args,'-c:v','copy','-movflags','+faststart','kuttaka3-film.mp4')
print(here/'kuttaka3-film.mp4')

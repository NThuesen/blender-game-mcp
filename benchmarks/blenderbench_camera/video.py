"""Offline TARGET/progress compositor from the exercised camera harness."""
import json,shutil,subprocess
from pathlib import Path
from PIL import Image,ImageDraw
INITIAL=None
TARGET=None
MODEL='configured model'

def load(p): return json.loads(Path(p).read_text())


def timeline_state(elapsed,ledger,sessions):
    active=0; completed=0; image=INITIAL
    for event in ledger:
        if event['elapsed_seconds']>elapsed: break
        active=event['round'] or active; completed=event['completed_rounds']
        if event['phase']=='candidate_checkpoint': image=Path(event['png'])
    calls=0; tokens=0
    for session in sessions:
        offset=session['offset_seconds']; summary=session['summary']
        events=load(Path(summary['root'])/'events.json')
        calls+=len({e['item']['id'] for e in events if e.get('elapsed_seconds',0)+offset<=elapsed and e.get('type') in ('item.started','item.updated','item.completed') and e.get('item',{}).get('type')=='mcp_tool_call'})
        if elapsed<offset: continue
        usage=summary['telemetry']
        supplied=usage.get('raw_usage_redacted',[])
        if not usage.get('usage_stream_complete') or not supplied:
            tokens=None; continue
        total=sum(u['input_tokens']+u['output_tokens'] for u in supplied)
        wall=summary.get('wall_seconds')
        if not wall: tokens=None; continue
        if tokens is not None: tokens+=round(total*min(1,max(0,(elapsed-offset)/wall)))
    return active,completed,image,calls,tokens


def compose(root, output):
    summary=load(root/'summary.json')
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    ledger=[json.loads(line) for line in (root/'round-ledger.jsonl').read_text().splitlines()]
    sessions=summary['sessions']; wall=summary['wall_seconds']
    with Image.open(TARGET) as im: target=im.convert('RGB').resize((512,512))
    ffmpeg=shutil.which('ffmpeg'); ffprobe=shutil.which('ffprobe')
    if not ffmpeg or not ffprobe: raise RuntimeError('ffmpeg/ffprobe missing')
    out=output/'camera-ten-round-15s.mp4'
    if out.exists(): raise FileExistsError(out)
    p=subprocess.Popen([ffmpeg,'-hide_banner','-loglevel','error','-n','-f','rawvideo','-pix_fmt','rgb24','-s','1024x602','-r','24','-i','-','-an','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p',str(out)],stdin=subprocess.PIPE)
    overlays=[]
    try:
        for i in range(360):
            elapsed=min(wall,wall*i/335)
            active,completed,image,calls,tokens=timeline_state(elapsed,ledger,sessions)
            frame=Image.new('RGB',(1024,602),'black'); frame.paste(target,(0,90))
            with Image.open(image) as im: frame.paste(im.convert('RGB').resize((512,512)),(512,90))
            draw=ImageDraw.Draw(frame)
            draw.text((12,15),'TARGET (static)',fill='white')
            draw.text((524,8),f'{MODEL} enhanced | Round {active}/10 | verified {completed}/10',fill='white')
            draw.text((524,27),f'Elapsed: {elapsed:.1f}s | Calls: {calls}',fill='white')
            draw.text((524,46),'Tokens (interpolated): '+('unavailable' if tokens is None else f'~{tokens:,}'),fill='white')
            label='Verified initial scene' if completed==0 else 'Independently rendered candidate'
            if summary.get('continuation'): label='Continuation | active time; downtime excluded'
            draw.text((524,65),label,fill='white')
            p.stdin.write(frame.tobytes())
            overlays.append({'frame':i,'elapsed_seconds':elapsed,'round':active,'verified_rounds':completed,'calls':calls,'tokens_interpolated':tokens,'source_image':str(image)})
            if i in (0,180,359): frame.save(output/f'video-keyframe-{i:03d}.png')
        p.stdin.close()
        if p.wait(timeout=120): raise RuntimeError('ffmpeg failed')
    except BaseException:
        p.kill(); p.wait(); raise
    probe=json.loads(subprocess.check_output([ffprobe,'-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=width,height,nb_read_frames,avg_frame_rate:format=duration','-of','json',str(out)],text=True,timeout=30))
    assert probe['streams'][0]['nb_read_frames']=='360' and abs(float(probe['format']['duration'])-15)<.05
    # Decode an actual encoded endpoint, not only the source compositor frame.
    subprocess.run([ffmpeg,'-v','error','-n','-sseof','-0.05','-i',str(out),'-frames:v','1',str(output/'decoded-endpoint.png')],check=True,timeout=30)
    with Image.open(output/'decoded-endpoint.png') as im: im.load(); assert im.size==(1024,602)
    (output/'video-report.json').write_text(json.dumps({'probe':probe,'overlays':overlays,'duration_seconds':15,'round_source':'explicit ledger; never tool or render count','token_source':'Per-session supplied final input+output, interpolated over actual session wall time; missing usage remains unavailable'},indent=2))

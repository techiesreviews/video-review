"""Shared storage for review comments, used by the web server and the MCP server."""
from pathlib import Path
import hashlib, json, math, os, re, shutil, subprocess, threading, time, uuid
from fractions import Fraction
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent
LOCK=threading.RLock()
AUTHORS=('you','ai')

def data_dir(path=None):
    directory=Path(path or os.environ.get('VIDEO_REVIEW_DATA_DIR') or ROOT/'data').expanduser().resolve()
    directory.mkdir(parents=True,exist_ok=True)
    for name,initial in [('manifest.json',[]),('comments.json',{'comments':[]})]:
        if not (directory/name).exists():(directory/name).write_text(json.dumps(initial,indent=2),encoding='utf-8')
    return directory

def now():return datetime.now(timezone.utc).isoformat()
def read(path,default):return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default
def write(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8');os.replace(temp,path)

class FileLock:
    """Cross-process lock so the browser and the AI never overwrite each other's changes."""
    def __init__(self,path):self.path=path
    def __enter__(self):
        LOCK.acquire();deadline=time.time()+10
        while True:
            try:self.fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY);return self
            except FileExistsError:
                try:
                    if time.time()-self.path.stat().st_mtime>30:self.path.unlink()
                except FileNotFoundError:pass
                if time.time()>deadline:LOCK.release();raise ValueError('Comments are busy; try again')
                time.sleep(0.05)
    def __exit__(self,*exc):
        os.close(self.fd)
        try:self.path.unlink()
        except FileNotFoundError:pass
        LOCK.release()

def videos(directory):return read(directory/'manifest.json',[])
def comments(directory):return read(directory/'comments.json',{'comments':[]})['comments']
def find_video(directory,video_id):
    video=next((x for x in videos(directory) if x['id']==video_id),None)
    if not video:raise ValueError('Unknown video version')
    return video

def parse_time(value):
    """Milliseconds from an int, or from h:mm:ss.mmm, m:ss.mmm or seconds."""
    if value is None or value=='':return None
    if isinstance(value,bool):raise ValueError('Invalid time')
    if isinstance(value,(int,float)):return int(round(value))
    text=str(value).strip()
    if re.fullmatch(r'\d+(\.\d+)?',text):return int(round(float(text)*1000))
    m=re.fullmatch(r'(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?',text)
    if not m or int(m[3])>59 or (m[1] and int(m[2])>59):raise ValueError('Use h:mm:ss.mmm, m:ss.mmm, seconds or milliseconds')
    return (int(m[1] or 0)*3600+int(m[2])*60+int(m[3]))*1000+int((m[4] or '0').ljust(3,'0'))

def timecode(ms):
    ms=max(0,int(round(ms)));return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'

def source_spans(video,start_ms,end_ms):
    fps=video['fps'];start=start_ms*fps/1000;end=(end_ms if end_ms is not None else start_ms)*fps/1000;cursor=0;spans=[]
    for a,b in video.get('keepRanges') or []:
        finish=cursor+b-a
        if end_ms is None:
            if cursor<=start<finish:spans.append({'sourceFrame':round(a+start-cursor,3),'sourceMs':round((a+start-cursor)*1000/fps,3)})
        elif start<finish and end>cursor:
            lo=a+max(0,start-cursor);hi=a+min(b-a,end-cursor)
            spans.append({'sourceStartFrame':round(lo,3),'sourceEndFrame':round(hi,3),'sourceStartMs':round(lo*1000/fps,3),'sourceEndMs':round(hi*1000/fps,3)})
        cursor=finish
    return spans

def thread(comment):
    """Replies, with a legacy single `resolution` shown as an AI reply."""
    replies=list(comment.get('replies') or [])
    if comment.get('resolution') and not any(r.get('legacy') for r in replies):
        replies.insert(0,{'id':'resolution','author':'ai','text':comment['resolution'],'createdAt':comment.get('updatedAt') or comment.get('createdAt'),'legacy':True})
    return replies

def describe(video,c):
    """A comment with everything an editor needs: timecodes, frames and original-source mapping."""
    fps=video['fps']
    return {'id':c['id'],'status':c['status'],'author':c.get('author','you'),'text':c['text'],
        'start':timecode(c['startMs']),'end':timecode(c['endMs']) if c.get('endMs') is not None else None,
        'startMs':c['startMs'],'endMs':c.get('endMs'),'startFrame':round(c['startMs']*fps/1000),
        'endFrame':round(c['endMs']*fps/1000) if c.get('endMs') is not None else None,
        'sourceSpans':c.get('sourceSpans',c.get('editRequest',{}).get('sourceSpans',source_spans(video,c['startMs'],c.get('endMs')))),
        **({'editRequest':c['editRequest']} if c.get('editRequest') else {}),
        'replies':[{'author':r['author'],'text':r['text'],'createdAt':r.get('createdAt')} for r in thread(c)],
        'createdAt':c.get('createdAt')}

def check_text(text):
    text=(text or '').strip()
    if not text or len(text)>10000:raise ValueError('Please enter a comment (up to 10,000 characters)')
    return text

def check_author(author):
    if author not in AUTHORS:raise ValueError('Author must be "you" or "ai"')
    return author

def check_edit_request(request,video,start,end):
    if not isinstance(request,dict) or request.get('type') not in ['remove','restore','clipped','extend_before','extend_after','mistake','pause','comment']:
        raise ValueError('Invalid transcript edit request')
    result={'type':request['type']}
    for key in ['quote','firstWordId','lastWordId','cutId','transcriptRevision']:
        if key in request:
            if not isinstance(request[key],str) or len(request[key])>(3000 if key=='quote' else 120):raise ValueError('Invalid transcript selection')
            result[key]=request[key]
    result['sourceSpans']=source_spans(video,start,end)
    if request['type']=='restore':
        import math
        a,b,d=(request.get(k) for k in ['sourceStartMs','sourceEndMs','sourceDurationMs'])
        if any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) for t in [a,b,d]) or not 0<=a<b<=d<=86400000:raise ValueError('Invalid original-recording restore range')
        result.update(sourceStartMs=round(a),sourceEndMs=round(b),sourceDurationMs=round(d))
        if 'sourceId' in request:
            if not isinstance(request['sourceId'],str) or len(request['sourceId'])>120:raise ValueError('Invalid original source ID')
            result['sourceId']=request['sourceId']
        result['sourceSpans']=[{'sourceStartFrame':round(a*video['fps']/1000,3),'sourceEndFrame':round(b*video['fps']/1000,3),'sourceStartMs':round(a,3),'sourceEndMs':round(b,3)}]
    return result

def transcript_path(directory,video_id):
    find_video(directory,video_id)
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',video_id):raise ValueError('Invalid video id')
    return directory/'transcripts'/(video_id+'.json')

def get_transcript(directory,video_id):
    return read(transcript_path(directory,video_id),None)

def search_library(directory,query=''):
    term=query.strip().casefold()[:200];results=[]
    for video in videos(directory):
        words=(get_transcript(directory,video['id']) or {}).get('words',[])
        text=' '.join(w['text'] for w in words);position=text.casefold().find(term) if term else -1
        at=None;snippet=''
        if position>=0:
            offset=0;index=0
            while index<len(words)-1 and offset+len(words[index]['text'])<=position:
                offset+=len(words[index]['text'])+1;index+=1
            at=words[index]['startMs'];snippet=' '.join(w['text'] for w in words[max(0,index-3):index+11])
        if not term or term in (video['title']+' '+video['label']+' '+video.get('fileName','')).casefold() or position>=0:
            results.append({'id':video['id'],'title':video['title'],'label':video['label'],'atMs':at,'snippet':snippet})
    return results

def archive_video(directory,video_id):
    with FileLock(directory/'manifest.lock'):
        manifest=videos(directory);item=next((v for v in manifest if v['id']==video_id),None)
        if item is None:raise ValueError('Unknown video version')
        archive=read(directory/'library-archive.json',[])
        archive=[v for v in archive if v['id']!=video_id]+[item]
        write(directory/'library-archive.json',archive)
        write(directory/'manifest.json',[v for v in manifest if v['id']!=video_id])
    # Comments, transcript and the media file are kept intact.
    return item

def restore_video(directory,video_id):
    with FileLock(directory/'manifest.lock'):
        manifest=videos(directory);archive=read(directory/'library-archive.json',[])
        item=next((v for v in archive if v['id']==video_id),None)
        if item is None:raise ValueError('No archived video found')
        if not any(v['id']==video_id for v in manifest):write(directory/'manifest.json',manifest+[item])
        write(directory/'library-archive.json',[v for v in archive if v['id']!=video_id])
    return item

def add_local_video(directory,path,title=''):
    media=Path(path).expanduser().resolve()
    if not media.is_file() or media.suffix.lower() not in ['.mp4','.mov','.m4v','.webm','.mkv','.avi']:
        raise ValueError('Enter the full path to an existing video file')
    for existing in videos(directory):
        if Path(existing['path']).resolve()==media:return existing
    probe=shutil.which('ffprobe')
    if not probe and os.name=='nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as key:
            user_path=winreg.QueryValueEx(key,'Path')[0]
        probe=shutil.which('ffprobe',path=os.environ.get('PATH','')+os.pathsep+os.path.expandvars(user_path))
    if not probe:raise ValueError('ffprobe is needed to read this video’s length and frame rate')
    try:
        result=subprocess.run([probe,'-v','error','-show_streams','-show_format','-of','json',str(media)],
            capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
        info=json.loads(result.stdout);video=next(s for s in info['streams'] if s['codec_type']=='video')
        fps=float(Fraction(video.get('avg_frame_rate') or video['r_frame_rate']))
        duration=round(float(info['format'].get('duration') or video['duration'])*1000)
        if not math.isfinite(fps) or fps<=0 or duration<=0:raise ValueError()
    except (subprocess.SubprocessError,ValueError,KeyError,StopIteration,ZeroDivisionError):
        raise ValueError('Could not read this video. Try an H.264 MP4 exported from Resolve.')
    name=title.strip()[:120] or media.stem
    return register_video(directory,{'id':'video-'+uuid.uuid4().hex[:12],'title':name,'label':name+' · '+media.name,
        'path':str(media),'fps':fps,'durationMs':duration})

def save_transcript(directory,video_id,data):
    video=find_video(directory,video_id)
    if not isinstance(data,dict) or not isinstance(data.get('words'),list) or not 0<len(data['words'])<=50000:
        raise ValueError('A word-timed transcript is required')
    previous=-1;ids=set();words=[]
    for word in data['words']:
        if not isinstance(word,dict):raise ValueError('Invalid timed word')
        start,end=word.get('startMs'),word.get('endMs');text=word.get('text');wid=word.get('id')
        if not isinstance(text,str) or not text.strip() or len(text)>500 or not isinstance(wid,str) or not wid or len(wid)>120 or wid in ids:
            raise ValueError('Invalid transcript word or id')
        if any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) for t in [start,end]) or not 0<=start<=end<=video['durationMs']+50 or start<previous:
            raise ValueError('Transcript timings do not match this video')
        previous=start;ids.add(wid)
        clean={'id':wid,'text':text.strip(),'startMs':round(start),'endMs':round(end)}
        for key in ['sourceStartMs','sourceEndMs']:
            if key in word:
                if not isinstance(word[key],(int,float)) or isinstance(word[key],bool) or not math.isfinite(word[key]) or word[key]<0:raise ValueError('Invalid source timing')
                clean[key]=word[key]
        if 'clipId' in word:clean['clipId']=str(word['clipId'])[:120]
        words.append(clean)
    cuts=[]
    for cut in data.get('cuts',[]):
        at=cut.get('atMs')
        if isinstance(at,bool) or not isinstance(at,(int,float)) or not math.isfinite(at) or not 0<=at<=video['durationMs']:raise ValueError('Invalid transcript cut')
        cuts.append({'id':str(cut.get('id','join'))[:100],'atMs':round(at),'risk':bool(cut.get('risk'))})
    result={'schemaVersion':1,'revision':str(data.get('revision','imported'))[:120],
            'label':str(data.get('label','Word-timed transcript'))[:120],'words':words,'cuts':cuts}
    if data.get('original') is not None:
        o=data['original']
        if not isinstance(o,dict) or isinstance(o.get('durationMs'),bool) or not isinstance(o.get('durationMs'),(int,float)) or not math.isfinite(o['durationMs']) or not 0<o['durationMs']<=86400000 or not isinstance(o.get('words'),list) or not 0<len(o['words'])<=50000:raise ValueError('Invalid original transcript')
        raw=[];raw_ids=set();previous=-1
        for w in o['words']:
            if not isinstance(w,dict):raise ValueError('Invalid original word')
            a,b=w.get('startMs'),w.get('endMs');wid,text=w.get('id'),w.get('text')
            if not isinstance(wid,str) or not wid or len(wid)>120 or wid in raw_ids or not isinstance(text,str) or not text.strip() or len(text)>500:raise ValueError('Invalid original word')
            if any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) for t in [a,b]) or not 0<=a<=b<=o['durationMs']+50 or a<previous:raise ValueError('Invalid original word timing')
            raw.append({'id':wid,'text':text.strip(),'startMs':round(a),'endMs':round(b)});raw_ids.add(wid);previous=a
        if any(w['id'] not in raw_ids for w in words):raise ValueError('Original and review word IDs do not match')
        result['original']={'durationMs':round(o['durationMs']),'label':str(o.get('label','Original recording'))[:120],'sourceId':str(o.get('sourceId',''))[:120],'words':raw}
    edits=data.get('draftEdits',[])
    if not isinstance(edits,list) or len(edits)>5000:raise ValueError('Invalid preview cuts')
    result['draftEdits']=[];edit_ids=set()
    for edit in edits:
        if not isinstance(edit,dict):raise ValueError('Invalid preview cut')
        eid=edit.get('id');start=edit.get('startMs');end=edit.get('endMs');word_ids=edit.get('wordIds')
        if not isinstance(eid,str) or not eid or len(eid)>120 or eid in edit_ids:raise ValueError('Invalid preview cut id')
        if any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) for t in [start,end]) or not 0<=start<end<=video['durationMs'] or round(end)<=round(start):raise ValueError('Preview cut lies outside video')
        if not isinstance(word_ids,list) or any(not isinstance(w,str) or len(w)>120 for w in word_ids):raise ValueError('Invalid removed word IDs')
        edit_ids.add(eid)
        result['draftEdits'].append({'id':eid,'startMs':round(start),'endMs':round(end),'wordIds':word_ids,
            'quote':str(edit.get('quote',''))[:3000],'kind':'pause' if edit.get('kind')=='pause' else 'words',
            'commentId':edit.get('commentId') if isinstance(edit.get('commentId'),str) else None})
    if data.get('keepRanges') is not None:
        ranges=data['keepRanges'];fps=data.get('fps')
        if isinstance(fps,bool) or not isinstance(fps,(int,float)) or not math.isfinite(fps) or fps<=0 or abs(fps-video['fps'])>.001:raise ValueError('The transcript frame rate does not match this video')
        if not isinstance(ranges,list) or not ranges or not all(isinstance(r,list) and len(r)==2 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in r) and 0<=r[0]<r[1] for r in ranges):raise ValueError('Invalid transcript source mapping')
        if abs(sum(b-a for a,b in ranges)/fps*1000-video['durationMs'])>1000/fps:raise ValueError('The source mapping belongs to a different cut')
        result['keepRanges']=ranges;result['fps']=fps
    path=transcript_path(directory,video_id);path.parent.mkdir(parents=True,exist_ok=True)
    with FileLock(directory/'transcripts.lock'):write(path,result)
    if result.get('keepRanges') is not None and result['keepRanges']!=video.get('keepRanges'):
        update_render(directory,video_id,None,result['keepRanges'])
    return result

def apply(directory,cmd):
    """Apply one change to comments.json atomically and return the saved data."""
    path=directory/'comments.json'
    with FileLock(directory/'comments.lock'):
        data=read(path,{'comments':[]});items=data['comments'];action=cmd.get('action')
        def get(cid):
            c=next((x for x in items if x['id']==cid),None)
            if not c:raise ValueError('Comment not found')
            return c
        if action=='add':
            video=find_video(directory,cmd['videoId'])
            start=cmd['startMs'];end=cmd.get('endMs')
            if isinstance(start,bool) or not isinstance(start,int) or start<0 or start>video['durationMs']:raise ValueError('Timestamp outside video')
            if end is not None and (isinstance(end,bool) or not isinstance(end,int) or end<=start or end>video['durationMs']):raise ValueError('Range end must follow start')
            comment={'id':str(uuid.uuid4()),'videoId':video['id'],'startMs':start,'endMs':end,'text':check_text(cmd.get('text')),'category':cmd.get('category','edit'),'status':'open','author':check_author(cmd.get('author','you')),'createdAt':cmd.get('createdAt') or now(),'replies':[]}
            comment['sourceSpans']=source_spans(video,start,end)
            if cmd.get('editRequest') is not None:comment['editRequest']=check_edit_request(cmd['editRequest'],video,start,end)
            if comment.get('editRequest',{}).get('type')=='restore':comment['sourceSpans']=comment['editRequest']['sourceSpans']
            items.append(comment);result=comment
        elif action=='update':
            c=get(cmd['id'])
            if 'status' in cmd:
                if cmd['status'] not in ['open','done']:raise ValueError('Invalid status')
                c['status']=cmd['status']
            if 'text' in cmd:c['text']=check_text(cmd['text'])
            c['updatedAt']=now();result=c
        elif action=='reply':
            c=get(cmd['id']);reply={'id':str(uuid.uuid4()),'author':check_author(cmd.get('author','you')),'text':check_text(cmd.get('text')),'createdAt':now()}
            c.setdefault('replies',[]).append(reply)
            if cmd.get('status') in ['open','done']:c['status']=cmd['status']
            c['updatedAt']=now();result=c
        elif action=='delete':
            get(cmd['id']);data['comments']=[x for x in items if x['id']!=cmd['id']];result=None
        else:raise ValueError('Unsupported action')
        write(path,data)
    return data,result

def view(directory):
    """Which version the reviewer has open in the offline app, and where the playhead is."""
    return read(directory/'view.json',{'videoId':None,'playheadMs':None})

def set_view(directory,value):
    video_id=value.get('videoId');playhead=value.get('playheadMs')
    if video_id is not None and not isinstance(video_id,str):raise ValueError('Invalid video')
    if playhead is not None and (isinstance(playhead,bool) or not isinstance(playhead,(int,float))):raise ValueError('Invalid playhead')
    write(directory/'view.json',{'videoId':video_id,'playheadMs':None if playhead is None else int(round(playhead)),'at':now()})

def request_view(directory,video_id,at_ms=None,comment_id=None):
    """Asks the open offline app to show a version; the page picks it up within a few seconds."""
    video=find_video(directory,video_id)
    if comment_id:
        c=next((x for x in comments(directory) if x['id']==comment_id),None)
        if not c or c['videoId']!=video['id']:raise ValueError('That comment is not on this video')
        if at_ms is None:at_ms=min(c['startMs'],video['durationMs'])
    if at_ms is not None and not 0<=at_ms<=video['durationMs']:raise ValueError('Time outside video')
    request={'id':str(uuid.uuid4()),'videoId':video['id'],'atMs':at_ms,'commentId':comment_id,'createdAt':now()}
    write(directory/'view-request.json',request)
    return request

def fingerprint(path):
    digest=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def file_version(path):
    """Changes when the file is replaced, so the page reloads it; None while it is missing."""
    try:stat=Path(path).stat()
    except OSError:return None
    return f'{stat.st_size}-{stat.st_mtime_ns}'

def update_render(directory,video_id,duration_ms=None,keep_ranges=None):
    """Records the new duration and keepRanges after the agent re-rendered a video under the same file name."""
    if duration_ms is None and keep_ranges is None:return find_video(directory,video_id)
    duration=parse_time(duration_ms)
    if duration_ms is not None and (not duration or duration<=0):raise ValueError('durationMs must be positive')
    if keep_ranges is not None and not all(isinstance(r,list) and len(r)==2 and 0<=r[0]<r[1] for r in keep_ranges):raise ValueError('keepRanges must be [[startFrame, endFrame], …]')
    path=directory/'manifest.json'
    with FileLock(directory/'manifest.lock'):
        manifest=read(path,[]);item=next((x for x in manifest if x['id']==video_id),None)
        if not item:raise ValueError('Unknown video version')
        if keep_ranges is not None and keep_ranges!=item.get('keepRanges'):
            rebase_comments(directory,item,keep_ranges,duration or item['durationMs'])
        if duration_ms is not None:item['durationMs']=duration
        if keep_ranges is not None:item['keepRanges']=keep_ranges
        if Path(item['path']).is_file():item['fingerprint']=fingerprint(item['path'])
        write(path,manifest)
    return item

def rebase_comments(directory,video,ranges,duration):
    def mapped(frame):
        cursor=0;best=(float('inf'),0)
        for a,b in ranges:
            clamped=max(a,min(b,frame));distance=abs(frame-clamped)
            if distance<best[0]:best=(distance,cursor+clamped-a)
            if a<=frame<b:return round((cursor+frame-a)*1000/video['fps'])
            cursor+=b-a
        return min(duration,round(best[1]*1000/video['fps']))
    with FileLock(directory/'comments.lock'):
        data=read(directory/'comments.json',{'comments':[]})
        for c in data['comments']:
            if c['videoId']!=video['id']:continue
            spans=c.get('sourceSpans') or c.get('editRequest',{}).get('sourceSpans') or source_spans(video,c['startMs'],c.get('endMs'))
            if not spans:continue
            c['sourceSpans']=spans;c['beforeRecut']={'startMs':c['startMs'],'endMs':c.get('endMs')}
            start=mapped(spans[0].get('sourceStartFrame',spans[0].get('sourceFrame')))
            end=mapped(spans[-1].get('sourceEndFrame',spans[-1].get('sourceFrame'))) if c.get('endMs') is not None else None
            c['startMs']=start;c['endMs']=end if end is not None and end>start else None
        write(directory/'comments.json',data)

def register_video(directory,entry):
    """Add a new video version to the manifest. Existing IDs are never overwritten."""
    for key in ['id','title','label','path','fps','durationMs']:
        if entry.get(key) in (None,''):raise ValueError(f'Missing {key}')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',entry['id']):raise ValueError('Use letters, digits, dot, dash or underscore for the id')
    media=Path(entry['path']).expanduser()
    if not media.is_file():raise ValueError('Video file not found')
    fps=entry['fps'];duration=parse_time(entry['durationMs'])
    if not isinstance(fps,(int,float)) or isinstance(fps,bool) or fps<=0:raise ValueError('fps must be a positive number')
    if not duration or duration<=0:raise ValueError('durationMs must be positive')
    total=round(duration*fps/1000);keep=entry.get('keepRanges') or [[0,total]]
    if not all(isinstance(r,list) and len(r)==2 and 0<=r[0]<r[1] for r in keep):raise ValueError('keepRanges must be [[startFrame, endFrame], …]')
    item={'id':entry['id'],'title':entry['title'],'label':entry['label'],'path':str(media.resolve()),'fps':fps,'durationMs':duration,'fingerprint':entry.get('fingerprint') or fingerprint(media),'keepRanges':keep}
    path=directory/'manifest.json'
    with FileLock(directory/'manifest.lock'):
        manifest=read(path,[])
        if any(x['id']==item['id'] for x in manifest):raise ValueError('That id already exists; use a new id for a new cut')
        manifest.append(item);write(path,manifest)
    return item

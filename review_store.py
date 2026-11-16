"""Shared storage for review comments, used by the web server and the MCP server.

The comment rules (fields, limits, who may write and edit, messages) are in web/review-rules.json, which the browser and
the hosted Worker read too (web/review-core.js); tests/fixtures/review-rule-cases.json is run against both."""
from pathlib import Path
import hashlib, json, math, os, re, uuid
from fractions import Fraction
from datetime import datetime, timezone
import file_store
import media_tools

ROOT=Path(__file__).resolve().parent
RULES=json.loads((ROOT/'web'/'review-rules.json').read_text(encoding='utf-8'))
MESSAGES=RULES['messages'];LIMITS=RULES['limits']
AUTHORS=tuple(RULES['authors'])
SECONDS,TIMECODE,VIDEO_ID=(re.compile(RULES['patterns'][name]) for name in ('seconds','timecode','videoId'))

def round_half_up(value):
    """Nearest whole number, halves up, as JavaScript's Math.round does (Python's round() sends halves to even)."""
    whole=math.floor(value);return whole+1 if value-whole>=.5 else whole

def round3(value):
    """Three decimals, halves up, as the browser rounds source frames."""
    return round_half_up(value*1000)/1000

def first(*values):
    """The first value that is not None, as JavaScript's ?? picks."""
    return next((value for value in values if value is not None),None)

def whole_number(value):
    """An int, also one written with a decimal point (5.0), as JavaScript's Number.isInteger accepts; else None."""
    if isinstance(value,bool):return None
    if isinstance(value,int):return value
    if isinstance(value,float) and value.is_integer():return int(value)
    return None

def finite_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def data_dir(path=None):
    directory=Path(path or os.environ.get('VIDEO_REVIEW_DATA_DIR') or ROOT/'data').expanduser().resolve()
    directory.mkdir(parents=True,exist_ok=True)
    for name,initial in [('manifest.json',[]),('comments.json',{'comments':[]})]:
        if not (directory/name).exists():(directory/name).write_text(json.dumps(initial,indent=2),encoding='utf-8')
    return directory

def now():return datetime.now(timezone.utc).isoformat()
read=file_store.read_json
write=file_store.write_json

def locked(path):
    """One program or thread at a time on this data file (file_store's lock, its own per file, so the browser and the
    AI never overwrite each other's changes). Held by another program past file_store.LOCK_WAIT_SECONDS: a ValueError
    to show."""
    return file_store.locked(file_store.lock_for(path),busy=f'{Path(path).name} is busy; try again in a moment.')

def videos(directory):return read(directory/'manifest.json',[])
def comments(directory):return read(directory/'comments.json',{'comments':[]})['comments']
def find_video(directory,video_id):
    video=next((x for x in videos(directory) if x['id']==video_id),None)
    if not video:raise ValueError(MESSAGES['unknownVideo'])
    return video

def parse_time(value):
    """Milliseconds from an int, or from h:mm:ss.mmm, m:ss.mmm or seconds."""
    if value is None or value=='':return None
    if isinstance(value,bool) or (isinstance(value,float) and not math.isfinite(value)):raise ValueError(MESSAGES['time'])
    if isinstance(value,(int,float)):return round_half_up(value)
    text=str(value).strip(RULES['trimCharacters'])
    if SECONDS.fullmatch(text):return round_half_up(float(text)*1000)
    m=TIMECODE.fullmatch(text)
    if not m or int(m[3])>59 or (m[1] and int(m[2])>59):raise ValueError(MESSAGES['timeFormat'])
    return (int(m[1] or 0)*3600+int(m[2])*60+int(m[3]))*1000+int((m[4] or '0').ljust(3,'0'))

def timecode(ms):
    ms=max(0,round_half_up(ms));return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'

def source_spans(video,start_ms,end_ms):
    fps=video['fps'];start=start_ms*fps/1000;end=(end_ms if end_ms is not None else start_ms)*fps/1000;cursor=0;spans=[]
    for a,b in video.get('keepRanges') or []:
        finish=cursor+b-a
        if end_ms is None:
            if cursor<=start<finish:spans.append({'sourceFrame':round3(a+start-cursor),'sourceMs':round3((a+start-cursor)*1000/fps)})
        elif start<finish and end>cursor:
            lo=a+max(0,start-cursor);hi=a+min(b-a,end-cursor)
            spans.append({'sourceStartFrame':round3(lo),'sourceEndFrame':round3(hi),'sourceStartMs':round3(lo*1000/fps),'sourceEndMs':round3(hi*1000/fps)})
        cursor=finish
    return spans

def thread(comment):
    """Replies, with a legacy single `resolution` shown as an AI reply."""
    replies=list(comment.get('replies') or [])
    if comment.get('resolution') and not any(r.get('legacy') for r in replies):
        replies.insert(0,{'id':'resolution','author':RULES['agentAuthor'],'text':comment['resolution'],'createdAt':comment.get('updatedAt') or comment.get('createdAt'),'legacy':True})
    return replies

def frame_at(ms,fps):
    """The frame at a time, rounding half up as the browser and the hosted worker do."""
    return round_half_up(ms*fps/1000)

def agent_request(video,c):
    """What the reviewer asked an agent to do, in one field: the comment's current text is the instruction."""
    e=c['editRequest'];fps=video['fps'];end=c.get('endMs')
    return {'type':'change','instruction':c['text'],'quote':e.get('quote'),'wordIds':e.get('wordIds') or [],'firstWordId':e.get('firstWordId'),'lastWordId':e.get('lastWordId'),
        'startFrame':frame_at(c['startMs'],fps),'endFrame':frame_at(end,fps) if end is not None else None,
        'sourceSpans':first(c.get('sourceSpans'),e.get('sourceSpans'),source_spans(video,c['startMs'],end)),'transcriptRevision':e.get('transcriptRevision'),**({'cutId':e['cutId']} if e.get('cutId') else {})}

def describe(video,c):
    """A comment with everything an editor needs: timecodes, frames and original-source mapping."""
    fps=video['fps'];request=c.get('editRequest')
    return {'id':c['id'],'videoId':c.get('videoId'),'status':c['status'],'author':c.get('author') or RULES['pageAuthor'],'text':c['text'],
        'start':timecode(c['startMs']),'end':timecode(c['endMs']) if c.get('endMs') is not None else None,
        'startMs':c['startMs'],'endMs':c.get('endMs'),'startFrame':frame_at(c['startMs'],fps),
        'endFrame':frame_at(c['endMs'],fps) if c.get('endMs') is not None else None,
        'sourceSpans':first(c.get('sourceSpans'),(request or {}).get('sourceSpans'),source_spans(video,c['startMs'],c.get('endMs'))),
        **({'editRequest':request} if request is not None else {}),
        **({'agentRequest':agent_request(video,c)} if (request or {}).get('type')=='change' else {}),
        'replies':[{'author':r['author'],'text':r['text'],'createdAt':r.get('createdAt')} for r in thread(c)],
        'createdAt':c.get('createdAt')}

def check_text(text):
    if text is None:text=''
    if not isinstance(text,str):raise ValueError(MESSAGES['text'])
    text=text.strip(RULES['trimCharacters'])
    if not text or len(text)>LIMITS['textLength']:raise ValueError(MESSAGES['text'])
    return text

def check_author(author):
    if author not in AUTHORS:raise ValueError(MESSAGES['author'])
    return author

def check_keep_ranges(ranges):
    if not isinstance(ranges,list) or not all(isinstance(r,list) and len(r)==2 and all(finite_number(x) for x in r) and 0<=r[0]<r[1] for r in ranges):
        raise ValueError(MESSAGES['keepRanges'])
    return ranges

def check_render(duration_ms=None,keep_ranges=None):
    """The new duration and keepRanges the agent sends after re-rendering a video under the same file name."""
    changes={}
    if duration_ms is not None:
        duration=parse_time(duration_ms)
        if duration is None or duration<=0:raise ValueError(MESSAGES['duration'])
        changes['durationMs']=duration
    if keep_ranges is not None:changes['keepRanges']=check_keep_ranges(keep_ranges)
    return changes

def check_edit_request(request,video,start,end):
    if not isinstance(request,dict) or request.get('type') not in RULES['editRequestTypes']:raise ValueError(MESSAGES['editRequest'])
    result={'type':request['type']}
    for key in RULES['selectionFields']:
        if request.get(key) is not None:
            if not isinstance(request[key],str) or len(request[key])>(LIMITS['quoteLength'] if key=='quote' else LIMITS['idLength']):raise ValueError(MESSAGES['selection'])
            result[key]=request[key]
    result['sourceSpans']=source_spans(video,start,end)
    if request['type']=='change':
        ids=first(request.get('wordIds'),[])
        if not isinstance(ids,list) or len(ids)>LIMITS['wordIds'] or any(not isinstance(i,str) or len(i)>LIMITS['idLength'] for i in ids):raise ValueError(MESSAGES['wordIds'])
        result.update(wordIds=list(ids),startFrame=frame_at(start,video['fps']),endFrame=frame_at(end,video['fps']) if end is not None else None)
    if request['type']=='restore':
        a,b,d=(request.get(k) for k in ['sourceStartMs','sourceEndMs','sourceDurationMs'])
        if not all(finite_number(t) for t in [a,b,d]) or not 0<=a<b<=d<=LIMITS['sourceDurationMs']:raise ValueError(MESSAGES['restoreRange'])
        result.update(sourceStartMs=round_half_up(a),sourceEndMs=round_half_up(b),sourceDurationMs=round_half_up(d))
        if request.get('sourceId') is not None:
            if not isinstance(request['sourceId'],str) or len(request['sourceId'])>LIMITS['idLength']:raise ValueError(MESSAGES['sourceId'])
            result['sourceId']=request['sourceId']
        result['sourceSpans']=[{'sourceStartFrame':round3(a*video['fps']/1000),'sourceEndFrame':round3(b*video['fps']/1000),'sourceStartMs':round3(a),'sourceEndMs':round3(b)}]
    return result

def transcript_path(directory,video_id):
    find_video(directory,video_id)
    if not VIDEO_ID.fullmatch(video_id):raise ValueError('Invalid video id')
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
    with locked(directory/'manifest.json'):
        manifest=videos(directory);item=next((v for v in manifest if v['id']==video_id),None)
        if item is None:raise ValueError(MESSAGES['unknownVideo'])
        archive=read(directory/'library-archive.json',[])
        archive=[v for v in archive if v['id']!=video_id]+[item]
        write(directory/'library-archive.json',archive)
        write(directory/'manifest.json',[v for v in manifest if v['id']!=video_id])
    # Comments, transcript and the media file are kept intact.
    return item

def restore_video(directory,video_id):
    with locked(directory/'manifest.json'):
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
    existing=same_file(videos(directory),media)
    if existing:return existing
    try:
        result=media_tools.run('ffprobe',['-v','error','-show_streams','-show_format','-of','json',str(media)],timeout=30)
        if result.returncode!=0:raise ValueError()
        info=json.loads(result.stdout);video=next(s for s in info['streams'] if s['codec_type']=='video')
        fps=float(Fraction(video.get('avg_frame_rate') or video['r_frame_rate']))
        duration=round(float(info['format'].get('duration') or video['duration'])*1000)
        if not math.isfinite(fps) or fps<=0 or duration<=0:raise ValueError()
    except media_tools.ToolMissing as error:
        raise ValueError(str(error)) from None
    except (media_tools.ToolTimedOut,ValueError,KeyError,StopIteration,ZeroDivisionError):
        raise ValueError('Could not read this video. Try an H.264 MP4 exported from Resolve.')
    name=title.strip()[:120] or media.stem
    item=video_item({'id':'video-'+uuid.uuid4().hex[:12],'title':name,'label':name+' · '+media.name,'path':str(media),'fps':fps,'durationMs':duration})
    return add_to_manifest(directory,item,reuse_same_file=True)

def same_file(manifest,media):
    return next((x for x in manifest if Path(x['path']).resolve()==media),None)

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
        if not isinstance(o,dict) or isinstance(o.get('durationMs'),bool) or not isinstance(o.get('durationMs'),(int,float)) or not math.isfinite(o['durationMs']) or not 0<o['durationMs']<=LIMITS['sourceDurationMs'] or not isinstance(o.get('words'),list) or not 0<len(o['words'])<=50000:raise ValueError('Invalid original transcript')
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
            'quote':str(edit.get('quote',''))[:LIMITS['quoteLength']],'kind':'pause' if edit.get('kind')=='pause' else 'words',
            'commentId':edit.get('commentId') if isinstance(edit.get('commentId'),str) else None})
    if data.get('keepRanges') is not None:
        ranges=data['keepRanges'];fps=data.get('fps')
        if isinstance(fps,bool) or not isinstance(fps,(int,float)) or not math.isfinite(fps) or fps<=0 or abs(fps-video['fps'])>.001:raise ValueError('The transcript frame rate does not match this video')
        if not isinstance(ranges,list) or not ranges or not all(isinstance(r,list) and len(r)==2 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in r) and 0<=r[0]<r[1] for r in ranges):raise ValueError('Invalid transcript source mapping')
        if abs(sum(b-a for a,b in ranges)/fps*1000-video['durationMs'])>1000/fps:raise ValueError('The source mapping belongs to a different cut')
        result['keepRanges']=ranges;result['fps']=fps
    path=transcript_path(directory,video_id);path.parent.mkdir(parents=True,exist_ok=True)
    with locked(path):write(path,result)
    if result.get('keepRanges') is not None and result['keepRanges']!=video.get('keepRanges'):
        update_render(directory,video_id,None,result['keepRanges'])
    return result

def apply(directory,cmd):
    """Apply one change to comments.json atomically and return the saved data."""
    path=directory/'comments.json'
    with locked(path):
        data=read(path,{'comments':[]});items=data['comments'];action=cmd.get('action')
        def get(cid):
            c=next((x for x in items if x['id']==cid),None)
            if not c:raise ValueError(MESSAGES['commentNotFound'])
            return c
        if action=='add':
            video=find_video(directory,cmd.get('videoId'))
            start=whole_number(cmd.get('startMs'));end=cmd.get('endMs')
            if start is None or start<0 or start>video['durationMs']:raise ValueError(MESSAGES['timestampOutside'])
            if end is not None:
                end=whole_number(end)
                if end is None or end<=start or end>video['durationMs']:raise ValueError(MESSAGES['rangeEnd'])
            comment={'id':str(uuid.uuid4()),'videoId':video['id'],'startMs':start,'endMs':end,'text':check_text(cmd.get('text')),'category':cmd.get('category') or RULES['defaultCategory'],'status':RULES['newStatus'],'author':check_author(cmd.get('author') or RULES['pageAuthor']),'createdAt':cmd.get('createdAt') or now(),'replies':[]}
            comment['sourceSpans']=source_spans(video,start,end)
            if cmd.get('editRequest') is not None:comment['editRequest']=check_edit_request(cmd['editRequest'],video,start,end)
            if comment.get('editRequest',{}).get('type')=='restore':comment['sourceSpans']=comment['editRequest']['sourceSpans']
            items.append(comment);result=comment
        elif action=='update':
            c=get(cmd.get('id'));stamp=now()
            if 'status' in cmd and cmd['status'] not in RULES['statuses']:raise ValueError(MESSAGES['status'])
            if 'text' in cmd:
                if (c.get('author') or RULES['pageAuthor']) not in RULES['textEditableBy']:raise ValueError(MESSAGES['notYourComment'])
                c.update(text=check_text(cmd['text']),editedAt=stamp)
            if 'status' in cmd:c['status']=cmd['status']
            c['updatedAt']=stamp;result=c
        elif action=='reply':
            c=get(cmd.get('id'));reply={'id':str(uuid.uuid4()),'author':check_author(cmd.get('author') or RULES['pageAuthor']),'text':check_text(cmd.get('text')),'createdAt':now()}
            c.setdefault('replies',[]).append(reply)
            if cmd.get('status') in RULES['statuses']:c['status']=cmd['status']
            c['updatedAt']=reply['createdAt'];result=c
        elif action=='delete':
            get(cmd.get('id'));data['comments']=[x for x in items if x['id']!=cmd['id']];result=None
        else:raise ValueError(MESSAGES['action'])
        write(path,data)
    return data,result

AGENT_FRESH_SECONDS=45

def touch_agent(directory):
    """Written every few seconds while the MCP server runs, so the page can tell whether an agent is attached."""
    write(directory/'agent-seen.json',{'pid':os.getpid(),'at':now()})

def agent_gone(directory):
    (directory/'agent-seen.json').unlink(missing_ok=True)

def agent_connected(directory):
    seen=read(directory/'agent-seen.json',None)
    if not isinstance(seen,dict) or not isinstance(seen.get('at'),str):return False
    try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(seen['at'])).total_seconds()
    except ValueError:return False
    return age<=AGENT_FRESH_SECONDS

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
    changes=check_render(duration_ms,keep_ranges)
    if not changes:return find_video(directory,video_id)
    path=directory/'manifest.json'
    # Hashing a long video can take a while; it happens before the lock, so nobody waits for it.
    media=find_video(directory,video_id)['path'];digest=fingerprint(media) if Path(media).is_file() else None
    # The comments stay locked until the new mapping is saved, so a comment added meanwhile is mapped with it.
    with locked(path),locked(directory/'comments.json'):
        manifest=read(path,[]);item=next((x for x in manifest if x['id']==video_id),None)
        if not item:raise ValueError(MESSAGES['unknownVideo'])
        if 'keepRanges' in changes and changes['keepRanges']!=item.get('keepRanges'):
            _rebase(directory,item,changes['keepRanges'],changes.get('durationMs') or item['durationMs'])
        item.update(changes)
        if digest is not None and item['path']==media:item['fingerprint']=digest
        write(path,manifest)
    return item

def rebase_comments(directory,video,ranges,duration):
    """Move a video's comments to their places in a new cut, by their frames in the original recording."""
    with locked(directory/'comments.json'):_rebase(directory,video,ranges,duration)

def _rebase(directory,video,ranges,duration):
    """rebase_comments for a caller that already holds the comments lock (locks do not nest)."""
    def mapped(frame):
        cursor=0;best=(float('inf'),0)
        for a,b in ranges:
            clamped=max(a,min(b,frame));distance=abs(frame-clamped)
            if distance<best[0]:best=(distance,cursor+clamped-a)
            if a<=frame<b:return round_half_up((cursor+frame-a)*1000/video['fps'])
            cursor+=b-a
        return min(duration,round_half_up(best[1]*1000/video['fps']))
    data=read(directory/'comments.json',{'comments':[]})
    for c in data['comments']:
        if c['videoId']!=video['id']:continue
        spans=first(c.get('sourceSpans'),(c.get('editRequest') or {}).get('sourceSpans'))
        if spans is None:spans=source_spans(video,c['startMs'],c.get('endMs'))
        if not spans:continue
        c['sourceSpans']=spans;c['beforeRecut']={'startMs':c['startMs'],'endMs':c.get('endMs')}
        start=mapped(spans[0].get('sourceStartFrame',spans[0].get('sourceFrame')))
        end=mapped(spans[-1].get('sourceEndFrame',spans[-1].get('sourceFrame'))) if c.get('endMs') is not None else None
        c['startMs']=start;c['endMs']=end if end is not None and end>start else None
    write(directory/'comments.json',data)

def register_video(directory,entry):
    """Add a new video version to the manifest. Existing IDs are never overwritten."""
    return add_to_manifest(directory,video_item(entry))

def video_item(entry):
    """A checked manifest entry, with the file's fingerprint (slow for a long video, so done before any lock)."""
    for key in ['id','title','label','path','fps','durationMs']:
        if entry.get(key) in (None,''):raise ValueError(f'Missing {key}')
    if not VIDEO_ID.fullmatch(entry['id']):raise ValueError(MESSAGES['videoIdFormat'])
    media=Path(entry['path']).expanduser()
    if not media.is_file():raise ValueError('Video file not found')
    fps=entry['fps'];duration=parse_time(entry['durationMs'])
    if not finite_number(fps) or fps<=0:raise ValueError(MESSAGES['fps'])
    if not duration or duration<=0:raise ValueError(MESSAGES['duration'])
    keep=check_keep_ranges(entry.get('keepRanges') or [[0,round_half_up(duration*fps/1000)]])
    return {'id':entry['id'],'title':entry['title'],'label':entry['label'],'path':str(media.resolve()),'fps':fps,'durationMs':duration,'fingerprint':entry.get('fingerprint') or fingerprint(media),'keepRanges':keep}

def add_to_manifest(directory,item,reuse_same_file=False):
    """Append item under the manifest's lock. With reuse_same_file, a video already listed for the same file is
    returned instead; the check runs under the lock, so two quick adds of one file make one entry."""
    path=directory/'manifest.json'
    with locked(path):
        manifest=read(path,[])
        existing=same_file(manifest,Path(item['path'])) if reuse_same_file else None
        if existing:return existing
        if any(x['id']==item['id'] for x in manifest):raise ValueError(MESSAGES['videoIdTaken'])
        manifest.append(item);write(path,manifest)
    return item

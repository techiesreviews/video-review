"""MCP server that lets an AI editor read and answer Video Review comments.

Speaks the Model Context Protocol over stdio using only the standard library.
Register it with:  claude mcp add video-review -- python /path/to/mcp_server.py
"""
from pathlib import Path
import argparse, json, os, sys, traceback
sys.path.insert(0,str(Path(__file__).resolve().parent))
import review_store as store

DATA=None
PORT=os.environ.get('VIDEO_REVIEW_PORT','8879')
INSTRUCTIONS=f"""Video Review holds the reviewer's timestamped comments on local video edits. The reviewer watches in the browser at http://127.0.0.1:{PORT} and sees your replies live.

Workflow: call list_comments to read open comments. Each has edited-video timecodes, frame numbers and sourceSpans (frames in the original footage, mapped through the version's keepRanges). Make the change in the edit. Then call resolve_comment with a one-line summary of what you changed. If a comment is ambiguous, ask with reply_to_comment instead of guessing and leave it open. When you render the fixes, first rename the reviewed file (for example add "-old" to its name), then render to a temporary name and rename it to the reviewed file's original path once it is finished, so the comments stay on that one video and the reviewer sees them marked done. Then call open_video on that video with the new durationMs and keepRanges: the reviewer's page reloads the file. Use register_video only for a different video, with a new id; never reuse an id."""

def obj(properties,required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
TIME={'type':['string','integer'],'description':'Milliseconds, seconds ("201.5"), or a timecode such as "3:21.133" / "00:03:21.133".'}
TOOLS=[
    {'name':'get_transcript','description':'Read the existing word-timed transcript for a video version. No transcription or credit spending. Words include stable IDs and review times; a transcript is not proof that a cut sounds natural.','inputSchema':obj({'videoId':{'type':'string'}},['videoId'])},
    {'name':'set_transcript','description':'Attach an existing word-timed transcript to a video version. Supply {words:[{id,text,startMs,endMs}],revision,label,cuts:[{id,atMs,risk}]}. Times are milliseconds in this exact rendered version. Derive recut timings from the raw transcript and actual Resolve cut list; never buy a new transcription.','inputSchema':obj({'videoId':{'type':'string'},'transcript':{'type':'object'}},['videoId','transcript'])},
    {'name':'list_videos','description':'List the video versions in the review library with file paths, frame rate, duration and comment counts.','inputSchema':obj({})},
    {'name':'list_comments','description':'Read review comments with timecodes, frame numbers, original-source frame mapping and reply threads. Defaults to open comments on every version.','inputSchema':obj({'videoId':{'type':'string','description':'Only this video version.'},'status':{'type':'string','enum':['open','done','all'],'default':'open'}})},
    {'name':'get_comment','description':'Read one comment and its reply thread.','inputSchema':obj({'commentId':{'type':'string'}},['commentId'])},
    {'name':'reply_to_comment','description':'Reply in a comment thread, for example to ask the reviewer a question or report progress. The comment stays open.','inputSchema':obj({'commentId':{'type':'string'},'text':{'type':'string'}},['commentId','text'])},
    {'name':'resolve_comment','description':'Mark a comment done after applying it, with a short summary of what changed. The summary appears as your reply under the comment.','inputSchema':obj({'commentId':{'type':'string'},'summary':{'type':'string','description':'What you changed, e.g. "Removed 0.4 s pause at 1:12.300."'}},['commentId','summary'])},
    {'name':'reopen_comment','description':'Reopen a resolved comment, optionally explaining why.','inputSchema':obj({'commentId':{'type':'string'},'reason':{'type':'string'}},['commentId'])},
    {'name':'add_comment','description':'Leave your own comment on a moment or range, e.g. to flag something for the reviewer to check.','inputSchema':obj({'videoId':{'type':'string'},'start':TIME,'end':{**TIME,'description':'Optional range end. '+TIME['description']},'text':{'type':'string'}},['videoId','start','text'])},
    {'name':'open_video','description':'Show a video version in the reviewer’s open Video Review page, optionally at a time or on a comment: for example the new cut you registered, or the moment you changed. The page switches within a few seconds. list_videos says which version the reviewer has open (selected) and where their playhead is. After re-rendering the file at the same path, pass its new durationMs and keepRanges: the page reloads the file.','inputSchema':obj({'videoId':{'type':'string'},'at':{**TIME,'description':'Optional time to jump to. '+TIME['description']},'commentId':{'type':'string','description':'Optional comment to show; jumps to its start unless at is given.'},
        'durationMs':{'type':'integer','description':'The re-rendered file\'s duration, if you replaced the file.'},'keepRanges':{'type':'array','items':{'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2},'description':'The re-rendered file\'s retained [startFrame, endFrame) ranges of the original footage, if you replaced the file.'}},['videoId'])},
    {'name':'register_video','description':'Add a different video to the review library so the reviewer can watch and comment on it. Use a new id for every video. Not for a render of review fixes: save that at the reviewed file\'s path (rename the old file first) so the comments stay on it.','inputSchema':obj({
        'id':{'type':'string','description':'New unique id, e.g. "episode-1-v4".'},'title':{'type':'string'},'label':{'type':'string','description':'Version label shown in the picker, e.g. "Episode 1 · v4 (fixes from review)".'},
        'path':{'type':'string','description':'Absolute path to the rendered video file.'},'fps':{'type':'number'},'durationMs':{'type':'integer'},
        'keepRanges':{'type':'array','items':{'type':'array','items':{'type':'number'},'minItems':2,'maxItems':2},'description':'Retained [startFrame, endFrame) ranges of the original footage, in playback order. Omit for an uncut video.'},
        'fingerprint':{'type':'string','description':'Optional; a SHA-256 of the file is computed if omitted.'}},['id','title','label','path','fps','durationMs'])},
]

def counts(video_id):
    items=[c for c in store.comments(DATA) if c['videoId']==video_id]
    return {'open':sum(c['status']=='open' for c in items),'done':sum(c['status']=='done' for c in items)}

def video_info(v):
    return {'id':v['id'],'title':v['title'],'label':v['label'],'path':v['path'],'fps':v['fps'],'duration':store.timecode(v['durationMs']),'durationMs':v['durationMs'],'fingerprint':v.get('fingerprint')}

def comment_view(cid):
    c=next((x for x in store.comments(DATA) if x['id']==cid),None)
    if not c:raise ValueError('Comment not found')
    video=store.find_video(DATA,c['videoId'])
    return {'video':video_info(video),'comment':store.describe(video,c)}

def call(name,args):
    if name=='get_transcript':return {'videoId':args['videoId'],'transcript':store.get_transcript(DATA,args['videoId'])}
    if name=='set_transcript':
        transcript=store.save_transcript(DATA,args['videoId'],args['transcript'])
        return {'videoId':args['videoId'],'words':len(transcript['words']),'revision':transcript['revision']}
    if name=='list_videos':
        view=store.view(DATA)
        return {'reviewUrl':f'http://127.0.0.1:{PORT}','selectedVideoId':view.get('videoId'),'playhead':store.timecode(view['playheadMs']) if view.get('playheadMs') is not None else None,
            'videos':[{**video_info(v),'selected':v['id']==view.get('videoId'),'comments':counts(v['id'])} for v in store.videos(DATA)]}
    if name=='list_comments':
        status=args.get('status','open');result=[]
        for v in store.videos(DATA):
            if args.get('videoId') and v['id']!=args['videoId']:continue
            items=sorted((c for c in store.comments(DATA) if c['videoId']==v['id'] and (status=='all' or c['status']==status)),key=lambda c:c['startMs'])
            if items:result.append({'video':video_info(v),'comments':[store.describe(v,c) for c in items]})
        if args.get('videoId') and not result:store.find_video(DATA,args['videoId'])
        return {'status':status,'versions':result,'total':sum(len(x['comments']) for x in result)}
    if name=='get_comment':return comment_view(args['commentId'])
    if name=='reply_to_comment':
        store.apply(DATA,{'action':'reply','id':args['commentId'],'author':'ai','text':args['text']});return comment_view(args['commentId'])
    if name=='resolve_comment':
        store.apply(DATA,{'action':'reply','id':args['commentId'],'author':'ai','text':args['summary'],'status':'done'});return comment_view(args['commentId'])
    if name=='reopen_comment':
        if args.get('reason'):store.apply(DATA,{'action':'reply','id':args['commentId'],'author':'ai','text':args['reason'],'status':'open'})
        else:store.apply(DATA,{'action':'update','id':args['commentId'],'status':'open'})
        return comment_view(args['commentId'])
    if name=='add_comment':
        start=store.parse_time(args['start']);end=store.parse_time(args.get('end'))
        if start is None:raise ValueError('start is required')
        _,c=store.apply(DATA,{'action':'add','videoId':args['videoId'],'startMs':start,'endMs':end,'text':args['text'],'author':'ai'})
        return comment_view(c['id'])
    if name=='open_video':
        store.update_render(DATA,args['videoId'],args.get('durationMs'),args.get('keepRanges'))
        request=store.request_view(DATA,args['videoId'],store.parse_time(args.get('at')),args.get('commentId'))
        return {'requested':request,'note':'The open Video Review page switches within a few seconds. If it is not open, ask the reviewer to open it.'}
    if name=='register_video':
        return {'registered':store.register_video(DATA,args),'reviewUrl':f'http://127.0.0.1:{PORT}'}
    raise LookupError(f'Unknown tool: {name}')

def handle(msg):
    method=msg.get('method');params=msg.get('params') or {}
    if method=='initialize':
        return {'protocolVersion':params.get('protocolVersion') or '2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'video-review','version':'1.0.0'},'instructions':INSTRUCTIONS}
    if method=='ping':return {}
    if method=='tools/list':return {'tools':TOOLS}
    if method=='tools/call':
        try:
            result=call(params.get('name'),params.get('arguments') or {})
            return {'content':[{'type':'text','text':json.dumps(result,indent=2,ensure_ascii=False)}]}
        except KeyError as e:return {'content':[{'type':'text','text':f'Missing argument: {e}'}],'isError':True}
        except LookupError:raise
        except (ValueError,TypeError) as e:return {'content':[{'type':'text','text':str(e)}],'isError':True}
    raise LookupError(f'Method not found: {method}')

def main():
    global DATA
    parser=argparse.ArgumentParser(description='Video Review MCP server (stdio)')
    parser.add_argument('--data-dir',default=None)
    DATA=store.data_dir(parser.parse_args().data_dir)
    out=sys.stdout.buffer
    for line in sys.stdin.buffer:
        if not line.strip():continue
        try:msg=json.loads(line)
        except json.JSONDecodeError:
            out.write(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}).encode()+b'\n');out.flush();continue
        if 'id' not in msg:continue  # notifications need no answer
        try:reply={'jsonrpc':'2.0','id':msg['id'],'result':handle(msg)}
        except LookupError as e:reply={'jsonrpc':'2.0','id':msg['id'],'error':{'code':-32601,'message':str(e)}}
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            reply={'jsonrpc':'2.0','id':msg['id'],'error':{'code':-32603,'message':str(e)}}
        out.write(json.dumps(reply,ensure_ascii=False).encode('utf-8')+b'\n');out.flush()

if __name__=='__main__':main()

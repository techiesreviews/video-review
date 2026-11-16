// Review rules shared by the browser and the Cloudflare Worker: timecodes, source mapping and comment changes.
// The rules themselves (fields, limits, who may write and edit, messages) are in review-rules.json, which
// review_store.py reads for the local Python app; tests/fixtures/review-rule-cases.json is run against both.
import RULES from './review-rules.json' with {type:'json'};
import {frame,timecode} from './time-format.js';

export {RULES,timecode};
export const AUTHORS=RULES.authors;
const M=RULES.messages,LIMITS=RULES.limits,TRIM=new Set(RULES.trimCharacters);
const SECONDS=new RegExp(RULES.patterns.seconds),TIMECODE=new RegExp(RULES.patterns.timecode),VIDEO_ID=new RegExp(RULES.patterns.videoId);
/** Length in characters (code points), as Python counts it: an emoji is one character, not two. */
const length=text=>[...text].length;
function trim(text){let a=0,b=text.length;while(a<b&&TRIM.has(text[a]))a++;while(b>a&&TRIM.has(text[b-1]))b--;return text.slice(a,b);}


/** Milliseconds from a number, or from h:mm:ss.mmm, m:ss.mmm or seconds. */
export function parseTime(value){
  if(value===null||value===undefined||value==='')return null;
  if(typeof value==='boolean'||(typeof value==='number'&&!Number.isFinite(value)))throw Error(M.time);
  if(typeof value==='number')return Math.round(value);
  const text=trim(String(value));
  if(SECONDS.test(text))return Math.round(Number(text)*1000);
  const m=text.match(TIMECODE);
  if(!m||Number(m[3])>59||(m[1]&&Number(m[2])>59))throw Error(M.timeFormat);
  return (Number(m[1]||0)*3600+Number(m[2])*60+Number(m[3]))*1000+Number((m[4]||'0').padEnd(3,'0'));
}

const round3=n=>Math.round(n*1000)/1000;
export function sourceSpans(video,startMs,endMs){
  const fps=video.fps,start=startMs*fps/1000,end=(endMs??startMs)*fps/1000;let cursor=0;const spans=[];
  for(const [a,b] of video.keepRanges||[]){
    const finish=cursor+b-a;
    if(endMs===null||endMs===undefined){if(start>=cursor&&start<finish)spans.push({sourceFrame:round3(a+start-cursor),sourceMs:round3((a+start-cursor)*1000/fps)});}
    else if(start<finish&&end>cursor){const lo=a+Math.max(0,start-cursor),hi=a+Math.min(b-a,end-cursor);spans.push({sourceStartFrame:round3(lo),sourceEndFrame:round3(hi),sourceStartMs:round3(lo*1000/fps),sourceEndMs:round3(hi*1000/fps)});}
    cursor=finish;
  }
  return spans;
}

/** Move displayed comment times after a render, keeping immutable source anchors. Call applyRender, which also saves
 * the new mapping on the video, rather than this alone. */
export function rebaseComments(video,comments,keepRanges,durationMs){
  const same=JSON.stringify(video.keepRanges)===JSON.stringify(keepRanges);if(same)return;
  function mapped(frame){let cursor=0,best={distance:Infinity,at:0};for(const [a,b] of keepRanges){const clamped=Math.max(a,Math.min(b,frame)),distance=Math.abs(frame-clamped);if(distance<best.distance)best={distance,at:cursor+clamped-a};if(frame>=a&&frame<b)return Math.round((cursor+frame-a)*1000/video.fps);cursor+=b-a;}return Math.min(durationMs,Math.round(best.at*1000/video.fps));}
  for(const comment of comments){if(comment.videoId!==video.id)continue;
    const spans=comment.sourceSpans??comment.editRequest?.sourceSpans??sourceSpans(video,comment.startMs,comment.endMs);
    if(!spans.length)continue;comment.sourceSpans=spans;
    comment.beforeRecut={startMs:comment.startMs,endMs:comment.endMs};
    const first=spans[0],last=spans.at(-1),start=mapped(first.sourceStartFrame??first.sourceFrame),end=comment.endMs!=null?mapped(last.sourceEndFrame??last.sourceFrame):null;
    comment.startMs=start;comment.endMs=end>start?end:null;
  }
}

/** Records a re-rendered video's new length and source mapping, after moving its comments to their places in the new
 * cut. Nothing else runs in between (the local app holds the comments' lock for this), so a comment added next is
 * mapped with the new frames. */
export function applyRender(state,videoId,render){
  const video=state.videos.find(v=>v.id===videoId);if(!video)throw Error(M.unknownVideo);
  const changes=checkRender(render);
  if(changes.keepRanges)rebaseComments(video,state.comments,changes.keepRanges,changes.durationMs??video.durationMs);
  return Object.assign(video,changes);
}

/** Replies, with a legacy single `resolution` shown as an AI reply. */
export function thread(c){const replies=[...(c.replies||[])];if(c.resolution&&!replies.some(r=>r.legacy))replies.unshift({id:'resolution',author:RULES.agentAuthor,text:c.resolution,createdAt:c.updatedAt||c.createdAt,legacy:true});return replies;}

/** When the reviewer last said something on this comment: writing it, editing it or replying. */
export function reviewerStamp(c){let stamp=(c.author||RULES.pageAuthor)===RULES.pageAuthor?c.createdAt||'':'';if(c.editedAt>stamp)stamp=c.editedAt;for(const r of c.replies||[])if(r.author===RULES.pageAuthor&&r.createdAt>stamp)stamp=r.createdAt;return stamp;}

/** What the reviewer asked an agent to do, in one field: the comment's current text is the instruction. */
function agentRequest(video,c){
  const e=c.editRequest,hasEnd=c.endMs!==null&&c.endMs!==undefined;
  return {type:'change',instruction:c.text,quote:e.quote??null,wordIds:e.wordIds||[],firstWordId:e.firstWordId??null,lastWordId:e.lastWordId??null,
    startFrame:frame(c.startMs,video.fps),endFrame:hasEnd?frame(c.endMs,video.fps):null,
    sourceSpans:c.sourceSpans??e.sourceSpans??sourceSpans(video,c.startMs,hasEnd?c.endMs:null),transcriptRevision:e.transcriptRevision??null,...(e.cutId?{cutId:e.cutId}:{})};
}

export function describe(video,c){
  const fps=video.fps,hasEnd=c.endMs!==null&&c.endMs!==undefined;
  return {id:c.id,videoId:c.videoId,status:c.status,author:c.author||RULES.pageAuthor,text:c.text,
    start:timecode(c.startMs),end:hasEnd?timecode(c.endMs):null,startMs:c.startMs,endMs:hasEnd?c.endMs:null,
    startFrame:frame(c.startMs,fps),endFrame:hasEnd?frame(c.endMs,fps):null,
    sourceSpans:c.sourceSpans??c.editRequest?.sourceSpans??sourceSpans(video,c.startMs,hasEnd?c.endMs:null),
    ...(c.editRequest?{editRequest:c.editRequest}:{}),
    ...(c.editRequest?.type==='change'?{agentRequest:agentRequest(video,c)}:{}),
    replies:thread(c).map(r=>({author:r.author,text:r.text,createdAt:r.createdAt})),createdAt:c.createdAt};
}

function checkText(text){if(text==null)text='';if(typeof text!=='string')throw Error(M.text);text=trim(text);if(!text||length(text)>LIMITS.textLength)throw Error(M.text);return text;}
function checkAuthor(author){if(!AUTHORS.includes(author))throw Error(M.author);return author;}
function checkKeepRanges(ranges){
  if(!Array.isArray(ranges)||!ranges.every(r=>Array.isArray(r)&&r.length===2&&r.every(n=>typeof n==='number'&&Number.isFinite(n))&&r[0]>=0&&r[0]<r[1]))throw Error(M.keepRanges);
  return ranges;
}
const uuid=()=>crypto.randomUUID();

export function checkEditRequest(request,video,startMs,endMs){
  if(request==null)return null;
  if(!request||typeof request!=='object'||Array.isArray(request)||!RULES.editRequestTypes.includes(request.type))throw Error(M.editRequest);
  const result={type:request.type};
  for(const key of RULES.selectionFields)if(request[key]!=null){
    if(typeof request[key]!=='string'||length(request[key])>(key==='quote'?LIMITS.quoteLength:LIMITS.idLength))throw Error(M.selection);
    result[key]=request[key];
  }
  result.sourceSpans=sourceSpans(video,startMs,endMs);
  if(request.type==='change'){
    const ids=request.wordIds??[];
    if(!Array.isArray(ids)||ids.length>LIMITS.wordIds||ids.some(id=>typeof id!=='string'||length(id)>LIMITS.idLength))throw Error(M.wordIds);
    result.wordIds=[...ids];result.startFrame=frame(startMs,video.fps);result.endFrame=endMs==null?null:frame(endMs,video.fps);
  }
  if(request.type==='restore'){
    const {sourceStartMs:a,sourceEndMs:b,sourceDurationMs:d}=request;
    if(![a,b,d].every(Number.isFinite)||a<0||b<=a||b>d||d>LIMITS.sourceDurationMs)throw Error(M.restoreRange);
    result.sourceStartMs=Math.round(a);result.sourceEndMs=Math.round(b);result.sourceDurationMs=Math.round(d);
    if(request.sourceId!=null){if(typeof request.sourceId!=='string'||length(request.sourceId)>LIMITS.idLength)throw Error(M.sourceId);result.sourceId=request.sourceId;}
    result.sourceSpans=[{sourceStartFrame:round3(a*video.fps/1000),sourceEndFrame:round3(b*video.fps/1000),sourceStartMs:round3(a),sourceEndMs:round3(b)}];
  }
  return result;
}

/** Applies one change to {videos, comments} in place and returns the changed comment (null for a delete). */
export function applyChange(state,cmd,now=new Date().toISOString()){
  const items=state.comments;
  const get=id=>{const c=items.find(x=>x.id===id);if(!c)throw Error(M.commentNotFound);return c;};
  switch(cmd.action){
    case 'add':{
      const video=state.videos.find(v=>v.id===cmd.videoId);if(!video)throw Error(M.unknownVideo);
      const start=cmd.startMs,end=cmd.endMs??null;
      if(!Number.isInteger(start)||start<0||start>video.durationMs)throw Error(M.timestampOutside);
      if(end!==null&&(!Number.isInteger(end)||end<=start||end>video.durationMs))throw Error(M.rangeEnd);
      const c={id:uuid(),videoId:video.id,startMs:start,endMs:end,text:checkText(cmd.text),category:cmd.category||RULES.defaultCategory,status:RULES.newStatus,author:checkAuthor(cmd.author||RULES.pageAuthor),createdAt:cmd.createdAt||now,replies:[]};
      c.sourceSpans=sourceSpans(video,start,end);
      if(cmd.editRequest!=null)c.editRequest=checkEditRequest(cmd.editRequest,video,start,end);
      if(c.editRequest?.type==='restore')c.sourceSpans=c.editRequest.sourceSpans;
      items.push(c);return c;
    }
    case 'update':{
      const c=get(cmd.id);
      // Everything is checked before anything changes, so a refused change leaves the comment as it was.
      if('status' in cmd&&!RULES.statuses.includes(cmd.status))throw Error(M.status);
      if('text' in cmd&&!RULES.textEditableBy.includes(c.author||RULES.pageAuthor))throw Error(M.notYourComment);
      const text='text' in cmd?checkText(cmd.text):null;
      if('status' in cmd)c.status=cmd.status;
      if(text!==null){c.text=text;c.editedAt=now;}
      c.updatedAt=now;return c;
    }
    case 'reply':{
      const c=get(cmd.id),reply={id:uuid(),author:checkAuthor(cmd.author||RULES.pageAuthor),text:checkText(cmd.text),createdAt:now};
      (c.replies||(c.replies=[])).push(reply);
      if(RULES.statuses.includes(cmd.status))c.status=cmd.status;
      c.updatedAt=now;return c;
    }
    case 'delete':{get(cmd.id);state.comments=items.filter(x=>x.id!==cmd.id);return null;}
    default:throw Error(M.action);
  }
}

/** Checks the new duration and keepRanges the agent sends after re-rendering a video under the same file name. */
export function checkRender({durationMs,keepRanges}={}){
  const changes={};
  if(durationMs!==undefined&&durationMs!==null){changes.durationMs=parseTime(durationMs);if(!(changes.durationMs>0))throw Error(M.duration);}
  if(keepRanges!==undefined&&keepRanges!==null)changes.keepRanges=checkKeepRanges(keepRanges);
  return changes;
}

/** Checks a new video version announced by the agent or added in the browser. */
export function checkVideo(entry,videos){
  for(const key of ['id','title','label','fps','durationMs'])if(entry[key]===undefined||entry[key]===null||entry[key]==='')throw Error(`Missing ${key}`);
  if(!VIDEO_ID.test(entry.id))throw Error(M.videoIdFormat);
  if(videos.some(v=>v.id===entry.id))throw Error(M.videoIdTaken);
  const fps=Number(entry.fps),durationMs=parseTime(entry.durationMs);
  if(!(fps>0))throw Error(M.fps);
  if(!(durationMs>0))throw Error(M.duration);
  const keep=checkKeepRanges(entry.keepRanges||[[0,Math.round(durationMs*fps/1000)]]);
  if(entry.sha256!=null&&!/^[0-9a-f]{64}$/i.test(entry.sha256))throw Error('sha256 must be 64 hex digits');
  return {id:entry.id,title:String(entry.title),label:String(entry.label),fps,durationMs,keepRanges:keep,fileName:entry.fileName?String(entry.fileName):null,fileSize:entry.fileSize??null,fingerprint:entry.fingerprint??null,sha256:entry.sha256?.toLowerCase()??null,addedBy:entry.addedBy||RULES.pageAuthor,createdAt:entry.createdAt||new Date().toISOString()};
}

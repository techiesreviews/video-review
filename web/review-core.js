// Review rules shared by the browser and the Cloudflare Worker: timecodes, source mapping and comment changes.
// Mirrors review_store.py, which does the same for the local Python app.

export const AUTHORS=['you','ai'];

export function timecode(ms){ms=Math.max(0,Math.round(ms));const h=Math.floor(ms/3600000),m=Math.floor(ms/60000)%60,s=Math.floor(ms/1000)%60;return [h,m,s].map(x=>String(x).padStart(2,'0')).join(':')+'.'+String(ms%1000).padStart(3,'0');}

/** Milliseconds from a number, or from h:mm:ss.mmm, m:ss.mmm or seconds. */
export function parseTime(value){
  if(value===null||value===undefined||value==='')return null;
  if(typeof value==='boolean')throw Error('Invalid time');
  if(typeof value==='number')return Math.round(value);
  const text=String(value).trim();
  if(/^\d+(\.\d+)?$/.test(text))return Math.round(Number(text)*1000);
  const m=text.match(/^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?$/);
  if(!m||Number(m[3])>59||(m[1]&&Number(m[2])>59))throw Error('Use h:mm:ss.mmm, m:ss.mmm, seconds or milliseconds');
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

/** Move displayed comment times after a render, keeping immutable source anchors. */
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

/** Replies, with a legacy single `resolution` shown as an AI reply. */
export function thread(c){const replies=[...(c.replies||[])];if(c.resolution&&!replies.some(r=>r.legacy))replies.unshift({id:'resolution',author:'ai',text:c.resolution,createdAt:c.updatedAt||c.createdAt,legacy:true});return replies;}

/** When the reviewer last said something on this comment: writing it, editing it or replying. */
export function reviewerStamp(c){let stamp=(c.author||'you')==='you'?c.createdAt||'':'';if(c.editedAt>stamp)stamp=c.editedAt;for(const r of c.replies||[])if(r.author==='you'&&r.createdAt>stamp)stamp=r.createdAt;return stamp;}

export function describe(video,c){
  const fps=video.fps,hasEnd=c.endMs!==null&&c.endMs!==undefined;
  return {id:c.id,videoId:c.videoId,status:c.status,author:c.author||'you',text:c.text,
    start:timecode(c.startMs),end:hasEnd?timecode(c.endMs):null,startMs:c.startMs,endMs:hasEnd?c.endMs:null,
    startFrame:Math.round(c.startMs*fps/1000),endFrame:hasEnd?Math.round(c.endMs*fps/1000):null,
    sourceSpans:c.sourceSpans??c.editRequest?.sourceSpans??sourceSpans(video,c.startMs,hasEnd?c.endMs:null),
    ...(c.editRequest?{editRequest:c.editRequest}:{}),
    replies:thread(c).map(r=>({author:r.author,text:r.text,createdAt:r.createdAt})),createdAt:c.createdAt};
}

function checkText(text){text=String(text??'').trim();if(!text||text.length>10000)throw Error('Please enter a comment (up to 10,000 characters)');return text;}
function checkAuthor(author){if(!AUTHORS.includes(author))throw Error('Author must be "you" or "ai"');return author;}
const uuid=()=>crypto.randomUUID();

export function checkEditRequest(request,video,startMs,endMs){
  if(request==null)return null;
  if(!request||typeof request!=='object'||!['remove','restore','clipped','extend_before','extend_after','mistake','pause','comment'].includes(request.type))throw Error('Invalid transcript edit request');
  const result={type:request.type};
  for(const key of ['quote','firstWordId','lastWordId','cutId','transcriptRevision'])if(request[key]!=null){
    if(typeof request[key]!=='string'||request[key].length>(key==='quote'?3000:120))throw Error('Invalid transcript selection');
    result[key]=request[key];
  }
  result.sourceSpans=sourceSpans(video,startMs,endMs);
  if(request.type==='restore'){
    const {sourceStartMs:a,sourceEndMs:b,sourceDurationMs:d}=request;
    if(![a,b,d].every(Number.isFinite)||a<0||b<=a||b>d||d>86400000)throw Error('Invalid original-recording restore range');
    result.sourceStartMs=Math.round(a);result.sourceEndMs=Math.round(b);result.sourceDurationMs=Math.round(d);
    if(request.sourceId!=null){if(typeof request.sourceId!=='string'||request.sourceId.length>120)throw Error('Invalid original source ID');result.sourceId=request.sourceId;}
    result.sourceSpans=[{sourceStartFrame:round3(a*video.fps/1000),sourceEndFrame:round3(b*video.fps/1000),sourceStartMs:round3(a),sourceEndMs:round3(b)}];
  }
  return result;
}

/** Applies one change to {videos, comments} in place and returns the changed comment (null for a delete). */
export function applyChange(state,cmd,now=new Date().toISOString()){
  const items=state.comments;
  const get=id=>{const c=items.find(x=>x.id===id);if(!c)throw Error('Comment not found');return c;};
  switch(cmd.action){
    case 'add':{
      const video=state.videos.find(v=>v.id===cmd.videoId);if(!video)throw Error('Unknown video version');
      const start=cmd.startMs,end=cmd.endMs??null;
      if(!Number.isInteger(start)||start<0||start>video.durationMs)throw Error('Timestamp outside video');
      if(end!==null&&(!Number.isInteger(end)||end<=start||end>video.durationMs))throw Error('Range end must follow start');
      const c={id:uuid(),videoId:video.id,startMs:start,endMs:end,text:checkText(cmd.text),category:cmd.category||'edit',status:'open',author:checkAuthor(cmd.author||'you'),createdAt:cmd.createdAt||now,replies:[]};
      c.sourceSpans=sourceSpans(video,start,end);
      if(cmd.editRequest!=null)c.editRequest=checkEditRequest(cmd.editRequest,video,start,end);
      if(c.editRequest?.type==='restore')c.sourceSpans=c.editRequest.sourceSpans;
      items.push(c);return c;
    }
    case 'update':{
      const c=get(cmd.id);
      if('status' in cmd){if(!['open','done'].includes(cmd.status))throw Error('Invalid status');c.status=cmd.status;}
      if('text' in cmd){c.text=checkText(cmd.text);c.editedAt=now;}
      c.updatedAt=now;return c;
    }
    case 'reply':{
      const c=get(cmd.id);(c.replies||(c.replies=[])).push({id:uuid(),author:checkAuthor(cmd.author||'you'),text:checkText(cmd.text),createdAt:now});
      if(['open','done'].includes(cmd.status))c.status=cmd.status;
      c.updatedAt=now;return c;
    }
    case 'delete':{get(cmd.id);state.comments=items.filter(x=>x.id!==cmd.id);return null;}
    default:throw Error('Unsupported action');
  }
}

/** Checks the new duration and keepRanges the agent sends after re-rendering a video under the same file name. */
export function checkRender({durationMs,keepRanges}={}){
  const changes={};
  if(durationMs!==undefined&&durationMs!==null){changes.durationMs=parseTime(durationMs);if(!(changes.durationMs>0))throw Error('durationMs must be positive');}
  if(keepRanges!==undefined&&keepRanges!==null){if(!Array.isArray(keepRanges)||!keepRanges.every(r=>Array.isArray(r)&&r.length===2&&r[0]>=0&&r[0]<r[1]))throw Error('keepRanges must be [[startFrame, endFrame], …]');changes.keepRanges=keepRanges;}
  return changes;
}

/** Checks a new video version announced by the agent or added in the browser. */
export function checkVideo(entry,videos){
  for(const key of ['id','title','label','fps','durationMs'])if(entry[key]===undefined||entry[key]===null||entry[key]==='')throw Error(`Missing ${key}`);
  if(!/^[A-Za-z0-9._-]{1,100}$/.test(entry.id))throw Error('Use letters, digits, dot, dash or underscore for the id');
  if(videos.some(v=>v.id===entry.id))throw Error('That id already exists; use a new id for a new cut');
  const fps=Number(entry.fps),durationMs=parseTime(entry.durationMs);
  if(!(fps>0))throw Error('fps must be a positive number');
  if(!(durationMs>0))throw Error('durationMs must be positive');
  const keep=entry.keepRanges||[[0,Math.round(durationMs*fps/1000)]];
  if(!Array.isArray(keep)||!keep.every(r=>Array.isArray(r)&&r.length===2&&r[0]>=0&&r[0]<r[1]))throw Error('keepRanges must be [[startFrame, endFrame], …]');
  return {id:entry.id,title:String(entry.title),label:String(entry.label),fps,durationMs,keepRanges:keep,fileName:entry.fileName?String(entry.fileName):null,fileSize:entry.fileSize??null,fingerprint:entry.fingerprint??null,addedBy:entry.addedBy||'you',createdAt:entry.createdAt||new Date().toISOString()};
}

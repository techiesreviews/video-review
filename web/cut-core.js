// Preview cuts use the loaded media clock; originals and comment times stay intact.
export function normalizeDraftEdits(edits=[],durationMs=Infinity){
  if(!Array.isArray(edits)||edits.length>5000)throw Error('Invalid preview cuts.');
  const ids=new Set();
  return edits.map(edit=>{
    if(!edit||typeof edit.id!=='string'||!edit.id||edit.id.length>120||ids.has(edit.id)||!Number.isFinite(edit.startMs)||!Number.isFinite(edit.endMs)||edit.startMs<0||Math.round(edit.endMs)<=Math.round(edit.startMs)||edit.endMs>durationMs)throw Error('A preview cut lies outside this video.');
    ids.add(edit.id);
    if(!Array.isArray(edit.wordIds)||edit.wordIds.some(id=>typeof id!=='string'||id.length>120))throw Error('Invalid removed word IDs.');
    return {id:edit.id,startMs:Math.round(edit.startMs),endMs:Math.round(edit.endMs),wordIds:[...edit.wordIds],quote:String(edit.quote||'').slice(0,3000),kind:edit.kind==='pause'?'pause':'words',commentId:typeof edit.commentId==='string'?edit.commentId:null};
  });
}
export function removedRanges(edits=[]){
  const result=[];for(const edit of [...edits].sort((a,b)=>a.startMs-b.startMs)){const last=result.at(-1);if(last&&edit.startMs<=last[1])last[1]=Math.max(last[1],edit.endMs);else result.push([edit.startMs,edit.endMs]);}return result;
}
export function skipRemoved(ms,edits=[]){const range=removedRanges(edits).find(([a,b])=>ms>=a&&ms<b);return range?range[1]:ms;}
/** What playback skips: the cut's removals while the cuts switch is on; a Hear cut audition is a removal being tried and always plays skipped. */
export function playbackRemovals(cutsOn,edits=[],audition=null){return [...(cutsOn?edits:[]),...(audition?[audition]:[])];}
export function previewDuration(durationMs,edits=[]){return durationMs-removedRanges(edits).reduce((n,[a,b])=>n+b-a,0);}
export const JOIN_FADE_MS=15;
/** The review-file ranges left after the removals, in playback order. */
export function keptRanges(durationMs,edits=[]){
  const kept=[];let at=0;
  for(const [a,b] of removedRanges(edits)){if(a>=durationMs)break;if(a>at)kept.push([at,a]);at=Math.max(at,b);}
  if(at<durationMs)kept.push([at,durationMs]);return kept;
}
export function cutLength(durationMs,edits=[]){return keptRanges(durationMs,edits).reduce((n,[a,b])=>n+b-a,0);}
/** Review-file time to cut time. A position inside a removal maps to its join. */
export function fileToCut(ms,durationMs,edits=[]){
  let cut=0;if(!(ms>0))return 0;
  for(const [a,b] of keptRanges(durationMs,edits)){if(ms<a)return cut;if(ms<b)return cut+ms-a;cut+=b-a;}
  return cut;
}
/** Cut time to review-file time. A join belongs to the material after it, so this only lands on a removal at the very end of the cut. */
export function cutToFile(ms,durationMs,edits=[]){
  const kept=keptRanges(durationMs,edits);if(!kept.length)return 0;if(!(ms>0))return kept[0][0];
  let cut=0;for(const [a,b] of kept){if(ms<cut+b-a)return a+ms-cut;cut+=b-a;}
  return kept.at(-1)[1];
}
/** The next jump within the look-ahead: when to make it and when to start its fade, in real milliseconds from now. */
export function jumpPlan(ms,edits=[],{fadeMs=JOIN_FADE_MS,rate=1,horizonMs=70}={}){
  for(const [a,b] of removedRanges(edits)){
    if(ms>=b)continue;
    if(ms>=a)return {startMs:a,toMs:b,jumpInMs:0,fadeInMs:0};
    const jumpInMs=(a-ms)/rate;if(jumpInMs>horizonMs)return null;
    return {startMs:a,toMs:b,jumpInMs,fadeInMs:Math.max(0,jumpInMs-fadeMs)};
  }
  return null;
}
export function removalBounds(transcript,selection,durationMs,fps=30){
  const words=transcript.words,wordIds=selection.kind==='pause'?[]:words.filter(w=>w.startMs<selection.endMs&&w.endMs>selection.startMs).map(w=>w.id);
  let start=selection.startMs,end=selection.endMs;
  if(!selection.precise&&selection.kind!=='pause'){
    const first=words.findIndex(w=>w.id===wordIds[0]),last=words.findIndex(w=>w.id===wordIds.at(-1)),before=words[first-1],after=words[last+1];
    if(first>=0&&before)start=Math.max(before.endMs,start-Math.min(120,Math.max(0,start-before.endMs)/2));
    if(last>=0&&after)end=Math.min(after.startMs,end+Math.min(120,Math.max(0,after.startMs-end)/2));
  }else if(!selection.precise){const room=Math.min(180,(end-start)/3);start+=room;end-=room;}
  start=Math.max(0,(selection.precise?Math.round:Math.ceil)(start*fps/1000)*1000/fps);end=Math.min(durationMs,(selection.precise?Math.round:Math.floor)(end*fps/1000)*1000/fps);
  if(end-start<1000/fps)throw Error('This selection is too short for a safe frame-aligned cut.');
  return {startMs:Math.round(start),endMs:Math.round(end),wordIds,kind:selection.kind==='pause'?'pause':'words',quote:selection.quote};
}
export function makeRemoval(transcript,selection,durationMs,fps=30){
  const edit={id:crypto.randomUUID(),...removalBounds(transcript,selection,durationMs,fps)};
  if(previewDuration(durationMs,[...(transcript.draftEdits||[]),edit])<1000/fps)throw Error('Keep at least one frame in the video.');return edit;
}
export function exportCut(transcript,video){
  const duration=video.durationMs,removed=removedRanges(transcript.draftEdits),kept=[];let at=0;
  for(const [a,b] of removed){if(a>at)kept.push([at,a]);at=b;}if(at<duration)kept.push([at,duration]);
  const fps=video.fps,sourceRanges=video.keepRanges||[[0,Math.round(duration*fps/1000)]],keepRanges=[];
  for(const [a,b] of kept){let cursor=0;for(const [start,end] of sourceRanges){const length=(end-start)/fps*1000,left=Math.max(a,cursor),right=Math.min(b,cursor+length);if(right>left)keepRanges.push([Math.round(start+(left-cursor)*fps/1000),Math.round(start+(right-cursor)*fps/1000)]);cursor+=length;}}
  return {schemaVersion:1,videoId:video.id,transcriptRevision:transcript.revision,fps,keepRanges,draftEdits:transcript.draftEdits||[],durationMs:Math.round(keepRanges.reduce((n,[a,b])=>n+b-a,0)/fps*1000),note:'Preview cut decisions for the existing Resolve timeline; original media and review timestamps are unchanged.'};
}

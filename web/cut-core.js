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
export function previewDuration(durationMs,edits=[]){return durationMs-removedRanges(edits).reduce((n,[a,b])=>n+b-a,0);}
export function makeRemoval(transcript,selection,durationMs,fps=30){
  const words=transcript.words,wordIds=selection.kind==='pause'?[]:words.filter(w=>w.startMs<selection.endMs&&w.endMs>selection.startMs).map(w=>w.id);
  let start=selection.startMs,end=selection.endMs;
  if(selection.kind!=='pause'){
    const first=words.findIndex(w=>w.id===wordIds[0]),last=words.findIndex(w=>w.id===wordIds.at(-1)),before=words[first-1],after=words[last+1];
    if(first>=0&&before)start=Math.max(before.endMs,start-Math.min(120,Math.max(0,start-before.endMs)/2));
    if(last>=0&&after)end=Math.min(after.startMs,end+Math.min(120,Math.max(0,after.startMs-end)/2));
  }else{const room=Math.min(180,(end-start)/3);start+=room;end-=room;}
  start=Math.max(0,Math.ceil(start*fps/1000)*1000/fps);end=Math.min(durationMs,Math.floor(end*fps/1000)*1000/fps);
  if(end-start<1000/fps)throw Error('This selection is too short for a safe frame-aligned cut.');
  const edit={id:crypto.randomUUID(),startMs:Math.round(start),endMs:Math.round(end),wordIds,kind:selection.kind==='pause'?'pause':'words',quote:selection.quote};
  if(previewDuration(durationMs,[...(transcript.draftEdits||[]),edit])<1000/fps)throw Error('Keep at least one frame in the video.');return edit;
}
export function exportCut(transcript,video){
  const duration=video.durationMs,removed=removedRanges(transcript.draftEdits),kept=[];let at=0;
  for(const [a,b] of removed){if(a>at)kept.push([at,a]);at=b;}if(at<duration)kept.push([at,duration]);
  const fps=video.fps,sourceRanges=video.keepRanges||[[0,Math.round(duration*fps/1000)]],keepRanges=[];
  for(const [a,b] of kept){let cursor=0;for(const [start,end] of sourceRanges){const length=(end-start)/fps*1000,left=Math.max(a,cursor),right=Math.min(b,cursor+length);if(right>left)keepRanges.push([Math.round(start+(left-cursor)*fps/1000),Math.round(start+(right-cursor)*fps/1000)]);cursor+=length;}}
  return {schemaVersion:1,videoId:video.id,transcriptRevision:transcript.revision,fps,keepRanges,draftEdits:transcript.draftEdits||[],durationMs:Math.round(keepRanges.reduce((n,[a,b])=>n+b-a,0)/fps*1000),note:'Preview cut decisions for the existing Resolve timeline; original media and review timestamps are unchanged.'};
}

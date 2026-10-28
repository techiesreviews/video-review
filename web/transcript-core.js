import {normalizeDraftEdits} from './cut-core.js';
// Word timings are navigation/review anchors, never a claim that a cut sounds right.
export function normalizeTranscript(data, durationMs=Infinity, videoFps=null) {
  let input=Array.isArray(data)?data:(data?.words??data?.transcript??data?.paragraphs);
  if(!Array.isArray(input))throw Error('Choose a word-timed JSON transcript, such as a HappyScribe JSON export.');
  if(input.some(p=>p&&Array.isArray(p.words)))input=input.flatMap(p=>p.words||[]).filter(w=>(w.type||'word')==='word');
  if(!input.length||input.length>50000)throw Error('The transcript must contain 1–50,000 timed words.');
  let previous=-1;const ids=new Set();
  const words=input.map((w,i)=>{
    const array=Array.isArray(w),text=String(array?w[0]:w.text??'').trim();
    const startMs=Math.round(array?w[1]*1000:w.startMs??(w.data_start??w.start)*1000);
    const endMs=Math.round(array?w[2]*1000:w.endMs??(w.data_end??w.end)*1000);
    if(!text||text.length>500||!Number.isFinite(startMs)||!Number.isFinite(endMs)||startMs<0||endMs<startMs||startMs<previous)
      throw Error(`Word ${i+1} has missing or invalid timing. Paragraph-only timestamps cannot align individual words.`);
    if(endMs>durationMs+50)throw Error('This transcript is longer than the video. Use the transcript derived for this cut, or open the original recording.');
    previous=startMs;const id=String(w.id??(w.rawWordId!=null?'W'+String(w.rawWordId).padStart(4,'0'):'word-'+(i+1)));
    if(ids.has(id)||id.length>120)throw Error('Transcript word IDs must be unique.');ids.add(id);
    const word={id,text,startMs,endMs};
    for(const key of ['sourceStartMs','sourceEndMs'])if(Number.isFinite(w[key]))word[key]=w[key];
    if(w.clipId!=null)word.clipId=String(w.clipId).slice(0,120);
    return word;
  });
  const cuts=(data?.cuts||[]).map((c,i)=>{
    if(!Number.isFinite(c.atMs)||c.atMs<0||c.atMs>durationMs)throw Error('A transcript cut lies outside this video.');
    return {id:String(c.id??'J'+(i+1)).slice(0,100),atMs:Math.round(c.atMs),risk:!!c.risk};
  });
  const mapping={};
  if(data?.keepRanges!=null){
    const fps=Number(data.fps),ranges=data.keepRanges;
    if(!Number.isFinite(fps)||fps<=0||(videoFps!=null&&Math.abs(fps-videoFps)>.001))throw Error('The transcript frame rate does not match this video.');
    if(!Array.isArray(ranges)||!ranges.length||!ranges.every(r=>Array.isArray(r)&&r.length===2&&r.every(Number.isFinite)&&r[0]>=0&&r[1]>r[0]))throw Error('Invalid transcript source mapping.');
    const frames=ranges.reduce((n,[a,b])=>n+b-a,0);
    if(Math.abs(frames/fps*1000-durationMs)>1000/fps)throw Error('The transcript source mapping belongs to a different cut.');
    mapping.keepRanges=ranges.map(r=>[...r]);mapping.fps=fps;
  }
  let original=null;
  if(data?.original!=null){
    const o=data.original;if(!o||!Number.isFinite(o.durationMs)||o.durationMs<=0||o.durationMs>86400000)throw Error('Invalid original recording duration.');
    const source=normalizeTranscript({words:o.words},o.durationMs).words,sourceIds=new Set(source.map(w=>w.id));
    if(words.some(w=>!sourceIds.has(w.id)))throw Error('The original transcript must share the review transcript’s word IDs.');
    original={durationMs:Math.round(o.durationMs),label:String(o.label||'Original recording').slice(0,120),sourceId:String(o.sourceId||'').slice(0,120),words:source};
  }
  return {schemaVersion:1,revision:String(data?.revision??'imported').slice(0,120),...mapping,...(original?{original}:{}),
    label:String(data?.label??'Word-timed transcript').slice(0,120),words,cuts,draftEdits:normalizeDraftEdits(data?.draftEdits||[],durationMs)};
}

// Removed source ranges use the recording clock; preview cuts use the review clock.
export function originalOmissions(transcript,video){
  if(!transcript?.original||!video?.keepRanges?.length)return [];
  const original=transcript.original,keptIds=new Set(transcript.words.map(w=>w.id)),ranges=[];
  for(const [a,b] of [...video.keepRanges].sort((x,y)=>x[0]-y[0])){const start=a/video.fps*1000,end=b/video.fps*1000,last=ranges.at(-1);if(last&&start<=last[1])last[1]=Math.max(last[1],end);else ranges.push([start,end]);}
  let at=0;const gaps=[];for(const [a,b] of ranges){if(a-at>=100)gaps.push([at,a]);at=Math.max(at,b);}if(original.durationMs-at>=100)gaps.push([at,original.durationMs]);
  const rawById=new Map(original.words.map(w=>[w.id,w]));
  const used=new Set();return gaps.map(([startMs,endMs],index)=>{
    const words=original.words.filter(w=>!keptIds.has(w.id)&&!used.has(w.id)&&w.startMs<endMs&&w.endMs>startMs);words.forEach(w=>used.add(w.id));
    const next=transcript.words.find(w=>(w.sourceStartMs??rawById.get(w.id)?.startMs??Infinity)>=endMs-.5);
    return {id:'source-gap-'+index,sourceStartMs:Math.round(startMs),sourceEndMs:Math.round(endMs),words,beforeWordId:next?.id??null,atMs:sourceToReview(startMs,video)};
  });
}
export function sourceToReview(sourceMs,video){
  const frame=sourceMs*video.fps/1000;let cursor=0,best={distance:Infinity,at:0};
  for(const [a,b] of video.keepRanges||[]){const clamped=Math.max(a,Math.min(b,frame)),distance=Math.abs(frame-clamped);if(distance<best.distance)best={distance,at:cursor+clamped-a};if(frame>=a&&frame<b)return Math.round((cursor+frame-a)*1000/video.fps);cursor+=b-a;}
  return Math.min(video.durationMs,Math.round(best.at*1000/video.fps));
}

export function activeWordIndex(words,ms){
  let low=0,high=words.length-1,best=-1;
  while(low<=high){const mid=(low+high)>>1;if(words[mid].startMs<=ms){best=mid;low=mid+1;}else high=mid-1;}
  return best>=0&&ms<words[best].endMs?best:-1;
}

export function wordSelection(words,a,b=a){
  const selected=words.slice(Math.min(a,b),Math.max(a,b)+1);
  if(!selected.length)throw Error('Select a word or phrase first.');
  return {startMs:selected[0].startMs,endMs:Math.max(selected[0].startMs+1,selected.at(-1).endMs),
    quote:selected.map(w=>w.text).join(' '),firstWordId:selected[0].id,lastWordId:selected.at(-1).id};
}

export function searchLibrary(videos,transcripts,query=''){
  const term=query.trim().toLowerCase().slice(0,200),results=[];
  for(const video of videos){
    const words=transcripts[video.id]?.words||[],text=words.map(w=>w.text).join(' '),position=term?text.toLowerCase().indexOf(term):-1;
    let atMs=null,snippet='';
    if(position>=0){let offset=0,index=0;while(index<words.length-1&&offset+words[index].text.length<=position){offset+=words[index].text.length+1;index++;}atMs=words[index].startMs;snippet=words.slice(Math.max(0,index-3),Math.min(words.length,index+11)).map(w=>w.text).join(' ');}
    if(!term||`${video.title} ${video.label} ${video.fileName||''}`.toLowerCase().includes(term)||position>=0)
      results.push({id:video.id,title:video.title,label:video.label,atMs,snippet});
  }
  return results;
}

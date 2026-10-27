import { openBackend, pickVideoFile, canRememberFiles } from './backend.js';
import { sourceSpans, thread } from './review-core.js';
import { createTranscriptPanel } from './transcript-panel.js';
import { createLibraryPanel } from './library-panel.js';

const $=s=>document.querySelector(s), video=$('#video');
let backend=null, videos=[], notes=[], active=null, filter='open', presented=0, explicitTime=null, saving=false, pinnedStart=null, pinnedEnd=null, editing=null, lastSnapshot='', toastTimer=null, pendingAdd=null, pendingSeek=null, reloadTimer=null, announceReload=false, pendingEditRequest=null;
const PLAY='M8 5v14l11-7z', PAUSE='M7 5h4v14H7zM13 5h4v14h-4z', SPEEDS=[0.5,0.75,1,1.25,1.5,2], FPS=[23.976,24,25,29.97,30,48,50,59.94,60];

// Time formats: full for exports, precise while composing, short in the list.
const format=ms=>{ms=Math.max(0,Math.round(ms));const h=Math.floor(ms/3600000),m=Math.floor(ms/60000)%60,s=Math.floor(ms/1000)%60;return [h,m,s].map(x=>String(x).padStart(2,'0')).join(':')+'.'+String(ms%1000).padStart(3,'0');};
const short=ms=>{ms=Math.max(0,Math.round(ms));const h=Math.floor(ms/3600000),m=Math.floor(ms/60000)%60,s=String(Math.floor(ms/1000)%60).padStart(2,'0');return h?`${h}:${String(m).padStart(2,'0')}:${s}`:`${m}:${s}`;};
const precise=ms=>short(ms)+'.'+String(Math.round(Math.max(0,ms))%1000).padStart(3,'0');
const frameOf=ms=>Math.round(ms*active.fps/1000);
function parse(s){s=s.trim();if(!s)return null;if(/^\d+(\.\d+)?$/.test(s))return Math.round(Number(s)*1000);const m=s.match(/^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?$/);if(!m||Number(m[3])>59||(m[1]&&Number(m[2])>59))throw Error('Use h:mm:ss.mmm, m:ss.mmm or seconds.');return (Number(m[1]||0)*3600+Number(m[2])*60+Number(m[3]))*1000+Number((m[4]||'0').padEnd(3,'0'));}
function ago(iso){const s=(Date.now()-new Date(iso))/1000;if(!(s>=0))return '';if(s<60)return 'Just now';if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h';if(s<604800)return Math.floor(s/86400)+'d';return new Date(iso).toLocaleDateString();}
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function download(name,data){const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

let previewStop=null;
const transcriptPanel=createTranscriptPanel({seek:ms=>{if(video.readyState>=1)seek(ms);else pendingSeek=ms;},pause:()=>video.pause(),playContext:(start,end)=>{seek(start);previewStop=end;video.play().catch(error=>showToastError(error.message));},showError:showToastError,
  request:async selection=>{if(!active)return;video.pause();const videoId=active.id,createdAt=new Date().toISOString();setSaveState('Saving…');const list=await backend.apply({action:'add',videoId,startMs:selection.startMs,endMs:selection.endMs,text:selection.text,editRequest:selection.editRequest,createdAt});setNotes(list);if(filter!=='open')setFilter('open');setSaveState(savedLabel());return list.findLast(n=>n.videoId===videoId&&n.createdAt===createdAt&&n.text===selection.text);},
  updateRequest:async(id,text)=>{const list=await backend.apply({action:'update',id,text});setNotes(list);setSaveState(savedLabel());return list.find(n=>n.id===id);},
  cancelRequest:async id=>{setNotes(await backend.apply({action:'reply',id,author:'you',text:'Undid this preview removal; keep the passage.',status:'done'}));}});
const libraryPanel=createLibraryPanel({getBackend:()=>backend,showError:showToastError,
  open:async(id,atMs)=>{await selectVideo(id);if(atMs!=null){if(video.readyState>=1)seek(atMs);else pendingSeek=atMs;}},
  add:chooseVideoForBackend,
  remove:async(id,title)=>{if(!confirm(`Remove “${title}” from the library? The recording and review notes are kept in Removed videos.`))return false;await backend.removeVideo(id);await loadNotes();toast('Removed from the library.',async()=>{await backend.restoreVideo(id);await loadNotes();selectVideo(id);});return true;},
  restore:async id=>{await backend.restoreVideo(id);await loadNotes();selectVideo(id);toast('Video and review notes restored.');}});

// Playback
function clamp(ms){return Math.max(0,Math.min(active.durationMs,ms));}
function currentMs(){return explicitTime??Math.round((video.paused?video.currentTime:presented)*1000);}
function renderTime(){if(!active)return;const ms=currentMs();$('#currentTime').textContent=precise(ms);$('#frameInfo').textContent=`frame ${frameOf(ms)}`;$('#seek').value=ms/1000;$('#playIcon').setAttribute('d',video.paused?PLAY:PAUSE);$('#play').setAttribute('aria-label',video.paused?'Play':'Pause');renderStamp();highlightActive(ms);transcriptPanel.tick(ms,!video.paused);}
function enforcePreview(){if(video.paused)return;const ms=video.currentTime*1000,next=transcriptPanel.skip(ms);if(next>ms+.5){if(next>=video.duration*1000-1){video.pause();video.currentTime=video.duration;}else video.currentTime=next/1000;}if(previewStop!==null&&video.currentTime*1000>=previewStop){video.pause();previewStop=null;}}
function frameCallback(now,metadata){enforcePreview();presented=metadata.mediaTime;if(!video.paused)explicitTime=null;renderTime();video.requestVideoFrameCallback(frameCallback);}
if(video.requestVideoFrameCallback)video.requestVideoFrameCallback(frameCallback);
video.addEventListener('timeupdate',()=>{enforcePreview();if(!video.requestVideoFrameCallback)presented=video.currentTime;renderTime();});
video.addEventListener('play',()=>{explicitTime=null;$('#bigPlay').hidden=true;renderTime();});video.addEventListener('pause',renderTime);
video.addEventListener('loadedmetadata',()=>{$('#loading').style.display='none';if(!syncDuration())return;renderTime();if(announceReload){announceReload=false;toast(`Loaded the new render of ${active.title}.`);}if(pendingSeek!==null){const ms=pendingSeek;pendingSeek=null;seek(ms);}});
video.addEventListener('pause',()=>reportView());video.addEventListener('seeked',()=>reportView());
// A file replaced on disk stops playing; reloadIfChanged then picks up the new render.
video.addEventListener('error',()=>{if(!video.getAttribute('src'))return;$('#loading').style.display='grid';$('#loading').textContent=backend?.mode==='local'?'Could not load this video. Check that the original file is still available.':'This browser cannot play this file. Try Chrome or Edge, or an MP4 (H.264) export.';clearTimeout(reloadTimer);reloadTimer=setTimeout(()=>reloadIfChanged().catch(()=>{}),3000);});
function renderDuration(){$('#duration').textContent=short(active.durationMs);$('#seek').max=active.durationMs/1000;}
// An older local file must never overwrite the new render's declared cut/map.
function syncDuration(){const ms=Math.round(video.duration*1000);if(!active||!isFinite(ms))return false;if(Math.abs(ms-active.durationMs)<=Math.max(60,1000/active.fps))return true;
  video.pause();video.removeAttribute('src');video.load();gate('Choose the updated video',`This file is ${short(ms)}, while this review is ${short(active.durationMs)}. Choose the updated render; your transcript and comments are kept.`,'Choose updated '+active.fileName+'…',()=>attachFile(active));return false;}
function seek(ms,snap=false){if(!active)return;previewStop=null;video.pause();$('#bigPlay').hidden=true;ms=clamp(ms);if(snap)ms=Math.round(ms*active.fps/1000)*1000/active.fps;explicitTime=Math.round(ms);video.currentTime=ms/1000;renderTime();}
function step(direction){seek((frameOf(currentMs())+direction)*1000/active.fps);}
async function togglePlay(){if(!video.getAttribute('src'))return;previewStop=null;try{if(video.paused){video.currentTime=transcriptPanel.skip(video.currentTime*1000)/1000;await video.play();}else video.pause();}catch(e){showError(e.message);}}

// Composer: the comment is pinned to the playhead unless a start or range has been set.
const startMs=()=>pinnedStart??currentMs();
function renderStamp(){if(!active)return;const start=startMs(),label=pinnedEnd!==null?`${short(start)} – ${short(pinnedEnd)}`:short(start);$('#stamp').textContent=pinnedEnd!==null?`${precise(start)} – ${precise(pinnedEnd)}`:precise(start);$('#saveNote').textContent='Comment at '+label;$('#clearRange').hidden=pinnedStart===null&&pinnedEnd===null;if(!$('#rangeEditor').hidden&&!$('#rangeEditor').contains(document.activeElement)){$('#noteStart').value=precise(start);$('#noteEnd').value=pinnedEnd===null?'':precise(pinnedEnd);}}
function openComposer(){if(!active)return;video.pause();$('#composer').classList.add('open');$('#noteText').focus();renderStamp();}
function resetComposer(){$('#noteText').value='';pinnedStart=pinnedEnd=null;pendingEditRequest=null;$('#rangeEditor').hidden=true;$('#composer').classList.remove('open');autosize();updateSend();showError('');renderStamp();}
function autosize(){const t=$('#noteText');t.style.height='auto';t.style.height=Math.min(t.scrollHeight,200)+'px';}
function updateSend(){$('#saveNote').disabled=saving||!active||!$('#noteText').value.trim();}
function markStart(){video.pause();pinnedStart=currentMs();if(pinnedEnd!==null&&pinnedEnd<=pinnedStart)pinnedEnd=null;renderStamp();toast(`Start set at ${precise(pinnedStart)} · press O to set the end`);}
function markEnd(){video.pause();const ms=currentMs();if(pinnedStart===null||ms<=pinnedStart){showToastError('Set the start with I first, then move past it and press O.');return;}pinnedEnd=ms;renderStamp();openComposer();}
function readRangeEditor(){const start=parse($('#noteStart').value),end=parse($('#noteEnd').value);if(start===null||start<0||start>active.durationMs)throw Error('Start must be inside the video.');if(end!==null&&(end<=start||end>active.durationMs))throw Error('End must be after the start and inside the video.');pinnedStart=start;pinnedEnd=end;}
async function submit(e){e?.preventDefault();if(saving||!active)return;const text=$('#noteText').value.trim();if(!text)return;try{showError('');if(!$('#rangeEditor').hidden)readRangeEditor();saving=true;updateSend();setSaveState('Saving…');setNotes(await backend.apply({action:'add',videoId:active.id,startMs:startMs(),endMs:pinnedEnd,text,editRequest:pendingEditRequest,createdAt:new Date().toISOString()}));if(filter!=='open')setFilter('open');resetComposer();transcriptPanel.reset();$('#noteText').blur();setSaveState(savedLabel());}catch(err){showError(err.message);setSaveState('Not saved');}finally{saving=false;updateSend();}}

// Comments
function showError(text){$('#error').textContent=text;}
function setSaveState(text){$('#saveState').textContent=text;}
const savedLabel=()=>backend?.mode==='local'?'Saved on this computer':'Saved in this browser';
function setNotes(list){notes=list;lastSnapshot=JSON.stringify(list);drawNotes();}
function mine(){return active?notes.filter(n=>n.videoId===active.id).sort((a,b)=>a.startMs-b.startMs):[];}
function drawPins(all){const pins=$('#pins');pins.replaceChildren();for(const n of all){const pin=el('button','pin'+(n.status==='done'?' done':'')+(n.endMs!==null?' range':''));pin.style.left=`${n.startMs/active.durationMs*100}%`;if(n.endMs!==null)pin.style.width=`${(n.endMs-n.startMs)/active.durationMs*100}%`;pin.setAttribute('aria-label','Jump to comment at '+short(n.startMs));const tip=el('span','pin-tip');tip.append(el('b',null,short(n.startMs)),document.createTextNode(' '+n.text));pin.append(tip);pin.onclick=()=>focusNote(n);pins.append(pin);}}
function drawNotes(){const list=$('#comments');list.replaceChildren();$('#pins').replaceChildren();
  if(!active){$('#count').textContent='0';$('#countLabel').textContent='comments';return;}
  const all=mine(),open=all.filter(n=>n.status==='open').length,shown=all.filter(n=>n.status===filter);$('#count').textContent=filter==='open'?open:shown.length;$('#countLabel').textContent=(filter==='open'?'open ':'resolved ')+((filter==='open'?open:shown.length)===1?'comment':'comments');drawPins(all);
  if(!shown.length){const e=el('div','empty');const art=el('div','empty-art');art.append(el('span'),el('span'));e.append(art,el('strong',null,filter==='done'?'Nothing resolved yet':'No comments… yet'));const hint=el('p');if(filter==='done')hint.textContent='Comments you or the AI resolve show up here.';else hint.append('Hit ',el('kbd',null,'C'),' to comment on the current frame.');e.append(hint);list.append(e);return;}
  for(const n of shown)list.append(card(n));highlightActive(currentMs());}
const WHO={you:['Y','You',''],ai:['AI','AI editor',' ai']};
function person(author){const [initials,name,cls]=WHO[author]||WHO.you;return [el('span','avatar'+cls,initials),name];}
function inlineBox(value,placeholder,label,onSave){const box=el('textarea','edit-box');box.value=value;box.rows=value?3:2;box.placeholder=placeholder;const save=el('button','primary small',label),cancel=el('button','link','Cancel');const done=()=>{editing=null;drawNotes();};save.onclick=async()=>{const text=box.value.trim();if(text){editing=null;await onSave(text);}};cancel.onclick=done;box.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();save.click();}if(e.key==='Escape')done();};const row=el('div','edit-row');row.append(cancel,save);setTimeout(()=>{box.focus();box.setSelectionRange(box.value.length,box.value.length);});return [box,row];}
function card(n){const c=el('article','comment'+(n.status==='done'?' done':''));c.dataset.id=n.id;c.dataset.start=n.startMs;c.dataset.end=n.endMs??n.startMs;const author=n.author||'you',[avatar,name]=person(author);
  const main=el('div','comment-main'),meta=el('div','comment-meta');const time=el('button','ts',n.endMs!==null?`${short(n.startMs)} – ${short(n.endMs)}`:short(n.startMs));time.title=`${format(n.startMs)}${n.endMs!==null?' → '+format(n.endMs):''} · frame ${frameOf(n.startMs)}`;time.onclick=()=>focusNote(n);meta.append(el('b',null,name),time,el('span','ago',ago(n.createdAt)));
  const resolve=el('button','resolve');resolve.title=n.status==='done'?'Reopen':'Resolve';resolve.setAttribute('aria-label',resolve.title);resolve.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/></svg>';resolve.onclick=()=>update(n,{status:n.status==='done'?'open':'done'});meta.append(resolve);main.append(meta);
  if(editing?.id===n.id&&editing.mode==='edit')main.append(...inlineBox(n.text,'','Save',text=>update(n,{text})));else main.append(el('p','text',n.text));
  for(const r of thread(n)){const reply=el('div','reply'),body=el('div'),[ra,rname]=person(r.author),head=el('div','reply-meta');head.append(el('b',null,rname),el('span','ago',ago(r.createdAt)));body.append(head,el('p',null,r.text));reply.append(ra,body);main.append(reply);}
  if(editing?.id===n.id&&editing.mode==='reply'){const wrap=el('div','reply-box');wrap.append(...inlineBox('','Reply…','Reply',text=>reply(n,text)));main.append(wrap);}
  else if(editing?.id!==n.id){const actions=el('div','comment-actions');const answer=el('button','link','Reply'),del=el('button','link','Delete');answer.onclick=()=>{editing={id:n.id,mode:'reply'};drawNotes();};del.onclick=()=>remove(n);actions.append(answer);if(author==='you'){const edit=el('button','link','Edit');edit.onclick=()=>{editing={id:n.id,mode:'edit'};drawNotes();};actions.append(edit);}actions.append(del);main.append(actions);}
  c.append(avatar,main);c.onclick=e=>{if(e.target===c||e.target===main||e.target.classList.contains('text'))focusNote(n);};return c;}
function highlightActive(ms){for(const c of document.querySelectorAll('.comment')){const start=Number(c.dataset.start),end=Math.max(Number(c.dataset.end),start+1500);c.classList.toggle('active',ms>=start-100&&ms<=end);}}
async function focusNote(n){if(n.videoId!==active?.id)await selectVideo(n.videoId);if(video.readyState>=1)seek(n.startMs);else pendingSeek=n.startMs;transcriptPanel.focusAt(n.startMs,n.editRequest,n);if(n.status!==filter)setFilter(n.status);const c=document.querySelector(`.comment[data-id="${n.id}"]`);if(c){c.scrollIntoView({block:'nearest',behavior:'smooth'});c.classList.add('flash');setTimeout(()=>c.classList.remove('flash'),900);}}
function setFilter(value){filter=value;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x.dataset.filter===value));drawNotes();}
async function update(n,changes){try{setNotes(await backend.apply({action:'update',id:n.id,...changes}));setSaveState(savedLabel());if(changes.status)toast(changes.status==='done'?'Comment resolved':'Comment reopened');}catch(e){showToastError(e.message);}}
async function remove(n){try{setNotes(await backend.apply({action:'delete',id:n.id}));toast('Deleted a comment.',async()=>setNotes(await backend.apply({action:'add',videoId:n.videoId,startMs:n.startMs,endMs:n.endMs,text:n.text,category:n.category,editRequest:n.editRequest,createdAt:n.createdAt})));}catch(e){showToastError(e.message);}}
async function reply(n,text){try{setNotes(await backend.apply({action:'reply',id:n.id,text}));setSaveState(savedLabel());}catch(e){showToastError(e.message);}}
// Announces what the AI changed: new replies, resolved comments and its own comments.
function aiActivity(before,after){const old=new Map(before.map(n=>[n.id,n]));let replies=0,resolved=0,added=0,first=null;for(const n of after){const prev=old.get(n.id);if(!prev){if(n.author==='ai'){added++;first??=n;}continue;}const fresh=thread(n).filter(r=>r.author==='ai').length-thread(prev).filter(r=>r.author==='ai').length;if(fresh>0){replies++;first??=n;}if(prev.status==='open'&&n.status==='done')resolved++;}
  if(!first)return;const count=n=>n+(n===1?' comment':' comments'),parts=[];if(resolved)parts.push('resolved '+count(resolved));if(replies>resolved)parts.push('replied on '+count(replies-resolved));if(added)parts.push('added '+count(added));toast(`AI editor ${parts.join(' and ')}.`,()=>focusNote(first),'Show');}
// Picks up changes made elsewhere (the agent, another tab) without disturbing an edit or reply in progress.
async function loadNotes(){const list=await backend.refresh();await localViewRequest();syncVideos();await reloadIfChanged();if(JSON.stringify(list)!==lastSnapshot&&!editing){const before=notes;setNotes(list);if(before.length||lastSnapshot)aiActivity(before,list);}setSaveState(savedLabel());}

// Offline app: the local MCP server leaves open-video requests for the page to pick up.
async function localViewRequest(){if(!backend.viewRequest)return;const r=await backend.viewRequest();if(!r?.id||r.id===localStorage.getItem('techies-review-view-request'))return;localStorage.setItem('techies-review-view-request',r.id);if(Date.now()-Date.parse(r.createdAt)<120000)await openFromAgent(r);}
function toast(text,undo,label='Undo'){clearTimeout(toastTimer);$('#toastText').textContent=text;const action=$('#toastAction');action.hidden=!undo;action.textContent=label;action.onclick=async()=>{$('#toast').classList.remove('show');try{await undo();}catch(e){showToastError(e.message);}};$('#toast').classList.remove('error');$('#toast').classList.add('show');toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),undo?6000:2600);}
function showToastError(text){toast(text);$('#toast').classList.add('error');}

// Video versions. In the browser the file comes from the reviewer's disk; the gate asks for it when needed.
function syncVideos(){const list=[...backend.videos()];if(list.map(v=>v.id).join()===videos.map(v=>v.id).join()){videos=list;rebindActive();return;}const fresh=list.find(v=>!videos.some(x=>x.id===v.id));videos=list;const select=$('#videoSelect');select.replaceChildren(...videos.map(v=>Object.assign(el('option',null,v.label),{value:v.id})));if(fresh&&videos.length>1&&fresh.addedBy==='ai')toast(`The AI added a new cut: ${fresh.label}`,()=>selectVideo(fresh.id),'Open');if(!videos.some(v=>v.id===active?.id))videos.length?selectVideo(videos[0].id):showEmpty();else{select.value=active.id;rebindActive();}}
// Keeps the open version current when its details change elsewhere, e.g. the agent's new duration.
function rebindActive(){const v=active&&videos.find(x=>x.id===active.id);if(!v||v===active)return;const resized=v.durationMs!==active.durationMs;active=v;if(resized){renderDuration();drawNotes();}}
function gate(title,text,button,action){$('#gate').hidden=false;$('#gateTitle').textContent=title;$('#gateText').textContent=text;$('#gateBtn').textContent=button;$('#gateBtn').onclick=action;$('#loading').style.display='none';$('#bigPlay').hidden=true;}
function showEmpty(){active=null;libraryPanel.setActive(null);transcriptPanel.load(null,backend);video.removeAttribute('src');video.load();$('#videoTitle').textContent='Video Review';$('#videoSelect').hidden=true;resetComposer();drawNotes();
  gate('Review a video from your computer','Pick a video file, or drop it here. It plays straight from your disk and is never uploaded.','Choose a video…',chooseVideoForBackend);}
async function selectVideo(id){video.pause();announceReload=false;active=videos.find(v=>v.id===id);presented=0;explicitTime=0;$('#videoTitle').textContent=active.title;$('#videoSelect').hidden=false;$('#videoSelect').value=active.id;renderDuration();$('#gate').hidden=true;resetComposer();localStorage.setItem('techies-review-video',id);drawNotes();renderTime();
  libraryPanel.setActive(active);
  transcriptPanel.load(active,backend);
  video.removeAttribute('src');$('#loading').textContent='Loading your video…';$('#loading').style.display='grid';pendingSeek=null;reportView();
  const media=await backend.media(active);if(active.id!==id)return;
  if(media.url)return play(media.url);
  offerMedia(active,media);}
function offerMedia(v,media){const name=v.fileName||'the video file';
  if(media.needsPermission)gate('Open the video again',`Allow this page to read ${name} from your computer again.`,'Open '+name,async()=>{try{play((await media.grant()).url);}catch(e){gate('Choose the updated video',e.message,'Choose '+name+'…',()=>attachFile(v));}});
  else gate(media.newRender||v.addedBy==='ai'?'Updated cut ready':'Open the video',`Choose ${name} once. This browser will remember the review copy for next time.`,'Choose '+name+'…',()=>attachFile(v));}
// Tells the agent which version is open and where the playhead is.
function reportView(){backend?.setView?.({videoId:active?.id??null,playheadMs:active?currentMs():null});}
// The agent asked to show a version, optionally at a time or on a comment.
async function openFromAgent({videoId,atMs=null,commentId=null}){
  syncVideos();const v=videos.find(x=>x.id===videoId);if(!v)return;
  if(active?.id!==videoId)await selectVideo(videoId);else{renderDuration();drawNotes();await reloadIfChanged();}
  if(atMs!==null){if(video.readyState>=1)seek(atMs);else pendingSeek=atMs;}
  const n=commentId&&notes.find(x=>x.id===commentId);
  if(n){if(n.status!==filter)setFilter(n.status);const c=document.querySelector(`.comment[data-id="${n.id}"]`);if(c){c.scrollIntoView({block:'nearest',behavior:'smooth'});c.classList.add('flash');setTimeout(()=>c.classList.remove('flash'),900);}}
  toast(`The AI opened ${v.title}${atMs!==null?' at '+short(atMs):''}.`);
}
// The agent replaced the open video's file under the same name: play the new render from the same moment.
async function reloadIfChanged(){if(!active)return;const id=active.id,media=await backend.media(active);if(active.id!==id)return;
  if(!media.url){if(media.newRender&&video.getAttribute('src')){video.pause();video.removeAttribute('src');video.load();offerMedia(active,media);}return;}if(media.url===video.getAttribute('src'))return;
  pendingSeek=currentMs();$('#loading').textContent='Loading the new render…';$('#loading').style.display='grid';announceReload=true;transcriptPanel.load(active,backend);play(media.url);}
function play(url){$('#gate').hidden=true;video.src=url;video.load();$('#bigPlay').hidden=false;}
async function attachFile(v){try{const {file,handle}=await pickVideoFile();if(v.fileName&&file.name!==v.fileName&&!confirm(`You picked ${file.name}, but this version is ${v.fileName}. Use it anyway?`))return;const media=await backend.attach(v,file,handle);if(active?.id===v.id)play(media.url);if(media.remembered)toast('Video remembered. It will reopen after refresh.');}catch(e){if(e.name!=='AbortError')showToastError(e.message);}}

// Adding a video: read its duration and estimate its frame rate from the frames the browser presents.
function probe(file){return new Promise((resolve,reject)=>{const v=Object.assign(document.createElement('video'),{muted:true,playsInline:true,preload:'auto'}),url=URL.createObjectURL(file);v.src=url;const finish=fps=>{v.pause();URL.revokeObjectURL(url);resolve({durationMs:Math.round(v.duration*1000),fps});};
  v.onerror=()=>{URL.revokeObjectURL(url);reject(Error('This browser cannot play that file. Try an MP4 (H.264) export, or Chrome or Edge.'));};
  v.onloadedmetadata=async()=>{if(!v.requestVideoFrameCallback||!isFinite(v.duration))return finish(null);const times=[];let done=false;const stop=setTimeout(()=>{done=true;finish(estimate(times));},1500);
    const tick=(now,meta)=>{if(done)return;times.push(meta.mediaTime);if(times.length>=24){done=true;clearTimeout(stop);finish(estimate(times));}else v.requestVideoFrameCallback(tick);};v.requestVideoFrameCallback(tick);try{await v.play();}catch{clearTimeout(stop);finish(null);}};});}
function estimate(times){const gaps=times.slice(1).map((t,i)=>t-times[i]).filter(g=>g>0.004);if(gaps.length<4)return null;const frame=Math.min(...gaps),fps=1/frame;return FPS.reduce((best,x)=>Math.abs(x-fps)<Math.abs(best-fps)?x:best,FPS[0]);}
async function chooseNewVideo(){try{await addFile(await pickVideoFile());}catch(e){if(e.name!=='AbortError')showToastError(e.message);}}
function chooseVideoForBackend(){if(backend?.mode==='local'){$('#localVideoError').textContent='';$('#localVideoDialog').showModal();}else chooseNewVideo();}
async function addFile({file,handle}){
  if(!file.type.startsWith('video/')&&!/\.(mp4|mov|m4v|webm|mkv)$/i.test(file.name))return showToastError('That is not a video file.');
  const dialog=$('#addDialog');$('#addFile').textContent=file.name;$('#addTitle').value=file.name.replace(/\.[^.]+$/,'').replace(/_+/g,' ').replace(/\s+/g,' ').trim();$('#addFpsNote').textContent='Detecting…';$('#addConfirm').disabled=true;dialog.showModal();
  pendingAdd={file,handle,durationMs:null};
  try{const info=await probe(file);if(pendingAdd?.file!==file)return;pendingAdd.durationMs=info.durationMs;if(info.fps)$('#addFps').value=String(info.fps);$('#addFpsNote').textContent=info.fps?'Detected from the video. Change it if it looks wrong.':'Could not detect it; pick the frame rate the video was exported at.';$('#addConfirm').disabled=false;}
  catch(e){dialog.close();pendingAdd=null;showToastError(e.message);}
}
$('#addDialog').addEventListener('close',async()=>{const item=pendingAdd;pendingAdd=null;if($('#addDialog').returnValue!=='add'||!item?.durationMs)return;try{const v=await backend.addVideo({file:item.file,handle:item.handle,title:$('#addTitle').value.trim()||item.file.name,fps:Number($('#addFps').value),durationMs:item.durationMs});syncVideos();selectVideo(v.id);toast(v.mediaRemembered?'Added. The video will reopen after refresh.':'Added. Browser storage could not remember the video; it will need reopening after refresh.');}catch(e){showToastError(e.message);}});

// Agent connection (hosted app): the same flow as the Native Site Editor's Connect with MCP.
let agentStatus={connected:false,agents:[],waiting:false};
function connectionPrompt(token){const url=location.origin+'/mcp';return `Connect to my Video Review so you can read and answer my timestamped video comments.

Claude Code:
claude mcp add --transport http video-review-web ${url} --header "Authorization: Bearer ${token}"

Codex (~/.codex/config.toml):
[mcp_servers.video_review_web]
url = "${url}"
http_headers = { Authorization = "Bearer ${token}" }

Then call list_comments and work through my open comments: make each change in your local copy of the edit, resolve_comment with a short summary, and ask with reply_to_comment when something is unclear. When you render the fixed edit, save it under the same file name as the video I am reviewing, so my comments stay on that one video and I can see them marked done. Rename the old file first (for example add "-old" to its name) so it is not lost, and do not register the render as a new video. Render to a temporary name and rename it into place once it is finished. Then call open_video on it with the new durationMs and keepRanges, so my tab loads the new render. Call wait_for_comments to pick up new comments while I keep reviewing. My Video Review tab stays open; the video files are on this computer.`;}
async function copy(text){try{await navigator.clipboard.writeText(text);}catch{const t=el('textarea');t.value=text;document.body.append(t);t.select();document.execCommand('copy');t.remove();}}
const CLIENTS={'claude-code':'Claude Code','claude-ai':'Claude','codex-mcp-client':'Codex',codex:'Codex',cursor:'Cursor'};
function renderAgent(status){agentStatus=status;const btn=$('#agentBtn'),names=[...new Set(status.agents.map(a=>CLIENTS[a.client]||a.client))];btn.classList.toggle('live',names.length>0);btn.classList.toggle('waiting',!names.length&&status.waiting);btn.classList.toggle('primary',!status.connected);btn.classList.toggle('ghost',status.connected);
  btn.disabled=!!status.reconnecting;if(status.reconnecting){$('#agentLabel').textContent='Reconnecting…';$('#agentInfo').textContent='Your saved agent connection will reconnect automatically. No new connection prompt is needed.';return;}
  $('#agentLabel').textContent=names.length?(names.length===1?names[0]:`${names.length} agents`)+' connected':status.waiting?'Waiting for agent…':'Connect agent';
  $('#agentInfo').textContent=names.length?`${names.join(', ')} can read your comments in this browser and reply. Keep this tab open while it works.`:'Paste the copied prompt into Claude Code or Codex. This button changes once the agent connects.';}
async function connectAgent(){try{const token=await backend.agent.connect();await copy(connectionPrompt(token));toast('Copied a prompt to connect your agent. Paste it into Claude Code or Codex.');}catch(e){showToastError(e.message);}}
function toggleMenu(menu,show){for(const m of document.querySelectorAll('.menu'))if(m!==menu)m.hidden=true;if(menu)menu.hidden=show===undefined?!menu.hidden:!show;$('#libraryBtn').setAttribute('aria-expanded',String(!$('#libraryMenu').hidden));}
$('#agentBtn').onclick=e=>{e.stopPropagation();agentStatus.connected?toggleMenu($('#agentMenu')):connectAgent();};
$('#agentCopy').onclick=()=>{toggleMenu($('#agentMenu'),false);connectAgent();};
$('#agentDisconnect').onclick=async()=>{toggleMenu($('#agentMenu'),false);try{await backend.agent.disconnect();toast('Disconnected. Agents can no longer read your comments.');}catch(error){showToastError(error.message);}};
$('#moreBtn').onclick=e=>{e.stopPropagation();toggleMenu($('#moreMenu'));};
document.addEventListener('click',e=>{if(!e.target.closest('.menu'))toggleMenu(null,false);});

// Wiring
$('#composer').onsubmit=submit;
$('#noteText').addEventListener('focus',()=>{video.pause();$('#composer').classList.add('open');});
$('#noteText').addEventListener('input',()=>{autosize();updateSend();});
$('#noteText').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submit();}if(e.key==='Escape'){if(!$('#noteText').value.trim())resetComposer();$('#noteText').blur();}});
$('#stamp').onclick=()=>{const editor=$('#rangeEditor');editor.hidden=!editor.hidden;if(!editor.hidden){$('#noteStart').value=precise(startMs());$('#noteEnd').value=pinnedEnd===null?'':precise(pinnedEnd);$('#noteStart').focus();}};
for(const id of ['#noteStart','#noteEnd']){$(id).addEventListener('change',()=>{try{readRangeEditor();showError('');seek(pinnedStart);}catch(e){showError(e.message);}});$(id).addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$(id).dispatchEvent(new Event('change'));$('#noteText').focus();}});}
$('#clearRange').onclick=()=>{pinnedStart=pinnedEnd=null;$('#rangeEditor').hidden=true;renderStamp();};
$('#importTranscript').onclick=()=>{if(!active)return showToastError('Open a video first.');const selected=active,input=Object.assign(el('input'),{type:'file',accept:'application/json,.json'});input.onchange=async()=>{if(!input.files[0])return;try{await transcriptPanel.import(selected,backend,input.files[0]);toast('Transcript attached. No transcription credits used.');}catch(error){showToastError(error.message);}};input.click();};
$('#play').onclick=togglePlay;$('#bigPlay').onclick=togglePlay;video.onclick=togglePlay;
$('#prevFrame').onclick=()=>active&&step(-1);$('#nextFrame').onclick=()=>active&&step(1);$('#seek').oninput=e=>seek(Number(e.target.value)*1000);
$('#speed').onclick=()=>{const next=SPEEDS[(SPEEDS.indexOf(video.playbackRate)+1)%SPEEDS.length];video.playbackRate=next;$('#speed').textContent=next+'×';};
$('#mute').onclick=()=>{video.muted=!video.muted;$('#mute').textContent=video.muted?'Muted':'Sound';};
$('#fullscreen').onclick=()=>document.fullscreenElement?document.exitFullscreen():$('#player').requestFullscreen();
$('#videoSelect').onchange=e=>selectVideo(e.target.value);
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>setFilter(b.dataset.filter));
$('#exportBtn').onclick=()=>{toggleMenu(null,false);if(!active)return;const items=mine().map(n=>({...n,timecode:format(n.startMs),startFrame:frameOf(n.startMs),endFrame:n.endMs===null?null:frameOf(n.endMs),sourceSpans:sourceSpans(active,n.startMs,n.endMs)}));download(active.id+'-review-comments.json',{schemaVersion:1,exportedAt:new Date().toISOString(),video:{id:active.id,title:active.title,label:active.label,fps:active.fps,fingerprint:active.fingerprint,durationMs:active.durationMs},comments:items});};
$('#backupBtn').onclick=()=>{toggleMenu(null,false);download(`video-review-backup-${new Date().toISOString().slice(0,10)}.json`,backend.backup());};
$('#importBtn').onclick=()=>{toggleMenu(null,false);const input=Object.assign(el('input'),{type:'file',accept:'application/json,.json'});input.onchange=async()=>{try{await backend.importState(JSON.parse(await input.files[0].text()));syncVideos();await loadNotes();toast('Backup imported.');if(!active&&videos.length)selectVideo(videos[0].id);}catch(e){showToastError(e.message);}};input.click();};
$('#removeVideo').onclick=async()=>{toggleMenu(null,false);if(!active||!confirm(`Remove "${active.label}" and its comments from this browser? The video file itself is not touched.`))return;await backend.removeVideo(active.id);syncVideos();await loadNotes();};
$('#addVideo').onclick=chooseNewVideo;
$('#localVideoForm').onsubmit=async event=>{if(event.submitter?.value!=='add')return;event.preventDefault();$('#localVideoConfirm').disabled=true;try{const added=await backend.addPath($('#localVideoPath').value,$('#localVideoTitle').value);await backend.refresh();syncVideos();await selectVideo(added.id);$('#localVideoDialog').close();toast('Added a reference to the recording.');}catch(error){$('#localVideoError').textContent=error.message;}finally{$('#localVideoConfirm').disabled=false;}};
document.addEventListener('keydown',e=>{if(['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)||e.target.closest('dialog,#transcriptText')||e.ctrlKey||e.metaKey||e.altKey||!active||!video.getAttribute('src'))return;const actions={' ':togglePlay,k:togglePlay,ArrowLeft:()=>step(-1),',':()=>step(-1),ArrowRight:()=>step(1),'.':()=>step(1),j:()=>seek(currentMs()-5000),l:()=>seek(currentMs()+5000),c:openComposer,n:openComposer,i:markStart,o:markEnd,Escape:()=>{pinnedStart=pinnedEnd=null;renderStamp();}};const action=actions[e.key.length===1?e.key.toLowerCase():e.key];if(action){e.preventDefault();action();}});
// Drop a video anywhere to add it (hosted app).
let dragDepth=0;
document.addEventListener('dragenter',e=>{if(backend?.mode!=='browser'||!e.dataTransfer.types.includes('Files'))return;dragDepth++;$('#dropHint').hidden=false;});
document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('#dropHint').hidden=true;}});
document.addEventListener('dragover',e=>{if(backend?.mode==='browser')e.preventDefault();});
document.addEventListener('drop',async e=>{if(backend?.mode!=='browser')return;e.preventDefault();dragDepth=0;$('#dropHint').hidden=true;const item=[...e.dataTransfer.items].find(i=>i.kind==='file');if(!item)return;const handle=item.getAsFileSystemHandle?await item.getAsFileSystemHandle():null;const file=handle?await handle.getFile():item.getAsFile();
  const waiting=active&&!video.getAttribute('src')&&!$('#gate').hidden&&active.fileName===file.name;if(waiting){const media=await backend.attach(active,file,handle);play(media.url);if(media.remembered)toast('Video remembered. It will reopen after refresh.');}else addFile({file,handle});});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&backend)loadNotes().catch(()=>{});});
setInterval(()=>{if(!document.hidden&&backend?.mode==='local')loadNotes().catch(()=>{});},4000);
setInterval(()=>document.querySelectorAll('.comment').forEach(c=>{const n=notes.find(x=>x.id===c.dataset.id);const a=c.querySelector('.ago');if(n&&a)a.textContent=ago(n.createdAt);}),60000);

async function init(){
  try{
    backend=await openBackend({onExternalChange:(origin,cmd)=>{if(cmd?.action==='set-transcript'&&cmd.videoId===active?.id)transcriptPanel.load(active,backend);loadNotes().catch(()=>{});},onAgentStatus:renderAgent,onOpenVideo:openFromAgent,onMediaNotice:message=>toast(message)});
    document.body.dataset.mode=backend.mode;
    if(backend.mode==='browser'){$('#addVideo').hidden=false;$('#agentBtn').hidden=false;}else document.querySelectorAll('[data-browser]').forEach(b=>b.hidden=true);
    await backend.load();syncVideos();notes=backend.comments();lastSnapshot=JSON.stringify(notes);
    if(!videos.length){if(backend.mode==='local')throw Error('No videos configured. Add one to data/manifest.json.');showEmpty();}
    else{const saved=localStorage.getItem('techies-review-video');selectVideo(videos.some(v=>v.id===saved)?saved:videos[0].id);}
    setSaveState(savedLabel());
  }catch(e){showError(e.message);$('#loading').textContent='Could not open the review library.';}
}
init();

// Where reviews live. Two backends behind one interface:
//  - local:   the Python app (server.py) on this computer stores videos and comments in its data folder.
//  - browser: the hosted app keeps comments in this browser (IndexedDB) and plays videos straight from disk.
//             While an agent is connected, one tab relays comments and the agent's changes through the Worker.
import { applyChange, checkVideo, describe, timecode, rebaseComments } from './review-core.js';
import { normalizeTranscript, searchLibrary } from './transcript-core.js';

async function api(route,body){const r=await fetch(route,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});const result=await r.json();if(!r.ok)throw Error(result.error||'Could not save.');return result;}

export async function openBackend(events){
  try{const r=await fetch('/api/local',{cache:'no-store'});if(r.ok&&(await r.json()).local)return localBackend();}catch{}
  return browserBackend(events);
}

function localBackend(){
  let videos=[],comments=[],viewTimer=null;const urls=new Map();
  return {
    mode:'local',
    async load(){videos=await api('/api/videos');comments=(await api('/api/comments')).comments;},
    videos:()=>videos, comments:()=>comments,
    async apply(cmd){comments=(await api('/api/comments',cmd)).comments;return comments;},
    async refresh(){[videos,{comments}]=await Promise.all([api('/api/videos'),api('/api/comments')]);return comments;},
    // fileVersion changes when the file is replaced; while it is missing (mid-render) keep the last URL.
    async media(v){const current=videos.find(x=>x.id===v.id)||v;if(current.fileVersion||!urls.has(v.id))urls.set(v.id,`/media/${encodeURIComponent(v.id)}?v=${current.fileVersion||''}`);return {url:urls.get(v.id)};},
    setView(view){clearTimeout(viewTimer);viewTimer=setTimeout(()=>api('/api/view',view).catch(()=>{}),400);},
    async viewRequest(){return api('/api/view-request');},
    async searchVideos(query){return api('/api/library/search?q='+encodeURIComponent(query));},
    async removedVideos(){return api('/api/library/removed');},
    async removeVideo(id){return api('/api/library',{action:'remove',videoId:id});},
    async restoreVideo(id){return api('/api/library',{action:'restore',videoId:id});},
    async addPath(path,title){const video=await api('/api/library',{action:'add',path,title});videos=await api('/api/videos');return video;},
    async transcript(v){return api('/api/transcript/'+encodeURIComponent(v.id));},
    async saveTranscript(v,data){return api('/api/transcript/'+encodeURIComponent(v.id),normalizeTranscript(data,v.durationMs,v.fps));},
  };
}

// IndexedDB: one key-value store for the review state, file handles and the session key.
const db=new Promise((resolve,reject)=>{const r=indexedDB.open('video-review',1);r.onupgradeneeded=()=>r.result.createObjectStore('kv');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
async function idb(mode,fn){const tx=(await db).transaction('kv',mode),store=tx.objectStore('kv');return new Promise((resolve,reject)=>{let result;const r=fn(store);r.onsuccess=()=>{result=r.result;};tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(tx.error||r.error);tx.onabort=()=>reject(tx.error||Error('Browser storage was interrupted.'));});}
const idbGet=key=>idb('readonly',s=>s.get(key)), idbSet=(key,value)=>idb('readwrite',s=>s.put(value,key)), idbDelete=key=>idb('readwrite',s=>s.delete(key));

async function sha256(data){const bytes=typeof data==='string'?new TextEncoder().encode(data):data;return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');}
/** sha256 over "<size>:" + the first and last MiB: identifies a file without reading all of it. */
export async function sampledFingerprint(file){const MiB=1<<20,head=await file.slice(0,MiB).arrayBuffer(),tail=file.size>MiB?await file.slice(Math.max(MiB,file.size-MiB)).arrayBuffer():new ArrayBuffer(0);return 'sample-sha256:'+await sha256(await new Blob([`${file.size}:`,head,tail]).arrayBuffer());}
function randomKey(){const bytes=crypto.getRandomValues(new Uint8Array(32));return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}

const mediaKey=video=>video.mediaRevision||'duration:'+video.durationMs;
async function checkMediaDuration(video,file){
  const actual=await new Promise((resolve,reject)=>{const probe=document.createElement('video'),url=URL.createObjectURL(file);let timer;
    const finish=(error,value)=>{clearTimeout(timer);probe.removeAttribute('src');probe.load();URL.revokeObjectURL(url);error?reject(error):resolve(value);};
    probe.preload='metadata';probe.onloadedmetadata=()=>finish(null,Math.round(probe.duration*1000));probe.onerror=()=>finish(Error('This browser cannot read that video. Choose an H.264 MP4.'));
    timer=setTimeout(()=>finish(Error('Could not read the video duration. Please choose the file again.')),20000);probe.src=url;
  });
  if(!Number.isFinite(actual)||Math.abs(actual-video.durationMs)>Math.max(60,1000/video.fps))throw Error(`That file is a different cut (${timecode(actual)}). This review needs ${timecode(video.durationMs)}. Choose the updated render; your comments are kept.`);
}

export const canRememberFiles='showOpenFilePicker' in window;
export async function pickVideoFile(){
  if(canRememberFiles){const [handle]=await window.showOpenFilePicker({id:'video-review',types:[{description:'Video',accept:{'video/*':['.mp4','.mov','.m4v','.webm','.mkv']}}]});return {file:await handle.getFile(),handle};}
  return new Promise((resolve,reject)=>{const input=Object.assign(document.createElement('input'),{type:'file',accept:'video/*'});input.onchange=()=>input.files[0]?resolve({file:input.files[0],handle:null}):reject(new DOMException('No file chosen','AbortError'));input.oncancel=()=>reject(new DOMException('No file chosen','AbortError'));input.click();});
}

async function browserBackend(events){
  let state=(await idbGet('state'))||{videos:[],comments:[],appliedOps:[]};
  const files=new Map(),urls=new Map(),channel=new BroadcastChannel('video-review'),remoteRequests=new Map();let view={videoId:null,playheadMs:null};
  const contextState=()=>({videos:state.videos.map(v=>({...v,fileOpen:files.get(v.id)?.key===mediaKey(v)})),comments:state.comments,view});
  async function save(){await idbSet('state',state);channel.postMessage('changed');relay.share();}
  channel.onmessage=async event=>{
    const msg=event.data;
    if(msg?.type==='relay-status'){if(!relay.owner){relay.status=msg.status;events.onAgentStatus?.(msg.status);}return;}
    if(msg?.type==='relay-status-request'){if(relay.owner)channel.postMessage({type:'relay-status',status:relay.status});return;}
    if(msg?.type==='relay-command'&&relay.owner){try{const value=msg.method==='connect'?await relay.connect():msg.method==='disconnect'?relay.disconnect():null;channel.postMessage({type:'relay-result',id:msg.id,value});}catch(error){channel.postMessage({type:'relay-result',id:msg.id,error:error.message});}return;}
    if(msg?.type==='relay-result'){const pending=remoteRequests.get(msg.id);if(pending){remoteRequests.delete(msg.id);clearTimeout(pending.timer);msg.error?pending.reject(Error(msg.error)):pending.resolve(msg.value);}return;}
    state=(await idbGet('state'))||state;events.onExternalChange?.('tab');
  };

  function useFile(id,file){const video=state.videos.find(v=>v.id===id);files.set(id,{file,key:mediaKey(video)});URL.revokeObjectURL(urls.get(id));const url=URL.createObjectURL(file);urls.set(id,url);relay.share();return {url};}
  async function rememberFile(v,file,handle){
    let remembered=false;
    if(handle){try{await idbSet('handle:'+v.id,handle);remembered=true;}catch{}}
    // A private browser copy covers ordinary file inputs and small review proxies.
    // File bytes are outside review state/backups and never enter the MCP relay.
    if(!handle||file.size<=128*1024*1024){try{await idbSet('media:'+v.id,{file,key:mediaKey(v)});remembered=true;}catch{if(!remembered)events.onMediaNotice?.('Video opened, but browser storage is full. Your notes are saved; the video will need reopening after refresh.');}}
    return remembered;
  }

  // Applies a change the agent queued, exactly once even if it is delivered twice.
  async function applyAgentOp(op){
    if(state.appliedOps?.includes(op.id))return {ok:true,result:{note:'Already applied'}};
    try{
      let result;
      if(op.cmd.action==='open-video'){const v=state.videos.find(x=>x.id===op.cmd.videoId);if(!v)throw Error('Unknown video version');if(op.cmd.render?.keepRanges)rebaseComments(v,state.comments,op.cmd.render.keepRanges,op.cmd.render.durationMs??v.durationMs);if(Object.keys(op.cmd.render||{}).length)v.mediaRevision=op.id;Object.assign(v,op.cmd.render);await events.onOpenVideo?.(op.cmd);const fileOpen=files.get(v.id)?.key===mediaKey(v);result={opened:v.label,at:op.cmd.atMs!=null?timecode(op.cmd.atMs):null,fileOpen,note:fileOpen?'Shown in the reviewer’s tab.':'The updated cut is ready; choose its file once to remember it for refresh.'};}
      else if(op.cmd.action==='set-transcript'){const v=state.videos.find(x=>x.id===op.cmd.videoId);if(!v)throw Error('Unknown video version');const transcript=normalizeTranscript(op.cmd.transcript,v.durationMs,v.fps);if(transcript.keepRanges)v.keepRanges=transcript.keepRanges;(state.transcripts||(state.transcripts={}))[v.id]=transcript;result={videoId:v.id,words:transcript.words.length,revision:transcript.revision};}
      else if(op.cmd.action==='get-transcript'){result={videoId:op.cmd.videoId,transcript:state.transcripts?.[op.cmd.videoId]??null};}
      else if(op.cmd.action==='register-video'){const v=checkVideo(op.cmd.video,state.videos);state.videos.push(v);result={video:v,note:'The reviewer is asked to open the file to review it.'};}
      else{const c=applyChange(state,op.cmd);result=c?{comment:describe(state.videos.find(v=>v.id===c.videoId),c)}:{};}
      state.appliedOps=[...(state.appliedOps||[]).slice(-99),op.id];await save();events.onExternalChange?.('agent',op.cmd);
      return {ok:true,result};
    }catch(e){return {ok:false,error:e.message};}
  }

  // The relay: one tab per browser talks to this session's Durable Object over a WebSocket.
  const relay={ws:null,owner:false,status:{connected:false,agents:[],waiting:false},key:null,hash:null,pendingToken:null,timer:null,retry:1000,retryTimer:null,
    async start(){
      const getKey=async()=>{let key=await idbGet('session-key');if(!key){key=randomKey();await idbSet('session-key',key);}return key;};
      this.key=navigator.locks?await navigator.locks.request('video-review-session-key',getKey):await getKey();
      this.hash=await sha256('session:'+this.key);
      this.status={...this.status,reconnecting:true};events.onAgentStatus?.(this.status);channel.postMessage({type:'relay-status-request'});
      // Only one tab relays, so the agent's changes are applied once; the others follow through BroadcastChannel.
      const lead=()=>{this.owner=true;this.open();};
      if(navigator.locks)navigator.locks.request('video-review-relay',()=>new Promise(()=>lead()));else lead();
      const reconnect=()=>{if(this.owner&&(!this.ws||this.ws.readyState>=2)){clearTimeout(this.retryTimer);this.retry=1000;this.open();}};
      window.addEventListener('online',reconnect);document.addEventListener('visibilitychange',()=>{if(!document.hidden)reconnect();});
    },
    open(){
      const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/tab/${this.hash}`);this.ws=ws;
      ws.onopen=()=>{this.retry=1000;ws.send(JSON.stringify({type:'hello',key:this.key}));};
      ws.onmessage=async e=>{const msg=JSON.parse(e.data);
        if(msg.type==='status'){this.status=msg;events.onAgentStatus?.(msg);channel.postMessage({type:'relay-status',status:msg});if(msg.connected)this.share(true);}
        else if(msg.type==='token'){this.pendingToken?.(msg.token);this.pendingToken=null;}
        else if(msg.type==='op'){const result=await applyAgentOp(msg.op);ws.send(JSON.stringify({type:'op-result',id:msg.op.id,...result}));this.share(true);}};
      ws.onclose=()=>{if(this.ws!==ws)return;this.ws=null;this.status={...this.status,reconnecting:true};events.onAgentStatus?.(this.status);channel.postMessage({type:'relay-status',status:this.status});this.retryTimer=setTimeout(()=>this.open(),this.retry);this.retry=Math.min(this.retry*2,30000);};
    },
    share(now){if(!this.status.connected||this.ws?.readyState!==1)return;clearTimeout(this.timer);const send=()=>this.ws?.readyState===1&&this.ws.send(JSON.stringify({type:'context',state:contextState()}));now?send():this.timer=setTimeout(send,150);},
    remote(method){return new Promise((resolve,reject)=>{const id=crypto.randomUUID(),timer=setTimeout(()=>{remoteRequests.delete(id);reject(Error('The review connection is restarting. Please try again shortly.'));},10000);remoteRequests.set(id,{resolve,reject,timer});channel.postMessage({type:'relay-command',method,id});});},
    async connect(){if(!this.owner)return this.remote('connect');if(this.ws?.readyState!==1)throw Error('Reconnecting to Video Review; please try again shortly.');const token=new Promise((resolve,reject)=>{this.pendingToken=resolve;setTimeout(()=>reject(Error('No answer from Video Review; try again.')),8000);});this.ws.send(JSON.stringify({type:'create-token'}));return token;},
    disconnect(){if(!this.owner)return this.remote('disconnect');this.ws?.readyState===1&&this.ws.send(JSON.stringify({type:'disconnect'}));},
  };

  return {
    mode:'browser',
    async load(){relay.start();},
    videos:()=>state.videos, comments:()=>state.comments,
    async apply(cmd){applyChange(state,{...cmd,author:'you'});await save();return state.comments;},
    async refresh(){return state.comments;},
    async transcript(v){return state.transcripts?.[v.id]??null;},
    async saveTranscript(v,data){const transcript=normalizeTranscript(data,v.durationMs,v.fps);if(transcript.keepRanges)v.keepRanges=transcript.keepRanges;(state.transcripts||(state.transcripts={}))[v.id]=transcript;await save();return transcript;},
    setView(next){view=next;relay.share();},
    /** The video to play: a URL once the file is open, or what the reviewer needs to do first. */
    async media(v){
      const handle=await idbGet('handle:'+v.id);
      const cached=await idbGet('media:'+v.id),old=files.get(v.id),key=mediaKey(v);let staleHandle=false;
      if(handle){try{if(await handle.queryPermission({mode:'read'})==='granted'){
        const file=await handle.getFile(),previous=old?.file||cached?.file;
        const same=previous&&file.size===previous.size&&file.lastModified===previous.lastModified;
        if((!old||old.key===key)&&(!cached||cached.key===key)||!same){
          if(same&&old?.key===key&&urls.has(v.id))return {url:urls.get(v.id)};
          await checkMediaDuration(v,file);await rememberFile(v,file,handle);return useFile(v.id,file);
        }else staleHandle=true;
      }}catch{staleHandle=true;}}
      if(old?.key===key&&urls.has(v.id))return {url:urls.get(v.id)};
      if(cached?.key===key&&cached.file instanceof Blob)return useFile(v.id,cached.file);
      if(handle&&!staleHandle)return {needsPermission:true,newRender:!!v.mediaRevision,fileName:v.fileName,grant:async()=>{if(await handle.requestPermission({mode:'read'})!=='granted')throw Error('Permission was not given.');const file=await handle.getFile();await checkMediaDuration(v,file);await rememberFile(v,file,handle);return useFile(v.id,file);}};
      return {needsFile:true,newRender:!!v.mediaRevision,fileName:v.fileName};
    },
    /** Opens the file for an existing version (an agent's new cut, or after the browser forgot it). */
    async attach(v,file,handle){
      await checkMediaDuration(v,file);const remembered=await rememberFile(v,file,handle);
      v.fileSize=file.size;v.fingerprint=await sampledFingerprint(file);v.fileName=file.name;await save();
      return {...useFile(v.id,file),remembered};
    },
    async addVideo({file,handle,title,fps,durationMs}){
      const id=(title.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,60)||'video')+'-'+randomKey().slice(0,5).toLowerCase().replace(/[^a-z0-9]/g,'x');
      const v=checkVideo({id,title,label:`${title} · ${file.name}`,fps,durationMs,fileName:file.name,fileSize:file.size,fingerprint:await sampledFingerprint(file)},state.videos);
      state.videos.push(v);const mediaRemembered=await rememberFile(v,file,handle);useFile(id,file);await save();return {...v,mediaRemembered};
    },
    /** Keeps the duration the browser measured, e.g. after the file was re-rendered. */
    async updateVideo(id,changes){const v=state.videos.find(x=>x.id===id);if(v){Object.assign(v,changes);await save();}},
    async searchVideos(query){return searchLibrary(state.videos,state.transcripts||{},query);},
    async removedVideos(){return (state.archivedVideos||[]).map(x=>x.video);},
    async removeVideo(id){const video=state.videos.find(v=>v.id===id);if(!video)throw Error('Unknown video');(state.archivedVideos||(state.archivedVideos=[])).push({video,comments:state.comments.filter(c=>c.videoId===id),transcript:state.transcripts?.[id]});state.videos=state.videos.filter(v=>v.id!==id);state.comments=state.comments.filter(c=>c.videoId!==id);if(state.transcripts)delete state.transcripts[id];await save();},
    async restoreVideo(id){const archive=state.archivedVideos?.find(v=>v.video.id===id);if(!archive)throw Error('No archived video found');if(!state.videos.some(v=>v.id===id))state.videos.push(archive.video);for(const c of archive.comments)if(!state.comments.some(x=>x.id===c.id))state.comments.push(c);if(archive.transcript)(state.transcripts||(state.transcripts={}))[id]=archive.transcript;state.archivedVideos=state.archivedVideos.filter(v=>v.video.id!==id);await save();},
    async importState(data){
      if(!Array.isArray(data?.videos)||!Array.isArray(data?.comments))throw Error('That file is not a Video Review backup.');
      for(const v of data.videos)if(!state.videos.some(x=>x.id===v.id))state.videos.push(v);
      for(const c of data.comments)if(!state.comments.some(x=>x.id===c.id))state.comments.push(c);
      for(const [id,transcript] of Object.entries(data.transcripts||{})){const v=state.videos.find(v=>v.id===id);if(v)(state.transcripts||(state.transcripts={}))[id]=normalizeTranscript(transcript,v.durationMs);}
      for(const archive of data.archivedVideos||[])if(archive?.video?.id&&!state.videos.some(v=>v.id===archive.video.id)&&!(state.archivedVideos||[]).some(v=>v.video.id===archive.video.id))(state.archivedVideos||(state.archivedVideos=[])).push(archive);
      await save();
    },
    backup:()=>({schemaVersion:2,exportedAt:new Date().toISOString(),videos:state.videos,comments:state.comments,transcripts:state.transcripts||{},archivedVideos:state.archivedVideos||[]}),
    agent:relay,
  };
}

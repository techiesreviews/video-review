// Probe failures must never expose an unrelated browser library. No real storage or network is used.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';

const saved={indexedDB:globalThis.indexedDB,window:globalThis.window,fetch:globalThis.fetch,BroadcastChannel:globalThis.BroadcastChannel,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};
let browserState,reads=0,writes=0;
globalThis.window={};
globalThis.BroadcastChannel=class {};
globalThis.indexedDB={open(){
  const request={result:{transaction(){
    const tx={objectStore:()=>({get(key){
      assert.equal(key,'state','tests never read session credentials');
      reads++;
      const request={result:structuredClone(browserState)};
      queueMicrotask(()=>{request.onsuccess();tx.oncomplete();});
      return request;
    },put(){writes++;throw Error('Unexpected browser write');}})};
    return tx;
  }}};
  queueMicrotask(()=>request.onsuccess());
  return request;
}};
const {openBackend}=await import('../web/backend.js');
after(()=>{for(const [key,value] of Object.entries(saved))value===undefined?delete globalThis[key]:globalThis[key]=value;});

const localVideo={id:'local-only',title:'Local review',label:'Local cut',durationMs:8000,fps:30};
const localComment={id:'local-note',videoId:localVideo.id,text:'Keep this note'};
const localTranscript={revision:'original',words:[{id:'W1',text:'Local words',startMs:0,endMs:500}]};
const response=(status,data)=>({ok:status>=200&&status<300,status,json:async()=>structuredClone(data)});
const failures={
  'network rejection':async()=>{throw TypeError('Failed to fetch');},
  'HTTP 503':async()=>response(503,{error:'Unavailable'}),
  'invalid JSON':async()=>({ok:true,status:200,json:async()=>{throw SyntaxError('Invalid JSON');}}),
  'invalid probe shape':async()=>response(200,{}),
};

for(const populated of [false,true])for(const [name,probe] of Object.entries(failures)){
  test(`${name}, ${populated?'existing':'empty'} browser library: refuse fallback, then recover local data without writes`,async()=>{
    browserState={videos:populated?[{id:'browser-only'}]:[],comments:[],transcripts:{}};
    const original=structuredClone(browserState);reads=0;writes=0;
    globalThis.fetch=probe;
    await assert.rejects(openBackend({}),error=>error.code==='LOCAL_UNAVAILABLE'&&/review library/i.test(error.message));
    assert.equal(reads,0,'the failed probe never opens the browser library');
    globalThis.fetch=async route=>{
      if(route==='/api/local')return response(200,{local:true});
      if(route==='/api/videos')return response(200,[localVideo]);
      if(route==='/api/comments')return response(200,{comments:[localComment]});
      if(route==='/api/transcript/local-only')return response(200,localTranscript);
      throw Error(`Unexpected request: ${route}`);
    };
    const backend=await openBackend({});await backend.load();
    assert.equal(backend.mode,'local');
    assert.deepEqual(backend.videos(),[localVideo]);
    assert.deepEqual(backend.comments(),[localComment]);
    assert.deepEqual(await backend.transcript(localVideo),localTranscript);
    assert.deepEqual(browserState,original);
    assert.equal(writes,0);
  });
}

for(const [name,status,data] of [['hosted 404',404,null],['explicit browser mode',200,{local:false}]]){
  test(`${name} still opens the existing browser library`,async()=>{
    browserState={videos:[{id:'browser-only'}],comments:[],transcripts:{}};reads=0;writes=0;
    globalThis.fetch=async()=>response(status,data);
    const backend=await openBackend({});
    assert.equal(backend.mode,'browser');
    assert.deepEqual(backend.videos(),browserState.videos);
    assert.equal(reads,1);assert.equal(writes,0);
  });
}

test('agent status failure is unknown, then recovers without writing a request',async()=>{
  let status='503',writes=0;
  globalThis.fetch=async(route,options)=>{
    if(options?.method==='POST')writes++;
    if(route==='/api/local')return response(200,{local:true});
    if(status==='network')throw TypeError('Failed to fetch');
    if(status==='503')return response(503,{error:'Unavailable'});
    return response(200,{connected:status==='connected'});
  };
  const backend=await openBackend({});
  assert.equal(await backend.agentConnected(),null,'503 must not mean no agent');
  status='network';assert.equal(await backend.agentConnected(),null);
  status='absent';assert.equal(await backend.agentConnected(),false);
  status='connected';assert.equal(await backend.agentConnected(),true);
  assert.equal(writes,0,'rechecking status never resends a saved request');
});

test('an unresponsive agent status request is aborted and can recover',async()=>{
  let expire,aborted=false;
  globalThis.setTimeout=(fn,delay)=>{assert.equal(delay,3000);expire=fn;return 1;};
  globalThis.clearTimeout=()=>{};
  globalThis.fetch=async(route,options)=>{
    if(route==='/api/local')return response(200,{local:true});
    return new Promise((resolve,reject)=>options?.signal?.addEventListener('abort',()=>{aborted=true;reject(Error('Aborted'));}));
  };
  try{
    const backend=await openBackend({}),check=backend.agentConnected();
    assert.equal(typeof expire,'function','status queries need a bounded deadline');
    expire();assert.equal(await check,null);assert.equal(aborted,true);
    globalThis.fetch=async()=>response(200,{connected:true});assert.equal(await backend.agentConnected(),true);
  }finally{globalThis.setTimeout=saved.setTimeout;globalThis.clearTimeout=saved.clearTimeout;}
});

test('view debounce reports failure and acknowledges only the newest view after recovery',async()=>{
  const timers=new Map(),states=[],writes=[];let counter=0,fail=true,finish;
  globalThis.setTimeout=(fn,delay)=>{assert.equal(delay,400);timers.set(++counter,fn);return counter;};
  globalThis.clearTimeout=id=>timers.delete(id);
  const flush=async()=>{for(const [id,fn] of timers){timers.delete(id);await fn();}await Promise.resolve();};
  globalThis.fetch=async(route,options)=>{
    if(route==='/api/local')return response(200,{local:true});
    assert.equal(route,'/api/view');writes.push(JSON.parse(options.body));
    if(fail)return response(503,{error:'Unavailable'});
    await new Promise(resolve=>{finish=resolve;});return response(200,{ok:true});
  };
  try{
    const backend=await openBackend({onViewStatus:ok=>states.push(ok)});
    backend.setView({videoId:'old',playheadMs:1000});backend.setView({videoId:'new',playheadMs:2300});
    assert.equal(writes.length,0);assert.equal(timers.size,1);
    await flush();assert.deepEqual(states,[false],'failed acknowledgement reaches the UI');
    assert.deepEqual(writes,[{videoId:'new',playheadMs:2300}]);
    fail=false;backend.setView({videoId:'new',playheadMs:2400});
    const first=flush();await Promise.resolve();
    backend.setView({videoId:'newest',playheadMs:3000});const second=flush();await Promise.resolve();
    assert.equal(writes.length,2,'updates are serialized so an old request cannot overwrite the latest');
    finish();await new Promise(resolve=>saved.setTimeout(resolve,0));
    assert.deepEqual(states,[false],'obsolete success must not clear the stale notice');
    finish();await first;await second;
    assert.deepEqual(writes.at(-1),{videoId:'newest',playheadMs:3000});assert.deepEqual(states,[false,true]);
  }finally{globalThis.setTimeout=saved.setTimeout;globalThis.clearTimeout=saved.clearTimeout;}
});

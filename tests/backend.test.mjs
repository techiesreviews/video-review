// Probe failures must never expose an unrelated browser library. No real storage or network is used.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';

const saved={indexedDB:globalThis.indexedDB,window:globalThis.window,fetch:globalThis.fetch,BroadcastChannel:globalThis.BroadcastChannel};
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
    await assert.rejects(openBackend({}),error=>error.code==='LOCAL_UNAVAILABLE'&&/local service/i.test(error.message));
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

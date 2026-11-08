// What the browser cut preview promises without needing real media: time mapping, when to jump and fade,
// and the free-text "ask agent" request as both the browser and the hosted worker return it.
import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {keptRanges,cutLength,fileToCut,cutToFile,jumpPlan,JOIN_FADE_MS,removedRanges,playbackRemovals,skipRemoved} from '../web/cut-core.js';
import {wordsInSpan,normalizeTranscript} from '../web/transcript-core.js';
import {applyChange,describe} from '../web/review-core.js';
import {createCutPlayback} from '../web/cut-playback.js';

const edits=[{startMs:2000,endMs:3000},{startMs:5000,endMs:6000}],duration=8000;

test('kept ranges are what is left after removals, clipped to the file',()=>{
  assert.deepEqual(keptRanges(duration,[]),[[0,8000]]);
  assert.deepEqual(keptRanges(duration,edits),[[0,2000],[3000,5000],[6000,8000]]);
  assert.deepEqual(keptRanges(duration,[{startMs:0,endMs:1000}]),[[1000,8000]]);
  assert.deepEqual(keptRanges(duration,[{startMs:7000,endMs:9000}]),[[0,7000]]);
  assert.deepEqual(keptRanges(duration,[{startMs:500,endMs:1500},{startMs:1000,endMs:2000}]),[[0,500],[2000,8000]]);
  assert.equal(cutLength(duration,edits),6000);
  assert.equal(cutLength(duration,[{startMs:7000,endMs:9000}]),7000,'A removal past the end cannot make the cut shorter than what is kept.');
});

test('file time maps to cut time inside kept material',()=>{
  assert.equal(fileToCut(0,duration,edits),0);
  assert.equal(fileToCut(1500,duration,edits),1500);
  assert.equal(fileToCut(3500,duration,edits),2500);
  assert.equal(fileToCut(6500,duration,edits),4500);
  assert.equal(fileToCut(8000,duration,edits),6000);
});

test('a position inside a removal maps to the join',()=>{
  assert.equal(fileToCut(2000,duration,edits),2000);
  assert.equal(fileToCut(2500,duration,edits),2000);
  assert.equal(fileToCut(3000,duration,edits),2000);
  assert.equal(fileToCut(5500,duration,edits),4000);
});

test('cut time maps to file time and lands after the join on a boundary',()=>{
  assert.equal(cutToFile(0,duration,edits),0);
  assert.equal(cutToFile(1999,duration,edits),1999);
  assert.equal(cutToFile(2000,duration,edits),3000,'The join belongs to the material after it, so a seek never lands in a removal.');
  assert.equal(cutToFile(2500,duration,edits),3500);
  assert.equal(cutToFile(4000,duration,edits),6000);
  assert.equal(cutToFile(6000,duration,edits),8000);
});

test('times outside the cut clamp to its ends',()=>{
  assert.equal(cutToFile(-50,duration,edits),0);
  assert.equal(cutToFile(99999,duration,edits),8000);
  assert.equal(fileToCut(-50,duration,edits),0);
  assert.equal(fileToCut(99999,duration,edits),6000);
  assert.equal(cutToFile(Number.NaN,duration,edits),0);
  assert.equal(fileToCut(Number.NaN,duration,edits),0);
});

test('removals at the very start and end of the file',()=>{
  const ends=[{startMs:0,endMs:1000},{startMs:7000,endMs:8000}];
  assert.equal(cutToFile(0,duration,ends),1000,'Cut time zero is the first kept frame, not a removed one.');
  assert.equal(fileToCut(500,duration,ends),0);
  assert.equal(cutLength(duration,ends),6000);
  assert.equal(cutToFile(6000,duration,ends),7000,'The end of the cut is the end of the last kept material.');
  assert.equal(fileToCut(7500,duration,ends),6000);
});

test('no removals: both clocks are the same',()=>{
  for(const ms of [0,1,1234,8000]){assert.equal(fileToCut(ms,duration,[]),ms);assert.equal(cutToFile(ms,duration,[]),ms);}
  assert.equal(cutLength(duration,undefined),8000);
});

test('an empty file and an all-removed cut stay at zero',()=>{
  assert.deepEqual(keptRanges(0,[]),[]);
  assert.equal(cutLength(0,[]),0);assert.equal(fileToCut(100,0,[]),0);assert.equal(cutToFile(100,0,[]),0);
  const all=[{startMs:0,endMs:8000}];
  assert.deepEqual(keptRanges(duration,all),[]);
  assert.equal(cutLength(duration,all),0);assert.equal(fileToCut(4000,duration,all),0);assert.equal(cutToFile(0,duration,all),0);assert.equal(cutToFile(500,duration,all),0);
});

test('cut time survives a round trip through file time',()=>{
  const messy=[{startMs:90,endMs:100},{startMs:100,endMs:233},{startMs:1000,endMs:1033},{startMs:7967,endMs:8000}],length=cutLength(duration,messy);
  for(let t=0;t<=length;t+=7)assert.equal(fileToCut(cutToFile(t,duration,messy),duration,messy),t);
  for(let ms=0;ms<=duration;ms+=11)assert.ok(!removedRanges(messy).some(([a,b])=>{const cut=fileToCut(ms,duration,messy),at=cutToFile(cut,duration,messy);return cut<length&&at>=a&&at<b;}),'Before the end of the cut, a mapped position is never inside a removal.');
});

test('jump plan: nothing to do when the next removal is beyond the look-ahead',()=>{
  assert.equal(jumpPlan(0,edits),null);
  assert.equal(jumpPlan(1900,edits,{horizonMs:70}),null);
  assert.equal(jumpPlan(6500,edits),null,'No removals left after this point.');
  assert.equal(jumpPlan(3000,[{startMs:2000,endMs:3000}]),null,'The end of a removal is kept material.');
});

test('jump plan: the fade starts early so the jump stays on the join',()=>{
  const plan=jumpPlan(1950,edits,{horizonMs:70,fadeMs:JOIN_FADE_MS});
  assert.deepEqual(plan,{startMs:2000,toMs:3000,jumpInMs:50,fadeInMs:35});
  assert.equal(JOIN_FADE_MS,15,'Matches the native audio fades in EDITING-LESSONS.md.');
});

test('jump plan: turning the fade off never moves the jump',()=>{
  const on=jumpPlan(1950,edits,{horizonMs:70,fadeMs:15}),off=jumpPlan(1950,edits,{horizonMs:70,fadeMs:0});
  assert.equal(on.jumpInMs,off.jumpInMs);assert.equal(off.fadeInMs,off.jumpInMs);
});

test('jump plan: less room than the fade means the fade starts now and the jump still waits for nobody',()=>{
  assert.deepEqual(jumpPlan(1995,edits,{horizonMs:70,fadeMs:15}),{startMs:2000,toMs:3000,jumpInMs:5,fadeInMs:0});
});

test('jump plan: already inside a removal jumps at once',()=>{
  assert.deepEqual(jumpPlan(2000,edits),{startMs:2000,toMs:3000,jumpInMs:0,fadeInMs:0});
  assert.deepEqual(jumpPlan(2500,edits),{startMs:2000,toMs:3000,jumpInMs:0,fadeInMs:0});
});

test('jump plan: playback speed shortens the wait in real time, and overlapping removals are one jump',()=>{
  assert.equal(jumpPlan(1900,edits,{horizonMs:70,rate:2}).jumpInMs,50);
  assert.equal(jumpPlan(1900,edits,{horizonMs:70,rate:.5}),null);
  assert.equal(jumpPlan(950,[{startMs:1000,endMs:1500},{startMs:1400,endMs:1800}],{horizonMs:70}).toMs,1800);
});

test('words in a span give the quote and IDs for a request; a gap gives none',()=>{
  const {words}=normalizeTranscript({words:[{id:'W1',text:'keep',startMs:100,endMs:300},{id:'W2',text:'the',startMs:400,endMs:500},{id:'W3',text:'breath',startMs:600,endMs:900}]},1000);
  assert.deepEqual(wordsInSpan(words,350,700),{quote:'the breath',firstWordId:'W2',lastWordId:'W3',wordIds:['W2','W3']});
  assert.deepEqual(wordsInSpan(words,150,null),{quote:'keep',firstWordId:'W1',lastWordId:'W1',wordIds:['W1']},'One frame gets the word being spoken.');
  assert.deepEqual(wordsInSpan(words,350,null),{wordIds:[]},'A frame between words has no quote; the frame span still says where.');
  assert.deepEqual(wordsInSpan([],0,100),{wordIds:[]});
});

const reviewed=()=>({videos:[{id:'v',durationMs:60000,fps:30,keepRanges:[[0,600],[900,2100]]}],comments:[]});
const ask=(extra={})=>({action:'add',videoId:'v',startMs:25000,endMs:26000,text:'Tighten this and keep the laugh',editRequest:{type:'change',quote:'keep the laugh',firstWordId:'W5',lastWordId:'W7',wordIds:['W5','W6','W7'],transcriptRevision:'r1',...extra}});

test('a change request stores its quote, word IDs and frame span',()=>{
  const state=reviewed(),c=applyChange(state,ask());
  assert.deepEqual(c.editRequest.wordIds,['W5','W6','W7']);assert.equal(c.editRequest.quote,'keep the laugh');
  assert.equal(c.editRequest.startFrame,750);assert.equal(c.editRequest.endFrame,780);
  assert.deepEqual(c.editRequest.sourceSpans,[{sourceStartFrame:1050,sourceEndFrame:1080,sourceStartMs:35000,sourceEndMs:36000}]);
});

test('a change request on a single frame has no end frame',()=>{
  const state=reviewed(),c=applyChange(state,{action:'add',videoId:'v',startMs:1000,endMs:null,text:'Hold on this frame',editRequest:{type:'change',wordIds:[]}});
  assert.equal(c.editRequest.startFrame,30);assert.equal(c.editRequest.endFrame,null);
  const seen=describe(state.videos[0],c).agentRequest;assert.equal(seen.instruction,'Hold on this frame');assert.deepEqual(seen.wordIds,[]);assert.equal(seen.quote,null);assert.equal(seen.endFrame,null);
});

test('agents get one clear field to act on, and it follows the reviewer\'s edits',()=>{
  const state=reviewed(),c=applyChange(state,ask()),video=state.videos[0];
  const seen=describe(video,c);
  assert.equal(seen.editRequest.type,'change');
  assert.deepEqual(seen.agentRequest,{type:'change',instruction:'Tighten this and keep the laugh',quote:'keep the laugh',wordIds:['W5','W6','W7'],firstWordId:'W5',lastWordId:'W7',startFrame:750,endFrame:780,sourceSpans:seen.sourceSpans,transcriptRevision:'r1'});
  applyChange(state,{action:'update',id:c.id,text:'Actually cut the laugh'});
  assert.equal(describe(video,c).agentRequest.instruction,'Actually cut the laugh');
});

test('other request types are unchanged and carry no agent field',()=>{
  const state=reviewed(),video=state.videos[0];
  for(const type of ['remove','clipped','extend_before','extend_after','mistake','pause','comment']){
    const c=applyChange(state,{action:'add',videoId:'v',startMs:100,endMs:200,text:'x',editRequest:{type,quote:'q',wordIds:['W1']}}),seen=describe(video,c);
    assert.equal(seen.editRequest.type,type);assert.equal(seen.agentRequest,undefined);
    assert.equal(c.editRequest.wordIds,undefined,'Word lists and frame spans belong to change requests only.');assert.equal(c.editRequest.startFrame,undefined);
  }
  assert.equal(describe(video,applyChange(state,{action:'add',videoId:'v',startMs:100,text:'plain'})).agentRequest,undefined);
});

test('malformed change requests are refused and nothing is saved',()=>{
  const state=reviewed();
  for(const wordIds of ['W1',[1],['x'.repeat(121)],Array(2001).fill('W')])assert.throws(()=>applyChange(state,ask({wordIds})),/word IDs/);
  assert.throws(()=>applyChange(state,{...ask(),text:'  '}),/comment/);
  assert.equal(state.comments.length,0);
});

test('a deleted change request can be put back with Undo',()=>{
  const state=reviewed(),c=applyChange(state,ask());
  const again=applyChange({...state,comments:[]},{action:'add',videoId:'v',startMs:c.startMs,endMs:c.endMs,text:c.text,editRequest:c.editRequest});
  assert.deepEqual(again.editRequest,c.editRequest);
});

// The hosted worker only needs Cloudflare's base class to load; nothing here touches the network.
registerHooks({resolve(specifier,context,next){if(specifier==='cloudflare:workers')return {url:'data:text/javascript,export class DurableObject{constructor(ctx,env){this.ctx=ctx;this.env=env;}}',shortCircuit:true};return next(specifier,context);}});
const {ReviewSession}=await import('../cloud/worker.js');
function hostedSession(state){
  const stored=new Map([['context',{videos:state.videos,comments:state.comments,view:null,at:0}]]);
  return new ReviewSession({storage:{get:async key=>stored.get(key),put:async(key,value)=>{stored.set(key,value);}},getWebSockets:()=>[]},{});
}

test('the hosted worker returns the change request in list_comments, get_comment and wait_for_comments',async()=>{
  const state=reviewed(),c=applyChange(state,ask()),session=hostedSession(state);
  const listed=await session.callTool('list_comments',{videoId:'v'},'https://example.test');
  assert.equal(listed.versions[0].comments[0].agentRequest.instruction,'Tighten this and keep the laugh');
  assert.equal(listed.versions[0].comments[0].editRequest.type,'change');
  const one=await session.callTool('get_comment',{commentId:c.id},'https://example.test');
  assert.deepEqual(one.comment.agentRequest.wordIds,['W5','W6','W7']);
  const waited=await session.callTool('wait_for_comments',{waitSeconds:0,all:true},'https://example.test');
  assert.equal(waited.comments[0].agentRequest.startFrame,750);
});

test('both agent connections tell the agent about change requests and preview removals',async()=>{
  const state=reviewed(),session=hostedSession(state);session.ctx.storage.put('tokens',[{hash:await sha('s'),usedAt:1,lastSeen:Date.now()}]);
  const init=await session.mcp({jsonrpc:'2.0',id:1,method:'initialize',params:{}},'s','https://example.test');
  assert.match(init.response.result.instructions,/agentRequest/);assert.match(init.response.result.instructions,/draftEdits/);
  const tools=(await session.mcp({jsonrpc:'2.0',id:2,method:'tools/list'},'s','https://example.test')).response.result.tools;
  assert.match(tools.find(t=>t.name==='get_transcript').description,/draftEdits/);
  assert.match(tools.find(t=>t.name==='list_comments').description,/agentRequest/);
});
async function sha(text){const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));return [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');}

function fakePlayer(start=0){
  const video=Object.assign(new EventTarget(),{paused:false,seeking:false,playbackRate:1,duration:8,time:start,seeks:[],pause(){this.paused=true;this.dispatchEvent(new Event('pause'));}});
  Object.defineProperty(video,'currentTime',{get(){return this.time;},set(value){this.time=value;this.seeks.push(value);}});
  const calls=[],fade={fadeOut:ms=>calls.push(['out',ms??0]),fadeIn:()=>calls.push(['in']),reset:()=>calls.push(['reset'])};
  return {video,calls,playback:createCutPlayback({video,removals:()=>edits,fps:()=>30,fade})};
}

test('playback starts the fade once, ahead of the join, and jumps on time',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const {video,calls,playback}=fakePlayer(1.95);
  playback.tick();
  assert.deepEqual(calls,[['out',35]],'The fade is scheduled to finish on the join.');
  playback.tick();assert.equal(calls.length,1,'Another frame does not schedule it again.');
  video.time=2;t.mock.timers.tick(50);
  assert.deepEqual(video.seeks,[3]);assert.equal(calls.length,1);
});

test('a frame that lands on the join before the timer does not restart a fade in progress',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const {video,calls,playback}=fakePlayer(1.95);
  playback.tick();video.time=2;playback.tick();
  assert.deepEqual(calls,[['out',35]],'Restarting here would bring the sound back to full volume and click.');
  assert.deepEqual(video.seeks,[3]);
});

test('sound comes back when the seek lands, however long it takes',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const {video,calls,playback}=fakePlayer(2);
  playback.tick();
  assert.deepEqual(video.seeks,[3]);assert.deepEqual(calls,[['out',0]],'Past the look-ahead: fade while jumping, without waiting.');
  t.mock.timers.tick(900);assert.equal(calls.length,1,'Still seeking: fading in now would click when the sound returns.');
  playback.tick();assert.equal(video.seeks.length,1,'No second jump while the first is landing.');
  video.dispatchEvent(new Event('seeked'));assert.deepEqual(calls.at(-1),['in']);
  video.dispatchEvent(new Event('seeked'));assert.equal(calls.filter(c=>c[0]==='in').length,1,'A later seek by the reviewer does not fade.');
});

test('the sound can never stay down: a lost seek or a pause restores it',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const lost=fakePlayer(2);lost.playback.tick();t.mock.timers.tick(5000);assert.deepEqual(lost.calls.at(-1),['in']);
  const paused=fakePlayer(1.95);paused.playback.tick();paused.video.pause();assert.deepEqual(paused.calls.at(-1),['reset']);
  t.mock.timers.tick(100);assert.deepEqual(paused.video.seeks,[],'A pause cancels the jump that was waiting.');
});

test('a removal at the end of the file stops playback at the end',()=>{
  const {video,playback}=fakePlayer(5.5);video.duration=6;
  playback.tick();
  assert.equal(video.paused,true);assert.deepEqual(video.seeks,[6]);
});

test('restoring a removal while a jump is waiting cancels the jump and restores the sound',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const list=[...edits],video=Object.assign(new EventTarget(),{paused:false,seeking:false,playbackRate:1,duration:8,currentTime:1.95}),calls=[];
  const playback=createCutPlayback({video,removals:()=>list,fps:()=>30,fade:{fadeOut:()=>calls.push('out'),fadeIn:()=>calls.push('in'),reset:()=>calls.push('reset')}});
  playback.tick();list.length=0;t.mock.timers.tick(50);
  assert.equal(video.currentTime,1.95);assert.deepEqual(calls,['out','reset']);
});

test('the cuts switch: on plays the removals, off plays the original',()=>{
  assert.deepEqual(playbackRemovals(true,edits),edits);
  assert.deepEqual(playbackRemovals(false,edits),[]);
  assert.equal(skipRemoved(2500,playbackRemovals(true,edits)),3000,'On: a position inside a removal skips to its join.');
  assert.equal(skipRemoved(2500,playbackRemovals(false,edits)),2500,'Off: the same position plays as it is.');
  assert.equal(jumpPlan(1950,playbackRemovals(false,edits)),null,'Off: no jump is planned, however close the removal.');
});

test('the cuts switch never turns off a Hear cut audition, which is a removal being tried',()=>{
  const trial={startMs:4000,endMs:4500};
  assert.deepEqual(playbackRemovals(false,edits,trial),[trial]);
  assert.deepEqual(playbackRemovals(true,edits,trial),[...edits,trial]);
});

test('switching the cuts off while a jump waits cancels it and restores the sound',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const {video,calls}=fakePlayer(1.95);
  let on=true;
  const playback=createCutPlayback({video,removals:()=>playbackRemovals(on,edits),fps:()=>30,fade:{fadeOut:()=>calls.push('out'),fadeIn:()=>calls.push('in'),reset:()=>calls.push('reset')}});
  playback.tick();on=false;playback.tick();t.mock.timers.tick(50);
  assert.deepEqual(video.seeks,[],'Nothing jumps once the cuts are off.');
  assert.deepEqual(calls,['out','reset']);
});

test('with the cuts off, playing straight through a removal never jumps',t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const {video,calls}=fakePlayer(2.5);
  const playback=createCutPlayback({video,removals:()=>playbackRemovals(false,edits),fps:()=>30,fade:{fadeOut:()=>calls.push('out'),fadeIn:()=>calls.push('in'),reset:()=>calls.push('reset')}});
  playback.tick();t.mock.timers.tick(50);
  assert.deepEqual(video.seeks,[]);assert.deepEqual(calls,[]);
});

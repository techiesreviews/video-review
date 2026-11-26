// The transcript panel's editing rules, without a page: select, delete, the pieces one Delete makes, Undo, Restore,
// notes, and what happens when a save fails, overlaps another save, or a new render (a recut) arrives meanwhile.
// Saves go through the shared review rules (review-core.js, transcript-core.js), as the browser backend saves them.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createTranscriptEditing,JOINED_TEXT,TOO_SHORT_TEXT} from '../web/transcript-editing.js';
import {applyChange} from '../web/review-core.js';
import {normalizeTranscript,sameWords,storeTranscript} from '../web/transcript-core.js';
import {HOP_MS,envelope} from '../web/cut-boundary.js';
import {removedRanges} from '../web/cut-core.js';

const VIDEO={id:'v',title:'Test',fileName:'test.mp4',durationMs:10000,fps:30};
const TEXT=['zero','one','two','three','four','five','six','seven','eight','nine'];
const word=(i,shift=0)=>({id:'W'+i,text:TEXT[i],startMs:i*1000+100-shift,endMs:i*1000+800-shift});
const TRANSCRIPT={revision:'r1',words:TEXT.map((_,i)=>word(i)),cuts:[{id:'J1',atMs:5000}]};

/** A promise the test settles by hand, to hold a save or a request open. */
function held(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}

/** A review state with one video, saved through the shared rules, and an editing module that writes to it.
 *  hold.save / hold.request: the next save or request waits for the test. fail.save: the next save is refused. */
function setup({transcript=TRANSCRIPT}={}){
  const state={videos:[{...VIDEO}],comments:[],appliedOps:[]},calls={saves:[],requests:[],updates:[],cancels:[],reasons:[],errors:[]};
  const hold={},fail={},timers=[];
  let now=0;
  const editing=createTranscriptEditing({
    async saveTranscript(video,data,options){
      calls.saves.push(data);
      assert.deepEqual(options,{editsOnly:true},'an edit saves only the preview cuts');
      if(fail.save){const error=fail.save;delete fail.save;throw error;}
      if(hold.save){const wait=hold.save;delete hold.save;await wait.promise;}
      return storeTranscript(state,video.id,data,options);
    },
    async request(selection){
      calls.requests.push(selection);
      if(hold.request){const wait=hold.request;delete hold.request;await wait.promise;}
      return applyChange(state,{action:'add',videoId:VIDEO.id,startMs:selection.startMs,endMs:selection.endMs,text:selection.text,editRequest:selection.editRequest,createdAt:new Date(++now*1000).toISOString()});
    },
    async updateRequest(id,text){calls.updates.push([id,text]);return applyChange(state,{action:'update',id,text});},
    async cancelRequest(id,reason){
      calls.cancels.push(id);if(reason)calls.reasons.push([id,reason]);
      if(hold.cancel){const wait=hold.cancel;delete hold.cancel;await wait.promise;}
      applyChange(state,{action:'reply',id,author:'you',text:'Undid this preview removal; keep the passage.',status:'done'});},
    onError:error=>calls.errors.push(error.message),
    schedule(run,ms){const timer={run,ms,live:true};timers.push(timer);return ()=>{timer.live=false;};},
  });
  editing.load(storeTranscript(state,VIDEO.id,transcript),state.videos[0]);
  const fire=()=>{for(const timer of timers.splice(0))if(timer.live)timer.run();};
  return {editing,state,calls,hold,fail,fire};
}
const words=edit=>edit.wordIds;

test('a Delete on selected words saves one cut with their ids, linked to its note, and points at the next word',async()=>{
  const {editing,state,calls}=setup();
  editing.selectWords(1,2);
  assert.deepEqual(editing.selectedIds(),['W1','W2']);
  const result=await editing.remove({typed:'  too slow '});
  assert.equal(calls.requests.length,1);
  assert.equal(calls.requests[0].text,'Remove from cut: “one two” — too slow');
  assert.deepEqual(calls.requests[0].editRequest,{type:'remove',quote:'one two',firstWordId:'W1',lastWordId:'W2',cutId:undefined,transcriptRevision:'r1'});
  const [edit]=editing.edits();
  assert.deepEqual(words(edit),['W1','W2']);
  assert.equal(edit.commentId,state.comments[0].id,'the cut knows its note');
  assert.equal(state.transcripts.v.draftEdits.length,1,'saved through the shared transcript rules');
  assert.equal(result.next,3);assert.equal(result.first,1);assert.equal(result.last,2);assert.equal(result.wasWords,true);
  assert.equal(result.line,'Deleted, with your note. Ctrl+Z brings both back.');
  assert.equal(editing.selection,null,'the saved cut clears the selection; the panel then picks the next word');
  assert.equal(editing.busy,false);
});

/** The test transcript's loudness: each word is speech (-20 dB) from 30 ms after its transcript start to 20 ms before
 *  its end, with silence (-80 dB) between, as the server would measure it. */
function loudness(){
  const db=new Float32Array(VIDEO.durationMs/HOP_MS).fill(-80);
  for(const w of TRANSCRIPT.words)for(let i=(w.startMs+30)/HOP_MS;i<(w.endMs-20)/HOP_MS;i++)db[i]=-20;
  return envelope(db);
}
/** True when no cut covers any of the word's speech. */
const heard=(editing,i)=>editing.edits().every(e=>e.endMs<=i*1000+130||e.startMs>=i*1000+780);

test('a Delete over words already deleted (dragged across them) makes one cut with all of them, sharing one note',async()=>{
  const {editing,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(2);await editing.remove();
  const inner=editing.edits()[0];
  editing.selectWords(0,4);
  assert.deepEqual(editing.toRestore(),[],'some words are still in the cut, so the button deletes');
  const result=await editing.remove();
  assert.deepEqual(editing.edits().map(words),[['W0','W1','W2','W3','W4']],'the earlier cut joins the new one');
  const [cut]=editing.edits();
  assert.ok(cut.startMs<=inner.startMs&&cut.endMs>=inner.endMs,'nothing the earlier cut removed comes back');
  const ask=calls.requests[1];
  assert.equal(ask.text,'Remove from cut: “zero one two three four”');
  assert.equal(ask.editRequest.firstWordId,'W0');assert.equal(ask.editRequest.lastWordId,'W4');
  assert.equal(ask.startMs,cut.startMs);assert.equal(ask.endMs,cut.endMs);
  assert.equal(result.next,5);
});

test('a Delete next to a cut joins it, so the run of deleted words is one cut',async()=>{
  const {editing}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(3);await editing.remove();
  editing.selectWords(1,2);await editing.remove();
  assert.deepEqual(editing.edits().map(words),[['W1','W2','W3']]);
  assert.ok(heard(editing,0)&&heard(editing,4),'the words either side stay');
});

test('Undo of a Delete that took in earlier cuts brings them back as they were, and closes only its own note',async()=>{
  const {editing,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(2);await editing.remove();
  const inner=editing.edits()[0];
  editing.selectWords(0,4);await editing.remove({typed:'cut it'});
  const commentId=editing.edits().at(-1).commentId;
  const undone=await editing.undo();
  assert.deepEqual(editing.edits(),[inner],'the earlier cut is back, exactly as it was');
  assert.deepEqual(calls.cancels,[commentId]);
  assert.equal(undone.note,'cut it','the note typed with the Delete comes back for the box');
  assert.equal(undone.first,0);assert.equal(undone.last,4);
  assert.equal(await editing.undo().then(r=>r.first),2,'the next Undo brings back the first cut');
  assert.deepEqual(editing.edits(),[]);
  assert.equal(await editing.undo(),null,'nothing left to undo');
});

test('after a reload or a video switch, Undo of a joined Delete still brings back the cuts it took in, notes open',async()=>{
  const {editing,state,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(2);await editing.remove();
  const inner=editing.edits()[0];
  editing.selectWords(0,4);await editing.remove();
  const joined=editing.edits()[0];
  assert.deepEqual(state.transcripts.v.draftEdits[0].takenIn,[{...inner,at:0}],'the taken-in cut is saved with the joined cut, with its place');
  editing.load(null,{...VIDEO,id:'other'});
  editing.load(JSON.parse(JSON.stringify(state.transcripts.v)),state.videos[0]);
  await editing.undo();
  assert.deepEqual(editing.edits(),[inner],'only the joined Delete is undone');
  assert.deepEqual(calls.cancels,[joined.commentId],'the taken-in cut keeps its note open');
});

test('a long run of single-word Deletes keeps saving, and Undo still steps back one Delete at a time, closing every note',async()=>{
  const many=Array.from({length:60},(_,i)=>({id:'W'+i,text:'w'+i,startMs:i*150+10,endMs:i*150+110}));
  const {editing,state,calls}=setup({transcript:{revision:'r1',words:many,cuts:[]}});
  for(let i=0;i<55;i++){
    editing.selectWords(i);
    assert.ok(await editing.remove(),`Delete ${i+1} saves`);
  }
  // The oldest Deletes are too far back to undo one by one: their notes close now, saying their words stay cut under
  // the newer note, never that the passage is kept.
  const oldest=state.comments[0].id;
  assert.ok(editing.edits()[0].wordIds.includes('W0'),'W0 is still cut');
  assert.deepEqual(calls.reasons.find(([id])=>id===oldest)?.[1],{text:JOINED_TEXT});
  assert.equal(calls.cancels.length,calls.reasons.length,'no note was closed as kept');
  assert.deepEqual(editing.edits().map(e=>e.wordIds.length),[55],'one cut');
  for(const left of [54,53,52]){await editing.undo();assert.deepEqual(editing.edits().map(e=>e.wordIds.length),[left]);}
  // Undo too far back for the saved history: the words come back with the oldest Delete still kept, and no note is
  // left open for words that play again.
  while(editing.edits().length)assert.ok(await editing.undo());
  const notes=state.comments.map(c=>c.id);
  assert.equal(notes.length,55);
  assert.deepEqual(notes.filter(id=>!calls.cancels.includes(id)),[],'every Delete note is closed');
});

test('bringing one word back and deleting it again, over and over, keeps the saved history small and saving',async()=>{
  const many=Array.from({length:60},(_,i)=>({id:'W'+i,text:'w'+i,startMs:i*150+10,endMs:i*150+110}));
  const {editing}=setup({transcript:{revision:'r1',words:many,cuts:[]}});
  const size=list=>(list||[]).reduce((n,e)=>n+1+size(e.takenIn),0);
  editing.selectWords(10,20);await editing.remove();
  for(let round=1;round<=12;round++){
    assert.ok(await editing.restoreWords(['W15']),`round ${round}: W15 comes back`);
    assert.equal(editing.edits().length,2,'the cut is split in two');
    editing.selectWords(15);
    assert.ok(await editing.remove(),`round ${round}: deleting W15 again saves`);
    assert.deepEqual(editing.edits().map(e=>e.wordIds.length),[11],'one cut again');
  }
  assert.ok(size(editing.edits())<=40,`the history grows by a few cuts a round, not doubling (${size(editing.edits())})`);
  editing.selectWords(40);assert.ok(await editing.remove(),'a Delete elsewhere still saves');
});

test('a Delete cuts the words selected when it was pressed, even if another word is clicked while it waits',async()=>{
  const {editing,fire}=setup();editing.setLoudness('v',new Promise(()=>{}));
  editing.selectWords(1,2);
  const done=editing.remove();
  editing.selectWords(7);
  fire();await done;
  assert.deepEqual(editing.edits().map(words),[['W1','W2']]);
});

test('while the loudness is still being measured, only the first edit waits for it',async()=>{
  const {editing,fire}=setup();editing.setLoudness('v',new Promise(()=>{}));
  editing.selectWords(1);const first=editing.remove();
  await new Promise(done=>setTimeout(done,0));fire();await first;
  editing.selectWords(5);
  const second=await Promise.race([editing.remove(),new Promise(done=>setTimeout(()=>done('waited'),50))]);
  assert.notEqual(second,'waited','the second Delete cuts on the word times at once');
  assert.deepEqual(editing.edits().map(words),[['W1'],['W5']]);
});

test('restoring one word in the middle of a cut splits it in two, keeping its outer edges and its note',async()=>{
  const {editing,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(1,3);await editing.remove();
  const [cut]=editing.edits();
  const back=await editing.restoreWords(['W2']);
  assert.deepEqual(editing.edits().map(words),[['W1'],['W3']]);
  const [left,right]=editing.edits();
  assert.equal(left.startMs,cut.startMs);assert.equal(right.endMs,cut.endMs);
  assert.ok(heard(editing,2),'all of the restored word plays');
  assert.ok(!heard(editing,1)&&!heard(editing,3),'the words either side stay cut');
  assert.equal(left.commentId,cut.commentId);assert.equal(right.commentId,cut.commentId);
  assert.deepEqual(calls.cancels,[],'the note still has words in the cut');
  assert.equal(back.first,2);assert.equal(back.last,2);
  await editing.restoreWords(['W1']);
  assert.deepEqual(editing.edits().map(words),[['W3']]);
  await editing.restoreWords(['W3']);
  assert.deepEqual(editing.edits(),[]);
  assert.deepEqual(calls.cancels,[cut.commentId],'the note closes with its last word');
});

test('restoring a word at the edge of a cut shortens it; without loudness the cut still stops at that word',async()=>{
  const {editing}=setup();
  editing.selectWords(1,3);await editing.remove();
  await editing.restoreWords(['W3']);
  assert.deepEqual(editing.edits().map(words),[['W1','W2']]);
  assert.ok(heard(editing,3),'the restored word plays');
});

test('Restore on a selection of deleted words brings back just those words; the note closes with its last word',async()=>{
  const {editing,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(1,4);await editing.remove();
  const [cut]=editing.edits();
  editing.selectWords(1,2);
  assert.deepEqual(editing.toRestore().map(e=>e.id),[cut.id],'every selected word is deleted, so the button restores');
  let back=await editing.restoreWords(editing.selectedIds(),{keep:true});
  assert.equal(back.noteResolved,false);
  assert.deepEqual(editing.edits().map(words),[['W3','W4']]);
  assert.deepEqual(calls.cancels,[],'the note still has words in the cut');
  assert.deepEqual(editing.selectedIds(),['W1','W2'],'keep leaves the selection as it was');
  back=await editing.restoreWords(['W3','W4']);
  assert.deepEqual(calls.cancels,[cut.commentId]);
  assert.equal(editing.selection,null);
  assert.deepEqual(editing.edits(),[]);
});

test('a selected pause that is cut offers Restore, which brings that pause back',async()=>{
  const {editing}=setup();
  editing.selectPause(TRANSCRIPT.words[3],TRANSCRIPT.words[4]);await editing.remove();
  const [edit]=editing.edits();
  editing.selectPause(TRANSCRIPT.words[3],TRANSCRIPT.words[4]);
  assert.deepEqual(editing.toRestore().map(e=>e.id),[edit.id]);
  await editing.restore([edit.id]);
  assert.deepEqual(editing.edits(),[]);
});

test('a join is selected around its cut point; a note on it goes to the agent with the join id',async()=>{
  const {editing,state,calls}=setup();
  editing.selectCut(TRANSCRIPT.cuts[0]);
  assert.deepEqual(editing.selection,{startMs:4800,endMs:5200,cutId:'J1',atMs:5000,quote:'Join J1'});
  const sent=await editing.send('Check this join');
  assert.equal(sent.kind,'sent');assert.equal(sent.current,true);
  assert.equal(calls.requests[0].editRequest.type,'change');
  assert.equal(calls.requests[0].editRequest.cutId,'J1');
  assert.equal(state.comments[0].editRequest.startFrame,144,'the shared rules turned the time into a frame');
});

test('Enter on a kept join that moved elsewhere sends nothing while Undo or Restore is still closing the cut note',async()=>{
  // Another editor moves J1 from 5 s to 6 s, or removes it; the store keeps its joins when this page saves its cut list.
  for(const [change,cuts] of [['moved',[{id:'J1',atMs:6000}]],['removed',[]]])for(const action of ['undo','restore']){
    const {editing,state,calls,hold}=setup(),label=`${change}, ${action}`;
    editing.selectWords(1);await editing.remove();
    const [cut]=editing.edits();
    editing.selectCut(TRANSCRIPT.cuts[0]);
    state.transcripts.v={...state.transcripts.v,cuts};
    const closing=held();hold.cancel=closing;
    const running=action==='undo'?editing.undo({keep:true}):editing.restore([cut.id],{keep:true});
    while(!calls.cancels.length)await new Promise(next=>setImmediate(next));
    assert.deepEqual(editing.transcript.cuts,cuts,`${label}: the saved transcript with the changed join is loaded`);
    const requests=calls.requests.length;
    const sent=await editing.send('Check this join');
    assert.equal(sent,null,`${label}: Enter does not send`);
    assert.equal(calls.requests.length,requests,`${label}: no request uses the join's old time`);
    closing.resolve();await running;
  }
});

test('a pause selection cuts only the gap, inside its edges',async()=>{
  const {editing}=setup();
  editing.selectPause(TRANSCRIPT.words[3],TRANSCRIPT.words[4]);
  const result=await editing.remove();
  const [edit]=editing.edits();
  assert.equal(edit.kind,'pause');assert.deepEqual(edit.wordIds,[]);
  assert.ok(edit.startMs>3800&&edit.endMs<4100,`${edit.startMs}–${edit.endMs}`);
  assert.equal(result.wasWords,false);assert.equal(result.next,-1);
});

test('a save that fails leaves the cut out, the selection in place, and closes the note made for it',async()=>{
  const {editing,state,calls,fail}=setup();
  editing.selectWords(1,2);
  fail.save=new Error('Disk full');
  await assert.rejects(editing.remove(),/Disk full/);
  assert.deepEqual(editing.edits(),[]);
  assert.deepEqual(state.transcripts.v.draftEdits,[]);
  assert.deepEqual(editing.selectedIds(),['W1','W2'],'the words stay selected, so Delete can be tried again');
  assert.equal(editing.busy,false);
  assert.deepEqual(calls.cancels,[state.comments[0].id],'no open note claims a cut that was never saved');
  await editing.remove();
  assert.equal(editing.edits().length,1,'trying again works');
});

// An agent or another tab replaces the transcript in the store, and this page never hears of it.
const replacedElsewhere=state=>storeTranscript(state,VIDEO.id,{revision:'r2',words:TEXT.map((_,i)=>i===6?{...word(6),text:'sixty'}:word(i)),cuts:[]});

test('a Delete the store refuses because the words changed elsewhere is marked for a reload, and keeps the newer words',async()=>{
  const {editing,state,calls}=setup();
  replacedElsewhere(state);
  editing.selectWords(5,6);
  const error=await editing.remove({typed:'tighter'}).catch(e=>e);
  assert.match(error.message,/transcript changed/);
  assert.equal(error.refused,true,'the panel loads that video\'s transcript again');
  assert.equal(error.videoId,'v','tagged with the video it was made on');
  assert.notEqual(error.saved,true);
  assert.equal(state.transcripts.v.revision,'r2','the newer words stay in the store');
  assert.equal(state.transcripts.v.words[6].text,'sixty');assert.deepEqual(state.transcripts.v.draftEdits,[]);
  assert.deepEqual(editing.selectedIds(),['W5','W6'],'the place to come back to after the reload');
  assert.deepEqual(calls.cancels,[state.comments[0].id],'no open note claims the refused cut');
  assert.equal(editing.busy,false);
});

test('an Undo or Restore the store refuses because the words changed elsewhere is marked for a reload, and its note stays open',async()=>{
  const {editing,state,calls}=setup();
  editing.selectWords(1);await editing.remove();
  replacedElsewhere(state);
  const undo=await editing.undo().catch(e=>e);
  assert.equal(undo.refused,true);assert.equal(undo.videoId,'v');
  const restore=await editing.restore([editing.edits()[0].id]).catch(e=>e);
  assert.equal(restore.refused,true);assert.equal(restore.videoId,'v');
  assert.equal(state.transcripts.v.words[6].text,'sixty','the newer words stay in the store');
  assert.deepEqual(calls.cancels,[],'the cut was not brought back, so its note stays open');
});

test('a refusal that lands after a new transcript came in is not marked for a reload, since the new words are loaded',async()=>{
  const {editing,state,hold}=setup();
  editing.selectWords(1);
  hold.save=held();const wait=hold.save;
  const cut=editing.remove();
  await new Promise(r=>setTimeout(r));
  editing.load(replacedElsewhere(state),state.videos[0]);
  wait.resolve();
  const error=await cut.catch(e=>e);
  assert.match(error.message,/transcript changed/);
  assert.equal(error.refused,undefined,'the panel already shows the new words');
});

test('a save that fails for another reason is not marked for a reload',async()=>{
  const {editing,fail}=setup();
  editing.selectWords(1);
  fail.save=new Error('Disk full');
  const error=await editing.remove().catch(e=>e);
  assert.equal(error.refused,undefined);
});

test('an Undo or Restore whose save fails keeps the cut and its note',async()=>{
  const {editing,calls,fail}=setup();
  editing.selectWords(1);await editing.remove();
  fail.save=new Error('Disk full');
  await assert.rejects(editing.undo(),/Disk full/);
  assert.equal(editing.edits().length,1);assert.deepEqual(calls.cancels,[]);assert.equal(editing.busy,false);
  fail.save=new Error('Disk full');
  await assert.rejects(editing.restore([editing.edits()[0].id]),/Disk full/);
  assert.equal(editing.edits().length,1);assert.deepEqual(calls.cancels,[]);
});

test('an edit while another save runs is refused, and works once that save is done',async()=>{
  const {editing,calls,hold}=setup();
  editing.selectWords(1);
  hold.save=held();const wait=hold.save;
  const first=editing.remove();
  await new Promise(r=>setTimeout(r));
  assert.equal(editing.busy,true);
  editing.selectWords(5);
  assert.equal(await editing.remove(),null,'a second Delete waits for nothing and does nothing');
  assert.equal(await editing.undo(),null);
  assert.equal(await editing.restore(['anything']),null);
  assert.equal(calls.requests.length,1);assert.equal(calls.saves.length,1);
  wait.resolve();await first;
  assert.equal(editing.edits().length,1);
  editing.selectWords(5);await editing.remove();
  assert.deepEqual(editing.edits().map(words),[['W1'],['W5']]);
});

test('a note sent while a cut is saving is saved too, and a second Enter while sending is ignored',async()=>{
  const {editing,state,hold}=setup();
  editing.selectWords(1);
  hold.save=held();const wait=hold.save;
  const cut=editing.remove();
  await new Promise(r=>setTimeout(r));
  editing.selectWords(6);
  hold.request=held();const ask=hold.request;
  const sent=editing.send('Louder here');
  assert.equal(editing.sending,true);
  assert.equal(await editing.send('Louder here'),null);
  ask.resolve();wait.resolve();
  assert.equal((await sent).kind,'sent');await cut;
  assert.deepEqual(state.comments.map(c=>c.text),['Remove from cut: “one”','Louder here']);
});

test('after a recut, a Delete lands on the word with that id at its new time',async()=>{
  const {editing,state}=setup();
  // The new render dropped the first two words, so every later word moved 2 s earlier and two places up.
  const recut={revision:'r2',words:TEXT.slice(2).map((_,i)=>word(i+2,2000)),cuts:[]};
  editing.load(storeTranscript(state,VIDEO.id,recut),state.videos[0]);
  editing.selectWords(3);
  assert.deepEqual(editing.selectedIds(),['W5']);
  await editing.remove();
  const [edit]=editing.edits();
  assert.deepEqual(edit.wordIds,['W5']);
  assert.ok(edit.startMs>=2800&&edit.endMs<=3900,`${edit.startMs}–${edit.endMs} is around W5's new time`);
  assert.deepEqual(editing.target(0,{firstWordId:'W5',lastWordId:'W5'}),{first:3,last:3,cut:null,seekMs:3100});
});

test('a Delete that a recut overtakes before it saves is dropped, not written onto the new words',async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);
  hold.request=held();const ask=hold.request;
  const cut=editing.remove();
  await new Promise(r=>setTimeout(r));
  const recut={revision:'r2',words:TEXT.slice(2).map((_,i)=>word(i+2,2000)),cuts:[]};
  editing.load(storeTranscript(state,VIDEO.id,recut),state.videos[0]);
  ask.resolve();
  await assert.rejects(cut,/transcript changed/);
  assert.equal(calls.saves.length,0,'the old cut never reaches the new transcript');
  assert.equal(editing.transcript.revision,'r2');assert.deepEqual(editing.edits(),[]);
  assert.deepEqual(calls.cancels,[state.comments[0].id]);
  assert.equal(editing.busy,false);
});

test('an Undo whose save reaches the store after a recut is refused there, so the new words stay saved',async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);await editing.remove();
  hold.save=held();const wait=hold.save;
  const undo=editing.undo();
  await new Promise(r=>setTimeout(r));
  const recut={revision:'r2',words:TEXT.slice(2).map((_,i)=>word(i+2,2000)),cuts:[]};
  const fresh=storeTranscript(state,VIDEO.id,recut);
  editing.load(fresh,state.videos[0]);
  wait.resolve();
  await assert.rejects(undo,/transcript changed/);
  assert.equal(editing.transcript,fresh);
  assert.equal(state.transcripts.v.revision,'r2','the store kept the new render');
  assert.deepEqual(calls.cancels,[],'the cut was not brought back, so its note stays open');
});

test('an Undo whose save reaches the store after a shorter render is refused as changed words, so the page reloads them',async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);await editing.remove();
  hold.save=held();const wait=hold.save;
  const undo=editing.undo();
  await new Promise(r=>setTimeout(r));
  // The new render is 8 s long, so the old words run past its end; another tab saved its transcript.
  state.videos[0].durationMs=8000;
  storeTranscript(state,VIDEO.id,{revision:'r2',words:TEXT.slice(2).map((_,i)=>word(i+2,2000)),cuts:[]});
  wait.resolve();
  const error=await undo.then(()=>null,error=>error);
  assert.match(error?.message,/transcript changed/);
  assert.equal(error.refused,true,'the panel loads the newer words');
  assert.equal(state.transcripts.v.revision,'r2');
  assert.deepEqual(calls.cancels,[]);
});

test('a Delete whose save reaches the store after a recut is refused there, and its note is closed',async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);
  hold.save=held();const wait=hold.save;
  const cut=editing.remove();
  await new Promise(r=>setTimeout(r));
  editing.load(storeTranscript(state,VIDEO.id,{revision:'r2',words:TEXT.slice(2).map((_,i)=>word(i+2,2000)),cuts:[]}),state.videos[0]);
  wait.resolve();
  await assert.rejects(cut,/transcript changed/);
  assert.equal(state.transcripts.v.revision,'r2');assert.deepEqual(state.transcripts.v.draftEdits,[]);
  assert.deepEqual(calls.cancels,[state.comments[0].id]);
});

test('a Delete saved while the same words came in again is kept, says so, and keeps its note',async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);
  hold.save=held();const wait=hold.save;
  const cut=editing.remove();
  await new Promise(r=>setTimeout(r));
  // An agent attaches the same transcript again: the words did not change, so the store takes the cut.
  editing.load(storeTranscript(state,VIDEO.id,TRANSCRIPT),state.videos[0]);
  wait.resolve();
  const error=await cut.then(()=>null,error=>error);
  assert.equal(error.saved,true,'the panel loads the transcript again to show the saved cut');
  assert.match(error.message,/The edit was saved/);
  assert.deepEqual(state.transcripts.v.draftEdits.map(e=>e.wordIds),[['W1']]);
  assert.deepEqual(calls.cancels,[],'its note stays open with its cut');
  assert.equal(error.videoId,'v','the panel reloads only while this video is still open');
});

test('a Delete saved after another video was opened says which video it was saved on',async()=>{
  const {editing,hold}=setup();
  editing.selectWords(1);
  hold.save=held();const wait=hold.save;
  const cut=editing.remove();
  await new Promise(r=>setTimeout(r));
  editing.load(null,{...VIDEO,id:'w'});
  wait.resolve();
  const error=await cut.then(()=>null,error=>error);
  assert.equal(error.saved,true);assert.equal(error.videoId,'v','so the panel does not reload the video now open');
});

for(const kind of ['Undo','Restore'])test(`an ${kind} saved while the same words came in again closes the note of the cut it brought back`,async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);await editing.remove({typed:'why'});
  hold.save=held();const wait=hold.save;
  const back=kind==='Undo'?editing.undo():editing.restore([editing.edits()[0].id]);
  await new Promise(r=>setTimeout(r));
  editing.load(storeTranscript(state,VIDEO.id,{...TRANSCRIPT,draftEdits:state.transcripts.v.draftEdits}),state.videos[0]);
  wait.resolve();
  const error=await back.then(()=>null,error=>error);
  assert.equal(error.saved,true);
  assert.deepEqual(state.transcripts.v.draftEdits,[],'the store took it');
  assert.deepEqual(calls.cancels,[state.comments[0].id],'no open note asks for a cut that is gone');
});

test('a full transcript (original words, joins, source mapping) loaded by the page takes an edit-only save',()=>{
  const video={...VIDEO,keepRanges:[[0,300]]},state={videos:[video],comments:[]};
  const full={...TRANSCRIPT,fps:30,keepRanges:[[0,300]],original:{durationMs:20000,label:'',sourceId:'raw',words:TRANSCRIPT.words}};
  storeTranscript(state,VIDEO.id,full);
  const page=normalizeTranscript(JSON.parse(JSON.stringify(state.transcripts.v)),video.durationMs,video.fps);
  const edit={id:'e1',startMs:1000,endMs:1900,wordIds:['W1'],quote:'one',kind:'words'};
  assert.equal(storeTranscript(state,VIDEO.id,{...page,draftEdits:[edit]},{editsOnly:true}).draftEdits.length,1);
  assert.equal(sameWords(full,{...page,label:'Another label',revision:'other'}),true,'labels and revisions are not words');
  assert.equal(sameWords(full,{...page,keepRanges:[[10,310]]}),false,'a new source mapping is a new render');
});

test('a note that cannot be closed while such a save lands is reported, and the save still says it landed',async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);await editing.remove({typed:'why'});
  hold.save=held();const wait=hold.save;
  const undo=editing.undo();
  await new Promise(r=>setTimeout(r));
  editing.load(storeTranscript(state,VIDEO.id,{...TRANSCRIPT,draftEdits:state.transcripts.v.draftEdits}),state.videos[0]);
  state.comments.length=0;
  wait.resolve();
  const error=await undo.then(()=>null,error=>error);
  assert.equal(error.saved,true,'the panel still loads the transcript again');
  assert.deepEqual(calls.errors,['Comment not found'],'the failed close is reported');
});

for(const kind of ['Undo','Restore'])test(`an ${kind} whose note is still closing when another video opens ends as saved, not drawn on the new video`,async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);await editing.remove({typed:'why'});
  hold.cancel=held();const wait=hold.cancel;
  const back=kind==='Undo'?editing.undo():editing.restore([editing.edits()[0].id]);
  while(!calls.cancels.length)await new Promise(r=>setTimeout(r));
  editing.load(null,{...VIDEO,id:'w'});
  wait.resolve();
  const error=await back.then(()=>null,error=>error);
  assert.equal(error?.saved,true,'no result for the panel to seek or select with');
  assert.equal(error.videoId,'v');
  assert.deepEqual(state.transcripts.v.draftEdits,[]);
});

for(const kind of ['Undo','Restore'])test(`an ${kind} whose note fails to close after another video opens ends as saved, with nothing said over the new video`,async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);await editing.remove({typed:'why'});
  hold.cancel=held();const wait=hold.cancel;
  const back=kind==='Undo'?editing.undo():editing.restore([editing.edits()[0].id]);
  while(!calls.cancels.length)await new Promise(r=>setTimeout(r));
  editing.load(null,{...VIDEO,id:'w'});
  wait.reject(Error('Network down'));
  const error=await back.then(()=>null,error=>error);
  assert.equal(error?.saved,true,'the panel treats it as saved on the old video');
  assert.equal(error.videoId,'v');
  assert.deepEqual(calls.errors,[],'no error over the new video');
  assert.deepEqual(state.transcripts.v.draftEdits,[],'the saved cut stays removed');
});

for(const close of ['closes','fails to close'])test(`a Delete whose let-go note ${close} after another video opens ends as saved, so the old selection is not used on the new video`,async()=>{
  // The 32nd Delete in a row reaches past the Undo depth, so it closes the oldest note while it ends (see joinedCut).
  const many=Array.from({length:40},(_,i)=>({id:'W'+i,text:'w'+i,startMs:i*150+10,endMs:i*150+110}));
  const {editing,state,calls,hold}=setup({transcript:{revision:'r1',words:many,cuts:[]}});
  for(let i=0;i<31;i++){editing.selectWords(i);await editing.remove();}
  assert.deepEqual(calls.cancels,[],'no note let go yet');
  hold.cancel=held();const wait=hold.cancel;
  editing.selectWords(31);const deleting=editing.remove();
  while(!calls.cancels.length)await new Promise(r=>setTimeout(r));
  editing.load(null,{...VIDEO,id:'w'});
  if(close==='closes')wait.resolve();else wait.reject(Error('Network down'));
  const error=await deleting.then(()=>null,error=>error);
  assert.equal(error?.saved,true,'no result for the panel to seek or select with');
  assert.equal(error.videoId,'v');
  assert.deepEqual(calls.errors,[],'nothing said over the new video');
  assert.ok(state.transcripts.v.draftEdits[0].wordIds.includes('W31'),'the Delete is saved on the old video');
});

test('a note that fails to close after the same video loads again is reported, and the Undo ends as saved',async()=>{
  const {editing,state,calls,hold}=setup();
  editing.selectWords(1);await editing.remove({typed:'why'});
  hold.cancel=held();const wait=hold.cancel;
  const undo=editing.undo();
  while(!calls.cancels.length)await new Promise(r=>setTimeout(r));
  editing.load(storeTranscript(state,VIDEO.id,{...TRANSCRIPT,draftEdits:state.transcripts.v.draftEdits}),state.videos[0]);
  wait.reject(Error('Network down'));
  const error=await undo.then(()=>null,error=>error);
  assert.equal(error?.saved,true);assert.equal(error.videoId,'v');
  assert.deepEqual(calls.errors,['Network down']);
});

test('an Undo saved after another video opens, whose note then fails to close, says nothing over the new video',async()=>{
  const {editing,calls,hold}=setup();
  editing.selectWords(1);await editing.remove({typed:'why'});
  hold.save=held();hold.cancel=held();const save=hold.save,close=hold.cancel;
  const undo=editing.undo();
  await new Promise(r=>setTimeout(r));
  editing.load(null,{...VIDEO,id:'w'});
  save.resolve();
  while(!calls.cancels.length)await new Promise(r=>setTimeout(r));
  close.reject(Error('Network down'));
  const error=await undo.then(()=>null,error=>error);
  assert.equal(error?.saved,true);assert.equal(error.videoId,'v');
  assert.deepEqual(calls.errors,[]);
});

test('an edit-only save writes only the cut list, so a newer revision, label or joins stay',()=>{
  const state={videos:[{...VIDEO}],comments:[]};
  storeTranscript(state,VIDEO.id,TRANSCRIPT);
  storeTranscript(state,VIDEO.id,{...TRANSCRIPT,revision:'r2',label:'Corrected',cuts:[{id:'J9',atMs:6000,risk:true}]});
  const edit={id:'e1',startMs:1000,endMs:1900,wordIds:['W1'],quote:'one',kind:'words'};
  const saved=storeTranscript(state,VIDEO.id,{...TRANSCRIPT,draftEdits:[edit]},{editsOnly:true});
  assert.equal(saved,state.transcripts.v);
  assert.deepEqual([saved.revision,saved.label,saved.cuts.map(c=>c.id)],['r2','Corrected',['J9']]);
  assert.deepEqual(saved.draftEdits.map(e=>e.id),['e1']);
});

test('an edit-only save is refused by the browser store when the saved words differ, or nothing is saved yet',()=>{
  const state={videos:[{...VIDEO}],comments:[]};
  assert.throws(()=>storeTranscript(state,VIDEO.id,TRANSCRIPT,{editsOnly:true}),/transcript changed/);
  storeTranscript(state,VIDEO.id,TRANSCRIPT);
  const edit={id:'e1',startMs:1000,endMs:1900,wordIds:['W1'],quote:'one',kind:'words'};
  assert.equal(storeTranscript(state,VIDEO.id,{...TRANSCRIPT,draftEdits:[edit]},{editsOnly:true}).draftEdits.length,1);
  const moved={...TRANSCRIPT,words:TRANSCRIPT.words.map(w=>w.id==='W3'?{...w,text:'tree'}:w)};
  assert.throws(()=>storeTranscript(state,VIDEO.id,moved,{editsOnly:true}),/transcript changed/);
  assert.equal(state.transcripts.v.draftEdits.length,1);
  assert.throws(()=>storeTranscript(state,VIDEO.id,{draftEdits:[edit]},{editsOnly:true}),/word-timed transcript is required/,'as review_store says it');
});

test('a note opened from a comment saves its typing after a pause, and leaving it saves at once',async()=>{
  const {editing,calls,fire}=setup();
  editing.selectWords(1);
  const comment=await editing.send('First thought');
  assert.equal(comment.kind,'sent');
  editing.editNote('c-1','Base');
  editing.noteTyped('more');
  assert.deepEqual(calls.updates,[],'not before the pause');
  fire();
  assert.deepEqual(calls.updates,[['c-1','Base — more']]);
  editing.noteTyped('again');
  editing.selectWords(4);
  assert.deepEqual(calls.updates.at(-1),['c-1','Base — again'],'choosing other words saves the typing first');
  assert.equal(editing.noteId,null);
  await new Promise(r=>setTimeout(r));
  assert.deepEqual(calls.errors,['Comment not found','Comment not found'],'a failed note save is reported, not dropped');
});

test('the typing in a note opened from a comment is saved to that comment after the pause',async()=>{
  const {editing,state,fire}=setup();
  editing.selectWords(1);await editing.send('First thought');
  editing.editNote(state.comments[0].id,'Base');
  editing.noteTyped('more');
  fire();await new Promise(r=>setTimeout(r));
  assert.equal(state.comments[0].text,'Base — more');
});

test('Enter on a note opened from a comment updates that comment',async()=>{
  const {editing,state,calls}=setup();
  editing.selectWords(1);await editing.send('First thought');
  editing.editNote(state.comments[0].id,'');
  const result=await editing.send('Second thought');
  assert.equal(result.kind,'updated');assert.equal(result.current,true);
  assert.deepEqual(calls.updates,[[state.comments[0].id,'Second thought']]);
  assert.equal(state.comments[0].text,'Second thought');
});

test('a restore from the original recording is asked once, with times in the shared clock format',async()=>{
  const {editing,state,calls}=setup({transcript:{...TRANSCRIPT,original:{durationMs:20000,sourceId:'raw',words:[...TRANSCRIPT.words,{id:'R1',text:'lost',startMs:12000,endMs:12500}]}}});
  const group={id:'source-gap-0',atMs:9000,sourceStartMs:11900,sourceEndMs:12600,words:[{id:'R1',text:'lost',startMs:12000,endMs:12500}]};
  editing.selectSource(group,0);
  assert.equal(editing.selection.kind,'source');
  assert.equal(await editing.remove(),null,'a Delete does nothing on words from the original');
  await editing.askRestore(editing.selection);
  assert.equal(calls.requests[0].text,'Restore from the original recording (0:12.000 – 0:12.500): “lost”');
  assert.equal(calls.requests[0].editRequest.sourceDurationMs,20000);
  assert.equal(state.comments[0].editRequest.type,'restore');
  assert.equal(editing.alreadyAsked(editing.selection,state.comments),true);
  assert.equal(editing.alreadyAsked({...editing.selection,sourceEndMs:12700},state.comments),false);
  assert.deepEqual([editing.wholeGap(group).sourceStartMs,editing.wholeGap(group).sourceEndMs],[11900,12600],'the gap button asks for the whole gap');
});

test('Shift picks a range of original words from the same gap only',()=>{
  const {editing}=setup();
  const group={id:'g',atMs:0,sourceStartMs:0,sourceEndMs:900,words:[{id:'a',text:'a',startMs:0,endMs:100},{id:'b',text:'b',startMs:200,endMs:300},{id:'c',text:'c',startMs:400,endMs:500}]};
  editing.selectSource(group,0);
  assert.deepEqual(editing.selectSource(group,2,{extend:true}),{lo:0,hi:2});
  assert.equal(editing.selection.quote,'a b c');
  assert.deepEqual(editing.selectSource({...group,id:'other'},1,{extend:true}),{lo:1,hi:1});
});

test('waveform handles make the selection an exact span; a span too short for a frame shows no range',()=>{
  const {editing}=setup();
  editing.selectWords(2);
  assert.ok(editing.bounds().startMs<2100);
  assert.equal(editing.adjust({startMs:2200,endMs:2600}),true);
  assert.equal(editing.selection.precise,true);
  assert.deepEqual([editing.bounds().startMs,editing.bounds().endMs],[2200,2600]);
  editing.adjust({startMs:2200,endMs:2210});
  assert.equal(editing.bounds(),null);
  assert.equal(editing.adjust(null),false);assert.equal(editing.selection,null);
});

test('a comment opens on its words by id, a join by its cut, and anything else on the word at its time',()=>{
  const {editing}=setup();
  assert.deepEqual(editing.target(0,{firstWordId:'W2',lastWordId:'W4'}),{first:2,last:4,cut:null,seekMs:2100});
  assert.deepEqual(editing.target(0,{cutId:'J1'}),{first:5,last:5,cut:editing.transcript.cuts[0],seekMs:null},'the first word at or after the join');
  assert.deepEqual(editing.target(7300),{first:7,last:7,cut:null,seekMs:null});
  assert.deepEqual(editing.target(99999),{first:9,last:9,cut:null,seekMs:null});
  assert.deepEqual(editing.changeContext(1200,2500),{type:'change',quote:'one two',firstWordId:'W1',lastWordId:'W2',wordIds:['W1','W2'],transcriptRevision:'r1'});
});

test('the panel only draws: the editing rules and saves live in transcript-editing.js',async()=>{
  const {readFile}=await import('node:fs/promises');
  const panel=await readFile(new URL('../web/transcript-panel.js',import.meta.url),'utf8');
  assert.match(panel,/import \{createTranscriptEditing[,\w]*\} from '\.\/transcript-editing\.js';/,'the panel uses the editing module');
  for(const rule of ['makeRemoval','piecesLeft','joinedCut','lastCutGroup','cutsToRestore','nextWordAfterDelete','wordSelection','removalBounds','cutNotes','notePending','draftEdits:'])
    assert.ok(!panel.includes(rule),`${rule} belongs in transcript-editing.js, not the page`);
});

test('bringing back every word of three neighbouring Deletes closes all three notes',async()=>{
  const {editing,state,calls}=setup();editing.setLoudness('v',loudness());
  for(const i of [2,3,4]){editing.selectWords(i);await editing.remove();}
  assert.deepEqual(editing.edits().map(words),[['W2','W3','W4']]);
  await editing.restoreWords(['W2','W3','W4']);
  assert.deepEqual(editing.edits(),[]);
  assert.deepEqual(state.comments.map(c=>c.id).filter(id=>!calls.cancels.includes(id)),[],'no note is left open');
});

test('Undo keeps the order of the Deletes after a join, also after a reload',async()=>{
  const {editing,state}=setup();editing.setLoudness('v',loudness());
  for(const i of [2,7,3]){editing.selectWords(i);await editing.remove();}
  editing.load(JSON.parse(JSON.stringify(state.transcripts.v)),state.videos[0]);
  await editing.undo();
  assert.deepEqual(editing.edits().map(words),[['W2'],['W7']],'the joined cut is undone, and word 2 goes back to its place');
  await editing.undo();
  assert.deepEqual(editing.edits().map(words),[['W2']],'the next Undo brings back word 7, the Delete made after word 2');
});

test('a Delete that joins a cut near the note length limit still saves, its note quote shortened',async()=>{
  const long=Array.from({length:40},(_,i)=>({id:'W'+i,text:String(i%10).repeat(99),startMs:i*200+10,endMs:i*200+150}));
  const {editing,calls}=setup({transcript:{revision:'r1',words:long,cuts:[]}});
  editing.selectWords(0,29);assert.ok(await editing.remove(),'a 2,999-character cut saves');
  editing.selectWords(30);assert.ok(await editing.remove(),'joining one more word still saves');
  assert.deepEqual(editing.edits().map(e=>e.wordIds.length),[31],'the cut keeps every word');
  const ask=calls.requests.at(-1);
  assert.ok(ask.editRequest.quote.length<=3000&&ask.editRequest.quote.endsWith('…'));
});

test('bringing back the words beside a word too short for a frame is refused, so that word stays cut',async()=>{
  // “one a three”, close together: “a” is 20 ms, under a frame at 30 fps.
  const times={W1:[1100,1490],W2:[1500,1520],W3:[1530,1900]};
  const short=TRANSCRIPT.words.map(w=>times[w.id]?{...w,startMs:times[w.id][0],endMs:times[w.id][1]}:w);
  const {editing,calls}=setup({transcript:{revision:'r1',words:short,cuts:[]}});
  editing.selectWords(1,3);await editing.remove();
  await editing.restoreWords(['W1']);
  const before=editing.edits();
  await assert.rejects(editing.restoreWords(['W3']),{message:TOO_SHORT_TEXT});
  assert.deepEqual(editing.edits(),before,'nothing changes');
  assert.deepEqual(calls.cancels,[],'its note stays open');
});

test('a cut with no words, made by moving the handles into a short pause, is brought back from that pause',async()=>{
  const {editing}=setup();
  editing.selectCut({id:'J1',atMs:5000});editing.adjust({startMs:4850,endMs:5050});await editing.remove();
  const [cut]=editing.edits();assert.deepEqual(cut.wordIds,[]);
  editing.selectPause(TRANSCRIPT.words[4],TRANSCRIPT.words[5]);
  assert.deepEqual(editing.toRestore().map(e=>e.id),[cut.id]);
});

// “four” is 4100–4800 ms and “five” 5100–5800 ms, with a pause between them.
for(const [into,range] of [['the next word',{startMs:5300,endMs:5500}],['the word before',{startMs:4500,endMs:4700}]])test(`a pause cut whose handles moved inside ${into} is brought back from that pause, and its note closes`,async()=>{
  const {editing,calls}=setup();
  editing.selectPause(TRANSCRIPT.words[4],TRANSCRIPT.words[5]);editing.adjust(range);await editing.remove();
  const [cut]=editing.edits();assert.deepEqual(cut.wordIds,[]);
  assert.ok(cut.startMs>=range.startMs-34&&cut.endMs<=range.endMs+34,'the cut lies inside the word, outside the pause');
  editing.selectPause(TRANSCRIPT.words[4],TRANSCRIPT.words[5]);
  assert.deepEqual(editing.toRestore().map(e=>e.id),[cut.id],'the pause offers Restore');
  assert.equal(await editing.remove(),null,'and not a second Delete');
  await editing.restore(editing.toRestore().map(e=>e.id));
  assert.deepEqual(editing.edits(),[]);
  assert.deepEqual(calls.cancels,[cut.commentId]);
});

test('a note stays open while a later cut still holds its words in its saved history',async()=>{
  const {editing,state,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(1,3);await editing.remove();const first=state.comments[0].id;
  await editing.restoreWords(['W2']);
  editing.selectWords(4);await editing.remove();
  await editing.restoreWords(['W1']);
  assert.ok(!calls.cancels.includes(first),'word 3 is still cut, held by the cut that took it in');
  await editing.undo();
  assert.deepEqual(editing.edits().map(words),[['W3']]);
  assert.ok(!calls.cancels.includes(first),'and after Undo its note is open');
});

test('loudness from a waveform decode goes when a new render replaces the file; the server\'s stays',()=>{
  const {editing}=setup();
  editing.setLoudness('v',loudness(),{from:'blob:a'});
  editing.mediaChanged('v','blob:a');assert.ok(editing.hasLoudness('v'),'the same file keeps it');
  assert.ok(editing.decodedLoudness('v'),'a newer decode may replace it');
  editing.mediaChanged('v','blob:b');assert.ok(!editing.hasLoudness('v'),'a new render drops it');
  editing.setLoudness('v',loudness());
  editing.mediaChanged('v','blob:c');assert.ok(editing.hasLoudness('v'),'the server measures each file version itself');
  assert.ok(!editing.decodedLoudness('v'));
});

test('a decode that comes in while the server still measures stands in for it; the server\'s measurement wins when it arrives (TOOLS-473)',async()=>{
  // The decoded loudness here has no pauses at all, so a Delete cut on it lands somewhere else than on the server's.
  const flat=()=>envelope(new Float32Array(VIDEO.durationMs/HOP_MS).fill(-20));
  const cutWith=async(...steps)=>{const {editing}=setup();for(const step of steps)await step(editing);editing.selectWords(1);await editing.remove();return editing.edits().map(e=>[e.startMs,e.endMs]);};
  const onServer=await cutWith(e=>e.setLoudness('v',loudness()));
  assert.notDeepEqual(await cutWith(e=>e.setLoudness('v',flat(),{from:'blob:a'})),onServer,'the two loudnesses cut differently');
  const server=held();
  const raced=await cutWith(
    e=>e.setLoudness('v',server.promise),
    // As the panel does when Build waveform finishes first: the server has no loudness yet, so the decode is taken.
    e=>{assert.ok(!e.hasLoudness('v'));e.setLoudness('v',flat(),{from:'blob:a'});assert.ok(e.hasLoudness('v'));},
    async e=>{server.resolve(loudness());await server.promise;assert.ok(!e.decodedLoudness('v'),'the server measurement replaces the decode');},
  );
  assert.deepEqual(raced,onServer,'the Delete after it cuts on the server\'s measurement');
  // A server with nothing to give (no ffmpeg) leaves the decode in place.
  const none=held(),{editing}=setup();
  editing.setLoudness('v',none.promise);editing.setLoudness('v',flat(),{from:'blob:a'});
  none.resolve(null);await none.promise;
  assert.ok(editing.hasLoudness('v')&&editing.decodedLoudness('v'),'the decode stays');
  // So does a server measurement that fails.
  const failing=held(),failed=setup().editing;
  failed.setLoudness('v',failing.promise);failed.setLoudness('v',flat(),{from:'blob:a'});
  failing.reject(new Error('no loudness'));await failing.promise.catch(()=>{});await null;
  assert.ok(failed.hasLoudness('v')&&failed.decodedLoudness('v'),'the decode stays after a failed measurement');
  // A second decode while the server still measures replaces the first, and the server still wins in the end.
  const later=held();
  const twice=await cutWith(
    e=>e.setLoudness('v',later.promise),
    e=>e.setLoudness('v',flat(),{from:'blob:a'}),
    e=>e.setLoudness('v',flat(),{from:'blob:b'}),
    async e=>{later.resolve(loudness());await later.promise;assert.ok(!e.decodedLoudness('v'),'the server measurement replaces the second decode');},
  );
  assert.deepEqual(twice,onServer);
});

// Saved history after a click-back (TOOLS-463): a cut holds, of the cuts it took in, only what is still cut under it.

test('a pause cut joined into a word Delete stays in its history when one of the words comes back, so Undo puts it back',async()=>{
  const {editing,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectPause(TRANSCRIPT.words[1],TRANSCRIPT.words[2]);await editing.remove();
  const pause=editing.edits()[0];
  editing.selectWords(2,3);await editing.remove();
  assert.deepEqual(editing.edits().map(words),[['W2','W3']],'the pause cut is joined into the Delete of the two words');
  await editing.restoreWords(['W3']);
  const [left]=editing.edits();
  assert.ok(left.startMs<=pause.startMs&&left.endMs>=pause.endMs,'the pause is still cut');
  assert.deepEqual(calls.cancels,[],'so its note stays open');
  await editing.undo();
  assert.deepEqual(editing.edits(),[pause],'Undo of the Delete puts the pause cut back as it was');
  assert.ok(!calls.cancels.includes(pause.commentId),'with its note open');
});

test('a pause cut joined into a word Delete goes, and its note closes, once the click-back makes that pause play again',async()=>{
  const {editing,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectPause(TRANSCRIPT.words[1],TRANSCRIPT.words[2]);await editing.remove();
  const pause=editing.edits()[0];
  editing.selectWords(2,3);await editing.remove();
  await editing.restoreWords(['W2']);
  const [right]=editing.edits();
  assert.ok(right.startMs>=pause.endMs,'the pause before the word plays again with it');
  assert.deepEqual(calls.cancels,[pause.commentId],'so its note closes; the Delete note still has a word');
  await editing.undo();
  assert.deepEqual(editing.edits(),[],'and Undo does not cut it again');
});

test('Undo after a word was clicked back never cuts that word again',async()=>{
  const {editing,state,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(4,6);await editing.remove();const first=state.comments[0].id;
  editing.selectWords(7);await editing.remove();
  await editing.restoreWords(['W6']);
  await editing.undo();
  assert.deepEqual(editing.edits().map(words),[['W4','W5']],'the first Delete is back without the word clicked back since');
  assert.ok(heard(editing,6)&&heard(editing,7),'words 6 and 7 play');
  assert.ok(!calls.cancels.includes(first),'the first note is open: two of its words are cut');
});

test('Undo after a word in the middle was clicked back puts the earlier Delete back in two parts, still one Delete',async()=>{
  const {editing,state,calls}=setup();editing.setLoudness('v',loudness());
  editing.selectWords(2);await editing.remove();
  editing.selectWords(4,6);await editing.remove();const first=state.comments[1].id;
  editing.selectWords(7);await editing.remove();
  await editing.restoreWords(['W5']);
  editing.load(JSON.parse(JSON.stringify(state.transcripts.v)),state.videos[0]);
  await editing.undo();
  assert.deepEqual(editing.edits().map(words),[['W2'],['W4'],['W6']],'word 5 stays back, and the parts stand where the Delete stood');
  assert.ok(heard(editing,5)&&heard(editing,7));
  assert.deepEqual(editing.edits().slice(1).map(e=>e.commentId),[first,first],'both parts keep the first note');
  assert.ok(!calls.cancels.includes(first));
  await editing.undo();
  assert.deepEqual(editing.edits().map(words),[['W2']],'the next Undo brings both parts back together');
  assert.ok(calls.cancels.includes(first),'and closes their note');
});

test('with loudness and no pause around it, a word too short for a frame is not stretched into the word clicked back',async()=>{
  // “is a test”, run together: “a” is 20 ms, under a frame at 30 fps, and the loudness shows no pause around it.
  const run=[{id:'a',text:'is',startMs:650,endMs:1000},{id:'b',text:'a',startMs:1000,endMs:1020},{id:'c',text:'test',startMs:1020,endMs:2350}];
  const {editing,calls}=setup({transcript:{revision:'r1',words:run,cuts:[]}});
  editing.setLoudness('v',envelope(new Float32Array(VIDEO.durationMs/HOP_MS).fill(-20)));
  editing.selectWords(0,2);await editing.remove();
  await editing.restoreWords(['a']);
  const before=editing.edits();
  await assert.rejects(editing.restoreWords(['c']),{message:TOO_SHORT_TEXT});
  assert.deepEqual(editing.edits(),before,'nothing changes: no cut is left reaching into “test”');
  assert.deepEqual(calls.cancels,[],'its note stays open');
});

test('with loudness and no pause around it, a Delete of a word too short for a frame is refused and saves nothing',async()=>{
  // “is a test”, run together: “a” is 1020–1040, and the frame lines at 30 fps are 1000, 1033 and 1067. One frame cut
  // there would leave part of “a” playing and take more of “test” than of “a”.
  const run=[{id:'a',text:'is',startMs:650,endMs:1020},{id:'b',text:'a',startMs:1020,endMs:1040},{id:'c',text:'test',startMs:1040,endMs:2350}];
  const {editing,calls}=setup({transcript:{revision:'r1',words:run,cuts:[]}});
  editing.setLoudness('v',envelope(new Float32Array(VIDEO.durationMs/HOP_MS).fill(-20)));
  editing.selectWords(1,1);
  await assert.rejects(editing.remove(),/too short/);
  assert.deepEqual(editing.edits(),[],'no cut is saved');
  assert.deepEqual([calls.saves,calls.requests],[[],[]],'and no note is made');
  editing.selectWords(1,2);await editing.remove();
  assert.deepEqual(words(editing.edits()[0]),['b','c'],'with the word next to it there is room, and both are cut');
});

test('with loudness, a word clicked back beside a cut word it runs into plays from the quietest point between them',async()=>{
  // “one two”, run together at 1510 with only a dip between them, no pause.
  const times={W1:[1100,1510],W2:[1510,1900]};
  const close=TRANSCRIPT.words.map(w=>times[w.id]?{...w,startMs:times[w.id][0],endMs:times[w.id][1]}:w);
  const db=new Float32Array(VIDEO.durationMs/HOP_MS).fill(-80);
  for(let ms=1100;ms<1900;ms+=HOP_MS)db[ms/HOP_MS]=ms>=1505&&ms<1515?-40:-20;
  const {editing}=setup({transcript:{revision:'r1',words:close,cuts:[]}});editing.setLoudness('v',envelope(db));
  editing.selectWords(1,2);await editing.remove();
  await editing.restoreWords(['W2']);
  const [cut]=editing.edits();
  assert.deepEqual(words(cut),['W1']);
  assert.equal(cut.endMs,1500,`the cut ends on the frame line before the dip, not after it: ${cut.endMs}`);
});

/** A small repeatable random source, so a failing run can be played again from its seed. */
function dice(seed){let s=seed>>>0;return ()=>{s=(s*1664525+1013904223)>>>0;return s/2**32;};}

test('in any order of Deletes, click-backs and Undos: nothing that plays is cut by a click-back or an Undo, and notes follow the cuts',async()=>{
  const covers=(big,small)=>small.every(([a,b])=>big.some(([c,d])=>c<=a&&d>=b));
  const notes=list=>(list||[]).flatMap(e=>[e.commentId,...notes(e.takenIn)]).filter(Boolean);
  for(let seed=1;seed<=60;seed++){
    const roll=dice(seed*7919),pick=n=>Math.floor(roll()*n),run=[];
    // Up to ten words, as many as fit the video: most a normal length, a few under two frames; some run together,
    // some have a pause between them.
    for(let i=0,t=100;i<10;i++){
      const length=roll()<0.15?20+pick(30):300+pick(500);
      if(t+length>VIDEO.durationMs-100)break;
      run.push({id:'W'+i,text:'w'+i,startMs:t,endMs:t+length});
      t+=length+(roll()<0.3?10:roll()<0.5?100:300+pick(400));
    }
    const {editing,state,calls}=setup({transcript:{revision:'r1',words:run,cuts:[]}});
    // With loudness (most runs): each word sounds from about 5 ms after its transcript start to 5 ms before its end.
    const sound=new Map();
    if(roll()<0.7){
      const db=new Float32Array(VIDEO.durationMs/HOP_MS).fill(-80);
      for(const w of run){
        const from=Math.floor((w.startMs+5)/HOP_MS),to=Math.ceil((w.endMs-5)/HOP_MS);
        db.fill(-20,from,to);sound.set(w.id,[from*HOP_MS,to*HOP_MS]);
      }
      editing.setLoudness('v',envelope(db));
    }
    const trail=[],struck=()=>new Set(editing.edits().flatMap(e=>e.wordIds));
    for(let step=0;step<12;step++){
      const before=editing.edits(),timeBefore=removedRanges(before),struckBefore=struck(),say=text=>`seed ${seed}: ${[...trail,text].join(', ')}`;
      const chance=roll();let did='nothing',back=[];
      try{
        if(chance<0.4){
          const lo=pick(run.length),hi=Math.min(run.length-1,lo+pick(3));editing.selectWords(lo,hi);
          if(!editing.toRestore().length){did=`Delete ${lo}-${hi}`;await editing.remove();}
        }else if(chance<0.5){
          const i=pick(run.length-1);editing.selectPause(run[i],run[i+1]);
          const cut=editing.toRestore();did=`${cut.length?'Restore':'Delete'} pause ${i}`;
          if(cut.length)await editing.restore(cut.map(e=>e.id));else await editing.remove();
        }else if(chance<0.78&&struckBefore.size){
          const first=[...struckBefore][pick(struckBefore.size)],next='W'+(+first.slice(1)+1);
          back=roll()<0.3&&struckBefore.has(next)?[first,next]:[first];did=`click back ${back.join('+')}`;
          await editing.restoreWords(back);
        }else if(chance>=0.78){did='Undo';await editing.undo();}
      }catch(error){
        assert.deepEqual(editing.edits(),before,say(`${did} was refused (${error.message}), so nothing may change`));
        trail.push(did+' (refused)');continue;
      }
      trail.push(did);
      const after=removedRanges(editing.edits()),struckNow=struck();
      if(did.startsWith('Delete')){
        assert.ok(covers(after,timeBefore),say('a Delete brought time back'));
        assert.ok([...struckBefore].every(id=>struckNow.has(id)),say('a Delete brought a word back'));
      }else{
        assert.ok(covers(timeBefore,after),say('cut time that was playing'));
        assert.ok([...struckNow].every(id=>struckBefore.has(id)),say('cut a word that was playing'));
      }
      if(back.length)assert.deepEqual([...struckBefore].filter(id=>!struckNow.has(id)).sort(),[...back].sort(),say('brought back other words than the clicked ones'));
      for(const id of back.filter(id=>sound.has(id))){
        const [from,to]=sound.get(id);
        assert.ok(!after.some(([a,b])=>a<to&&b>from),say(`left some of the sound of ${id} cut`));
      }
      // A note is open exactly while a cut holds it, in the list or in a saved history.
      const held=new Set(notes(editing.edits()));
      for(const {id} of state.comments)assert.equal(calls.cancels.includes(id),!held.has(id),say(held.has(id)?'closed the note of a cut still held':'left a note open with no cut'));
      assert.equal(new Set(editing.edits().flatMap(e=>e.wordIds)).size,editing.edits().flatMap(e=>e.wordIds).length,say('left a word in two cuts'));
      // A saved history holds only what its cut still cuts, at every depth.
      const inside=e=>(e.takenIn||[]).every(k=>k.startMs>=e.startMs&&k.endMs<=e.endMs&&inside(k));
      assert.ok(editing.edits().every(inside),say('left a saved history reaching outside its cut'));
    }
  }
});

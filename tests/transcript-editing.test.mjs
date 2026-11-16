// The transcript panel's editing rules, without a page: select, delete, the pieces one Delete makes, Undo, Restore,
// notes, and what happens when a save fails, overlaps another save, or a new render (a recut) arrives meanwhile.
// Saves go through the shared review rules (review-core.js, transcript-core.js), as the browser backend saves them.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createTranscriptEditing} from '../web/transcript-editing.js';
import {applyChange} from '../web/review-core.js';
import {storeTranscript} from '../web/transcript-core.js';

const VIDEO={id:'v',title:'Test',fileName:'test.mp4',durationMs:10000,fps:30};
const TEXT=['zero','one','two','three','four','five','six','seven','eight','nine'];
const word=(i,shift=0)=>({id:'W'+i,text:TEXT[i],startMs:i*1000+100-shift,endMs:i*1000+800-shift});
const TRANSCRIPT={revision:'r1',words:TEXT.map((_,i)=>word(i)),cuts:[{id:'J1',atMs:5000}]};

/** A promise the test settles by hand, to hold a save or a request open. */
function held(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}

/** A review state with one video, saved through the shared rules, and an editing module that writes to it.
 *  hold.save / hold.request: the next save or request waits for the test. fail.save: the next save is refused. */
function setup({transcript=TRANSCRIPT}={}){
  const state={videos:[{...VIDEO}],comments:[],appliedOps:[]},calls={saves:[],requests:[],updates:[],cancels:[],errors:[]};
  const hold={},fail={},timers=[];
  let now=0;
  const editing=createTranscriptEditing({
    async saveTranscript(video,data){
      calls.saves.push(data);
      if(fail.save){const error=fail.save;delete fail.save;throw error;}
      if(hold.save){const wait=hold.save;delete hold.save;await wait.promise;}
      return storeTranscript(state,video.id,data);
    },
    async request(selection){
      calls.requests.push(selection);
      if(hold.request){const wait=hold.request;delete hold.request;await wait.promise;}
      return applyChange(state,{action:'add',videoId:VIDEO.id,startMs:selection.startMs,endMs:selection.endMs,text:selection.text,editRequest:selection.editRequest,createdAt:new Date(++now*1000).toISOString()});
    },
    async updateRequest(id,text){calls.updates.push([id,text]);return applyChange(state,{action:'update',id,text});},
    async cancelRequest(id){calls.cancels.push(id);applyChange(state,{action:'reply',id,author:'you',text:'Undid this preview removal; keep the passage.',status:'done'});},
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

test('a Delete over words already deleted splits into one piece per kept run, sharing one note',async()=>{
  const {editing,calls}=setup();
  editing.selectWords(2);await editing.remove();
  editing.selectWords(0,4);
  assert.deepEqual(editing.toRestore(),[],'some words are still in the cut, so the button deletes');
  const result=await editing.remove();
  const pieces=editing.edits().slice(1);
  assert.deepEqual(pieces.map(words),[['W0','W1'],['W3','W4']]);
  assert.equal(pieces[0].commentId,pieces[1].commentId);
  const ask=calls.requests[1];
  assert.equal(ask.text,'Remove from cut: “zero one … three four”');
  assert.equal(ask.editRequest.firstWordId,'W0');assert.equal(ask.editRequest.lastWordId,'W4');
  assert.equal(ask.startMs,pieces[0].startMs);assert.equal(ask.endMs,pieces[1].endMs);
  assert.equal(result.next,5);
});

test('Undo brings the split pieces back together and closes their one note',async()=>{
  const {editing,calls}=setup();
  editing.selectWords(2);await editing.remove();
  editing.selectWords(0,4);await editing.remove({typed:'cut it'});
  const commentId=editing.edits().at(-1).commentId;
  const undone=await editing.undo();
  assert.deepEqual(editing.edits().map(words),[['W2']],'both pieces of the last Delete are back, the earlier cut stays');
  assert.deepEqual(calls.cancels,[commentId]);
  assert.equal(undone.note,'cut it','the note typed with the Delete comes back for the box');
  assert.equal(undone.first,0);assert.equal(undone.last,4);
  assert.equal(await editing.undo().then(r=>r.first),2,'the next Undo brings back the first cut');
  assert.deepEqual(editing.edits(),[]);
  assert.equal(await editing.undo(),null,'nothing left to undo');
});

test('Restore on deleted words brings back each cut they are in; the note closes with its last piece',async()=>{
  const {editing,calls}=setup();
  editing.selectWords(2);await editing.remove();
  editing.selectWords(0,4);await editing.remove();
  const [first,second,third]=editing.edits();
  editing.selectWords(0,1);
  assert.deepEqual(editing.toRestore().map(e=>e.id),[second.id]);
  let back=await editing.restore([second.id],{keep:true});
  assert.equal(back.noteResolved,false);
  assert.deepEqual(calls.cancels,[],'the note still has a piece in the cut');
  assert.deepEqual(editing.selectedIds(),['W0','W1'],'keep leaves the selection as it was');
  back=await editing.restore([third.id]);
  assert.deepEqual(calls.cancels,[third.commentId]);
  assert.equal(editing.selection,null);
  assert.deepEqual(editing.edits().map(e=>e.id),[first.id]);
});

test('a join is selected around its cut point; a note on it goes to the agent with the join id',async()=>{
  const {editing,state,calls}=setup();
  editing.selectCut(TRANSCRIPT.cuts[0]);
  assert.deepEqual(editing.selection,{startMs:4800,endMs:5200,cutId:'J1',quote:'Join J1'});
  const sent=await editing.send('Check this join');
  assert.equal(sent.kind,'sent');assert.equal(sent.current,true);
  assert.equal(calls.requests[0].editRequest.type,'change');
  assert.equal(calls.requests[0].editRequest.cutId,'J1');
  assert.equal(state.comments[0].editRequest.startFrame,144,'the shared rules turned the time into a frame');
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
  await assert.rejects(cut,/new render/);
  assert.equal(calls.saves.length,0,'the old cut never reaches the new transcript');
  assert.equal(editing.transcript.revision,'r2');assert.deepEqual(editing.edits(),[]);
  assert.deepEqual(calls.cancels,[state.comments[0].id]);
  assert.equal(editing.busy,false);
});

test('a save that finishes after a recut does not replace the new transcript',async()=>{
  const {editing,state,hold}=setup();
  editing.selectWords(1);await editing.remove();
  hold.save=held();const wait=hold.save;
  const undo=editing.undo();
  await new Promise(r=>setTimeout(r));
  const recut={revision:'r2',words:TEXT.slice(2).map((_,i)=>word(i+2,2000)),cuts:[]};
  const fresh=storeTranscript(state,VIDEO.id,recut);
  editing.load(fresh,state.videos[0]);
  wait.resolve();
  await assert.rejects(undo,/new render/);
  assert.equal(editing.transcript,fresh);
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
  assert.deepEqual(editing.target(0,{cutId:'J1'}),{first:5,last:5,cut:TRANSCRIPT.cuts[0],seekMs:null},'the first word at or after the join');
  assert.deepEqual(editing.target(7300),{first:7,last:7,cut:null,seekMs:null});
  assert.deepEqual(editing.target(99999),{first:9,last:9,cut:null,seekMs:null});
  assert.deepEqual(editing.changeContext(1200,2500),{type:'change',quote:'one two',firstWordId:'W1',lastWordId:'W2',wordIds:['W1','W2'],transcriptRevision:'r1'});
});

test('the panel only draws: the editing rules and saves live in transcript-editing.js',async()=>{
  const {readFile}=await import('node:fs/promises');
  const panel=await readFile(new URL('../web/transcript-panel.js',import.meta.url),'utf8');
  assert.match(panel,/import \{createTranscriptEditing\} from '\.\/transcript-editing\.js'/);
  for(const rule of ['makeRemoval','keptRuns','lastCutGroup','cutsToRestore','nextWordAfterDelete','wordSelection','saveTranscript','draftEdits:'])
    assert.ok(!panel.includes(rule),`${rule} belongs in transcript-editing.js, not the page`);
});

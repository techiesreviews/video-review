import test from 'node:test';
import assert from 'node:assert/strict';
import {barKey,suggestion,recentNotes,nextWordAfterDelete,sentLine,QUICK_PHRASES,barSpot,barSpotOffWords,deletedIds,cutsToRestore,cutMarks,keptRuns,lastCutGroup} from '../web/edit-bar-core.js';

const key=(k,extra={})=>({key:k,...extra});

test('Delete and Backspace remove the words only when the box is empty',()=>{
  assert.equal(barKey(key('Delete'),{value:''}),'delete');
  assert.equal(barKey(key('Backspace'),{value:''}),'delete');
  assert.equal(barKey(key('Backspace'),{value:'tighten'}),null,'typed text then Backspace only edits the text');
  assert.equal(barKey(key('Delete'),{value:' '}),null,'a lone space is still text');
});
test('holding Backspace does not keep eating words, and modifiers are left alone',()=>{
  assert.equal(barKey(key('Backspace',{repeat:true}),{value:''}),null);
  assert.equal(barKey(key('Backspace',{ctrlKey:true}),{value:''}),null);
  assert.equal(barKey(key('Delete',{altKey:true}),{value:''}),null);
});
test('Tab takes the suggestion only when there is one, so Tab still moves focus otherwise',()=>{
  assert.equal(barKey(key('Tab'),{value:'sho',suggestion:'shorten this',caretAtEnd:true}),'accept');
  assert.equal(barKey(key('Tab'),{value:'sho',suggestion:'',caretAtEnd:true}),null);
  assert.equal(barKey(key('Tab',{shiftKey:true}),{value:'sho',suggestion:'shorten',caretAtEnd:true}),null,'Shift+Tab goes back');
  assert.equal(barKey(key('Tab'),{value:'sho',suggestion:'shorten',caretAtEnd:false}),null,'not at the end of the text');
  assert.equal(barKey(key('ArrowRight'),{value:'sho',suggestion:'shorten',caretAtEnd:true}),'accept');
});
test('Enter sends typed text, never an empty box, and Shift+Enter does not send',()=>{
  assert.equal(barKey(key('Enter'),{value:'Tighten this'}),'send');
  assert.equal(barKey(key('Enter'),{value:'   '}),null);
  assert.equal(barKey(key('Enter'),{value:''}),null);
  assert.equal(barKey(key('Enter',{shiftKey:true}),{value:'x'}),null);
  assert.equal(barKey(key('Enter',{isComposing:true}),{value:'x'}),null,'an input method is still composing');
});
test('Space in an empty box still plays and pauses; inside text it is a space',()=>{
  assert.equal(barKey(key(' '),{value:''}),'play');
  assert.equal(barKey(key(' '),{value:'two'}),null);
  assert.equal(barKey(key(' ',{shiftKey:true}),{value:''}),null);
});
test('Escape clears the text first, then the selection',()=>{
  assert.equal(barKey(key('Escape'),{value:'half a note'}),'clear-text');
  assert.equal(barKey(key('Escape'),{value:''}),'clear-selection');
});
test('Ctrl+Z in an empty box undoes the last cut; with text it is left to the box',()=>{
  assert.equal(barKey(key('z',{ctrlKey:true}),{value:''}),'undo');
  assert.equal(barKey(key('z',{ctrlKey:true}),{value:'abc'}),null);
});
test('suggestion completes what was typed, newest note first, ignoring case',()=>{
  assert.equal(suggestion('sho',[],QUICK_PHRASES),'shorten this pause, keep the breath','a phrase continues the typed letters');
  assert.equal(suggestion('SHO',['shorten the intro'],QUICK_PHRASES),'SHOrten the intro','recent notes win and the typed case is kept');
  assert.equal(suggestion('',['anything']),'');
  assert.equal(suggestion('zzz',['abc'],QUICK_PHRASES),'');
  assert.equal(suggestion('Remove this passage',[],QUICK_PHRASES),'','a finished phrase has nothing left to suggest');
});
test('every quick phrase can be reached from its first letters',()=>{
  for(const phrase of QUICK_PHRASES){const got=suggestion(phrase.slice(0,3),[],QUICK_PHRASES);assert.ok(got.toLowerCase().startsWith(phrase.slice(0,3).toLowerCase()));}
});
test('recent notes are the reviewer’s own words, newest first, without app-written sentences or duplicates',()=>{
  const comments=[{text:'Old one here'},{text:'Remove from cut: “um”'},{text:'Tighten the intro'},{text:'tighten the intro'},{text:'ab'},{text:'Undid this preview removal; keep the passage.'}];
  assert.deepEqual(recentNotes(comments),['tighten the intro','Old one here']);
});
test('after a delete the selection moves to the next kept word, else the one before',()=>{
  const words=['a','b','c','d','e'].map(id=>({id}));
  assert.equal(nextWordAfterDelete(words,['b','c'],1,2),3);
  assert.equal(nextWordAfterDelete(words,['b','c','d','e'],1,4),0,'nothing after: step back');
  assert.equal(nextWordAfterDelete(words,['c','d'],2,3),4);
  assert.equal(nextWordAfterDelete(words,['a','b','c','d','e'],0,4),-1);
  assert.equal(nextWordAfterDelete(words,['c','d','e'],2,2),1,'skips words that were removed earlier, then steps back');
});
test('the status line is honest about the agent',()=>{
  assert.match(sentLine(false),/No agent connected/);assert.match(sentLine(false),/saved/);
  assert.match(sentLine(true),/Sent to your agent/);
});
test('a long recent note is still a suggestion; only the browser bar hides one that does not fit',()=>{
  const long='Please keep the whole sentence here because the punchline only lands when the setup before it is complete and unhurried okay';
  assert.equal(suggestion(long.slice(0,30),recentNotes([{text:long}])),long);
});

// Where the bar goes (TOOLS-129): next to the selected words, like the script editor's bar, and always on screen.
const box=(top,left,height=30,width=60)=>({top,left,bottom:top+height,right:left+width,height,width});
const room={w:420,h:60,top:80,vw:1440,vh:900,line:40};
test('the bar sits just below the selected words when there is room, lined up with the last line',()=>{
  const spot=barSpot({...room,first:box(200,300),last:box(240,120)});
  assert.deepEqual(spot,{x:116,y:278});
});
test('near the bottom it goes above the words, one line clear so the line above stays readable',()=>{
  const spot=barSpot({...room,first:box(800,300),last:box(820,300)});
  assert.equal(spot.y,800-40-8-60,'above the words with one line of room');
  assert.equal(spot.x,296,'lined up with the first line');
});
test('with no room for a clear line it sits right above the words',()=>{
  const spot=barSpot({...room,first:box(160,300),last:box(850,300)});
  assert.equal(spot.y,160-8-60);
});
test('when the words fill the window the bar goes to the end with more room, still on screen',()=>{
  assert.equal(barSpot({...room,first:box(90,300),last:box(870,300)}).y,room.top+8,'more room above: at the top');
  assert.equal(barSpot({...room,first:box(130,300),last:box(800,300)}).y,room.vh-8-room.h,'more room below: at the bottom');
});
test('the bar never leaves the window sideways or goes under the header',()=>{
  assert.equal(barSpot({...room,first:box(200,1400),last:box(200,1400)}).x,1440-420-8,'pulled back from the right edge');
  assert.equal(barSpot({...room,first:box(200,0),last:box(200,0)}).x,8,'kept off the left edge');
  assert.ok(barSpot({...room,first:box(20,300),last:box(30,300),vh:120}).y>=room.top+8,'never under the header');
});
test('when the transcript is too short to keep the bar off the words, it may reach past the transcript to the window end',()=>{
  // A phone: the words area ends at 366 and the window at 844.
  const words={first:box(259,113,35,69),last:box(259,113,35,69),w:374,h:73,top:188,vw:390,line:44};
  const inside=barSpot({...words,vh:366});
  assert.ok(inside.y<294&&inside.y+73>259,'inside the words area alone it would cover the word');
  assert.deepEqual(barSpotOffWords({...words,end:366,seenEnd:844}),{x:8,y:302},'so it goes just below the word instead');
  assert.deepEqual(barSpotOffWords({...room,first:box(200,300),last:box(240,120),end:900,seenEnd:900}),{x:116,y:278},'with room, nothing changes');
});
// Deleted words stay in the transcript, marked; a selection of only deleted words can be brought back.
const edits=[{id:'e1',wordIds:['b','c'],kind:'words'},{id:'e2',wordIds:[],kind:'pause'},{id:'e3',wordIds:['e'],kind:'words'}];
test('deletedIds lists every deleted word once',()=>{
  assert.deepEqual([...deletedIds(edits)].sort(),['b','c','e']);
  assert.equal(deletedIds([]).size,0);
});
test('a selection of only deleted words names the cuts that bring it back; any kept word means nothing to restore',()=>{
  assert.deepEqual(cutsToRestore(edits,['b','c']).map(e=>e.id),['e1']);
  assert.deepEqual(cutsToRestore(edits,['c','e']).map(e=>e.id),['e1','e3'],'two cuts, both come back');
  assert.deepEqual(cutsToRestore(edits,['b','d']),[],'d is still in the cut, so this is a selection to delete');
  assert.deepEqual(cutsToRestore(edits,[]),[],'a pause or join selection has no words to restore');
});
test('where each cut is marked: the first of its words still in the transcript, a pause cut by its start time',()=>{
  const words=['a','b','c','d','e'].map((id,i)=>({id,startMs:i*1000}));
  assert.deepEqual(cutMarks(words,[{id:'e1',wordIds:['c','b'],startMs:1000,kind:'words'},{id:'p',wordIds:[],startMs:2500,kind:'pause'}]),[{id:'e1',index:1},{id:'p',index:3}]);
  assert.deepEqual(cutMarks(words,[{id:'x',wordIds:['gone'],startMs:9000,kind:'words'}]),[{id:'x',index:4}],'after the last word: marked on the last word');
});
test('Delete over kept and deleted words cuts only the runs of kept words, so no word ends up in two cuts',()=>{
  const words=['a','b','c','d','e','f'].map(id=>({id}));
  assert.deepEqual(keptRuns(words,0,5,new Set(['c','d'])),[[0,1],[4,5]]);
  assert.deepEqual(keptRuns(words,1,3,new Set(['c','d'])),[[1,1]],'deleted words at the end are left out');
  assert.deepEqual(keptRuns(words,2,3,new Set(['c','d'])),[],'all deleted: nothing to cut');
  assert.deepEqual(keptRuns(words,0,2,new Set()),[[0,2]],'nothing deleted: the whole selection');
});
test('the pieces of one Delete share its note, so Ctrl+Z brings them all back at once',()=>{
  assert.equal(lastCutGroup([{commentId:'x'},{commentId:'y'},{commentId:'y'}]),2);
  assert.equal(lastCutGroup([{commentId:'y'},{commentId:null},{commentId:null}]),1,'cuts without a note are undone one by one');
  assert.equal(lastCutGroup([{commentId:'x'}]),1);
  assert.equal(lastCutGroup([]),0);
});

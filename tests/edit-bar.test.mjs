import test from 'node:test';
import assert from 'node:assert/strict';
import {barKey,suggestion,recentNotes,nextWordAfterDelete,sentLine,QUICK_PHRASES} from '../web/edit-bar-core.js';

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

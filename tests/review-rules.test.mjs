// The browser and the hosted Worker answer the shared table of review-comment cases (tests/fixtures/review-rule-cases.json)
// the same way the local app does; tests/test_review_rules.py runs the same table against review_store.py.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {applyChange,applyRender,checkEditRequest,checkRender,describe,parseTime,sourceSpans,timecode} from '../web/review-core.js';
import {storeTranscript} from '../web/transcript-core.js';

const read=async path=>JSON.parse(await readFile(new URL(path,import.meta.url),'utf8'));
const RULES=await read('../web/review-rules.json'),TABLE=await read('./fixtures/review-rule-cases.json');
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The table's shorthand: repeated text, long lists and the fixed clock. */
function expand(value){
  if(Array.isArray(value))return value.map(expand);
  if(value&&typeof value==='object'){
    const keys=Object.keys(value);
    if(keys.length===1&&keys[0]==='$repeat')return value.$repeat[0].repeat(value.$repeat[1]);
    if(keys.length===1&&keys[0]==='$list')return Array(value.$list[1]).fill(value.$list[0]);
    return Object.fromEntries(keys.map(key=>[key,expand(value[key])]));
  }
  return value==='<now>'?TABLE.now:value;
}
/** An answer as it is saved or sent (JSON), with new random ids as <uuid>. */
function plain(value){
  return JSON.parse(JSON.stringify(value??null),(key,item)=>key==='id'&&typeof item==='string'&&UUID.test(item)?'<uuid>':item);
}

function runHosted({call,args}){
  args=expand(args);
  const simple={parseTime,timecode,sourceSpans,checkEditRequest,describe,checkRender};
  if(simple[call])return {returns:simple[call](...args)};
  const state=args[0],calls={applyChange:()=>applyChange(state,args[1],TABLE.now),applyRender:()=>applyRender(state,args[1],args[2]),saveTranscript:()=>storeTranscript(state,args[1],args[2])};
  if(!calls[call])throw Error(`The table names a call the hosted runner does not know: ${call}`);
  // A refused change: the state shows what it left behind.
  let answer;try{answer={returns:calls[call]()};}catch(error){answer={error};}
  return {...answer,comments:state.comments,videos:state.videos};
}

test('the table covers every kind of rule',()=>{assert.ok(TABLE.cases.length>40);});
for(const c of TABLE.cases){
  test(`hosted: ${c.name}`,()=>{
    let answer;try{answer=runHosted(c);}catch(error){answer={error};}
    if('error' in c||'error' in answer)assert.equal(answer.error?.message,RULES.messages[c.error]);
    for(const key of ['returns','comments','videos'])if(key in c)assert.deepEqual(plain(answer[key]),expand(c[key]),key);
  });
}

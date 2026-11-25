// Where a word cut starts and ends, found in the audio's loudness rather than the transcript's word times (TOOLS-463).
// The loudness here is drawn by hand: speech at -20 dB, silence at -80 dB, so each test says exactly what the audio holds.
import test from 'node:test';
import assert from 'node:assert/strict';
import {HOP_MS,envelope,envelopeFromBytes,envelopeFromSamples,cleanCut} from '../web/cut-boundary.js';
import {removalBounds} from '../web/cut-core.js';
import {wordSelection} from '../web/transcript-core.js';

/** Loudness for a clip of durationMs: silence, with each [startMs, endMs, dB] laid over it. */
function drawn(durationMs,spans){
  const db=new Float32Array(Math.ceil(durationMs/HOP_MS)).fill(-80);
  for(const [a,b,level] of spans)for(let i=Math.floor(a/HOP_MS);i<Math.ceil(b/HOP_MS);i++)db[i]=level;
  return envelope(db);
}
const word=(id,text,startMs,endMs)=>({id,text,startMs,endMs});
const onFrame=(ms,fps)=>Math.abs(ms*fps/1000-Math.round(ms*fps/1000))<0.04;

test('run-together words: the cut starts in the dip where the word really starts, not at its transcript time',()=>{
  // "sample" really ends at 1030 and "rate" starts at 1060; the transcript puts the change at 1000. There is no silence
  // between them, only a dip that stays above the silence line.
  const env=drawn(3000,[[500,1030,-20],[1030,1060,-55],[1060,1500,-20],[1600,2400,-20]]);
  const transcript={words:[word('a','sample',500,1000),word('b','rate',1000,1480),word('c','next',1620,2380)]};
  const cut=removalBounds(transcript,wordSelection(transcript.words,1,1),3000,30,env);
  assert.ok(cut.startMs>=1020&&cut.startMs<=1060,`starts in the dip, not at 1000: ${cut.startMs}`);
  assert.ok(cut.endMs>=1500&&cut.endMs<=1600,`ends in the pause after "rate": ${cut.endMs}`);
  assert.ok(onFrame(cut.startMs,30)&&onFrame(cut.endMs,30),'both edges are on frame lines');
  assert.deepEqual(cut.wordIds,['b']);
});

test('a very short word with quiet around it is cut whole, where the transcript alone refuses it',()=>{
  // "a" is 20 ms long in the transcript, right against its neighbours; in the audio it is 50 ms with a short quiet each side.
  const env=drawn(3000,[[600,960,-20],[990,1040,-20],[1070,2400,-20]]);
  const transcript={words:[word('x','is',650,1000),word('y','a',1000,1020),word('z','test',1020,2350)]};
  const selection=wordSelection(transcript.words,1,1);
  assert.throws(()=>removalBounds(transcript,selection,3000,30),/too short/,'without the audio it is too short to cut');
  const cut=removalBounds(transcript,selection,3000,30,env);
  assert.ok(cut.startMs>=959&&cut.startMs<=990,`starts in the quiet before "a": ${cut.startMs}`);
  assert.ok(cut.endMs>=1040&&cut.endMs<=1071,`ends in the quiet after "a": ${cut.endMs}`);
  assert.ok(onFrame(cut.startMs,30)&&onFrame(cut.endMs,30),'both edges are on frame lines');
});

test('the longer pause stays in the cut, shared out between the two sides',()=>{
  // 200 ms of pause before the word, 60 ms after it: the joined result keeps one 200 ms pause, not 260 or 60.
  const env=drawn(4000,[[500,1000,-20],[1200,1500,-20],[1560,3000,-20]]);
  const words=[word('a','one',500,1000),word('b','two',1200,1500),word('c','three',1560,3000)];
  const cut=cleanCut(words,1,1,env,{fps:1000,durationMs:4000});
  const kept=(cut.startMs-1000)+(1560-cut.endMs);
  assert.ok(Math.abs(kept-200)<=10,`about 200 ms of pause is left: ${kept}`);
});

test('the server\'s loudness bytes read back as dB, half a dB per step',()=>{
  const env=envelopeFromBytes(Uint8Array.from([0,40,200,200,200,200,200,200,200,200]));
  assert.deepEqual([...env.db.slice(0,3)],[0,-20,-100]);
  assert.ok(env.threshold>-100&&env.threshold<0);
});

test('loudness from decoded samples: a tone reads near -3 dB, silence at the floor',()=>{
  const rate=8000,samples=new Float32Array(rate);
  for(let i=0;i<rate/2;i++)samples[i]=Math.sin(2*Math.PI*440*i/rate);
  const env=envelopeFromSamples([samples],rate);
  assert.equal(env.db.length,rate/(rate*HOP_MS/1000));
  assert.ok(Math.abs(env.db[10]+3)<0.5,`tone ${env.db[10]}`);
  assert.equal(env.db.at(-1),-100);
});

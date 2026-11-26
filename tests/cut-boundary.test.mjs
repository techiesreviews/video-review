// Where a word cut starts and ends, found in the audio's loudness rather than the transcript's word times (TOOLS-463).
// The loudness here is drawn by hand: speech at -20 dB, silence at -80 dB, so each test says exactly what the audio holds.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as boundary from '../web/cut-boundary.js';
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

test('loudness from decoded samples: a tone reads near -3 dB, silence at the floor',async()=>{
  const rate=8000,samples=new Float32Array(rate);
  for(let i=0;i<rate/2;i++)samples[i]=Math.sin(2*Math.PI*440*i/rate);
  const env=await envelopeFromSamples([samples],rate);
  assert.equal(env.db.length,rate/(rate*HOP_MS/1000));
  assert.ok(Math.abs(env.db[10]+3)<0.5,`tone ${env.db[10]}`);
  assert.equal(env.db.at(-1),-100);
});

test('loudness from a long decode is measured in slices, so the page keeps answering between them (TOOLS-475)',async()=>{
  // A minute at 16 kHz: a tone for the first 30 seconds, then silence. Each slice is followed by a pause that gives the
  // page back, and the result is the same as measured in one go.
  const rate=boundary.LOUDNESS_RATE,samples=new Float32Array(60*rate);
  for(let i=0;i<30*rate;i++)samples[i]=Math.sin(2*Math.PI*440*i/rate);
  let pauses=0;
  const env=await envelopeFromSamples([samples],rate,{pause:async()=>{pauses++;}});
  assert.ok(pauses>=3,`a minute of audio is measured in several slices: ${pauses}`);
  assert.equal(env.db.length,60000/HOP_MS);
  for(const ms of [10,14990,29000])assert.ok(Math.abs(env.db[ms/HOP_MS]+3)<0.5,`tone at ${ms} ms: ${env.db[ms/HOP_MS]}`);
  for(const ms of [30010,45000,59995])assert.equal(env.db[ms/HOP_MS],-100,`silence at ${ms} ms`);
});

test('the pause between slices is not a timer, which a browser slows to one a second in a hidden tab (TOOLS-475)',async()=>{
  const timer=globalThis.setTimeout;let timers=0;
  globalThis.setTimeout=(...args)=>{timers++;return timer(...args);};
  try{await boundary.nextTask();}finally{globalThis.setTimeout=timer;}
  assert.equal(timers,0);
});

test('Build waveform, for mono or stereo, never holds more decoded audio than an hour of stereo at 8 kHz did, and uses 16 kHz only for the loudness (TOOLS-475)',()=>{
  // decodeAudioData keeps every channel at the decode's rate: an hour of stereo at 8 kHz is 57.6 million samples
  // (about 230 MB), the most Build waveform held before TOOLS-473.
  const before=3600*8000*2,minute=60000;
  assert.equal(boundary.decodeRate(60*minute,true),8000,'an hour: 8 kHz, as before, even with no loudness yet');
  assert.equal(boundary.decodeRate(30*minute,true),boundary.LOUDNESS_RATE,'half an hour with no loudness yet: the loudness rate');
  assert.equal(boundary.decodeRate(31*minute,true),8000,'past half an hour: 8 kHz');
  for(const minutes of [1,10,20,30,31,45,60,90,180]){
    assert.equal(boundary.decodeRate(minutes*minute,false),8000,`${minutes} min with the loudness in: bars only, 8 kHz`);
    const rate=boundary.decodeRate(minutes*minute,true);
    assert.ok(rate===8000||minutes*60*rate*2<=before,`${minutes} min: no more samples than before (${rate} Hz)`);
  }
});

test('a word the loudness shows no sound for is cut on its transcript times, not as a one-frame cut',()=>{
  // "uh" sits in one long silence, so both edge searches find the same pause.
  const env=drawn(3000,[[500,1000,-20],[1700,2400,-20]]);
  const transcript={words:[word('a','well',500,1000),word('b','uh',1200,1500),word('c','then',1700,2400)]};
  assert.equal(cleanCut(transcript.words,1,1,env,{fps:30,durationMs:3000}),null);
  const cut=removalBounds(transcript,wordSelection(transcript.words,1,1),3000,30,env);
  assert.ok(cut.startMs<=1200&&cut.endMs>=1500,`covers the word's transcript span: ${cut.startMs}–${cut.endMs}`);
  assert.ok(cut.startMs>=1000&&cut.endMs<=1700,'and leaves its neighbours alone');
});

test('beside a word that plays again, a cut with no pause there stops before the quietest point and is not stretched',()=>{
  // "is a test", run together with no pause: "a" is 20 ms, under a frame. clear: the word on that side plays again.
  const env=drawn(3000,[[0,3000,-20]]);
  const words=[word('x','is',650,1000),word('y','a',1000,1020),word('z','test',1020,2350)];
  const loose=cleanCut(words,1,1,env,{fps:30,durationMs:3000});
  assert.ok(loose.endMs-loose.startMs>=33,'a Delete of the short word alone still gets its one frame');
  const tight=cleanCut(words,1,1,env,{fps:30,durationMs:3000,clear:[true,true]});
  assert.ok(tight.startMs>=1000&&tight.endMs<=1020,`nothing of "is" or "test" is cut: ${tight.startMs}–${tight.endMs}`);
  assert.ok(tight.endMs-tight.startMs<33,'so no whole frame is left for "a", and the caller refuses');
});

test('a word shorter than a frame, off the frame lines and with no pause around it, is refused rather than cut badly',()=>{
  // "is a test", run together: "a" is 1020–1040, and the frame lines at 30 fps are 1000, 1033 and 1067. One frame from
  // 1033 would leave 13 ms of "a" playing and cut 27 ms of "test".
  const env=drawn(3000,[[0,3000,-20]]);
  const transcript={words:[word('x','is',650,1020),word('y','a',1020,1040),word('z','test',1040,2350)]};
  assert.throws(()=>removalBounds(transcript,wordSelection(transcript.words,1,1),3000,30,env),/too short/);
  // On the frame lines there is room: the frame from 1000 holds all of "a" (1000–1020).
  const fits={words:[word('x','is',650,1000),word('y','a',1000,1020),word('z','test',1020,2350)]};
  const cut=removalBounds(fits,wordSelection(fits.words,1,1),3000,30,env);
  assert.deepEqual([cut.startMs,cut.endMs],[1000,1033]);
});

test('a short word at the edge of a cut, run into its neighbour, is cut too: the pause on its inner side is not its edge',()=>{
  // "well ... a test": "a" (70 ms) runs straight into "test", and the only pause near it is the one before it.
  const last=drawn(4000,[[500,1000,-20],[1500,1570,-20],[1570,1580,-50],[1580,2300,-20],[2800,3400,-20]]);
  const words=[word('w','well',500,1000),word('a','a',1500,1570),word('t','test',1580,2300),word('n','next',2800,3400)];
  const cut=cleanCut(words,0,1,last,{fps:30,durationMs:4000});
  assert.ok(cut.endMs>=1560&&cut.endMs<=1600,`"well a" ends where "a" ends, not in the pause before it: ${cut.endMs}`);
  // The other way round: "is a ... test", "a" (70 ms) runs straight on from "is", the only pause is after it.
  const first=drawn(4000,[[500,1000,-20],[1000,1010,-50],[1010,1080,-20],[1500,2300,-20],[2800,3400,-20]]);
  const others=[word('i','is',500,1000),word('a','a',1010,1080),word('t','test',1500,2300),word('n','next',2800,3400)];
  const second=cleanCut(others,1,2,first,{fps:30,durationMs:4000});
  assert.ok(second.startMs>=980&&second.startMs<=1020,`"a test" starts where "a" starts, not in the pause after it: ${second.startMs}`);
});

test('online loudness is decoded at the server\'s rate, which keeps a word\'s last soft sound out of the cut (TOOLS-473)',async()=>{
  // "this next ... end": "this" ends in an "s", a 60 ms burst of 5–7 kHz noise (950–1010), then 50 ms of pause before
  // "next". Deleting "next" must start the cut after the "s". A decode at 8 kHz holds nothing above 4 kHz, so there the
  // "s" is silence, the pause looks 110 ms long, and the cut starts inside the "s".
  assert.equal(boundary.LOUDNESS_RATE,16000,'the waveform decodes at the rate loudness.py measures at');
  const serverRate=Number(/^RATE = (\d+)$/m.exec(readFileSync(new URL('../loudness.py',import.meta.url),'utf8'))[1]);
  assert.equal(boundary.LOUDNESS_RATE,serverRate,'the same rate as loudness.py');
  const tone=[[500,950],[1060,1500],[1610,2400]],hiss=[5000,5500,6000,6500,7000];
  /** The clip at a sample rate: what a correct decode at that rate holds, so nothing at or above half the rate. */
  function decoded(rate){
    const samples=new Float32Array(3*rate);
    for(let i=0;i<samples.length;i++){
      const ms=i*1000/rate,t=i/rate;
      if(tone.some(([a,b])=>ms>=a&&ms<b))samples[i]=0.5*Math.sin(2*Math.PI*440*t);
      else if(ms>=950&&ms<1010)for(const f of hiss)if(f<rate/2)samples[i]+=0.01*Math.sin(2*Math.PI*f*t+f);
    }
    return samples;
  }
  const words=[word('a','this',500,1000),word('b','next',1060,1500),word('c','end',1610,2400)];
  const cutAt=async rate=>cleanCut(words,1,1,await envelopeFromSamples([decoded(rate)],rate),{fps:30,durationMs:3000});
  const sharp=await cutAt(boundary.LOUDNESS_RATE),low=await cutAt(8000);
  assert.ok(sharp.startMs>=1010,`at the server's rate the "s" stays: ${sharp.startMs}`);
  assert.ok(low.startMs<1010,`at 8 kHz the cut would clip the "s": ${low.startMs}`);
});

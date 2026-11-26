// A long recording's sound measured a stretch at a time (TOOLS-479): the bars and the loudness come out as they do from
// the whole decode, and while only part of the loudness is in, a cut is placed by the sound only where it is in.
import test from 'node:test';
import assert from 'node:assert/strict';
import {audioPeaks,peakMeter} from '../web/waveform-core.js';
import {HOP_MS,envelope,envelopeFromSamples,loudnessMeter,cleanCut,stretchRate,decodeRate,LOUDNESS_RATE} from '../web/cut-boundary.js';
import {removalBounds} from '../web/cut-core.js';
import {wordSelection} from '../web/transcript-core.js';

const now=async()=>{};
/** Two channels of made-up sound: a rising and falling tone with quiet stretches, the same every run. */
function sound(length){
  const left=new Float32Array(length),right=new Float32Array(length);
  for(let i=0;i<length;i++){const loud=Math.floor(i/1777)%3?0.6:0.004;left[i]=loud*Math.sin(i*0.37);right[i]=loud*0.5*Math.cos(i*0.11);}
  return [left,right];
}
/** The sound handed over in uneven pieces, as stretches of a decode come in. */
function* pieces(channels,sizes){
  let at=0,k=0;
  while(at<channels[0].length){const size=sizes[k++%sizes.length],end=Math.min(channels[0].length,at+size);yield [channels.map(c=>c.subarray(at,end)),at];at=end;}
}

test('bars measured a stretch at a time are the bars of the whole decode',async()=>{
  const rate=8000,channels=sound(rate*7+123),whole=await audioPeaks(channels,rate,8,{pause:now});
  const meter=peakMeter(rate,channels[0].length);
  for(const [part,at] of pieces(channels,[5000,17,64,999,40000]))meter.add(part,at);
  assert.equal(meter.bucketMs,whole.bucketMs);
  assert.deepEqual([...meter.peaks],[...whole.peaks]);
});

test('loudness measured a stretch at a time is the loudness of the whole decode',async()=>{
  const rate=LOUDNESS_RATE,channels=sound(rate*5+77),whole=await envelopeFromSamples(channels,rate,{pause:now});
  const meter=loudnessMeter(rate,channels[0].length);
  for(const [part,at] of pieces(channels,[3001,160,80,7,20000]))meter.add(part,at);
  const env=meter.envelope(channels[0].length,true);
  assert.equal(env.readyMs,undefined,'all of it is in');
  assert.equal(env.db.length,whole.db.length);
  for(let i=0;i<env.db.length;i++)assert.ok(Math.abs(env.db[i]-whole.db[i])<1e-3,`step ${i}: ${env.db[i]} vs ${whole.db[i]}`);
  assert.ok(Math.abs(env.threshold-whole.threshold)<1e-3);
});

test('loudness part way in reaches only as far as steps whose whole window is in, and those are already final',async()=>{
  const rate=LOUDNESS_RATE,channels=sound(rate*4),whole=await envelopeFromSamples(channels,rate,{pause:now});
  const meter=loudnessMeter(rate,channels[0].length),ready=rate*2+37;
  meter.add(channels.map(c=>c.subarray(0,ready)),0);
  const part=meter.envelope(ready);
  const win=rate*10/1000,hop=rate*HOP_MS/1000,steps=Math.floor((ready-win)/hop)+1;
  assert.equal(part.db.length,steps);
  assert.equal(part.readyMs,steps*HOP_MS);
  assert.ok(part.readyMs<=ready/rate*1000);
  for(let i=0;i<steps;i++)assert.ok(Math.abs(part.db[i]-whole.db[i])<1e-3,`step ${i} is final`);
  assert.deepEqual(meter.envelope(0),{...envelope(new Float32Array(0)),readyMs:0},'nothing in yet');
});

test('a stretched read decodes at the loudness rate whenever it supplies the loudness, whatever the length',()=>{
  assert.equal(stretchRate(true),LOUDNESS_RATE);
  assert.equal(stretchRate(false),8000);
  // The whole-file decode still drops to 8 kHz past half an hour, as it holds every sample at once.
  assert.equal(decodeRate(3*3600*1000,true),8000);
});

/** Loudness for a clip of durationMs: silence, with each [startMs, endMs, dB] laid over it. */
function drawn(durationMs,spans){
  const db=new Float32Array(Math.ceil(durationMs/HOP_MS)).fill(-80);
  for(const [a,b,level] of spans)for(let i=Math.floor(a/HOP_MS);i<Math.ceil(b/HOP_MS);i++)db[i]=level;
  return envelope(db);
}
const word=(id,text,startMs,endMs)=>({id,text,startMs,endMs});

test('while the loudness comes in, a cut in the part that is in uses the sound, and one past it follows the word times',()=>{
  const spans=[[500,1030,-20],[1030,1060,-55],[1060,1500,-20],[1600,2400,-20],[20500,21000,-20],[21100,21900,-20],[22000,22600,-20]];
  const full=drawn(30000,spans);
  const transcript={words:[word('a','sample',500,1000),word('b','rate',1000,1480),word('c','next',1620,2380),
    word('d','far',20520,20980),word('e','away',21120,21880),word('f','here',22020,22580)]};
  const ready={...drawn(10000,spans.filter(([a])=>a<10000)),readyMs:10000};
  // "rate" lies well inside the part that is in: the cut is the one the whole loudness gives.
  const near=wordSelection(transcript.words,1,1);
  assert.deepEqual(removalBounds(transcript,near,30000,30,ready),removalBounds(transcript,near,30000,30,full));
  assert.ok(cleanCut(transcript.words,1,1,ready,{fps:30,durationMs:30000}),'the sound places it');
  // "away" lies past it: no sound to go by yet, so it is cut as with no loudness at all.
  const far=wordSelection(transcript.words,4,4);
  assert.equal(cleanCut(transcript.words,4,4,ready,{fps:30,durationMs:30000}),null);
  assert.deepEqual(removalBounds(transcript,far,30000,30,ready),removalBounds(transcript,far,30000,30,null));
  assert.notDeepEqual(removalBounds(transcript,far,30000,30,full),removalBounds(transcript,far,30000,30,null),'the whole loudness would move it');
  // Just before the edge: its pause could run on past what is in, so it waits for the sound too.
  const edge={...drawn(2700,spans.filter(([a])=>a<2700)),readyMs:2700};
  assert.equal(cleanCut(transcript.words,1,1,edge,{fps:30,durationMs:30000}),null);
});

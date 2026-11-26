// Where a word cut starts and ends, found in the audio itself (TOOLS-463). Transcript word times are a starting guess;
// the cut moves to the real silence (or the quietest moment) around the words, keeps the natural pause, then snaps to
// the quietest frame line. The loudness comes once per video: from the local server (loudness.py), or, online, from
// the waveform the browser already decoded (at the server's rate, LOUDNESS_RATE).

export const HOP_MS=5;            // loudness measured every 5 ms, over a 10 ms window
const WINDOW_MS=10;
// The sample rate the loudness is measured at, the server's (loudness.py RATE). The waveform decodes at it too when it
// supplies the loudness: at a lower rate the soft high sounds that end words ("s", "f", "th", mostly above 4 kHz) are
// gone, and a cut clips them.
export const LOUDNESS_RATE=16000;
// Enough for the waveform's bars alone.
const BARS_RATE=8000;
// The decode keeps every channel at its rate. Before TOOLS-473 it was always at 8 kHz, so an hour of stereo held
// 57.6 million samples (about 230 MB); a mono or stereo decode at the loudness rate may not hold more than that.
const MOST_SAMPLES=3600*BARS_RATE*2;
/** The rate Build waveform decodes at: the loudness rate when the decode supplies the loudness (needsLoudness) and the
 *  recording is short enough, else 8 kHz for the bars only. Channels are not known before the decode, so two are
 *  assumed: the loudness rate covers up to half an hour, and longer recordings online cut at 8 kHz edges. */
export function decodeRate(durationMs,needsLoudness){
  return needsLoudness&&durationMs/1000*LOUDNESS_RATE*2<=MOST_SAMPLES?LOUDNESS_RATE:BARS_RATE;
}
/** The rate an MP4 read in stretches decodes at (TOOLS-479): it holds one stretch at a time, whatever the length, so the
 *  loudness rate whenever the decode supplies the loudness. */
export const stretchRate=needsLoudness=>needsLoudness?LOUDNESS_RATE:BARS_RATE;
// Measuring a long decode takes a while; it runs in slices with a pause after each, so the page keeps answering clicks.
// The pause is a message, not a timer: a browser slows timers to one a second in a hidden tab.
export const nextTask=()=>new Promise(resolve=>{
  const {port1,port2}=new MessageChannel();
  port1.onmessage=()=>{port1.close();resolve();};
  port2.postMessage(null);
});
const SLICE_STEPS=4000;           // 20 s of audio per slice
// Further out, the short silence inside a word ("sam-p-le") starts to pass for the pause between words.
const SEARCH_BEFORE_MS=100;       // look this far outside the words for their silence
const SEARCH_INSIDE_MS=80;        // and at most this far inside them (transcripts are often early or late)
const VALLEY_MS=60;               // no silence: the quietest moment within ±60 ms of the transcript edge
const MIN_SILENCE_MS=20;          // a quiet run shorter than this is not a pause
const LONGEST_PAUSE_MS=2000;      // a pause reaching past the search window is followed this much further

/** The loudness envelope from dB values every HOP_MS: the values, and the line below which the audio counts as quiet. */
export function envelope(db){
  if(!db.length)return {db,threshold:-Infinity,floor:-100,peak:-100};
  const sorted=Float32Array.from(db).sort();
  const floor=sorted[Math.floor(sorted.length*0.1)],peak=sorted[Math.floor(sorted.length*0.995)];
  // Silence: within 12 dB of the room noise, but never closer than 45 dB below the loudest speech, and always at least
  // 12 dB below it, so sound with no quiet parts at all (music, a steady tone) has no pauses rather than only pauses.
  return {db,threshold:Math.min(Math.max(floor+12,peak-45),peak-12),floor,peak};
}

/** The server's loudness file: one byte every HOP_MS, the level in half dB below full scale (0 to 200). */
export function envelopeFromBytes(bytes){
  const db=new Float32Array(bytes.length);
  for(let i=0;i<bytes.length;i++)db[i]=bytes[i]?-bytes[i]/2:0;
  return envelope(db);
}

/** Loudness from decoded samples (one array per channel), in the server's steps and window. Decoded at LOUDNESS_RATE
 *  it measures as the server does but for one thing: channels are averaged by energy, where the server first mixes them
 *  to one channel. With the same sound on every channel (one microphone) the two are the same. Measured in slices,
 *  awaiting pause after each. */
export async function envelopeFromSamples(channels,sampleRate,{pause=nextTask}={}){
  const hop=Math.round(sampleRate*HOP_MS/1000),win=Math.round(sampleRate*WINDOW_MS/1000);
  const length=channels[0]?.length||0,db=new Float32Array(Math.ceil(length/hop));
  for(let slice=0;slice<db.length;slice+=SLICE_STEPS){
    for(let i=slice;i<Math.min(db.length,slice+SLICE_STEPS);i++){
      let sum=0,n=0;const from=i*hop,to=Math.min(length,from+win);
      for(const channel of channels)for(let j=from;j<to;j++){sum+=channel[j]*channel[j];n++;}
      db[i]=n?Math.max(-100,10*Math.log10(sum/n+1e-12)):-100;
    }
    await pause();
  }
  return envelope(db);
}

/** The same loudness, measured as a long recording's sound comes in a stretch at a time (TOOLS-479), so the decoded
 *  sound never has to be kept: add(channels, at) measures decoded samples that start at sample `at` of a recording of
 *  totalSamples. envelope(readySamples) is the loudness of the first readySamples, with readyMs saying how far it
 *  reaches; with every sample in (done), it is the whole recording's, as envelopeFromSamples measures it. */
export function loudnessMeter(sampleRate,totalSamples){
  const hop=Math.round(sampleRate*HOP_MS/1000),win=Math.round(sampleRate*WINDOW_MS/1000),steps=Math.ceil(totalSamples/hop);
  const sums=new Float64Array(steps),counts=new Uint32Array(steps);
  function add(channels,at){
    const length=channels[0]?.length||0;
    for(let i=Math.max(0,Math.floor((at-win)/hop)+1);i<steps&&i*hop<at+length;i++){
      const from=Math.max(0,i*hop-at),to=Math.min(length,i*hop+win-at);if(to<=from)continue;
      let sum=0;for(const channel of channels)for(let j=from;j<to;j++)sum+=channel[j]*channel[j];
      sums[i]+=sum;counts[i]+=(to-from)*channels.length;
    }
  }
  function measured(readySamples,done=false){
    // A step's window is only complete once the samples after it are in, except at the very end.
    const ready=done?Math.ceil(readySamples/hop):Math.max(0,Math.min(steps,Math.floor((readySamples-win)/hop)+1));
    const db=new Float32Array(ready);
    for(let i=0;i<ready;i++)db[i]=counts[i]?Math.max(-100,10*Math.log10(sums[i]/counts[i]+1e-12)):-100;
    const env=envelope(db);return done?env:{...env,readyMs:ready*HOP_MS};
  }
  return {add,envelope:measured};
}

const at=(env,ms)=>env.db[Math.max(0,Math.min(env.db.length-1,Math.floor(ms/HOP_MS)))];
const quiet=(env,ms)=>at(env,ms)<env.threshold;
/** Average loudness in a short window around a time: how loud a cut there would land. */
function levelAround(env,ms,half=8){
  let sum=0,n=0;
  for(let t=ms-half;t<=ms+half;t+=HOP_MS){sum+=10**(at(env,t)/10);n++;}
  return 10*Math.log10(sum/n);
}

/** The pause nearest to a transcript edge, or the quietest moment near it when the words run together. */
function findGap(env,edgeMs,lo,hi){
  const runs=[];let start=null;
  for(let t=lo;t<=hi;t+=HOP_MS){
    if(quiet(env,t)){if(start===null)start=t;}
    else if(start!==null){runs.push([start,t]);start=null;}
  }
  if(start!==null)runs.push([start,hi]);
  // A pause reaching the edge of the search window keeps going: measure all of it.
  for(const run of runs){
    while(run[0]>0&&quiet(env,run[0]-HOP_MS)&&run[0]>lo-LONGEST_PAUSE_MS)run[0]-=HOP_MS;
    while(quiet(env,run[1])&&run[1]<hi+LONGEST_PAUSE_MS)run[1]+=HOP_MS;
  }
  const pauses=runs.filter(([a,b])=>b-a>=MIN_SILENCE_MS);
  if(pauses.length){
    const distance=([a,b])=>edgeMs<a?a-edgeMs:edgeMs>b?edgeMs-b:0;
    const [a,b]=pauses.reduce((best,run)=>distance(run)<distance(best)?run:best);
    return {startMs:a,endMs:b,silent:true};
  }
  let best=edgeMs,bestDb=Infinity;
  for(let t=Math.max(lo,edgeMs-VALLEY_MS);t<=Math.min(hi,edgeMs+VALLEY_MS);t+=HOP_MS){
    // A dip far from the transcript edge is more likely inside a word ("c-an"), so distance costs 0.15 dB per ms.
    const level=levelAround(env,t,5)+Math.abs(t-edgeMs)*0.15;
    if(level<bestDb){bestDb=level;best=t;}
  }
  return {startMs:best,endMs:best,silent:false};
}

/** The frame line inside [lo, hi] where the cut lands quietest; ties go to the one nearest the aim. */
function quietestFrame(env,aimMs,lo,hi,step){
  let best=null,bestDb=Infinity;
  for(let f=Math.ceil(lo/step-0.001);f*step<=hi+0.001;f++){
    const ms=f*step,level=levelAround(env,ms)+Math.abs(ms-aimMs)*0.02; // a little pull towards the aim
    if(level<bestDb){bestDb=level;best=ms;}
  }
  // A pause shorter than a frame holds no frame line: take the quietest one just around it.
  return best??quietestFrame(env,aimMs,lo-step/2,hi+step/2,step);
}

/**
 * The cut for words[first..last]: {startMs, endMs, aimStartMs, aimEndMs, silent}, or null when the loudness shows
 * no sound for them. startMs and endMs are on frame lines
 * (what plays and what exports); aim* are the exact audio points. The search never reaches past the middle of a
 * neighbouring word, and of the two pauses around the words the longer one stays, shared out between the two sides.
 * clear: [before, after], true where the word on that side plays again (it was clicked back). On such a side the cut
 * stops at the frame line before that word's sound (the pause's far edge, or with no pause the quietest point), so
 * none of it is cut.
 * A sound shorter than a frame with no pause around it can leave both edges on one frame line. The cut is never
 * stretched to a whole frame, which would take more of a neighbour than of the word: the caller refuses it.
 */
export function cleanCut(words,first,last,env,{fps=30,durationMs,clear=[false,false]}){
  const step=1000/fps,s=words[first].startMs,e=words[last].endMs,length=e-s;
  const before=words[first-1],after=words[last+1];
  const inside=Math.min(SEARCH_INSIDE_MS,length*0.3);
  const loStart=before?Math.max(before.startMs+(before.endMs-before.startMs)/2,s-SEARCH_BEFORE_MS):Math.max(0,s-SEARCH_BEFORE_MS);
  const hiEnd=after?Math.min(after.startMs+(after.endMs-after.startMs)/2,e+SEARCH_BEFORE_MS):Math.min(durationMs,e+SEARCH_BEFORE_MS);
  // Nor past the middle of the first or last word itself: a short one would take the pause on its far side for its edge.
  // Loudness still coming in (readyMs, see loudnessMeter) cannot place a cut whose search reaches past what is in: such
  // a cut follows the word times (null), as with no loudness at all.
  if(env.readyMs!=null&&hiEnd+LONGEST_PAUSE_MS+step>env.readyMs)return null;
  const insideFirst=Math.min(inside,(words[first].endMs-s)/2),insideLast=Math.min(inside,(e-words[last].startMs)/2);
  const g1=findGap(env,s,loStart,s+insideFirst),g2=findGap(env,e,e-insideLast,hiEnd);
  // Both edges in one pause: the words make no sound the loudness can see, so it cannot place the cut (null).
  if(g1.endMs>g2.startMs)return null;
  if(!before)g1.startMs=Math.max(0,g1.startMs);
  if(!after)g2.endMs=Math.min(durationMs,g2.endMs);
  // Keep one natural pause: the longer of the two. The shorter pause goes with the words.
  const len1=g1.endMs-g1.startMs,len2=g2.endMs-g2.startMs,keep=Math.max(len1,len2);
  const keepBefore=len1+len2?keep*len1/(len1+len2):0;
  const aimStartMs=g1.startMs+keepBefore,aimEndMs=g2.endMs-(keep-keepBefore);
  // Frame lines: stay inside the pause (or within most of a frame either side of a valley), aiming at the split point.
  const pad=step*0.75;
  let startMs=quietestFrame(env,aimStartMs,g1.silent?g1.startMs:g1.startMs-pad,g1.silent?Math.max(g1.startMs,Math.min(g1.endMs,aimStartMs+pad)):g1.endMs+pad,step);
  let endMs=quietestFrame(env,aimEndMs,g2.silent?Math.min(g2.endMs,Math.max(g2.startMs,aimEndMs-pad)):g2.startMs-pad,g2.silent?g2.endMs:g2.endMs+pad,step);
  // Beside a word that plays again the cut may not pass where that word's sound ends or starts (the far edge of the
  // pause, or the quietest point): it takes the nearest frame line on its own side of it.
  const soundEndsMs=g1.silent?g1.startMs:aimStartMs,soundStartsMs=g2.silent?g2.endMs:aimEndMs;
  if(clear[0]&&startMs<soundEndsMs-0.001)startMs=Math.ceil(soundEndsMs/step-0.001)*step;
  if(clear[1]&&endMs>soundStartsMs+0.001)endMs=Math.floor(soundStartsMs/step+0.001)*step;
  return {startMs:Math.max(0,startMs),endMs:Math.min(durationMs,endMs),aimStartMs,aimEndMs,silent:[g1.silent,g2.silent]};
}

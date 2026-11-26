import {nextTask} from './cut-boundary.js';
const SLICE_MS=20000;
// Peaks are measured from decoded audio. Transcript gaps do not stand in for a waveform. A long recording takes a
// while, so it is measured in slices of 20 s, awaiting pause after each, and the page keeps answering clicks.
export async function audioPeaks(channels,sampleRate,bucketMs=8,{pause=nextTask}={}){
  if(!channels.length||!Number.isFinite(sampleRate)||sampleRate<=0)throw Error('No decoded audio.');
  const length=channels[0].length,step=Math.max(1,Math.round(sampleRate*bucketMs/1000));
  const peaks=new Float32Array(Math.ceil(length/step)),slice=Math.max(1,Math.round(SLICE_MS/bucketMs));
  for(let first=0;first<peaks.length;first+=slice){
    for(let i=first;i<Math.min(peaks.length,first+slice);i++){
      let peak=0;const end=Math.min(length,(i+1)*step);
      for(const channel of channels)for(let j=i*step;j<end;j++)peak=Math.max(peak,Math.abs(channel[j]||0));
      peaks[i]=peak;
    }
    await pause();
  }
  return {peaks,bucketMs:step/sampleRate*1000,durationMs:length/sampleRate*1000};
}
export function waveformWindow(durationMs,atMs,range=null,spanMs=8000){
  const size=Math.min(durationMs,Math.max(spanMs,range?range.endMs-range.startMs+1600:0));
  const centre=range?(range.startMs+range.endMs)/2:atMs;
  const start=Math.max(0,Math.min(durationMs-size,centre-size/2));return {startMs:start,endMs:start+size};
}
// Keep the playhead at the same horizontal position while changing the visible span.
export function zoomWaveformWindow(durationMs,view,atMs,delta){
  const oldSize=view.endMs-view.startMs,anchor=Math.max(view.startMs,Math.min(view.endMs,atMs));
  const fraction=(anchor-view.startMs)/oldSize;
  const size=Math.min(durationMs,Math.max(Math.min(250,durationMs),oldSize*Math.exp(Math.max(-300,Math.min(300,delta))*.0025)));
  const start=Math.max(0,Math.min(durationMs-size,anchor-fraction*size));
  return {startMs:start,endMs:start+size};
}
export function adjustBoundary(range,edge,value,durationMs,fps=30){
  const step=1000/fps,limit=Math.floor(durationMs/step+.00001)*step;
  const snap=n=>Math.round(n/step)*step;
  const start=snap(range.startMs),end=Math.min(limit,snap(range.endMs));
  return edge==='start'?{startMs:Math.round(Math.max(0,Math.min(end-step,snap(value)))),endMs:Math.round(end)}:
    {startMs:Math.round(start),endMs:Math.round(Math.min(limit,Math.max(start+step,snap(value))))};
}

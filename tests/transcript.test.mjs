import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeTranscript,activeWordIndex,wordSelection,searchLibrary,originalOmissions} from '../web/transcript-core.js';
import {zoomWaveformWindow} from '../web/waveform-core.js';
import {applyChange,describe,rebaseComments} from '../web/review-core.js';
import {normalizeDraftEdits,makeRemoval,removalBounds,skipRemoved,previewDuration,exportCut} from '../web/cut-core.js';
import {audioPeaks,waveformWindow,adjustBoundary} from '../web/waveform-core.js';

test('HappyScribe word objects preserve text and actual word timings',()=>{
  const data=[{words:[{text:' café',data_start:1.12,data_end:1.4,type:'word'},{text:'dashboard',data_start:1.5,data_end:2}]}];
  const t=normalizeTranscript(data,3000);assert.deepEqual(t.words.map(w=>[w.text,w.startMs,w.endMs]),[['café',1120,1400],['dashboard',1500,2000]]);
});
test('paragraph timestamps are never invented into word timings',()=>{
  assert.throws(()=>normalizeTranscript([{text:'two words',start:0,end:2,words:[]}],3000));
  assert.throws(()=>normalizeTranscript({text:'two words'},3000));
});
test('timings from another cut and duplicate word IDs are rejected',()=>{
  assert.throws(()=>normalizeTranscript([['word',1,6]],3000),/longer/);
  assert.throws(()=>normalizeTranscript({words:[{id:'W1',text:'a',startMs:0,endMs:100},{id:'W1',text:'b',startMs:200,endMs:300}]},1000),/unique/);
  assert.throws(()=>normalizeTranscript([['a',2,1]],3000),/invalid/);
});
test('word highlight stops in real gaps and at the exclusive end',()=>{
  const words=normalizeTranscript([['a',0,1],['b',2,3]],3000).words;
  assert.equal(activeWordIndex(words,500),0);assert.equal(activeWordIndex(words,1000),-1);assert.equal(activeWordIndex(words,2500),1);assert.equal(activeWordIndex(words,3000),-1);
});
test('backward phrase selection preserves first/last source word IDs',()=>{
  const words=normalizeTranscript({words:[{id:'W5',text:'keep',startMs:100,endMs:300},{id:'W9',text:'breaths',startMs:500,endMs:900}]},1000).words;
  assert.deepEqual(wordSelection(words,1,0),{startMs:100,endMs:900,quote:'keep breaths',firstWordId:'W5',lastWordId:'W9'});
});
test('text requests keep source anchors even when later keep ranges change',()=>{
  const video={id:'v1',durationMs:2000,fps:30,keepRanges:[[300,360]]},state={videos:[video],comments:[]};
  const c=applyChange(state,{action:'add',videoId:'v1',startMs:100,endMs:900,text:'Let the word finish',editRequest:{type:'extend_after',firstWordId:'W5',lastWordId:'W9',quote:'keep breaths'}});
  assert.equal(c.status,'open');assert.equal(c.editRequest.type,'extend_after');
  const anchors=structuredClone(describe(video,c).sourceSpans);video.keepRanges=[[900,960]];
  assert.deepEqual(describe(video,c).sourceSpans,anchors);assert.equal(state.comments.length,1);
});
test('arbitrary or malformed transcript actions are not saved',()=>{
  const state={videos:[{id:'v',durationMs:1000,fps:30,keepRanges:[[0,30]]}],comments:[]};
  assert.throws(()=>applyChange(state,{action:'add',videoId:'v',startMs:0,text:'x',editRequest:{type:'execute-code'}}));assert.equal(state.comments.length,0);
});
test('library search finds titles and spoken phrases with their playback time',()=>{
  const videos=[{id:'a',title:'Security episode',label:'Opening'},{id:'b',title:'Other',label:'Demo'}];
  const transcripts={a:{words:[{text:'There',startMs:100},{text:'was',startMs:200},{text:'a',startMs:300},{text:'backup.',startMs:400}]}};
  assert.equal(searchLibrary(videos,transcripts,'security')[0].id,'a');
  assert.equal(searchLibrary(videos,transcripts,'a backup')[0].atMs,300);
  assert.equal(searchLibrary(videos,transcripts,'missing').length,0);
});
test('preview removals merge overlaps, skip media and export original source frames',()=>{
  const edits=[{id:'a',startMs:200,endMs:800,wordIds:['b']},{id:'b',startMs:500,endMs:1000,wordIds:['c']}];
  assert.equal(previewDuration(2000,edits),1200);assert.equal(skipRemoved(600,edits),1000);assert.equal(skipRemoved(1000,edits),1000);
  const cut=exportCut({draftEdits:edits,revision:'r'},{id:'v',fps:30,durationMs:2000,keepRanges:[[300,360]]});
  assert.deepEqual(cut.keepRanges,[[300,306],[330,360]]);assert.equal(cut.durationMs,1200);
  assert.throws(()=>normalizeDraftEdits([{id:'x',startMs:0,endMs:3000,wordIds:[]}],2000));
});
test('shortening a pause preserves both adjacent words and breath margins',()=>{
  const t=normalizeTranscript([['before',0,.4],['after',1.5,2]],2000);
  const edit=makeRemoval(t,{kind:'pause',startMs:400,endMs:1500,quote:'pause'},2000,30);
  assert.deepEqual(edit.wordIds,[]);assert.ok(edit.startMs>=580);assert.ok(edit.endMs<=1320);
  assert.deepEqual(normalizeTranscript({...t,draftEdits:[edit]},2000).draftEdits[0].wordIds,[]);
});
test('comments follow retained source material after recuts and removed comments land at joins',()=>{
  const video={id:'v',fps:30,durationMs:4000,keepRanges:[[0,120]]},comments=[{videoId:'v',startMs:3000,endMs:3500},{videoId:'v',startMs:1200,endMs:1800}];
  rebaseComments(video,comments,[[0,30],[60,120]],3000);
  assert.equal(comments[0].startMs,2000);assert.equal(comments[0].endMs,2500);
  assert.equal(comments[1].startMs,1000);assert.equal(comments[1].endMs,null);
  assert.equal(comments[0].sourceSpans[0].sourceStartFrame,90);
});

test('waveform preserves channel peaks and uses the decoded audio clock',()=>{
  const p=audioPeaks([Float32Array.from([0,.5,0,0,-.8,0,0,0]),Float32Array.from([0,-.5,0,0,0,0,0,0])],1000,4);
  assert.equal(p.bucketMs,4);assert.equal(p.durationMs,8);assert.equal(p.peaks[0],.5);assert.ok(Math.abs(p.peaks[1]-.8)<.00001);
});
test('waveform windows include both cut handles and stay inside the file',()=>{
  assert.deepEqual(waveformWindow(10000,1000,null,4000),{startMs:0,endMs:4000});
  const view=waveformWindow(10000,9000,{startMs:7000,endMs:9500},4000);
  assert.ok(view.startMs<=7000&&view.endMs>=9500&&view.endMs<=10000);
});
test('manual waveform boundaries keep a frame, preserve the chosen edge, and survive serialization',()=>{
  const range=adjustBoundary({startMs:500,endMs:1500},'start',1000,3000,30);
  assert.deepEqual(range,{startMs:1000,endMs:1500});
  assert.equal(adjustBoundary(range,'start',2500,3000,30).startMs,1467);
  const transcript=normalizeTranscript([['before',0,.4],['after',1.5,2]],3000);
  const manual=adjustBoundary({startMs:500,endMs:1500},'end',1433,3000,30);
  const edit=removalBounds(transcript,{...manual,kind:'pause',precise:true,quote:'gap'},3000,30);
  assert.equal(edit.startMs,500);assert.equal(edit.endMs,1433);
  assert.deepEqual(normalizeDraftEdits([{id:'manual',...edit}],3000)[0].wordIds,[]);
});

test('waveform zoom keeps the playhead fixed and clamps at recording bounds',()=>{
  const zoom=zoomWaveformWindow(10000,{startMs:0,endMs:10000},3000,-200);
  assert.ok(zoom.endMs-zoom.startMs<10000);assert.ok(Math.abs((3000-zoom.startMs)/(zoom.endMs-zoom.startMs)-.3)<.00001);
  const fit=zoomWaveformWindow(10000,zoom,3000,300);assert.deepEqual(fit,{startMs:0,endMs:10000});
  let min=zoom;for(let i=0;i<30;i++)min=zoomWaveformWindow(10000,min,3000,-300);assert.ok(Math.abs(min.endMs-min.startMs-250)<.001);
  assert.equal(zoomWaveformWindow(10000,{startMs:0,endMs:10000},0,-200).startMs,0);
  assert.equal(zoomWaveformWindow(10000,{startMs:0,endMs:10000},10000,-200).endMs,10000);
});
test('original transcript is preserved separately and finds speech already removed in Resolve',()=>{
  const video={id:'v',fps:30,durationMs:2000,keepRanges:[[300,360]]};
  const t=normalizeTranscript({words:[{id:'kept',text:'keep',startMs:100,endMs:400,sourceStartMs:10100,sourceEndMs:10400}],original:{durationMs:14000,sourceId:'raw',words:[{id:'before',text:'before',startMs:100,endMs:500},{id:'kept',text:'keep',startMs:10100,endMs:10400},{id:'after',text:'after',startMs:13000,endMs:13500}]}},2000);
  const omitted=originalOmissions(t,video);assert.equal(omitted.length,2);assert.equal(omitted[0].words[0].id,'before');assert.equal(omitted[0].atMs,0);assert.equal(omitted[1].words[0].id,'after');assert.equal(omitted[1].atMs,2000);
  assert.equal(normalizeTranscript(t,2000).original.words.length,3);
  assert.throws(()=>normalizeTranscript({...t,original:{...t.original,words:[{id:'other',text:'bad',startMs:0,endMs:10}]}},2000),/word IDs/);
});
test('source restore requests retain original-clock anchors beyond the reviewed video',()=>{
  const video={id:'v',fps:30,durationMs:2000,keepRanges:[[300,360]]},state={videos:[video],comments:[]};
  const c=applyChange(state,{action:'add',videoId:'v',startMs:2000,text:'Restore the ending',editRequest:{type:'restore',sourceStartMs:13000,sourceEndMs:13500,sourceDurationMs:14000,firstWordId:'after'}});
  assert.equal(c.sourceSpans[0].sourceStartFrame,390);assert.equal(c.sourceSpans[0].sourceEndFrame,405);assert.equal(describe(video,c).editRequest.type,'restore');
  assert.throws(()=>applyChange(state,{action:'add',videoId:'v',startMs:0,text:'Invalid',editRequest:{type:'restore',sourceStartMs:13000,sourceEndMs:15000,sourceDurationMs:14000}}),/restore range/);
});

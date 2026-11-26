// Browser check of Build waveform on a review copy of 3 GB (TOOLS-479), made up on the spot: a 70 s MP4 whose index is
// moved behind 3 GB of padding (a sparse file, so it takes no disk space). It never opens a real review.
// It checks that the waveform builds with no "use a smaller file" message, fills in from left to right, reads only
// small parts of the file, that a Delete on the part already in uses the sound while one past it follows the word
// times, and that the sound read in stretches lines up with the whole file's decode. Opened as a chosen file, as online,
// a build stopped part way leaves nothing behind, and a 3 GB fragmented MP4 says what to do instead.
// Run: node tests/big-waveform-browser.mjs (needs Playwright and ffmpeg).
import {chromium} from 'playwright';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,mkdir,readFile,rm,open} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import assert from 'node:assert/strict';
import {removalBounds} from '../web/cut-core.js';
import {wordSelection} from '../web/transcript-core.js';

const root=resolve('.'),temp=await mkdtemp(join(tmpdir(),'video-big-waveform-')),port=Number(process.env.BIG_WAVEFORM_PORT||8898),base=`http://127.0.0.1:${port}`;
const python=process.env.VIDEO_REVIEW_PYTHON||(process.platform==='win32'?'python':'python3');
const small=join(temp,'small.mp4'),big=join(temp,'big.mp4'),fps=30,durationMs=70000,PAD=3*1024**3;
// Noise, loud for 0.95 s of every 1.5 s: noise has no repeating shape, so a stretch placed a little off would show.
const encoded=spawnSync('ffmpeg',['-v','error','-y','-f','lavfi','-i',`color=c=0x1b1f3b:s=320x180:r=${fps}:d=70`,'-f','lavfi','-i','anoisesrc=color=pink:sample_rate=48000:duration=70:amplitude=0.4','-af',"volume='if(lt(mod(t,1.5),0.95),1,0)':eval=frame",'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-shortest',small],{windowsHide:true,encoding:'utf8'});
assert.equal(encoded.status,0,encoded.stderr);
// The big copy: the same file with a 3 GB free box before its index (moov), which ffmpeg writes at the end.
const bytes=await readFile(small);let moovAt=0;
for(let at=0;at+8<=bytes.length;){const size=bytes.readUInt32BE(at);if(bytes.toString('latin1',at+4,at+8)==='moov'){moovAt=at;break;}at+=size===1?Number(bytes.readBigUInt64BE(at+8)):size;}
assert.ok(moovAt>0,'the made-up MP4 has its index at the end');
const free=Buffer.alloc(16);free.writeUInt32BE(1,0);free.write('free',4,'latin1');free.writeBigUInt64BE(BigInt(PAD),8);
const file=await open(big,'w');
// Windows keeps a file sparse only when told to; without it the padding would be 3 GB of real zeros on the disk.
if(process.platform==='win32')assert.equal(spawnSync('fsutil',['sparse','setflag',big],{windowsHide:true}).status,0,'the big copy is marked sparse');
try{await file.write(bytes,0,moovAt,0);await file.write(free,0,16,moovAt);await file.write(bytes,moovAt,bytes.length-moovAt,moovAt+PAD);}finally{await file.close();}
// A fragmented copy as a recorder writes it (its frames listed in each fragment, not in the index), 3 GB as well: the
// padding goes at its end, after the last fragment.
const fragmented=join(temp,'fragmented.mp4');
const remuxed=spawnSync('ffmpeg',['-v','error','-y','-i',small,'-c','copy','-movflags','frag_keyframe+empty_moov',fragmented],{windowsHide:true,encoding:'utf8'});
assert.equal(remuxed.status,0,remuxed.stderr);
const fragmentedBytes=(await readFile(fragmented)).length,tail=await open(fragmented,'r+');
if(process.platform==='win32')assert.equal(spawnSync('fsutil',['sparse','setflag',fragmented],{windowsHide:true}).status,0,'the fragmented copy is marked sparse');
try{await tail.write(free,0,16,fragmentedBytes);await tail.truncate(fragmentedBytes+PAD);}finally{await tail.close();}
// And a 3 GB copy with no sound at all, as a long screen recording can be.
const silent=join(temp,'silent.mp4');
const stripped=spawnSync('ffmpeg',['-v','error','-y','-i',small,'-c','copy','-an',silent],{windowsHide:true,encoding:'utf8'});
assert.equal(stripped.status,0,stripped.stderr);
const silentBytes=(await readFile(silent)).length,silentTail=await open(silent,'r+');
if(process.platform==='win32')assert.equal(spawnSync('fsutil',['sparse','setflag',silent],{windowsHide:true}).status,0,'the silent copy is marked sparse');
try{await silentTail.write(free,0,16,silentBytes);await silentTail.truncate(silentBytes+PAD);}finally{await silentTail.close();}
// A word over each loud stretch, a little inside its sound (0 to 950 ms of every 1.5 s), so the pauses either side are
// within the cut's search and a cut from the sound differs from one from the word times.
const words=[];for(let i=0;i<46;i++)words.push({id:'W'+i,text:'word'+i,startMs:i*1500+60,endMs:i*1500+890});
await writeFile(join(temp,'manifest.json'),JSON.stringify([
  {id:'big',title:'Big waveform test',label:'Synthetic',path:big,fps,durationMs,keepRanges:[[0,durationMs*fps/1000]]},
  {id:'small',title:'Big waveform test, small copy',label:'Synthetic',path:small,fps,durationMs,keepRanges:[[0,durationMs*fps/1000]]}]));
await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[]}));
await mkdir(join(temp,'transcripts'));
await writeFile(join(temp,'transcripts/big.json'),JSON.stringify({words,cuts:[],revision:'synthetic'}));

const server=spawn(python,[join(root,'server.py'),'--port',String(port),'--data-dir',temp],{cwd:root,windowsHide:true,stdio:'ignore'});
let browser;
try{
  for(let i=0;i<80;i++){try{if((await fetch(base+'/api/local')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  // Every read of the big file by the page's own code (not the video player's), with its range and size.
  const reads=[];
  page.on('response',async response=>{
    const request=response.request();if(request.resourceType()!=='fetch'||!request.url().includes('/media/big'))return;
    reads.push({range:request.headers().range||null,status:response.status(),bytes:Number(response.headers()['content-length']||0)});
  });
  await page.addInitScript(()=>{const Native=window.OfflineAudioContext;window.decodeRates=[];window.OfflineAudioContext=class extends Native{constructor(...args){super(...args);window.decodeRates.push(this.sampleRate);}};});
  // While heldPauses is a list, the pauses between stretches (a message, nextTask in cut-boundary.js) wait in it.
  await page.addInitScript(()=>{
    const Native=window.MessageChannel;window.heldPauses=null;
    window.MessageChannel=class extends Native{constructor(){super();const port=this.port2,post=port.postMessage.bind(port);port.postMessage=message=>window.heldPauses?window.heldPauses.push(()=>post(message)):post(message);}};
    window.releasePauses=()=>{const held=window.heldPauses||[];window.heldPauses=null;for(const post of held)post();};
  });
  // Every message the waveform shows while it builds.
  await page.addInitScript(()=>{window.statuses=[];addEventListener('DOMContentLoaded',()=>{const node=document.querySelector('#waveformStatus');new MutationObserver(()=>window.statuses.push(node.textContent)).observe(node,{childList:true,characterData:true,subtree:true});});});
  // The server cannot measure the loudness here (as online), so the waveform's decode supplies it.
  await page.route('**/api/loudness/**',route=>route.fulfill({status:503,body:''}));
  const onDisk=async()=>JSON.parse(await readFile(join(temp,'transcripts/big.json'),'utf8')).draftEdits||[];
  const word=i=>page.locator('.transcript-word').nth(i);
  await page.goto(base);await page.waitForSelector('.transcript-word');
  await page.waitForFunction(()=>document.querySelector('video').readyState>=1&&!document.querySelector('#buildWaveform').disabled);
  // Show the video and its waveform as an editor would.
  if(await page.locator('body').evaluate(n=>n.classList.contains('transcript-only')))await page.locator('#toggleTranscriptView').click();

  // The waveform's pixels at a time, on the whole video's view (Escape lets go of a selection, which zooms in on it).
  const pixels=async(ms,y,width)=>{
    await page.keyboard.press('Escape');
    return page.evaluate(([ms,y,width])=>{
      const canvas=document.querySelector('#waveformCanvas'),scale=canvas.width/canvas.clientWidth,start=Number(canvas.dataset.startMs),end=Number(canvas.dataset.endMs);
      if(start!==0||end<69000)throw new Error(`the waveform shows ${start}-${end} ms, not the whole video`);
      const x=Math.round((ms-start)/(end-start)*canvas.width);
      return [...canvas.getContext('2d').getImageData(Math.max(0,x-(width>>1)),Math.round(y*scale),width,1).data];
    },[ms,y,width]);
  };
  // Bars at a time: any pixel of a bar 5 px above the middle line (41 px), which silence (a 1 px bar) never reaches and
  // a quarter of the loudest sound does.
  const barAt=async ms=>(await pixels(ms,36,9)).some((value,i)=>i%4===3&&value>0);

  await page.evaluate(()=>{window.heldPauses=[];});
  await page.locator('#buildWaveform').click();
  await page.waitForFunction(()=>window.heldPauses?.length>0);
  // Held after the first stretch, about 20 s in: the bars are in on the left only, and it says how far it is.
  const first=await page.locator('#waveformStatus').innerText();
  const percent=Number(/^Building waveform… (\d+)%$/.exec(first)?.[1]);
  assert.ok(percent>20&&percent<40,`the first stretch is about 20 s of 70: "${first}"`);
  assert.equal(await barAt(9400),true,'bars at 9.4 s, in the part that is in');
  assert.equal(await barAt(45400),false,'no bars yet at 45.4 s');

  // A Delete now: in the part that is in, the cut goes by the sound; past it, by the word times, as with no loudness.
  const transcript={words};
  const fromWords=i=>removalBounds(transcript,wordSelection(words,i,i),durationMs,fps,null);
  // Word i sounds from i*1500 to i*1500+950 ms. A cut placed by the sound takes all of that and lands its edges in the
  // pauses, so it is not the cut the word times alone give (those cover the sound here too, with a fixed margin).
  const bySound=(cut,i,what)=>{
    const words=fromWords(i);
    assert.ok(cut&&cut.startMs<=i*1500&&cut.endMs>=i*1500+950,`${what} takes its whole sound: ${cut?.startMs}-${cut?.endMs}`);
    assert.notDeepEqual([cut.startMs,cut.endMs],[words.startMs,words.endMs],`${what} is placed by the sound, not by the word times`);
  };
  await word(3).click();await page.locator('.bar-delete').click();
  await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
  await word(40).click();await page.locator('.bar-delete').click();
  await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===2);
  let cuts=await onDisk();
  const near=cuts.find(c=>c.wordIds[0]==='W3'),far=cuts.find(c=>c.wordIds[0]==='W40');
  bySound(near,3,'word 3\'s cut');
  assert.deepEqual([far.startMs,far.endMs],[fromWords(40).startMs,fromWords(40).endMs],'word 40\'s cut follows the word times while its sound is not in');

  // The rest comes in a stretch at a time, each further right. Late in it, a Delete on a word the bars already show goes
  // by the sound too: the loudness keeps up with the bars.
  const seen=[percent];let midway=null;
  for(;;){
    const done=await page.evaluate(()=>{window.releasePauses();window.heldPauses=[];return document.querySelector('#buildWaveform').hidden;});
    if(done)break;
    await page.waitForFunction(()=>window.heldPauses?.length>0||document.querySelector('#buildWaveform').hidden);
    const text=await page.locator('#waveformStatus').innerText(),match=/^Building waveform… (\d+)%$/.exec(text);
    if(!match)continue;
    seen.push(Number(match[1]));
    if(Number(match[1])>=80&&!midway){
      await word(33).click();await page.locator('.bar-delete').click();
      await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===3);
      midway=(await onDisk()).find(c=>c.wordIds[0]==='W33');
    }
  }
  bySound(midway,33,'word 33\'s cut, at 50 s with 60 s in,');
  await page.evaluate(()=>window.releasePauses());
  assert.ok(seen.length>=3&&seen.every((n,i)=>!i||n>seen[i-1]),`it fills in from left to right: ${seen.join(', ')}%`);
  assert.equal(await page.locator('#waveformStatus').innerText(),'','the waveform is built');
  assert.equal(await barAt(45400),true,'bars at 45.4 s once it is in');
  const statuses=await page.evaluate(()=>window.statuses);
  assert.ok(!statuses.some(text=>/smaller/i.test(text)),`no "use a smaller file" message: ${JSON.stringify([...new Set(statuses)])}`);
  const rates=await page.evaluate(()=>window.decodeRates);
  assert.ok(rates.length===4&&rates.every(rate=>rate===16000),`four stretches, each decoded at the loudness rate: ${rates}`);

  // The page read the index and the sound's frames only, each in a small range, never the 3 GB.
  assert.ok(reads.length>3&&reads.every(read=>read.range&&read.status===206),`every read is a range: ${JSON.stringify(reads.slice(0,3))}`);
  const total=reads.reduce((n,read)=>n+read.bytes,0);
  assert.ok(reads.every(read=>read.bytes<=4*1024*1024)&&total<20*1024*1024,`small reads, ${(total/1024/1024).toFixed(1)} MB in all`);
  const heap=(await (await page.context().newCDPSession(page)).send('Performance.getMetrics').catch(()=>null))?.metrics?.find(m=>m.name==='JSHeapUsedSize')?.value;
  if(heap!=null)assert.ok(heap<150*1024*1024,`the page holds little: ${(heap/1024/1024).toFixed(0)} MB`);

  // With all of the sound in: click word 40 back, and its new Delete goes by the sound too.
  await word(40).click();
  await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===2);
  await word(40).click();await page.locator('.bar-delete').click();
  await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===3);
  cuts=await onDisk();const later=cuts.find(c=>c.wordIds[0]==='W40');
  bySound(later,40,'word 40\'s cut, once its sound is in,');
  // Its grey cut mark is on the waveform: the darker band above the bars.
  assert.deepEqual(await pixels((later.startMs+later.endMs)/2,18,1),[0x12,0x11,0x18,255],'the cut shows as a grey band');
  assert.deepEqual(await pixels(52400,18,1),[0,0,0,0],'and none where nothing is cut');

  // The sound read in stretches is the whole file's decode, sample for sample in time: compared on the small copy's
  // whole decode, no shift lines them up better, and the quiet and loud parts agree.
  const lineUp=await page.evaluate(async()=>{
    const m=await import('/mp4-audio.js'),{envelopeFromSamples}=await import('/cut-boundary.js'),rate=16000;
    const read=async(start,end)=>new Uint8Array(await (await fetch('/media/big',{headers:{Range:`bytes=${start}-${end-1}`}})).arrayBuffer());
    const size=Number((await fetch('/media/big',{headers:{Range:'bytes=0-0'}})).headers.get('Content-Range').split('/')[1]);
    const track=m.soundTrack(await m.readIndex(read,size)),total=Math.ceil(track.durationMs/1000*rate),out=new Float32Array(total);
    for(const [first,last] of m.stretches(track)){
      const {lead,drop,from,to}=m.placement(track,first,last,rate);
      const audio=await new OfflineAudioContext(1,1,rate).decodeAudioData((await m.adtsFile(track,lead,last,read)).buffer);
      out.set(audio.getChannelData(0).subarray(drop,drop+Math.min(to,total)-from),from);
    }
    const whole=(await new OfflineAudioContext(1,1,rate).decodeAudioData(await (await fetch('/media/small')).arrayBuffer())).getChannelData(0);
    const off=lag=>{let sum=0;for(let i=20000;i<1100000;i++)sum+=Math.abs(out[i]-whole[i+lag]);return sum;};
    const shifts=[-48,-16,-4,-1,1,4,16,48].map(lag=>off(lag)/off(0));
    const pause=async()=>{},a=await envelopeFromSamples([out],rate,{pause}),b=await envelopeFromSamples([whole.subarray(0,total)],rate,{pause});
    let differ=0;for(let i=0;i<a.db.length;i++)if((a.db[i]<a.threshold)!==(b.db[i]<b.threshold))differ++;
    return {shifts,differ,steps:a.db.length,size};
  });
  assert.ok(lineUp.size>PAD,'read from the big copy');
  assert.ok(lineUp.shifts.every(ratio=>ratio>1.5),`no shift lines them up better: ${lineUp.shifts.map(r=>r.toFixed(2))}`);
  assert.ok(lineUp.differ<lineUp.steps/1000,`quiet and loud agree on all but ${lineUp.differ} of ${lineUp.steps} steps`);

  // Online, the video is a file chosen on this computer, played from a blob: URL. The same 3 GB file, chosen that way:
  // a build stopped part way by the video changing leaves no bars and no stuck Delete, and a build to the end reads the
  // chosen file in small ranges too.
  const picker=await page.evaluateHandle(()=>{const input=Object.assign(document.createElement('input'),{type:'file',hidden:true});document.body.append(input);return input;});
  await picker.setInputFiles(big);
  await page.evaluate(()=>{
    const file=document.querySelector('input[type=file][hidden]').files[0];window.blobs=[URL.createObjectURL(file),URL.createObjectURL(file)];
    window.blobReads=[];const native=window.fetch;
    window.fetch=async(url,init)=>{
      const response=await native(url,init);
      if(String(url).startsWith('blob:'))window.blobReads.push({range:init?.headers?.Range||null,status:response.status,bytes:Number(response.headers.get('Content-Length')||0)});
      return response;
    };
  });
  const play=async i=>{
    await page.evaluate(i=>{document.querySelector('video').src=window.blobs[i];},i);
    await page.waitForFunction(i=>{const video=document.querySelector('video');return video.currentSrc===window.blobs[i]&&video.readyState>=1;},i);
  };
  await play(0);
  assert.equal(await page.locator('#buildWaveform').isVisible(),true,'a new video asks for its waveform again');
  await page.evaluate(()=>{window.heldPauses=[];});
  await page.locator('#buildWaveform').click();
  await page.waitForFunction(()=>window.heldPauses?.length>0);
  assert.match(await page.locator('#waveformStatus').innerText(),/^Building waveform… \d+%$/,'the chosen file builds a stretch at a time');
  await play(1);
  const decodesBefore=await page.evaluate(()=>window.decodeRates.length);
  await page.evaluate(()=>window.releasePauses());
  // The stopped build gets its turn (the pause it was held in, then a few more tasks) and must do nothing with it.
  await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>window.decodeRates.length),decodesBefore,'the stopped build decodes no further stretch');
  assert.equal(await page.locator('#buildWaveform').isVisible(),true,'Build waveform is still offered');
  assert.equal(await page.locator('#waveformStatus').innerText(),'','a stopped build says nothing');
  assert.equal(await page.locator('#buildWaveform').isEnabled(),true,'and Build waveform can start again');
  assert.equal(await barAt(9400),false,'its bars are gone with the video they came from');
  await word(20).click();await page.locator('.bar-delete').click();
  await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===4);
  const stopped=(await onDisk()).find(c=>c.wordIds[0]==='W20');
  assert.deepEqual([stopped.startMs,stopped.endMs],[fromWords(20).startMs,fromWords(20).endMs],'a Delete after a stopped build is not held up, and goes by the word times');
  await page.evaluate(()=>{window.blobReads=[];});
  await page.locator('#buildWaveform').click();
  await page.waitForFunction(()=>document.querySelector('#buildWaveform').hidden,null,{timeout:120000});
  assert.equal(await barAt(45400),true,'the chosen file\'s bars, all the way in');
  const blobReads=await page.evaluate(()=>window.blobReads),blobTotal=blobReads.reduce((n,read)=>n+read.bytes,0);
  assert.ok(blobReads.length>3&&blobReads.every(read=>read.range&&read.status===206&&read.bytes<=4*1024*1024),`the chosen file is read in small ranges: ${JSON.stringify(blobReads.slice(0,3))}`);
  assert.ok(blobTotal<20*1024*1024,`${(blobTotal/1024/1024).toFixed(1)} MB of the chosen file read in all`);
  const allStatuses=await page.evaluate(()=>window.statuses);
  // A 3 GB file whose sound cannot be read in stretches (here a fragmented MP4) says why and what to do instead.
  await picker.setInputFiles(fragmented);
  await page.evaluate(()=>window.blobs.push(URL.createObjectURL(document.querySelector('input[type=file][hidden]').files[0])));
  await play(2);
  await page.locator('#buildWaveform').click();
  await page.waitForFunction(()=>!document.querySelector('#buildWaveform').disabled&&document.querySelector('#waveformStatus').textContent);
  assert.match(await page.locator('#waveformStatus').innerText(),/fragmented.*Make a review copy of this video to build its waveform\./,'a file it cannot read says what to do');
  // One with no sound says only that: a review copy of it would have no sound either.
  await picker.setInputFiles(silent);
  await page.evaluate(()=>window.blobs.push(URL.createObjectURL(document.querySelector('input[type=file][hidden]').files[0])));
  await play(3);
  await page.locator('#buildWaveform').click();
  await page.waitForFunction(()=>!document.querySelector('#buildWaveform').disabled&&document.querySelector('#waveformStatus').textContent);
  assert.equal(await page.locator('#waveformStatus').innerText(),'This video has no sound track.','a video with no sound says so, and nothing more');
  assert.ok(!allStatuses.some(text=>/smaller|large/i.test(text)),`no "file too large" message: ${JSON.stringify([...new Set(allStatuses)])}`);
  assert.deepEqual(errors,[],'no page errors');
  console.log(`PASS: a 3 GB review copy builds its waveform in ${seen.length} stretches (${seen.join(', ')}%), reads ${(total/1024/1024).toFixed(1)} MB of it, cuts on the part that is in, and lines up with the whole decode (${lineUp.differ} of ${lineUp.steps} loudness steps differ); chosen as a file, it stops cleanly part way and reads ${(blobTotal/1024/1024).toFixed(1)} MB in ranges.`);
}finally{
  await browser?.close();server.kill();
  await rm(temp,{recursive:true,force:true});
}

// Isolated listening/waveform acceptance test. Creates synthetic media; never opens real reviews.
import {chromium} from 'playwright';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,mkdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname,basename} from 'node:path';
import assert from 'node:assert/strict';
const root=resolve('.'),temp=await mkdtemp(join(tmpdir(),'video-listening-'));
const python=process.env.VIDEO_REVIEW_PYTHON||join(process.env.LOCALAPPDATA,'Programs/Python/Python313/python.exe');
const media=join(temp,'listening.mp4'),durationMs=24000;
const encoded=spawnSync('ffmpeg',['-v','error','-y','-f','lavfi','-i','color=c=0x1b1f3b:s=640x360:r=30:d=24','-f','lavfi','-i','sine=frequency=330:sample_rate=48000:duration=24','-af',"volume='if(lt(mod(t,1.5),0.95),0.5,0)':eval=frame",'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-b:a','64k','-shortest',media],{windowsHide:true,encoding:'utf8'});
assert.equal(encoded.status,0,encoded.stderr);
const words=[];for(let group=0;group<16;group++)for(let n=0;n<4;n++){const startMs=Math.round(group*1500+100+n*220);words.push({id:'word-'+words.length,text:['Here','is','sentence','number'][n]+(n===3?group+'.':''),startMs,endMs:startMs+180});}
const transcript={words,cuts:[],revision:'synthetic-listening'};
await writeFile(join(temp,'manifest.json'),JSON.stringify([{id:'listening',title:'Listening test',label:'Synthetic',path:media,fps:30,durationMs,keepRanges:[[300,1020]]}]));
await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[]}));await mkdir(join(temp,'transcripts'));
await writeFile(join(temp,'transcripts/listening.json'),JSON.stringify(transcript));
const server=spawn(python,[join(root,'server.py'),'--port','8893','--data-dir',temp],{cwd:root,windowsHide:true,stdio:'ignore'});
let browser;
try{
 for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:8893/api/local')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1040}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8893');await page.waitForSelector('.transcript-word');await page.waitForFunction(()=>document.querySelector('video').readyState>=2);
 assert.equal(await page.locator('body').evaluate(n=>n.classList.contains('transcript-only')),true);
 assert.equal(await page.locator('video').isVisible(),false);
 assert.ok(await page.locator('#transcriptText').evaluate(n=>parseFloat(getComputedStyle(n).fontSize))>=28);
 async function centred(index){
  await page.locator('.transcript-word').nth(index).click();
  await page.waitForFunction(i=>document.querySelector('.transcript-word.spoken')?.dataset.index===String(i),index);
  await page.waitForFunction(()=>{const word=document.querySelector('.transcript-word.spoken').getBoundingClientRect(),body=document.querySelector('#transcriptText').getBoundingClientRect();return Math.abs(word.top+word.height/2-body.top-body.height/2)<5;});
 }
 await centred(0);await centred(30);await centred(63);
 await centred(20);const before=await page.locator('video').evaluate(v=>v.currentTime);await page.locator('#play').click();
 await page.waitForFunction(t=>document.querySelector('video').currentTime>t+.5,before);await page.locator('#play').click();
 assert.notEqual(await page.locator('#currentTime').innerText(),'0:00.000','Hidden-video audio playback advances the visible clock.');
 const transport=await page.locator('.player').boundingBox(),stage=await page.locator('.stage').boundingBox();assert.ok(Math.abs(transport.y+transport.height-stage.y-stage.height)<3,'Transport sits at the bottom of the stage.');
 await page.locator('#buildWaveform').click();await page.waitForFunction(()=>document.querySelector('#buildWaveform').hidden,{timeout:20000});
 await centred(21);await page.waitForSelector('#waveformStart:not([hidden])');
 const originalEnd=Number(await page.locator('#waveformOut').inputValue());
 await page.locator('#waveformEnd').focus();await page.keyboard.press('ArrowRight');
 const extended=Number(await page.locator('#waveformOut').inputValue());assert.ok(extended>originalEnd&&extended-originalEnd<.04);
 const expectedStart=Number(await page.locator('#waveformIn').inputValue()),expectedEnd=Number(await page.locator('#waveformOut').inputValue());
 await page.locator('#auditionWaveformCut').click();await page.waitForFunction(end=>document.querySelector('video').currentTime>=end,expectedEnd);await page.locator('video').evaluate(v=>v.pause());
 await page.locator('[data-transcript-action="remove"]').click();await page.waitForFunction(()=>!document.querySelector('#cutPreviewBar').hidden);
 let saved=JSON.parse(await readFile(join(temp,'transcripts/listening.json'),'utf8')).draftEdits;
 assert.equal(saved.length,1);assert.equal(saved[0].startMs,Math.round(expectedStart*1000));assert.equal(saved[0].endMs,Math.round(expectedEnd*1000));
 await page.locator('#undoTranscriptCut').click();await page.waitForFunction(()=>document.querySelector('#cutPreviewBar').hidden);
 await page.locator('#pauseThreshold').selectOption('1000');assert.equal(await page.locator('.transcript-pause').count(),0);
 await page.locator('#pauseThreshold').selectOption('450');assert.ok(await page.locator('.transcript-pause').count()>0);
 await page.locator('.transcript-pause').first().click();assert.equal(await page.locator('video').evaluate(v=>v.paused),true);
 const start=await page.locator('#waveformStart').boundingBox();await page.mouse.move(start.x+start.width/2,start.y+start.height/2);await page.mouse.down();await page.mouse.move(start.x+start.width/2+15,start.y+start.height/2,{steps:5});await page.mouse.up();
 await page.locator('[data-transcript-action="pause"]').click();await page.waitForFunction(()=>!document.querySelector('#cutPreviewBar').hidden);
 saved=JSON.parse(await readFile(join(temp,'transcripts/listening.json'),'utf8')).draftEdits;assert.deepEqual(saved[0].wordIds,[],'A manually adjusted pause leaves adjacent transcript words intact.');
 await page.locator('#undoTranscriptCut').click();
 await page.locator('#toggleTranscriptView').click();assert.equal(await page.locator('video').isVisible(),true);
 await page.reload();await page.waitForSelector('.transcript-word');assert.equal(await page.locator('body').evaluate(n=>n.classList.contains('transcript-only')),false);
 await page.locator('#toggleTranscriptView').click();await page.setViewportSize({width:390,height:844});await centred(30);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Listening mode has no horizontal overflow on mobile.');
 await mkdir('data/test-artifacts',{recursive:true});await page.screenshot({path:'data/test-artifacts/listening-mobile.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1040});await page.locator('#clearTranscriptSelection').click();await centred(24);await page.locator('#buildWaveform').click();await page.waitForFunction(()=>document.querySelector('#buildWaveform').hidden);await page.screenshot({path:'data/test-artifacts/listening-desktop.png'});
 const note=await page.locator('#transcriptNote').boundingBox(),panel=await page.locator('#transcriptPanel').boundingBox();assert.ok(note.y+note.height<=panel.y+panel.height+1,'Inline note remains inside the transcript panel.');
 assert.deepEqual(errors,[]);
 console.log('PASS: hidden-picture playback, centred first/middle/last words, local waveform decoding, handle and keyboard cuts, audition, pause preservation, undo, saved view and mobile layout.');
}finally{
 await browser?.close();server.kill();await new Promise(r=>server.once('exit',r));assert.equal(dirname(temp),resolve(tmpdir()));assert.ok(basename(temp).startsWith('video-listening-'));await rm(temp,{recursive:true,force:true});
}

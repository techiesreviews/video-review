// Browser check of the cut preview on a tiny made-up video: a steady tone, so joins can be measured, not heard.
// It never opens a real review. Run: node tests/cut-preview-browser.mjs
// CUT_PREVIEW_HEAVY=1 uses a busy 1080p picture, a worst case for seeking. CUT_PREVIEW_GOP=15 puts a keyframe every 15 frames.
import {chromium} from 'playwright';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,mkdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname,basename} from 'node:path';
import assert from 'node:assert/strict';

const root=resolve('.'),temp=await mkdtemp(join(tmpdir(),'video-cut-preview-')),port=8894,base=`http://127.0.0.1:${port}`;
const python=process.env.VIDEO_REVIEW_PYTHON||join(process.env.LOCALAPPDATA,'Programs/Python/Python313/python.exe');
const media=join(temp,'tone.mp4'),durationMs=8000,fps=30,FRAME=1000/fps;
const encoded=spawnSync('ffmpeg',['-v','error','-y','-f','lavfi','-i',process.env.CUT_PREVIEW_HEAVY?`testsrc2=s=1920x1080:r=${fps}:d=8,noise=alls=25:allf=t`:`color=c=0x1b1f3b:s=320x180:r=${fps}:d=8`,'-f','lavfi','-i','sine=frequency=440.25:sample_rate=48000:duration=8','-af','volume=4','-c:v','libx264','-preset','ultrafast',...(process.env.CUT_PREVIEW_GOP?['-g',process.env.CUT_PREVIEW_GOP]:[]),'-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-shortest',media],{windowsHide:true,encoding:'utf8'});
assert.equal(encoded.status,0,encoded.stderr);
const words=[];for(let i=0;i<16;i++)words.push({id:'W'+i,text:'word'+i,startMs:i*500+50,endMs:i*500+450});
const removed=[{id:'cut-a',startMs:2000,endMs:3000,wordIds:['W4','W5'],quote:'word4 word5',kind:'words'},{id:'cut-b',startMs:5000,endMs:6000,wordIds:['W10','W11'],quote:'word10 word11',kind:'words'}];
await writeFile(join(temp,'manifest.json'),JSON.stringify([{id:'tone',title:'Cut preview test',label:'Synthetic',path:media,fps,durationMs,keepRanges:[[0,240]]}]));
await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[]}));await mkdir(join(temp,'transcripts'));
await writeFile(join(temp,'transcripts/tone.json'),JSON.stringify({words,cuts:[],revision:'synthetic',draftEdits:removed}));

// Watches the page from outside: every jump the player makes, every frame shown, and the sound before and after the fade,
// recorded sample by sample on the audio thread so nothing is lost when the page is busy.
function spy(){
  const record=window.__spy={jumps:[],seeked:[],frames:[],taps:{},gain:null,context:null};
  const stamp=()=>record.context?Math.round(record.context.currentTime*record.context.sampleRate):null;
  const time=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime');
  Object.defineProperty(HTMLMediaElement.prototype,'currentTime',{get(){return time.get.call(this);},set(value){if(!this.paused)record.jumps.push({fromMs:time.get.call(this)*1000,toMs:value*1000,frame:stamp()});time.set.call(this,value);}});
  const code=`class Tap extends AudioWorkletProcessor{constructor(){super();this.samples=new Float32Array(sampleRate*40);this.port.onmessage=()=>this.port.postMessage(this.samples);}process(inputs){const channel=inputs[0][0];if(channel&&currentFrame+128<this.samples.length)this.samples.set(channel,currentFrame);return true;}}registerProcessor('tap',Tap);`;
  const connect=AudioNode.prototype.connect;
  let ready=null;
  function tap(node,name){
    const context=node.context;record.context=context;
    ready??=context.audioWorklet.addModule(URL.createObjectURL(new Blob([code],{type:'text/javascript'})));
    ready.then(()=>{const probe=new AudioWorkletNode(context,'tap',{numberOfOutputs:0});connect.call(node,probe);record.taps[name]=probe;});
  }
  AudioNode.prototype.connect=function(target,...rest){const result=connect.call(this,target,...rest);if(this instanceof MediaElementAudioSourceNode)tap(this,'before');else if(this instanceof GainNode&&target instanceof AudioDestinationNode){record.gain=this;tap(this,'after');}return result;};
  document.addEventListener('DOMContentLoaded',()=>{const video=document.querySelector('video');video.addEventListener('seeked',()=>record.seeked.push(stamp()));const frame=(now,meta)=>{if(!video.paused)record.frames.push(meta.mediaTime*1000);video.requestVideoFrameCallback(frame);};video.requestVideoFrameCallback(frame);});
  record.read=async name=>new Promise(resolve=>{const probe=record.taps[name];probe.port.onmessage=e=>resolve(e.data);probe.port.postMessage('read');});
  // For each jump: the biggest step between samples (a click), how long the sound drops out, and how much of the
  // returning sound the fade holds back (a long hold would shave the start of the next word).
  record.joins=async()=>{
    const before=await record.read('before'),after=await record.read('after'),rate=record.context.sampleRate,span=Math.round(rate*.003);
    const ms=frames=>+(frames/rate*1000).toFixed(1);
    const level=(data,at)=>{let high=0;for(let j=at;j<at+span;j++)high=Math.max(high,Math.abs(data[j]));return high;};
    const stepIn=(data,from,to)=>{let max=0;for(let i=from+1;i<to;i++)max=Math.max(max,Math.abs(data[i]-data[i-1]));return +max.toFixed(4);};
    return record.jumps.map((jump,index)=>{
      const seeked=record.seeked.find(frame=>frame>=jump.frame)??jump.frame,from=jump.frame-Math.round(rate*.05),to=seeked+Math.round(rate*.25);
      let gapStart=-1,returned=-1,restored=-1;
      for(let i=from;i<to;i+=24){const high=level(before,i);if(gapStart<0&&high<.05)gapStart=i;if(gapStart>=0&&returned<0&&high>.3)returned=i;}
      if(returned>=0)for(let i=returned;i<to;i+=24)if(level(after,i)>.4){restored=i;break;}
      return {index,seekWaitMs:ms(seeked-jump.frame),dropoutMs:gapStart>=0&&returned>=0?ms(returned-gapStart):0,heldBackMs:returned>=0&&restored>=0?ms(restored-returned):null,stepBefore:stepIn(before,from,to),stepAfter:stepIn(after,from,to)};
    });
  };
  record.reset=()=>{record.jumps.length=0;record.seeked.length=0;record.frames.length=0;};
}
const inRemoval=ms=>removed.some(r=>ms>=r.startMs&&ms<r.endMs);

const server=spawn(python,[join(root,'server.py'),'--port',String(port),'--data-dir',temp],{cwd:root,windowsHide:true,stdio:'ignore'});
let browser;
try{
  for(let i=0;i<60;i++){try{if((await fetch(base+'/api/local')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,args:['--autoplay-policy=user-gesture-required']});
  const context=await browser.newContext({viewport:{width:1440,height:1040}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error('Page error:',e.message);});
  await page.addInitScript(spy);
  await page.goto(base);await page.waitForSelector('.transcript-word');await page.waitForFunction(()=>document.querySelector('video').readyState>=2);
  const text=id=>page.locator(id).innerText(),video=fn=>page.locator('video').evaluate(fn);

  // One clock (the file's own), and one switch for the cuts. The old clock toggle, cut clock and join-fade control are gone.
  assert.equal(await text('#duration'),'0:08');
  for(const gone of ['#timeMode','#joinFade','#altTime','#altDuration','#clockLabel','#altLabel'])assert.equal(await page.locator(gone).count(),0,`${gone} is removed.`);
  const cuts=page.locator('#cutsSwitch');
  assert.equal(await cuts.getAttribute('role'),'switch');assert.equal(await cuts.getAttribute('aria-label'),'Cuts');
  assert.equal(await cuts.getAttribute('aria-checked'),'true','The cuts are on by default.');assert.equal(await text('#cutsState'),'Cuts on','The state is on the control itself.');
  assert.equal(await page.locator('#toast').evaluate(n=>n.classList.contains('show')),false,'No toast for the switch.');

  async function playThrough(label){
    await page.evaluate(()=>window.__spy.reset());await page.locator('#playCut').click();
    await page.waitForFunction(()=>document.querySelector('video').paused===false);
    await page.waitForFunction(()=>document.querySelector('video').ended,{timeout:20000});
    const data=await page.evaluate(async()=>{const r=window.__spy;await new Promise(done=>setTimeout(done,400));return {jumps:r.jumps,frames:r.frames,joins:await r.joins(),gain:r.gain.gain.value};});
    assert.equal(data.jumps.length,2,`${label}: one jump per removal.`);
    const lateness=data.jumps.map(j=>{const removal=removed.find(r=>Math.abs(r.endMs-j.toMs)<1);assert.ok(removal,`${label}: jumped to ${j.toMs}, which is not the end of a removal.`);return +(j.fromMs-removal.startMs).toFixed(1);});
    return {lateness,framesShownInRemoval:data.frames.filter(inRemoval),joins:data.joins,gain:data.gain};
  }
  const on=await playThrough('cuts on');
  console.log(JSON.stringify({cutsOn:on}));
  assert.ok(on.gain>.99,'The sound is fully back up after the last join.');
  for(const late of on.lateness)assert.ok(Math.abs(late)<=FRAME,`cuts on: the jump was ${late} ms off the join; it must stay within one frame.`);
  assert.ok(on.framesShownInRemoval.length<=4,'cuts on: at most two removed frames show at each join, before the jump lands.');
  // The tone gains a quarter cycle across each 1 s removal, so a jump without a fade is a visible step.
  const normalStep=.5*2*Math.PI*440.25/48000;
  // A busy page can leave the fade a few milliseconds short, because it never holds the jump back. Allow that, not a full click.
  const clickStep=normalStep*6;
  assert.equal(on.joins.length,2,'Both joins were measured.');
  assert.ok(on.joins.some(join=>join.stepBefore>clickStep),'The raw sound before the fade clicks at a join, so the measurement can see a click.');
  for(const join of on.joins)assert.ok(join.stepAfter<=clickStep,`The fixed join fade must keep a join from clicking: largest step ${join.stepAfter}, ordinary tone ${normalStep.toFixed(3)}.`);

  // Cuts off plays the original: the playhead runs straight through every removal, with no jump and no fade.
  await cuts.click();
  assert.equal(await cuts.getAttribute('aria-checked'),'false');assert.equal(await text('#cutsState'),'Cuts off');
  await page.evaluate(()=>{window.__spy.reset();document.querySelector('video').currentTime=0;});
  await page.waitForFunction(()=>document.querySelector('video').currentTime<.1&&!document.querySelector('video').seeking);
  await page.evaluate(()=>window.__spy.reset());
  await page.locator('#play').click();await page.waitForFunction(()=>document.querySelector('video').ended,{timeout:20000});
  const original=await page.evaluate(()=>({jumps:window.__spy.jumps.length,frames:window.__spy.frames}));
  assert.equal(original.jumps,0,'Cuts off: playback never jumps.');
  assert.ok(original.frames.filter(inRemoval).length>30,'Cuts off: the removed passages are played.');
  // Flipping it while playing takes effect at once: off, the playhead is let through a removal; on, it is skipped.
  await page.evaluate(()=>{document.querySelector('video').currentTime=1.5;});
  await page.locator('#play').click();await page.waitForFunction(()=>document.querySelector('video').currentTime>2.3);
  assert.equal(await video(v=>v.paused),false);
  await page.evaluate(()=>window.__spy.reset());
  await cuts.click();assert.equal(await text('#cutsState'),'Cuts on');
  await page.waitForFunction(()=>window.__spy.jumps.length>0);
  const skipped=await page.evaluate(()=>({jumps:window.__spy.jumps}));
  assert.equal(skipped.jumps.length,1,'Turning the cuts on inside a removal makes exactly one jump.');
  assert.ok(Math.abs(skipped.jumps[0].toMs-3000)<1,'The jump lands on the join at the end of the removal.');
  await page.keyboard.press('Escape');
  await page.locator('#play').click();await page.waitForFunction(()=>document.querySelector('video').paused);
  // Play whole cut is a request to hear the cut, so it turns the cuts on.
  await cuts.click();assert.equal(await text('#cutsState'),'Cuts off');
  await page.locator('#playCut').click();assert.equal(await text('#cutsState'),'Cuts on');assert.equal(await cuts.getAttribute('aria-checked'),'true');
  await page.waitForFunction(()=>document.querySelector('video').currentTime>.2);
  await page.locator('#play').click();await page.waitForFunction(()=>document.querySelector('video').paused);

  // Space pauses and resumes without restarting, even with focus left on the button or on a word.
  await page.locator('#playCut').click();await page.waitForFunction(()=>document.querySelector('video').currentTime>.3);
  await page.keyboard.press(' ');await page.waitForFunction(()=>document.querySelector('video').paused);
  const held=await video(v=>v.currentTime);assert.ok(held>.3,'Space pauses where it is, not back at the start.');
  await page.keyboard.press(' ');await page.waitForFunction(t=>document.querySelector('video').currentTime>t+.1,held);
  await page.keyboard.press(' ');await page.waitForFunction(()=>document.querySelector('video').paused);
  await page.locator('.transcript-word').nth(2).click();assert.equal(await video(v=>v.paused),true);
  await page.keyboard.press(' ');await page.waitForFunction(()=>!document.querySelector('video').paused);
  await page.keyboard.press(' ');await page.waitForFunction(()=>document.querySelector('video').paused);
  await page.keyboard.press('Enter');assert.equal(await page.locator('#transcriptSelection').isVisible(),true,'Enter still selects a focused word.');
  await page.locator('#clearTranscriptSelection').click();

  // The switch works from the keyboard, announces its state, remembers it, and does not animate when motion is reduced.
  await cuts.focus();await page.keyboard.press('Space');
  assert.equal(await cuts.getAttribute('aria-checked'),'false');assert.equal(await text('#cutsState'),'Cuts off');
  await page.reload();await page.waitForSelector('.transcript-word');await page.waitForFunction(()=>document.querySelector('video').readyState>=2);
  assert.equal(await cuts.getAttribute('aria-checked'),'false','The choice survives a refresh.');
  await cuts.focus();await page.keyboard.press('Enter');
  assert.equal(await cuts.getAttribute('aria-checked'),'true');assert.equal(await text('#cutsState'),'Cuts on');
  assert.equal(await cuts.evaluate(n=>getComputedStyle(n).outlineStyle!=='none'||getComputedStyle(n).boxShadow!=='none'),true,'Keyboard focus is visible.');
  assert.match(await page.locator('.switch-track').evaluate(n=>getComputedStyle(n,'::after').transitionDuration),/0\.15s/,'With no preference, the switch slides.');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.switch-track').evaluate(n=>getComputedStyle(n,'::after').transitionDuration),'0s','Reduced motion: the switch jumps rather than slides.');
  await page.emulateMedia({reducedMotion:'no-preference'});

  // The bar opens next to the selected words: just below or above them, never on them, and inside the transcript.
  async function nextToWords(label,anchor=null){
    await page.waitForTimeout(50);
    const bar=await page.locator('#transcriptSelection').boundingBox(),area=await page.locator('#transcriptText').boundingBox();
    const words=await page.locator(anchor??'#transcriptText .selected').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};}));
    assert.ok(words.length,`${label}: words are selected`);
    const top=Math.min(...words.map(r=>r.top)),bottom=Math.max(...words.map(r=>r.bottom));
    const below=bar.y-bottom,above=top-(bar.y+bar.height);
    assert.ok((below>=0&&below<=80)||(above>=0&&above<=80),`${label}: the bar is right next to the words (gap below ${below}, above ${above}).`);
    assert.ok(bar.y>=area.y-1&&bar.y+bar.height<=await page.evaluate(()=>innerHeight)+1,`${label}: the bar never goes above the transcript or off screen.`);
    assert.ok(bar.x>=0&&bar.x+bar.width<=await page.evaluate(()=>innerWidth)+1,`${label}: the bar stays on screen.`);
  }

  // Ask agent on a selected word.
  await page.locator('.transcript-word').nth(1).click();
  await page.locator('.transcript-word').nth(2).click({modifiers:['Shift']});
  assert.equal(await page.evaluate(()=>document.activeElement.id),'transcriptNote','Selecting words puts the cursor in the edit bar.');
  await nextToWords('a Shift-click range');
  await page.keyboard.type('Tighten this and keep the laugh');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('No agent connected'));
  assert.match(await text('#barStatus'),/saved and waits for it/,'With no agent attached the bar says so and keeps the note.');
  assert.equal(await page.locator('#transcriptNote').inputValue(),'');
  const askCard=page.locator('.comment',{hasText:'Tighten this and keep the laugh'});await askCard.waitFor();
  assert.equal(await askCard.locator('.ask-badge').innerText(),'For agent');
  assert.match(await askCard.locator('.ask-where').innerText(),/word1 word2/);
  assert.match(await askCard.locator('.ts').innerText(),/^0:00 – 0:01$/,'The comment time follows the chosen clock.');
  let notes=JSON.parse(await readFile(join(temp,'comments.json'),'utf8')).comments,saved=notes.find(n=>n.text.startsWith('Tighten'));
  assert.equal(saved.editRequest.type,'change');assert.deepEqual(saved.editRequest.wordIds,['W1','W2']);assert.equal(saved.editRequest.quote,'word1 word2');
  assert.equal(saved.editRequest.startFrame,Math.round(saved.startMs*fps/1000));assert.equal(saved.editRequest.endFrame,Math.round(saved.endMs*fps/1000));
  assert.ok(saved.endMs>saved.startMs);

  // Ask agent on a frame, from the comment box.
  await page.locator('#clearTranscriptSelection').click();
  await page.locator('#seek').evaluate(n=>{n.value=1.25;n.dispatchEvent(new Event('input',{bubbles:true}));});
  await page.locator('#noteText').fill('Hold on this frame');await page.locator('#askAgentNote').click();
  await page.locator('.comment',{hasText:'Hold on this frame'}).waitFor();
  notes=JSON.parse(await readFile(join(temp,'comments.json'),'utf8')).comments;saved=notes.find(n=>n.text==='Hold on this frame');
  assert.equal(saved.editRequest.type,'change');assert.equal(saved.startMs,1250);assert.equal(saved.editRequest.startFrame,38);assert.equal(saved.editRequest.endFrame,null);
  assert.deepEqual(saved.editRequest.wordIds,['W2'],'The frame picks up the word being spoken at that moment.');
  assert.equal(await page.locator('.comment',{hasText:'Hold on this frame'}).locator('.ask-badge').count(),1);

  // Ordinary comments stay ordinary.
  await page.locator('#noteText').fill('Plain note');await page.locator('#saveNote').click();await page.locator('.comment',{hasText:'Plain note'}).waitFor();
  notes=JSON.parse(await readFile(join(temp,'comments.json'),'utf8')).comments;assert.equal(notes.find(n=>n.text==='Plain note').editRequest,undefined);

  // The edit bar: one box, focused at once, Delete first; Tab completes; an empty box turns Backspace into a cut.
  await page.locator('.transcript-word').nth(8).click();
  assert.equal(await page.evaluate(()=>document.activeElement.id),'transcriptNote');
  assert.equal((await page.locator('#transcriptSelection button:visible').first().innerText()).trim(),'Delete','Delete is the first button.');
  assert.deepEqual(await page.locator('#transcriptSelection button:visible').allInnerTexts(),['Delete','×'],'Only Delete and the close x: the quick buttons are gone.');
  await nextToWords('a click on one word');
  assert.equal(await page.locator('#barTab').isVisible(),false,'The Tab key shows only while there is a suggestion.');
  const before=await page.locator('#transcriptSelection').boundingBox(),boxBefore=await page.locator('#transcriptNote').boundingBox();
  await page.keyboard.type('Tigh');
  assert.ok((await page.locator('#transcriptGhost').textContent()).endsWith('ten this and keep the laugh'),'A grey suggestion appears, from the note sent a moment ago.');
  assert.equal(await page.locator('#barTab').isVisible(),true,'The Tab key shows on the box while a suggestion is there.');
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('#transcriptNote').inputValue(),'Tighten this and keep the laugh','Tab takes the suggestion.');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'transcriptNote','Tab does not leave the box when it completes.');
  const after=await page.locator('#transcriptSelection').boundingBox(),boxAfter=await page.locator('#transcriptNote').boundingBox();
  assert.deepEqual([after.height,boxAfter.width],[before.height,boxBefore.width],'Completing text does not move or grow anything.');
  const editsOnDisk=async()=>JSON.parse(await readFile(join(temp,'transcripts/tone.json'),'utf8')).draftEdits;
  const edits=(await editsOnDisk()).length;
  await page.keyboard.press('Backspace');assert.equal(await page.locator('#transcriptNote').inputValue(),'Tighten this and keep the laug');
  assert.equal((await editsOnDisk()).length,edits,'Backspace in text only edits the text.');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#transcriptNote').inputValue(),'');assert.equal(await page.locator('#transcriptSelection').isVisible(),true,'First Escape clears the text.');
  await writeFile(join(temp,'agent-seen.json'),JSON.stringify({pid:1,at:new Date().toISOString()}));
  await page.keyboard.type('Please make this quicker');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('Sent to your agent'));
  await page.keyboard.press('Backspace');await page.waitForTimeout(150);
  assert.equal((await editsOnDisk()).length,edits+1,'Backspace in the empty box removes the selected words from the cut.');
  assert.equal(await page.locator('.transcript-word.selected').first().innerText(),'word9','The selection moves on to the next words.');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'transcriptNote');
  await page.keyboard.press('Control+z');await page.waitForFunction(async()=>true);await page.waitForTimeout(250);
  assert.equal((await editsOnDisk()).length,edits,'Ctrl+Z in the empty box undoes the cut.');
  assert.equal(await page.locator('.transcript-word.selected').first().innerText(),'word8','Ctrl+Z selects the words that came back.');
  assert.match(await text('#barStatus'),/Brought back/);
  await page.keyboard.press('Delete');await page.waitForTimeout(250);
  assert.equal((await editsOnDisk()).length,edits+1,'Delete in the empty box removes the selected words too.');
  await page.keyboard.press('Control+z');await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,edits);
  // Nothing typed is lost: a note typed before clicking Delete is saved with the cut, and comes back with Ctrl+Z.
  const notesOnDisk=async()=>JSON.parse(await readFile(join(temp,'comments.json'),'utf8')).comments;
  await page.keyboard.type('Unsent idea');await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  assert.equal((await editsOnDisk()).length,edits+1);
  assert.ok((await notesOnDisk()).at(-1).text.endsWith(' — Unsent idea'),'A note typed before clicking Delete is saved with the cut.');
  assert.equal(await page.locator('#transcriptNote').inputValue(),'','The note does not move on to the next words.');
  await page.keyboard.press('Control+z');await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,edits);
  assert.equal(await page.locator('#transcriptNote').inputValue(),'Unsent idea','Ctrl+Z brings the note back with the words.');
  await page.keyboard.press('Escape');
  await page.locator('.transcript-word').nth(15).click();await page.keyboard.type('end note');await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  assert.ok((await notesOnDisk()).at(-1).text.endsWith(' — end note'),'A note on the last words is kept even with nothing after them to select.');
  await page.locator('#undoTranscriptCut').click();await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,edits);
  await page.keyboard.press('Escape');await page.keyboard.press('Escape');await page.locator('.transcript-word').nth(8).click();
  await page.route('**/api/transcript/**',async route=>{if(route.request().method()==='POST')await new Promise(r=>setTimeout(r,300));await route.continue();});
  await page.keyboard.press('Backspace');await page.keyboard.type('x');await page.waitForTimeout(600);
  assert.equal((await editsOnDisk()).length,edits+1);
  assert.equal(await page.locator('#transcriptNote').inputValue(),'x','Letters typed while a cut saves stay in the box.');
  await page.unroute('**/api/transcript/**');
  await page.keyboard.press('Escape');await page.keyboard.press('Control+z');await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,edits);
  // Undo cut while a note for other words is half typed: the note and its words stay as they are.
  await page.locator('.transcript-word').nth(8).click();await page.keyboard.type('cut note');await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,edits+1);
  await page.locator('.transcript-word').nth(12).click();await page.keyboard.type('other words');await page.locator('#undoTranscriptCut').click();await page.waitForTimeout(250);
  assert.equal((await editsOnDisk()).length,edits);
  assert.equal(await page.locator('#transcriptNote').inputValue(),'other words','Undo does not move a half-typed note onto the words it brings back.');
  assert.equal(await page.locator('.transcript-word.selected').first().innerText(),'word12');
  assert.equal(await text('#barStatus'),'Brought back. Its note is under Resolved.','The bar says the cut came back and where its note is.');
  assert.equal((await notesOnDisk()).find(n=>n.text.endsWith(' — cut note')).status,'done');
  // Shift+Enter does not send, and a suggestion too long to see in the box is not offered.
  await page.locator('#transcriptNote').focus();let count2=(await notesOnDisk()).length;await page.keyboard.press('Shift+Enter');await page.waitForTimeout(200);
  assert.equal((await notesOnDisk()).length,count2,'Shift+Enter does not send a half-written note.');
  await page.keyboard.press('Escape');
  const long='Please keep the whole sentence here because the punchline only lands when the setup before it is complete and unhurried okay';
  await page.keyboard.type(long);await page.keyboard.press('Enter');await page.waitForTimeout(400);
  assert.ok((await notesOnDisk()).some(n=>n.text===long));
  await page.keyboard.type(long.slice(0,30));
  assert.equal(await page.locator('#transcriptNote').getAttribute('data-suggestion'),'','A suggestion that does not fit in the box is not offered.');
  assert.equal(await page.locator('#transcriptGhost').textContent(),'');
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('#transcriptNote').inputValue(),long.slice(0,30),'Tab does not accept it unseen.');
  await page.locator('#transcriptNote').focus();await page.keyboard.press('Escape');await page.keyboard.type('Tigh');
  assert.equal(await page.locator('#transcriptNote').getAttribute('data-suggestion'),'Tighten this and keep the laugh','A short suggestion that fits is still offered.');
  await page.keyboard.press('Escape');
  await page.locator('.transcript-word').nth(8).click();
  await page.keyboard.press('Escape');assert.equal(await page.locator('#transcriptSelection').isVisible(),false,'Escape in an empty box clears the selection.');
  await page.locator('.transcript-word').nth(8).click();await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  assert.equal((await editsOnDisk()).length,edits+1,'The Delete button does the same.');
  await page.locator('#undoTranscriptCut').click();await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,edits);
  // Deleted words stay in the transcript, struck through, with a Restore mark; the cut still skips them.
  assert.equal(await page.locator('.transcript-word.removed-word:visible').count(),4,'The words of both cuts are still shown.');
  assert.deepEqual(await page.locator('.transcript-word.removed-word').allInnerTexts(),['word4','word5','word10','word11']);
  assert.match(await page.locator('.transcript-word.removed-word').first().evaluate(n=>getComputedStyle(n).textDecorationLine),/line-through/,'Deleted words are struck through.');
  assert.equal(await page.locator('.transcript-word').nth(4).getAttribute('aria-label'),'Deleted: word4, 0:02.050','A screen reader hears that the word is deleted.');
  assert.equal(await page.locator('.restore-cut:visible').count(),2,'Each cut has its Restore mark.');
  assert.match(await text('#cutPreviewStatus'),/Preview cut · 0:06\.000 · 2\.0 s removed$/,'The cut still skips the deleted words.');
  // Selecting deleted words turns Delete into Restore; Backspace never cuts them twice.
  await page.locator('.transcript-word').nth(4).click();await nextToWords('a deleted word');
  assert.equal((await page.locator('#transcriptSelection button:visible').first().innerText()).trim(),'Restore','On deleted words the first button brings them back.');
  await page.keyboard.press('Backspace');await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,edits,'Backspace does not cut deleted words again.');
  await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  assert.deepEqual((await editsOnDisk()).map(e=>e.id),['cut-b'],'Restore brings back only that cut.');
  assert.equal(await page.locator('.transcript-word').nth(4).evaluate(n=>n.classList.contains('removed-word')),false);
  assert.equal(await page.locator('.transcript-word.selected').first().innerText(),'word4','The words stay selected.');
  assert.equal((await page.locator('#transcriptSelection button:visible').first().innerText()).trim(),'Delete','and the button is Delete again.');
  assert.equal(await text('#barStatus'),'Brought back. Backspace deletes it again.','Restore says the same as Undo.');assert.equal(await page.evaluate(()=>document.activeElement.id),'transcriptNote');
  await page.locator('.transcript-word').nth(5).click({modifiers:['Shift']});await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  assert.equal((await editsOnDisk()).length,edits,'Delete cuts them again.');
  await page.keyboard.press('Escape');
  const cutA=(await editsOnDisk()).find(e=>e.wordIds.includes('W4')).id;
  await page.locator(`.restore-cut[data-edit-id="${cutA}"]`).click();await page.waitForTimeout(250);
  assert.deepEqual((await editsOnDisk()).map(e=>e.id),['cut-b'],'One click on a Restore mark brings that cut back.');
  await page.keyboard.press('Control+z');await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,0,'Ctrl+Z undoes the last cut that is left.');
  assert.equal(await page.locator('.transcript-word.removed-word').count(),0);
  // Delete over kept and deleted words cuts only the kept ones, so each word stays in one cut.
  await page.keyboard.press('Escape');await page.locator('.transcript-word').nth(5).click();await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  await page.locator('.transcript-word').nth(3).click();await page.locator('.transcript-word').nth(7).click({modifiers:['Shift']});await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  let mixed=await editsOnDisk();
  assert.deepEqual(mixed.map(e=>e.wordIds),[['W5'],['W3','W4'],['W6','W7']],'Two pieces, around the word already deleted.');
  assert.equal(mixed[1].commentId,mixed[2].commentId,'The pieces share one note.');
  const pieceNote=async()=>(await notesOnDisk()).find(n=>n.id===mixed[1].commentId);
  assert.deepEqual((({quote,firstWordId,lastWordId})=>({quote,firstWordId,lastWordId}))((await pieceNote()).editRequest),{quote:'word3 word4 … word6 word7',firstWordId:'W3',lastWordId:'W7'},'The note names only the cut words.');
  await page.keyboard.press('Escape');await page.locator(`.restore-cut[data-edit-id="${mixed[2].id}"]`).click();await page.waitForTimeout(250);
  assert.deepEqual((await editsOnDisk()).map(e=>e.id),[mixed[0].id,mixed[1].id],'One piece comes back on its own.');
  assert.equal((await pieceNote()).status,'open','Its note stays open while the other piece is still cut.');
  await page.locator(`.restore-cut[data-edit-id="${mixed[1].id}"]`).click();await page.waitForTimeout(250);
  assert.equal((await pieceNote()).status,'done','Once the last piece is back the note is resolved.');
  await page.locator('.transcript-word').nth(5).click();await page.locator('.transcript-word').nth(7).click({modifiers:['Shift']});await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  let fromDeleted=await editsOnDisk();assert.deepEqual(fromDeleted.at(-1).wordIds,['W6','W7'],'Starting on a deleted word: only the kept words are cut.');
  assert.equal((await notesOnDisk()).find(n=>n.id===fromDeleted.at(-1).commentId).startMs,fromDeleted.at(-1).startMs,'and the note starts where the cut starts.');
  await page.locator('#transcriptText').focus();await page.keyboard.press('Control+z');await page.waitForTimeout(250);assert.equal((await editsOnDisk()).length,1);
  await page.locator('.transcript-word').nth(3).click();await page.locator('.transcript-word').nth(7).click({modifiers:['Shift']});await page.locator('[data-transcript-action="remove"]').click();await page.waitForTimeout(250);
  mixed=await editsOnDisk();assert.equal(mixed.length,3);
  await page.locator(`.restore-cut[data-edit-id="${mixed[0].id}"]`).click();await page.waitForTimeout(250);
  assert.equal(await page.locator('.transcript-word').nth(5).evaluate(n=>n.classList.contains('removed-word')),false,'Restoring the earlier cut really brings its word back.');
  await page.keyboard.press('Escape');await page.locator('#transcriptText').focus();await page.keyboard.press('Control+z');await page.waitForTimeout(250);
  assert.equal((await editsOnDisk()).length,0,'Ctrl+Z brings back both pieces of that Delete at once.');
  // The bar also opens next to a pause, and follows the words when the transcript scrolls.
  await page.keyboard.press('Escape');
  await writeFile(join(temp,'transcripts/tone.json'),JSON.stringify({words,cuts:[],revision:'synthetic',draftEdits:removed}));
  await page.reload();await page.waitForSelector('.transcript-word');await page.waitForFunction(()=>document.querySelector('video').readyState>=2);
  await page.locator('.transcript-word').nth(15).click();await nextToWords('a word, before scrolling');
  await page.locator('#transcriptText').evaluate(n=>{n.style.paddingBottom='600px';n.scrollTop+=40;});await page.waitForTimeout(100);await nextToWords('a word, after scrolling');
  await page.locator('#transcriptText').evaluate(n=>{n.style.paddingBottom='';});await page.keyboard.press('Escape');
  // Screenshots for the PR: desktop, light and dark (the app has one light theme, so dark must not break it).
  await mkdir('data/test-artifacts',{recursive:true});
  for(const scheme of ['light','dark']){
    await page.emulateMedia({colorScheme:scheme});await page.locator('.transcript-word').nth(7).click();await page.keyboard.type('Tigh');await nextToWords(`desktop ${scheme}`);
    await page.screenshot({path:`data/test-artifacts/edit-bar-desktop-${scheme}.png`});await page.keyboard.press('Escape');await page.keyboard.press('Escape');
  }
  await page.emulateMedia({colorScheme:'light'});
  await rm(join(temp,'agent-seen.json'),{force:true});

  // Everything removed: nothing to play, and the page says so.
  await writeFile(join(temp,'transcripts/tone.json'),JSON.stringify({words,cuts:[],revision:'all-gone',draftEdits:[{id:'all',startMs:0,endMs:8000,wordIds:words.map(w=>w.id),quote:'all',kind:'words'}]}));
  await page.reload();await page.waitForSelector('.transcript-word',{state:'attached'});await page.waitForFunction(()=>document.querySelector('#cutPreviewStatus').textContent.includes('0:00.000 · 8.0 s removed'));
  await page.locator('#playCut').click();await page.waitForFunction(()=>document.querySelector('#toastText').textContent.includes('no cut to play'));
  assert.equal(await video(v=>v.paused),true);
  await writeFile(join(temp,'transcripts/tone.json'),JSON.stringify({words,cuts:[],revision:'synthetic',draftEdits:removed}));
  await page.reload();await page.waitForSelector('.transcript-word');

  // Evidence of the switch in both states, at desktop and phone width.
  await mkdir('data/test-artifacts',{recursive:true});
  async function switchShots(size){
    for(const [state,label] of [['true','on'],['false','off']]){
      if(await cuts.getAttribute('aria-checked')!==state)await cuts.click();
      await page.waitForTimeout(300);await page.locator('#player').screenshot({path:`data/test-artifacts/cuts-switch-${size}-${label}.png`});
    }
    if(await cuts.getAttribute('aria-checked')!=='true')await cuts.click();
  }
  await switchShots('desktop');
  // The new controls fit a phone without sideways scrolling.
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(100);await switchShots('phone');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No sideways scrolling on a phone.');
  await page.locator('.transcript-word').nth(1).click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'The edit bar fits a phone without sideways scrolling.');
  await nextToWords('phone');
  await mkdir('data/test-artifacts',{recursive:true});await page.screenshot({path:'data/test-artifacts/cut-preview-mobile.png',fullPage:true});
  for(const scheme of ['light','dark']){
    await page.emulateMedia({colorScheme:scheme});await page.locator('.transcript-word').nth(4).click();await nextToWords(`phone ${scheme}`);
    await page.screenshot({path:`data/test-artifacts/edit-bar-phone-${scheme}.png`});
  }
  await page.emulateMedia({colorScheme:'light'});
  await page.setViewportSize({width:1440,height:1040});await page.locator('.transcript-word').nth(1).click();await page.keyboard.type('Tigh');await page.screenshot({path:'data/test-artifacts/cut-preview-desktop.png'});
  assert.deepEqual(errors,[]);
  console.log('PASS: play whole cut, the one cuts switch (on plays the cut, off plays the original, mouse, Space, Enter, remembered, reduced motion), the fixed join fade, one clock, ask agent on a word and a frame, the edit bar (next to the words, autofocus, Tab, Backspace, Delete, Ctrl+Z, no-agent line, no typed text lost, deleted words shown and restored), all-removed cut, phone layout.');
}finally{
  await browser?.close();server.kill();await new Promise(r=>server.once('exit',r));assert.equal(dirname(temp),resolve(tmpdir()));assert.ok(basename(temp).startsWith('video-cut-preview-'));await rm(temp,{recursive:true,force:true});
}

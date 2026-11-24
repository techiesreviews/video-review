// Real preview, synthetic tone only. Audio failures must leave playback audible and mark the Cuts control.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
await mkdir('.scratch',{recursive:true});
const temp=await mkdtemp(resolve('.scratch/join-fade-browser-'));
const port=Number(process.env.JOIN_FADE_PORT||8899),base=`http://127.0.0.1:${port}`;
const media=join(temp,'tone.wav'),rate=48000,frames=rate*8,wav=Buffer.alloc(44+frames*2);
wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);
wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(rate,24);wav.writeUInt32LE(rate*2,28);
wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(frames*2,40);
for(let i=0;i<frames;i++)wav.writeInt16LE(Math.round(Math.sin(i*2*Math.PI*440/rate)*12000),44+i*2);
await writeFile(media,wav);
await writeFile(join(temp,'manifest.json'),JSON.stringify([{id:'tone',title:'Fade fixture',path:media,fps:30,durationMs:8000}]));
await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[]}));
await mkdir(join(temp,'transcripts'));
await writeFile(join(temp,'transcripts/tone.json'),JSON.stringify({revision:'synthetic',words:[
  {id:'W1',text:'Keep',startMs:0,endMs:500},{id:'W2',text:'Remove',startMs:1000,endMs:2000},
  {id:'W3',text:'Keep',startMs:2000,endMs:2500},{id:'W4',text:'Remove',startMs:3000,endMs:4000},
],draftEdits:[
  {id:'a',kind:'words',startMs:1000,endMs:2000,wordIds:['W2'],quote:'Remove'},
  {id:'b',kind:'words',startMs:3000,endMs:4000,wordIds:['W4'],quote:'Remove'},
]}));
const server=spawn(process.env.VIDEO_REVIEW_PYTHON||'python3',['server.py','--port',String(port),'--data-dir',temp],{stdio:'ignore'});
let browser;
try{
  let ready=false;
  for(let i=0;i<80;i++){try{if((await fetch(base+'/api/local')).ok){ready=true;break;}}catch{}await new Promise(resolve=>setTimeout(resolve,100));}
  assert.ok(ready,'Synthetic server starts');
  browser=await chromium.launch({headless:true});
  for(const failure of ['missing','constructor','resume','source','gain','gain connection','source connection','available'])for(const width of failure==='missing'?[1440,390]:[1440]){
    const context=await browser.newContext({viewport:{width,height:960}}),page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await context.addInitScript(failure=>{
      window.__NativeAudioContext=window.AudioContext;
      if(failure==='missing'){window.AudioContext=undefined;window.webkitAudioContext=undefined;}
      else if(failure==='constructor')window.AudioContext=class {constructor(){throw Error('Unavailable');}};
      else if(failure==='resume')window.AudioContext=class {state='suspended';resume(){return Promise.reject(Error('Denied'));}};
      else{
        const prototype=window.AudioContext.prototype,connect=AudioNode.prototype.connect,createSource=prototype.createMediaElementSource;
        prototype.createMediaElementSource=function(video){const source=createSource.call(this,video);window.__sourceCaptured=true;return source;};
        if(failure==='source')prototype.createMediaElementSource=()=>{throw Error('Source unavailable');};
        if(failure==='gain')prototype.createGain=()=>{throw Error('Gain unavailable');};
        AudioNode.prototype.connect=function(target,...rest){
          if(failure==='gain connection'&&this instanceof GainNode)throw Error('Gain route unavailable');
          if(failure==='source connection'&&this instanceof MediaElementAudioSourceNode&&target instanceof GainNode)throw Error('Source route unavailable');
          const result=connect.call(this,target,...rest);
          if(target instanceof AudioDestinationNode){
            const analyser=this.context.createAnalyser();connect.call(this,analyser);
            window.__outputAnalyser=analyser;
          }
          return result;
        };
      }
      window.WebSocket=class {readyState=0;};
    },failure);
    await page.goto(base);await page.waitForFunction(()=>document.querySelector('video').readyState>=2);
    await page.locator('#play').click();
    await page.waitForFunction(()=>document.querySelector('video').currentTime>.1);
    if(failure==='available')assert.equal(await page.locator('#cutsSwitch').innerText(),'Cuts on');
    else assert.match(await page.locator('#cutsSwitch').innerText(),/Fades unavailable/);
    assert.equal(await page.locator('#cutsSwitch').getAttribute('aria-describedby'),'cutsFade');
    assert.equal(await page.locator('#cutsFade').getAttribute('aria-live'),'polite');
    if(width===390){
      assert.ok(await page.locator('#cutsFade').evaluate(node=>node.getClientRects().length===1),'The phone notice stays on one line');
      assert.ok(await page.locator('#cutsFade').evaluate(node=>Math.abs(node.getBoundingClientRect().top-document.querySelector('#cutsState').getBoundingClientRect().top)<=1),'The phone notice stays beside the Cuts label');
      assert.ok(await page.locator('#cutsSwitch').evaluate(node=>node.scrollHeight<=node.clientHeight),'Text does not spill out of the phone control');
    }
    const sound=await page.evaluate(async()=>{
      const video=document.querySelector('video');
      let context,analyser=window.__sourceCaptured?window.__outputAnalyser:null;
      if(!analyser){
        context=new window.__NativeAudioContext();analyser=context.createAnalyser();
        context.createMediaStreamSource(video.captureStream()).connect(analyser);await context.resume();
      }
      await new Promise(resolve=>setTimeout(resolve,150));
      const pcm=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(pcm);
      const rms=Math.sqrt(pcm.reduce((sum,value)=>sum+value*value,0)/pcm.length);
      await context?.close();return {rms,paused:video.paused,muted:video.muted,volume:video.volume};
    });
    assert.ok(sound.rms>.2,`${failure}: playback remains audible: ${JSON.stringify(sound)}`);
    assert.equal(sound.paused,false);assert.equal(sound.muted,false);assert.equal(sound.volume,1);
    await page.locator('#cutsSwitch').click();
    assert.equal(await page.locator('#cutsSwitch').innerText(),'Cuts off');
    await page.locator('#cutsSwitch').click();
    if(failure==='available')assert.equal(await page.locator('#cutsSwitch').innerText(),'Cuts on');
    else assert.match(await page.locator('#cutsSwitch').innerText(),/Fades unavailable/);
    if(failure==='missing'&&width===1440){
      await page.evaluate(()=>{
        window.__seeks=[];window.__times=[];
        const video=document.querySelector('video');
        video.addEventListener('seeking',()=>window.__seeks.push(video.currentTime));
        video.addEventListener('timeupdate',()=>window.__times.push(video.currentTime));
      });
      await page.waitForFunction(()=>document.querySelector('video').currentTime>4.2,{},{timeout:7000});
      const seeks=await page.evaluate(()=>window.__seeks);
      for(const end of [2,4])assert.ok(seeks.some(time=>time>=end&&time<end+.1),`Cuts on still skips to ${end}s without fades`);
      await page.locator('#cutsSwitch').click();
      await page.evaluate(()=>{document.querySelector('video').currentTime=.5;window.__times=[];});
      await page.waitForFunction(()=>document.querySelector('video').currentTime>4.2,{},{timeout:7000});
      const times=await page.evaluate(()=>window.__times);
      for(const start of [1,3])assert.ok(times.some(time=>time>start&&time<start+1),`Cuts off plays through ${start}–${start+1}s`);
      await page.locator('#cutsSwitch').click();
    }
    if(process.env.JOIN_FADE_SHOTS&&failure==='missing'){await mkdir(process.env.JOIN_FADE_SHOTS,{recursive:true});await page.screenshot({path:join(process.env.JOIN_FADE_SHOTS,`fades-unavailable-${width}.png`),fullPage:true});}
    assert.deepEqual(errors,[]);
    await context.close();console.log(`PASS ${failure} ${width}px: correct Cuts state, audible playback RMS ${sound.rms.toFixed(3)}, switch still works`);
  }
  console.log('RESULT: ok join-fade-browser 7 audio failures, available control, desktop and phone');
}finally{
  await browser?.close();const exited=new Promise(resolve=>server.once('exit',resolve));server.kill();await exited;
  await rm(temp,{recursive:true,force:true});
}

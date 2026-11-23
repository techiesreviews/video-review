// Isolated startup failures and Retry, using the real local server and synthetic libraries only.
// Needs Playwright. BACKEND_SHOTS saves desktop/phone screenshots in both colour schemes.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=resolve('.');await mkdir('.scratch',{recursive:true});
const temp=await mkdtemp(resolve('.scratch/backend-browser-'));
const port=Number(process.env.BACKEND_PORT||8897),base=`http://127.0.0.1:${port}`;
const shots=process.env.BACKEND_SHOTS;
const media=join(temp,'local.wav'),sampleRate=8000,frames=sampleRate*3,wav=Buffer.alloc(44+frames*2);
wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);
wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(sampleRate,24);wav.writeUInt32LE(sampleRate*2,28);
wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(frames*2,40);
await writeFile(media,wav);
await writeFile(join(temp,'manifest.json'),JSON.stringify([{id:'local-only',title:'Local fixture',label:'Local cut',path:media,fps:30,durationMs:3000}]));
await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[{id:'local-note',videoId:'local-only',author:'you',text:'Local note retained',startMs:0,endMs:null,status:'open',createdAt:'2026-01-01T00:00:00Z',replies:[]}]}));
await mkdir(join(temp,'transcripts'));
await writeFile(join(temp,'transcripts/local-only.json'),JSON.stringify({revision:'original',words:[{id:'W1',text:'Local words retained',startMs:0,endMs:500}]}));
const originals=await Promise.all(['manifest.json','comments.json','transcripts/local-only.json'].map(async name=>[name,await readFile(join(temp,name),'utf8')]));
const server=spawn(process.env.VIDEO_REVIEW_PYTHON||'python3',[join(root,'server.py'),'--port',String(port),'--data-dir',temp],{cwd:root,stdio:'ignore'});
let browser;
try{
  let ready=false;
  for(let i=0;i<80;i++){try{if((await fetch(base+'/api/local')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}
  assert.ok(ready,'synthetic local server started');
  browser=await chromium.launch({headless:true});
  if(shots)await mkdir(shots,{recursive:true});
  for(const width of [1440,390])for(const colorScheme of ['light','dark']){
    for(const populated of [false,true])for(const failure of ['network','503','json','404','browser']){
      const context=await browser.newContext({viewport:{width,height:width===390?844:960},colorScheme});
      await context.addInitScript(({populated})=>{
        // A made-up browser library at this origin; never connect a relay or an agent.
        window.WebSocket=class {readyState=0;};
        const seed=new Promise((resolve,reject)=>{
          const request=indexedDB.open('video-review',1);
          request.onupgradeneeded=()=>request.result.createObjectStore('kv');
          request.onsuccess=()=>{
            const db=request.result,tx=db.transaction('kv','readwrite');
            const store=tx.objectStore('kv'),existing=store.get('state');
            existing.onsuccess=()=>{if(existing.result===undefined)store.put({videos:populated?[{id:'browser-only',title:'Browser fixture',label:'Browser cut',durationMs:3000,fps:30}]:[],comments:[],transcripts:{}},'state');};
            tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>reject(tx.error);
          };request.onerror=()=>reject(request.error);
        });
        const fetchOriginal=window.fetch;
        window.fetch=async(...args)=>{await seed;return fetchOriginal(...args);};
      },{populated});
      const page=await context.newPage();let recovered=false;
      await page.route('**/api/local',route=>{
        if(recovered)return route.continue();
        if(failure==='network')return route.abort('failed');
        return route.fulfill({status:failure==='404'?404:failure==='503'?503:200,contentType:'application/json',body:failure==='browser'?'{"local":false}':failure==='json'?'invalid JSON':'{}'});
      });
      await page.goto(base);
      const browserState=()=>page.evaluate(()=>new Promise((resolve,reject)=>{
        const request=indexedDB.open('video-review',1);
        request.onsuccess=()=>{
          const db=request.result,tx=db.transaction('kv','readonly'),get=tx.objectStore('kv').get('state');
          get.onsuccess=()=>resolve(get.result);tx.oncomplete=()=>db.close();tx.onerror=()=>reject(tx.error);
        };request.onerror=()=>reject(request.error);
      }));
      if(failure==='404'||failure==='browser'){
        await page.waitForFunction(()=>document.body.dataset.mode==='browser');
        if(populated)await page.waitForFunction(()=>document.querySelector('#videoTitle').textContent==='Browser fixture');
        assert.equal(await page.locator('#addVideo').isVisible(),true);
        assert.notEqual(await page.locator('#gateTitle').innerText(),'Review library unavailable');
        assert.deepEqual(await browserState(),{videos:populated?[{id:'browser-only',title:'Browser fixture',label:'Browser cut',durationMs:3000,fps:30}]:[],comments:[],transcripts:{}});
        console.log(`PASS ${width} ${colorScheme} ${failure} browser=${populated?'existing':'empty'}: intended browser fallback retained`);
        await context.close();continue;
      }
      await page.getByRole('button',{name:'Retry',exact:true}).waitFor();
      assert.equal(await page.locator('#gateTitle').innerText(),'Review library unavailable');
      assert.notEqual(await page.locator('body').getAttribute('data-mode'),'browser');
      assert.equal(await page.locator('#videoSelect option').count(),0);
      assert.equal(await page.locator('#addVideo').isVisible(),false);
      assert.equal(await page.locator('#toast.show').count(),0);
      assert.equal(await page.locator('#libraryBtn').isDisabled(),true);
      for(const id of ['backupBtn','importBtn','removeVideo'])assert.equal(await page.locator('#'+id).getAttribute('hidden'),'');
      const originalBrowser=await browserState();
      assert.deepEqual(originalBrowser.videos.map(v=>v.id),populated?['browser-only']:[]);
      if(shots&&!populated&&failure==='503')await page.screenshot({path:join(shots,`after-${colorScheme}-${width}.png`),fullPage:true});
      recovered=true;await page.getByRole('button',{name:'Retry',exact:true}).click();
      await page.waitForFunction(()=>document.body.dataset.mode==='local'&&document.querySelector('#videoTitle').textContent==='Local fixture');
      await page.getByText('Local words retained',{exact:true}).waitFor();
      await page.getByText('Local note retained',{exact:true}).waitFor();
      assert.equal(await page.locator('#gate').isVisible(),false);
      assert.deepEqual(await browserState(),originalBrowser,'browser library unchanged through Retry');
      for(const [name,original] of originals)assert.equal(await readFile(join(temp,name),'utf8'),original,`${name} unchanged`);
      console.log(`PASS ${width} ${colorScheme} ${failure} browser=${populated?'existing':'empty'}: unavailable, Retry restores local video/note/transcript`);
      await context.close();
    }
  }
  console.log('RESULT: ok backend-browser 24 failure/recovery checks and 16 hosted fallback checks');
}finally{
  await browser?.close();
  server.kill();await new Promise(resolve=>server.once('exit',resolve));
  await rm(temp,{recursive:true,force:true});
}

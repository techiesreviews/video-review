// Isolated local synchronization failures. Never opens a real review or an agent connection.
// SYNC_BEFORE=1 captures the pre-fix UI; SYNC_SHOTS saves light/dark desktop/phone evidence.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
await mkdir('.scratch',{recursive:true});
const root=resolve('.'),temp=await mkdtemp(resolve('.scratch/sync-browser-'));
const port=Number(process.env.SYNC_PORT||8898),base=`http://127.0.0.1:${port}`,shots=process.env.SYNC_SHOTS,before=!!process.env.SYNC_BEFORE;
const original={id:'note',videoId:'fixture',author:'you',text:'Original note retained',startMs:0,endMs:null,status:'open',createdAt:'2026-01-01T00:00:00Z',replies:[]};
const media=join(temp,'silence.wav'),wav=Buffer.alloc(44+8000*8*2);
wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
await writeFile(media,wav);
await writeFile(join(temp,'manifest.json'),JSON.stringify([{id:'fixture',title:'Review fixture',label:'Synthetic cut',path:media,fps:30,durationMs:8000}]));
await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[original]}));
await mkdir(join(temp,'transcripts'));
await writeFile(join(temp,'transcripts/fixture.json'),JSON.stringify({revision:'synthetic',words:[{id:'W1',text:'Review these words',startMs:0,endMs:1000}]}));
const server=spawn(process.env.VIDEO_REVIEW_PYTHON||'python3',[join(root,'server.py'),'--port',String(port),'--data-dir',temp],{cwd:root,stdio:'ignore'});
let browser;
try{
  let ready=false;for(let i=0;i<80;i++){try{if((await fetch(base+'/api/local')).ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}assert.ok(ready);
  browser=await chromium.launch({headless:true});if(shots)await mkdir(shots,{recursive:true});
  const focused=['save','restore','restore-failed','agent','restore-poll','remove-failed'];
  if(process.env.SYNC_REVIEW&&!focused.includes(process.env.SYNC_REVIEW))throw Error('Unknown SYNC_REVIEW case');
  const reviewCases=process.env.SYNC_REVIEW?[process.env.SYNC_REVIEW]:before?[null]:[null,...focused];
  for(const width of [1440,390])for(const colorScheme of ['light','dark'])for(const reviewCase of reviewCases){
    await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[original]}));
    const context=await browser.newContext({viewport:{width,height:width===390?844:960},colorScheme});
    const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
    let failed=null,newNote=false,failView=false,agent='absent',writes=0,holdVideos=false,releaseVideos,heldVideos,restoreDone,holdAgent=false,releaseAgent,agentHeld;
    await page.route('**/api/**',async route=>{
      const path=new URL(route.request().url()).pathname;
      if(path==='/api/videos'&&holdVideos){holdVideos=false;const response=await route.fetch();heldVideos?.();await new Promise(resolve=>{releaseVideos=resolve;});return route.fulfill({response});}
      if(path==='/api/library'&&route.request().method()==='POST'&&route.request().postDataJSON()?.action==='restore'){
        const response=await route.fetch();await route.fulfill({response});restoreDone?.();return;
      }
      if(path==='/api/view'&&route.request().method()==='POST'&&failView)return route.fulfill({status:503,json:{error:'Unavailable'}});
      if(path==='/api/agent'){
        if(holdAgent){agentHeld?.();await new Promise(resolve=>{releaseAgent=resolve;});}
        return route.fulfill({status:agent==='unknown'?503:200,json:agent==='unknown'?{error:'Unavailable'}:{connected:agent==='connected'}}).catch(()=>{});
      }
      if(path===failed)return route.fulfill({status:503,json:{error:'Unavailable'}});
      if(path==='/api/comments'){
        if(route.request().method()==='POST'){writes++;return route.continue();}
        const response=await route.fetch(),data=await response.json();
        if(newNote)data.comments=data.comments.map(note=>note.id==='note'?{...note,replies:[{author:'ai',text:'Fresh agent reply',createdAt:'2026-01-01T00:00:01Z'}]}:note);
        return route.fulfill({response,json:data});
      }
      return route.continue();
    });
    const visibility=()=>page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
    await page.goto(base);await page.getByText(original.text,{exact:true}).waitFor();await page.locator('.transcript-word').waitFor();
    if(reviewCase){
      if(reviewCase==='save'){
        failed='/api/comments';await page.locator('.transcript-word').click();await page.locator('#transcriptNote').fill('Refused request');await page.locator('#transcriptNote').press('Enter');
        await page.waitForFunction(()=>document.querySelector('#saveState').textContent==='Not saved',{},{timeout:1500});
      }else if(reviewCase==='agent'){
        agent='connected';await page.locator('.transcript-word').click();await page.locator('#transcriptNote').fill('Saved request');await page.locator('#transcriptNote').press('Enter');
        await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('Sent to your agent'));
        const held=new Promise(resolve=>{agentHeld=resolve;});holdAgent=true;await visibility();await held;
        newNote=true;
        await page.getByText('Fresh agent reply',{exact:true}).waitFor({timeout:6500});
        holdAgent=false;releaseAgent();
      }else{
        page.on('dialog',dialog=>dialog.accept());
        if(reviewCase==='remove-failed')failed='/api/videos';
        await page.locator('#libraryBtn').click();await page.getByRole('button',{name:'Remove Review fixture from library',exact:true}).click();
        if(reviewCase==='remove-failed'){
          await page.waitForFunction(()=>document.querySelector('#toastText').textContent==='Removed from the library.',{},{timeout:1500});
          assert.equal(await page.locator('#toastAction').isVisible(),true);
          await page.waitForFunction(()=>!document.querySelector('.library-remove'));
          const restored=new Promise(resolve=>{restoreDone=resolve;});failed=null;await page.locator('#toastAction').click();await restored;
          await page.waitForFunction(()=>!document.querySelector('#reviewSyncState').textContent&&document.querySelector('#videoTitle').textContent==='Review fixture');
          assert.deepEqual(errors,[]);console.log(`PASS ${width} ${colorScheme} remove-failed: removal keeps Undo despite stale refresh`);await context.close();continue;
        }
        await page.waitForFunction(()=>document.querySelector('#videoTitle').textContent==='Video Review');
        if(!await page.locator('#libraryMenu').isVisible())await page.locator('#libraryBtn').click();
        await page.locator('#libraryRemoved').click();await page.locator('.library-restore-label').waitFor();
        if(reviewCase==='restore-poll'){
          failed='/api/view-request';await page.locator('.library-open').click();
          await page.waitForFunction(()=>document.querySelector('#toastText').textContent==='Video and review notes restored.',{},{timeout:1500});
          assert.equal(await page.locator('#toast.error').count(),0);assert.equal(await page.locator('#videoTitle').innerText(),'Review fixture');
          assert.deepEqual(errors,[]);console.log(`PASS ${width} ${colorScheme} restore-poll: failed view polling does not fail a restored video`);await context.close();continue;
        }
        if(reviewCase==='restore-failed'){
          failed='/api/videos';await page.locator('.library-open').click();
          await page.waitForFunction(()=>document.querySelector('#toast.error #toastText')?.textContent==='Video restored, but review updates are unavailable.',{},{timeout:1500});
          assert.deepEqual(errors,[],'a failed refresh must not select a missing video');
          failed=null;await visibility();await page.waitForFunction(()=>document.querySelector('#videoTitle').textContent==='Review fixture');
          console.log(`PASS ${width} ${colorScheme} restore-failed: refresh error retained; recovery opens restored video`);await context.close();continue;
        }
        const held=new Promise(resolve=>{heldVideos=resolve;});holdVideos=true;await visibility();await held;
        const restored=new Promise(resolve=>{restoreDone=resolve;});await page.locator('.library-open').click();await restored;
        // Let the restore callback join the held refresh before releasing its stale response.
        await page.waitForTimeout(50);releaseVideos();
        await page.waitForFunction(()=>document.querySelector('#videoTitle').textContent==='Review fixture',{},{timeout:1500});
        assert.equal(await page.locator('#toast.error').count(),0,'restoring during a held refresh must not select an absent video');
      }
      assert.deepEqual(errors,[]);console.log(`PASS ${width} ${colorScheme} ${reviewCase}: ${reviewCase==='save'?'failed request says Not saved':reviewCase==='agent'?'held agent status does not block fresh replies':'restore waits for a fresh queued refresh'}`);await context.close();continue;
    }
    // Break both sync channels, capture what the reviewer sees, then recover.
    failed='/api/comments';failView=true;
    await page.locator('#seek').evaluate(node=>{node.value='2.3';node.dispatchEvent(new Event('input'));});
    await visibility();await page.waitForTimeout(700);
    if(shots)await page.screenshot({path:join(shots,`${before?'before':'after'}-${colorScheme}-${width}.png`),fullPage:true});
    if(before){await context.close();continue;}
    await page.getByText('Review updates stale',{exact:true}).waitFor();await page.getByText('Agent view not synced',{exact:true}).waitFor();
    assert.equal(await page.getByText(original.text,{exact:true}).isVisible(),true);
    // Failed view writes leave playback and ordinary note saving available.
    failed=null;
    await page.locator('#play').click();await page.waitForFunction(()=>document.querySelector('#video').currentTime>2.5);
    await page.locator('#play').click();await page.locator('#noteText').fill('Saved while view is stale');await page.locator('#noteText').press('Enter');
    await page.getByText('Saved while view is stale',{exact:true}).waitFor();
    failed=null;failView=false;await visibility();
    await page.waitForFunction(()=>!document.querySelector('#reviewSyncState').textContent&&!document.querySelector('#viewSyncState').textContent);
    // Status recovers on the saved bar without resending its request.
    agent='unknown';await page.locator('.transcript-word').click();await page.locator('#transcriptNote').fill('Keep this passage');await page.locator('#transcriptNote').press('Enter');
    await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('Agent status unavailable'));
    const savedWrites=writes;agent='connected';await visibility();
    await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('Sent to your agent'));
    assert.equal(writes,savedWrites,'status recovery must not duplicate the request');
    agent='absent';await visibility();await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('No agent connected'));
    // Each endpoint fails via both visibility refresh and the real four-second timer.
    await page.locator('#transcriptNote').press('Escape');
    for(const endpoint of ['/api/videos','/api/comments','/api/view-request'])for(const trigger of ['visibility','timer']){
      newNote=false;await visibility();
      await page.waitForFunction(()=>!document.querySelector('#comments').textContent.includes('Fresh agent reply'));
      failed=endpoint;newNote=true;
      if(trigger==='visibility')await visibility();
      await page.getByText('Review updates stale',{exact:true}).waitFor({timeout:6500});
      assert.equal(await page.getByText(original.text,{exact:true}).isVisible(),true);
      if(endpoint==='/api/view-request')await page.getByText('Fresh agent reply',{exact:true}).waitFor();
      failed=null;await visibility();
      await page.getByText('Fresh agent reply',{exact:true}).waitFor();
      await page.waitForFunction(()=>!document.querySelector('#reviewSyncState').textContent);
      console.log(`PASS ${width} ${colorScheme} ${endpoint} ${trigger}: stale, retained notes, recovered reply`);
    }
    // A refresh leaves a real inline edit and composer draft intact, then draws deferred replies after Cancel.
    newNote=false;await visibility();await page.waitForFunction(()=>!document.querySelector('#comments').textContent.includes('Fresh agent reply'));
    await page.locator('[data-id="note"]').getByRole('button',{name:'Edit',exact:true}).click();
    await page.locator('.edit-box').fill('Unsaved inline edit');await page.locator('#noteText').fill('Unsaved composer draft');
    failed='/api/comments';await visibility();await page.getByText('Review updates stale',{exact:true}).waitFor();
    failed=null;newNote=true;await visibility();await page.waitForFunction(()=>!document.querySelector('#reviewSyncState').textContent);
    assert.equal(await page.locator('.edit-box').inputValue(),'Unsaved inline edit');assert.equal(await page.locator('#noteText').inputValue(),'Unsaved composer draft');
    await page.getByRole('button',{name:'Cancel',exact:true}).click();await visibility();await page.getByText('Fresh agent reply',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);await context.close();
  }
  console.log(`RESULT: ok sync-browser ${before?'before screenshots':process.env.SYNC_REVIEW?'four '+process.env.SYNC_REVIEW+' regression cases':'24 endpoint failure/recovery cases; 24 save/library/held-status regressions; view and agent recovery; playback, note saving and drafts retained in four layouts'}`);
}finally{await browser?.close();server.kill();await new Promise(resolve=>server.once('exit',resolve));await rm(temp,{recursive:true,force:true});}

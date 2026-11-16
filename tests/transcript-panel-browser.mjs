// Browser check of the transcript panel's editing on a tiny made-up video, at desktop and phone width: Delete, a Delete
// over words already deleted (two pieces), Undo, Restore, a note to the agent, and a save the server refuses.
// It never opens a real review. Run: node tests/transcript-panel-browser.mjs (needs Playwright and ffmpeg).
// TRANSCRIPT_PANEL_SHOTS=<folder> also saves a screenshot of each width there.
import {chromium} from 'playwright';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,writeFile,mkdir,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import assert from 'node:assert/strict';

const root=resolve('.'),temp=await mkdtemp(join(tmpdir(),'video-transcript-panel-')),port=Number(process.env.TRANSCRIPT_PANEL_PORT||8896),base=`http://127.0.0.1:${port}`;
const python=process.env.VIDEO_REVIEW_PYTHON||(process.platform==='win32'?'python':'python3');
const media=join(temp,'words.mp4'),fps=30;
const encoded=spawnSync('ffmpeg',['-v','error','-y','-f','lavfi','-i',`color=c=0x1b1f3b:s=320x180:r=${fps}:d=8`,'-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=8','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-b:a','64k','-shortest',media],{windowsHide:true,encoding:'utf8'});
assert.equal(encoded.status,0,encoded.stderr);
const words=[];for(let i=0;i<16;i++)words.push({id:'W'+i,text:'word'+i,startMs:i*500+50,endMs:i*500+450});
await writeFile(join(temp,'manifest.json'),JSON.stringify([{id:'words',title:'Transcript panel test',label:'Synthetic',path:media,fps,durationMs:8000,keepRanges:[[0,240]]}]));
await mkdir(join(temp,'transcripts'));

const server=spawn(python,[join(root,'server.py'),'--port',String(port),'--data-dir',temp],{cwd:root,windowsHide:true,stdio:'ignore'});
const shots=process.env.TRANSCRIPT_PANEL_SHOTS;
let browser;
try{
  for(let i=0;i<80;i++){try{if((await fetch(base+'/api/local')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true});
  for(const [name,viewport] of [['desktop',{width:1440,height:960}],['phone',{width:390,height:844}]]){
    // A fresh transcript and comment list for each width.
    await writeFile(join(temp,'comments.json'),JSON.stringify({comments:[]}));
    await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({words,cuts:[{id:'J1',atMs:3000}],revision:'synthetic'}));
    const page=await browser.newPage({viewport,isMobile:name==='phone',hasTouch:name==='phone'}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const onDisk=async()=>JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8')).draftEdits||[];
    const comments=async()=>JSON.parse(await readFile(join(temp,'comments.json'),'utf8')).comments;
    const word=i=>page.locator('.transcript-word').nth(i);
    const deleted=()=>page.locator('.transcript-word.removed-word').evaluateAll(nodes=>nodes.map(n=>n.textContent));
    await page.goto(base);await page.waitForSelector('.transcript-word');

    // Delete one word with the bar's button: it is struck through, saved, and the next word is selected.
    await word(2).click();
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    assert.deepEqual(await deleted(),['word2'],`${name}: the deleted word stays in the text, struck through.`);
    assert.deepEqual((await onDisk()).map(e=>e.wordIds),[['W2']],`${name}: the cut is saved.`);
    assert.match(await page.locator('#barStatus').innerText(),/^Deleted\. Ctrl\+Z brings it back\.$/);
    assert.deepEqual(await page.locator('.transcript-word.selected').allInnerTexts(),['word3'],`${name}: the next word is selected.`);

    // A Delete over words 1–4 keeps word2 in its own cut and cuts the rest as two pieces with one note.
    await word(1).click();await word(4).click({modifiers:['Shift']});
    await page.locator('#transcriptNote').fill('too slow');
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===4);
    const pieces=(await onDisk()).slice(1);
    assert.deepEqual(pieces.map(e=>e.wordIds),[['W1'],['W3','W4']],`${name}: one piece for each run still in the cut.`);
    assert.equal(pieces[0].commentId,pieces[1].commentId);
    assert.equal((await comments()).find(c=>c.id===pieces[0].commentId).text,'Remove from cut: “word1 … word3 word4” — too slow');

    // Undo brings both pieces back together, and the typed note returns to the box.
    await page.locator('#undoTranscriptCut').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    assert.deepEqual((await onDisk()).map(e=>e.wordIds),[['W2']],`${name}: Undo brings back the last Delete only.`);
    assert.equal(await page.locator('#transcriptNote').inputValue(),'too slow',`${name}: the note typed with the Delete is back in the box.`);
    assert.equal((await comments()).find(c=>c.id===pieces[0].commentId).status,'done','its note is resolved');

    // Restore on the deleted word brings it back.
    await page.locator('.restore-cut').first().click();
    await page.waitForFunction(()=>!document.querySelector('.transcript-word.removed-word'));
    assert.deepEqual(await onDisk(),[],`${name}: Restore empties the cut list.`);

    // A save the server refuses: the word is not struck through, the error shows, the word stays selected.
    await page.route('**/api/transcript/words',route=>route.request().method()==='POST'?route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'Disk full'})}):route.continue());
    await word(6).click();
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelector('#toast.show.error'));
    assert.equal(await page.locator('#toastText').innerText(),'Disk full');
    assert.equal(await page.locator('.transcript-word.removed-word').count(),0,`${name}: a failed save leaves the word in the cut.`);
    assert.deepEqual(await page.locator('.transcript-word.selected').allInnerTexts(),['word6'],`${name}: the word stays selected to try again.`);
    const orphan=(await comments()).find(c=>c.text==='Remove from cut: “word6”');
    assert.equal(orphan.status,'done',`${name}: the note made for the failed cut does not stay open.`);
    await page.unroute('**/api/transcript/words');
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    assert.deepEqual((await onDisk()).map(e=>e.wordIds),[['W6']],`${name}: trying again saves it.`);

    // A note on a join goes to the agent with the join's id.
    await page.locator('.transcript-join',{hasText:'J1'}).click();
    await page.locator('#transcriptNote').fill('Check this join');await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('No agent connected'));
    const onJoin=(await comments()).find(c=>c.text==='Check this join');
    assert.equal(onJoin.editRequest.type,'change');assert.equal(onJoin.editRequest.cutId,'J1');

    // The bar and the words stay on screen.
    await word(9).click();
    const bar=await page.locator('#transcriptSelection').boundingBox();
    assert.ok(bar.x>=0&&bar.x+bar.width<=viewport.width+1,`${name}: the edit bar fits the width.`);
    if(shots){await mkdir(shots,{recursive:true});await page.screenshot({path:join(shots,`transcript-panel-${name}.png`)});}
    assert.deepEqual(errors,[],`${name}: no page errors.`);
    await page.close();
  }
  console.log('PASS: transcript panel editing at desktop and phone width: Delete, a split Delete, Undo, Restore, a refused save, a note on a join.');
}finally{
  await browser?.close();server.kill();
  await rm(temp,{recursive:true,force:true});
}

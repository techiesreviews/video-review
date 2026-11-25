// Browser check of the transcript panel's editing on a tiny made-up video, at desktop and phone width: Delete, a Delete
// over words already deleted (joined into one cut), Undo, a click on a deleted word to restore it, a note to the agent, a save the server refuses, and an edit refused because the words changed elsewhere.
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

    // A Delete over words 1–4 takes word2's cut into it: one cut, one note (TOOLS-463).
    await word(1).click();await word(4).click({modifiers:['Shift']});
    await page.locator('#transcriptNote').fill('too slow');
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===4);
    const pieces=await onDisk();
    assert.deepEqual(pieces.map(e=>e.wordIds),[['W1','W2','W3','W4']],`${name}: the words already cut join the new cut.`);
    assert.equal(await page.locator('.transcript-space.removed').count(),3,`${name}: the cut reads as one band, spaces included.`);
    assert.equal((await comments()).find(c=>c.id===pieces[0].commentId).text,'Remove from cut: “word1 word2 word3 word4” — too slow');

    // Undo puts word2's own cut back as it was, and the typed note returns to the box.
    await page.locator('#undoTranscriptCut').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    assert.deepEqual((await onDisk()).map(e=>e.wordIds),[['W2']],`${name}: Undo brings back the last Delete only.`);
    assert.equal(await page.locator('#transcriptNote').inputValue(),'too slow',`${name}: the note typed with the Delete is back in the box.`);
    assert.equal((await comments()).find(c=>c.id===pieces[0].commentId).status,'done','its note is resolved');

    // There are no Restore buttons; a click on the deleted word brings it back.
    assert.equal(await page.locator('.restore-cut').count(),0,`${name}: no Restore buttons in the text.`);
    await word(2).click();
    await page.waitForFunction(()=>!document.querySelector('.transcript-word.removed-word'));
    assert.deepEqual(await onDisk(),[],`${name}: Restore empties the cut list.`);

    // Undo with a half-typed note on other words brings the cut back and leaves that note and selection alone.
    await word(8).click();await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    await word(11).click();await page.locator('#transcriptNote').fill('half a thought');
    await page.locator('#undoTranscriptCut').click();
    await page.waitForFunction(()=>!document.querySelector('.transcript-word.removed-word'));
    assert.equal(await page.locator('#transcriptNote').inputValue(),'half a thought',`${name}: the half-typed note stays in the box.`);
    assert.deepEqual(await page.locator('.transcript-word.selected').allInnerTexts(),['word11'],`${name}: its words stay selected.`);
    assert.equal(await page.locator('#barStatus').innerText(),'Brought back.');

    // Undo whose save works but whose note cannot be closed: the words come back, the error shows, and the bar closes
    // rather than staying open on a selection that is gone.
    await word(13).click();await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    assert.equal(await page.locator('#transcriptSelection').isVisible(),true,'the bar is open on the next word, with an empty box');
    await page.route('**/api/comments',route=>route.request().method()==='POST'&&route.request().postDataJSON().action==='reply'?route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'Comments are busy'})}):route.continue());
    await page.locator('#undoTranscriptCut').click();
    await page.waitForFunction(()=>document.querySelector('#toast.show.error'));
    assert.equal(await page.locator('#toastText').innerText(),'Comments are busy');
    assert.equal(await page.locator('.transcript-word.removed-word').count(),0,`${name}: the words are back.`);
    assert.equal(await page.locator('#transcriptSelection').isHidden(),true,`${name}: the bar does not stay open on a dropped selection.`);
    await page.unroute('**/api/comments');
    await page.locator('#toast').evaluate(n=>n.classList.remove('show','error'));

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

    // Another editor swaps join J1 for J9 on the same words. A Delete still saves, and the join marks follow the saved
    // transcript: only J9 shows, and clicking it opens J9.
    const swapped=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({...swapped,cuts:[{id:'J9',atMs:3000}]}));
    await word(9).click();await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===2);
    assert.deepEqual(await page.locator('.transcript-join[data-cut-id]').allInnerTexts(),['↔ J9'],`${name}: the join marks follow the saved transcript.`);
    const before6=await word(6).evaluate(n=>{const out=[];for(let x=n,k=0;x&&k<3;x=x.previousElementSibling,k++)out.unshift(x.classList[0]);return out;});
    assert.deepEqual(before6,['transcript-space','transcript-join','transcript-word'],`${name}: the redrawn join sits right before the deleted word.`);
    await page.locator('.transcript-join[data-cut-id]').click();
    assert.match(await page.locator('#selectionQuote').innerText(),/^Join J9/,`${name}: the mark opens the new join.`);

    // J9 stays selected with a note typed while another editor swaps it for J5, and a Restore brings J5 back with the
    // save: the bar does not stay on a join that is gone. It closes with a short line, and the typed note waits for the
    // next selection.
    await page.locator('#transcriptNote').fill('join note');
    const again=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({...again,cuts:[{id:'J5',atMs:3000}]}));
    await word(9).click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    assert.deepEqual(await page.locator('.transcript-join[data-cut-id]').allInnerTexts(),['↔ J5'],`${name}: the join marks follow the saved transcript.`);
    assert.equal(await page.locator('#transcriptSelection').isHidden(),true,`${name}: the bar closes on a join that is gone.`);
    assert.equal(await page.locator('#transcriptNotice').innerText(),'That join changed elsewhere, so it is no longer selected.',`${name}: a short line says why the bar closed.`);
    await word(11).click();
    assert.equal(await page.locator('#transcriptNote').inputValue(),'join note',`${name}: the typed note comes back on the next selection.`);
    await page.locator('#clearTranscriptSelection').click();

    // J5 stays selected with a note while another editor adds J6 and leaves J5 where it was: the marks are drawn again,
    // and the bar stays open on J5 with its note, next to J5's new mark rather than at the bottom of the text.
    const restoreBefore=i=>word(i).click();
    const noteOnJoinWhileSwapped=async (cuts,note)=>{
      await page.locator('.transcript-join[data-cut-id]').first().click();await page.locator('#transcriptNote').fill(note);
      const now=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
      await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({...now,cuts}));
    };
    await noteOnJoinWhileSwapped([{id:'J5',atMs:3000},{id:'J6',atMs:6050}],'kept note');
    await restoreBefore(6);
    await page.waitForFunction(()=>!document.querySelector('.transcript-word.removed-word'));
    assert.deepEqual(await page.locator('.transcript-join[data-cut-id]').allInnerTexts(),['↔ J5','↔ J6'],`${name}: the join marks follow the saved transcript.`);
    assert.equal(await page.locator('#transcriptSelection').isVisible(),true,`${name}: the bar stays open on a join that is still there.`);
    assert.match(await page.locator('#selectionQuote').innerText(),/^Join J5/,`${name}: the same join stays selected.`);
    assert.equal(await page.locator('#transcriptNote').inputValue(),'kept note',`${name}: its note stays in the box.`);
    const nearMark=await page.evaluate(()=>{const bar=document.querySelector('#transcriptSelection').getBoundingClientRect(),mark=document.querySelector('.transcript-join[data-cut-id="J5"]').getBoundingClientRect(),line=parseFloat(getComputedStyle(document.querySelector('#transcriptText')).lineHeight)||24;return Math.min(Math.abs(bar.top-mark.bottom),Math.abs(bar.bottom-mark.top))<=2*line;});
    assert.equal(nearMark,true,`${name}: the bar sits next to the join's new mark.`);
    await page.locator('#clearTranscriptSelection').click();

    // The same, with the join's range changed by hand in the waveform's end box first while another editor swaps J6 for
    // J7: J5 did not move, so the bar stays open.
    await word(13).click();await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    await page.locator('#clearTranscriptSelection').click();
    await page.locator('.transcript-join[data-cut-id]').first().click();
    const span=async()=>page.locator('#waveformCutLength').textContent(),wasSpan=await span();
    await page.locator('#waveformOut').evaluate(n=>{n.value=(Number(n.value)+0.1).toFixed(3);n.dispatchEvent(new Event('change'));});
    assert.notEqual(await span(),wasSpan,`${name}: the join's range is changed by hand.`);
    await page.locator('#transcriptNote').fill('nudged note');
    const nudged=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({...nudged,cuts:[{id:'J5',atMs:3000},{id:'J7',atMs:7050}]}));
    await restoreBefore(13);
    await page.waitForFunction(()=>!document.querySelector('.transcript-word.removed-word'));
    assert.equal(await page.locator('#transcriptSelection').isVisible(),true,`${name}: a nudged join that did not move keeps the bar open.`);
    assert.match(await page.locator('#selectionQuote').innerText(),/^Join J5/,`${name}: the same join stays selected.`);
    assert.equal(await page.locator('#transcriptNotice').innerText(),'',`${name}: nothing says the join changed.`);
    await page.locator('#clearTranscriptSelection').click();

    // Another editor moves J5 to a later word while it is selected: the bar closes, so a note never goes out with the old time.
    await word(12).click();await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    await page.locator('#clearTranscriptSelection').click();
    await noteOnJoinWhileSwapped([{id:'J5',atMs:4050},{id:'J6',atMs:6050}],'moved note');
    await restoreBefore(12);
    await page.waitForFunction(()=>!document.querySelector('.transcript-word.removed-word'));
    assert.equal(await page.locator('#transcriptSelection').isHidden(),true,`${name}: the bar closes on a join that moved.`);
    assert.equal(await page.locator('#transcriptNotice').innerText(),'That join changed elsewhere, so it is no longer selected.',`${name}: a short line says why the bar closed.`);
    await word(11).click();
    assert.equal(await page.locator('#transcriptNote').inputValue(),'moved note',`${name}: the typed note comes back on the next selection.`);
    await page.locator('#clearTranscriptSelection').click();

    // The words change elsewhere (an agent or another tab) and this page never hears of it. A Delete is refused by the
    // store: the panel loads the newer words, keeps the same words selected with the typed note and the scroll, and says
    // so in a short line on the bar, not in a toast. The newer words are never overwritten, and Delete then saves.
    await page.locator('#toast').evaluate(n=>n.classList.remove('show','error'));
    const replace=async (id,text)=>writeFile(join(temp,'transcripts/words.json'),JSON.stringify({words:words.map(w=>w.id===id?{...w,text}:w),cuts:[{id:'J1',atMs:3000}],revision:'elsewhere-'+id}));
    await replace('W10','ten-new');
    await word(10).click();await page.locator('#transcriptNote').fill('tighter');
    const scrolled=await page.locator('#transcriptText').evaluate(n=>n.scrollTop);
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.startsWith('Not saved'));
    assert.equal(await page.locator('#barStatus').innerText(),'Not saved: the words changed elsewhere. Try again.');
    assert.equal(await page.locator('#barStatus').evaluate(n=>n.scrollWidth<=n.clientWidth),true,`${name}: the line fits the bar.`);
    assert.equal(await word(10).innerText(),'ten-new',`${name}: the newer words are shown.`);
    assert.deepEqual(await page.locator('.transcript-word.selected').allInnerTexts(),['ten-new'],`${name}: the same word stays selected.`);
    assert.equal(await page.locator('#transcriptNote').inputValue(),'tighter',`${name}: the typed note stays in the box.`);
    assert.ok(Math.abs(await page.locator('#transcriptText').evaluate(n=>n.scrollTop)-scrolled)<=2,`${name}: the place in the text is kept.`);
    assert.equal(await page.locator('#toast.show.error').count(),0,`${name}: no toast.`);
    assert.equal(await page.locator('.transcript-word.removed-word').count(),0,`${name}: the new transcript has no cuts yet.`);
    let stored=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    assert.equal(stored.words[10].text,'ten-new');assert.deepEqual(stored.draftEdits||[],[],`${name}: the newer words are not overwritten.`);
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word.removed-word').length===1);
    stored=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    assert.deepEqual(stored.draftEdits.map(e=>e.wordIds),[['W10']],`${name}: trying again saves the cut on the newer words.`);
    assert.equal(stored.words[10].text,'ten-new');

    // An Undo with nothing selected, refused the same way: the newer words load and the line shows on the panel.
    await replace('W12','twelve-new');
    await page.locator('#clearTranscriptSelection').click();
    await page.locator('#undoTranscriptCut').click();
    await page.waitForFunction(()=>document.querySelector('#transcriptNotice').textContent);
    assert.equal(await page.locator('#transcriptNotice').isVisible(),true);
    assert.equal(await page.locator('#transcriptNotice').innerText(),'Not saved: the words changed elsewhere. Try again.');
    assert.equal(await word(12).innerText(),'twelve-new',`${name}: the newer words are shown.`);
    assert.equal(await page.locator('#toast.show.error').count(),0,`${name}: no toast.`);
    stored=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    assert.equal(stored.words[12].text,'twelve-new');assert.deepEqual(stored.draftEdits||[],[],`${name}: the newer words are not overwritten.`);

    // A Delete on a join refused the same way: the same join is selected again, not a word, and Delete then cuts it.
    await replace('W14','fourteen-new');
    await page.locator('.transcript-join',{hasText:'J1'}).click();
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.startsWith('Not saved'));
    assert.equal(await word(14).innerText(),'fourteen-new');
    assert.match(await page.locator('#selectionQuote').innerText(),/^Join J1/,`${name}: the join stays selected.`);
    assert.equal(await page.locator('.transcript-word.selected').count(),0,`${name}: no word is selected instead.`);
    assert.equal(await page.locator('#transcriptNotice').isHidden(),true,`${name}: the line is on the bar, not the panel.`);
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelector('#cutPreviewStatus').textContent.includes('removed'));
    stored=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    assert.equal(stored.draftEdits?.length,1,`${name}: one cut, at the join.`);
    assert.ok(stored.draftEdits[0].startMs<=3000&&stored.draftEdits[0].endMs>=3000);
    assert.equal(stored.words[14].text,'fourteen-new');

    // A cut with its own note, for the Undo below; not next to the join's cut, which it would join.
    await word(9).click();await page.locator('#transcriptNote').fill('cut note');await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word')[9].classList.contains('removed-word'));
    stored=JSON.parse(await readFile(join(temp,'transcripts/words.json'),'utf8'));
    assert.equal(stored.draftEdits.length,2,JSON.stringify(stored.draftEdits));

    // A refused Delete on a join that is gone from the newer words: nothing is selected instead, the line shows on the
    // panel, and the typed note waits for the next selection.
    await page.locator('.transcript-join',{hasText:'J1'}).click();
    await page.locator('#transcriptNote').fill('rough join');
    const kept=stored.draftEdits;
    await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({words,cuts:[],draftEdits:kept,revision:'no-join'}));
    await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelector('#transcriptNotice').textContent);
    assert.equal(await page.locator('#transcriptNotice').innerText(),'Not saved: the words changed elsewhere. Try again.');
    assert.equal(await page.locator('.transcript-join',{hasText:'J1'}).count(),0);
    assert.equal(await page.locator('#transcriptSelection').isHidden(),true,`${name}: nothing else is selected in its place.`);
    // A second refused edit with nothing selected (an Undo) keeps the waiting note. The newer words now come with the
    // original recording, so a word Resolve removed shows under "Removed in Resolve".
    const original={durationMs:9000,words:[...words,{id:'R1',text:'resolvegone',startMs:8100,endMs:8400}]};
    await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({words:words.map(w=>w.id==='W13'?{...w,text:'thirteen-new'}:w),cuts:[],draftEdits:kept,original,revision:'no-join-again'}));
    await page.locator('#undoTranscriptCut').click();
    await page.waitForSelector('.source-word');
    assert.equal(await page.locator('#transcriptNotice').innerText(),'Not saved: the words changed elsewhere. Try again.');
    // A word under "Removed in Resolve" has no note box, so the waiting note is not put in the hidden one.
    await page.locator('.source-word',{hasText:'resolvegone'}).click();
    assert.equal(await page.locator('#transcriptNoteForm').isHidden(),true,`${name}: a removed word's bar has no note box.`);
    assert.equal(await page.locator('#transcriptNote').inputValue(),'',`${name}: the waiting note is not put in the hidden box.`);
    assert.equal(await page.locator('#transcriptNotice').innerText(),'',`${name}: the line goes once something is selected.`);
    // An Undo that works brings back the cut with its own note: the words come back selected with both notes in the box.
    await page.locator('#undoTranscriptCut').click();
    await page.waitForFunction(()=>!document.querySelectorAll('.transcript-word')[9].classList.contains('removed-word'));
    assert.deepEqual(await page.locator('.transcript-word.selected').allInnerTexts(),['word9']);
    assert.equal(await page.locator('#transcriptNote').inputValue(),'cut note rough join',`${name}: the typed note is kept for the next selection.`);

    // A refused Delete whose transcript was removed elsewhere meanwhile says so on the panel, not "try again".
    await word(1).click();await page.locator('.bar-delete').click();
    // Waits for the word to be struck through: the cut bar already shows, for the cut kept on the newer words above.
    await page.waitForFunction(()=>document.querySelectorAll('.transcript-word')[1].classList.contains('removed-word'));
    await replace('W15','fifteen-new');
    await page.route('**/api/transcript/words',route=>route.request().method()==='GET'?route.fulfill({status:200,contentType:'application/json',body:'null'}):route.continue());
    await word(9).click();await page.locator('#transcriptNote').fill('keep me');await page.locator('.bar-delete').click();
    await page.waitForFunction(()=>document.querySelector('#transcriptNotice').textContent);
    assert.equal(await page.locator('#transcriptNotice').innerText(),'Not saved: the transcript was removed elsewhere.');
    assert.match(await page.locator('#transcriptText').innerText(),/No transcript attached/);
    assert.equal(await page.locator('#cutPreviewBar').isHidden(),true,`${name}: no cut controls for words that are gone.`);
    assert.equal(await page.locator('#transcriptSelection').isHidden(),true);
    assert.equal(await page.locator('#toast.show.error').count(),0,`${name}: no toast.`);
    await page.unroute('**/api/transcript/words');
    // Attached again (here with Import transcript): the note typed before waits for the next selection.
    const [chooser]=await Promise.all([page.waitForEvent('filechooser'),page.locator('#importTranscript').click()]);
    await chooser.setFiles({name:'words.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({words,cuts:[{id:'J1',atMs:3000}],revision:'attached-again'}))});
    await page.waitForSelector('.transcript-word');
    await word(9).click();
    assert.equal(await page.locator('#transcriptNote').inputValue(),'keep me',`${name}: the typed note survives the transcript being away.`);
    await page.locator('#transcriptNote').fill('');

    // A note on a join goes to the agent with the join's id.
    await page.locator('.transcript-join',{hasText:'J1'}).click();
    await page.locator('#transcriptNote').fill('Check this join');await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('#barStatus').textContent.includes('No agent connected'));
    const onJoin=(await comments()).find(c=>c.text==='Check this join');
    assert.equal(onJoin.editRequest.type,'change');assert.equal(onJoin.editRequest.cutId,'J1');

    // A cut with no words in a gap shorter than the pause setting (handles moved into it) gets a pause mark, and that
    // mark brings back just this cut; the other Delete stays.
    const inGap={id:'gap',startMs:2967,endMs:3033,wordIds:[],quote:'Join J1',kind:'words',commentId:null};
    const other={id:'w11',startMs:5533,endMs:5967,wordIds:['W11'],quote:'word11',kind:'words',commentId:null};
    // So do cuts before the first word and after the last one.
    const opening={...inGap,id:'opening',startMs:0,endMs:33},trailing={...inGap,id:'trailing',startMs:7967,endMs:8000};
    await writeFile(join(temp,'transcripts/words.json'),JSON.stringify({words,cuts:[{id:'J1',atMs:3000}],draftEdits:[opening,inGap,other,trailing],revision:'gap-cut'}));
    await page.reload();await page.waitForSelector('.transcript-pause.cut-only');
    assert.equal(await page.locator('.transcript-pause.cut-only').count(),3,`${name}: a mark for each cut with no words.`);
    for(const [n,left] of [[1,['opening','w11','trailing']],[0,['w11','trailing']],[0,['w11']]]){
      await page.keyboard.press('Escape');await page.locator('.transcript-pause.cut-only').nth(n).click();
      assert.equal(await page.locator('.bar-delete').innerText(),'Restore',`${name}: the cut pause offers Restore.`);
      await page.locator('.bar-delete').click();
      await page.waitForFunction(count=>document.querySelectorAll('.transcript-pause.cut-only').length===count,left.length-1);
      assert.deepEqual((await onDisk()).map(e=>e.id),left,`${name}: only that pause's cut is brought back.`);
    }

    // The bar and the words stay on screen.
    await word(9).click();
    const bar=await page.locator('#transcriptSelection').boundingBox();
    assert.ok(bar.x>=0&&bar.x+bar.width<=viewport.width+1,`${name}: the edit bar fits the width.`);
    if(shots){await mkdir(shots,{recursive:true});await page.screenshot({path:join(shots,`transcript-panel-${name}.png`)});}
    assert.deepEqual(errors,[],`${name}: no page errors.`);
    await page.close();
  }
  console.log('PASS: transcript panel editing at desktop and phone width: Delete, a joined Delete, Undo, click to restore a word, a refused save, words changed elsewhere, a note on a join.');
}finally{
  await browser?.close();server.kill();
  await rm(temp,{recursive:true,force:true});
}

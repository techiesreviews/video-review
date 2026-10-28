import {chromium} from 'playwright';
import assert from 'node:assert/strict';

const base='https://video.techies.tools';
for(const name of ['','app.js','backend.js','cut-core.js','transcript-core.js','transcript-panel.js','waveform-core.js','waveform-panel.js','library-panel.js','review-core.js','style.css']){
  const response=await fetch(base+'/'+name+'?release=listening-20261028');
  const text=await response.text();assert.equal(response.status,200);
  if(!name){assert.ok(text.includes('transcriptNoteForm')&&text.includes('librarySearch')&&text.includes('undoTranscriptCut'));assert.ok(text.includes('waveformCanvas')&&text.includes('toggleTranscriptView')&&text.includes('pauseThreshold'));assert.ok(!text.includes('id="commentBtn"'));}
  console.log('PASS',name||'index.html',response.status,text.length);
}
const unauthorized=await fetch(base+'/mcp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/list'})});
assert.equal(unauthorized.status,401);console.log('PASS MCP requires authentication');
const browser=await chromium.launch({headless:true});
try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.waitForFunction(()=>document.body.dataset.mode==='browser');
  assert.equal(await page.locator('body').evaluate(n=>n.classList.contains('transcript-only')),true);
  await page.locator('#libraryBtn').click();await page.waitForSelector('.library-empty');
  assert.equal(await page.locator('#libraryAdd').isVisible(),true);
  assert.equal(await page.locator('#transcriptHelp,#commentBtn,[data-transcript-action="comment"]').count(),0);
  assert.deepEqual(errors,[]);console.log('PASS live browser loads the compact transcript panel and searchable library without script errors');
}finally{await browser.close();}

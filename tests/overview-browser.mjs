// Browser check of the overview page on a phone-sized screen: 40 made-up scenes and one long unbroken title.
// It reads no real video folder and needs no server. Run: node tests/overview-browser.mjs
// (Playwright's own Chromium by default; OVERVIEW_BROWSER=chrome uses the installed Chrome.)
import {chromium} from 'playwright';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';

const python=process.env.VIDEO_REVIEW_PYTHON||(process.platform==='win32'?'python':'python3');
const LONG='IMG_20250914_183022_HDR_portrait_edited_edited.HEIC';
const made=spawnSync(python,['-c',`
import sys
sys.path[:0] = ['.', 'scripts/video']
import overview_page, scene_store
scenes = [{'id': f'{n:02d}-scene', 'number': n, 'title': ${JSON.stringify(LONG)} if n == 1 else f'Scene {n}'} for n in range(1, 41)]
store = scene_store.new_store(scenes)
for scene in scenes:
    scene_store.add_take(store, scene['id'], 1, duration=5.0 + scene['number'] * 3)
scene_store.add_take(store, '01-scene', 2, duration=20.0)
scene_store.apply_best(store)
sys.stdout.reconfigure(encoding='utf-8')
sys.stdout.write(overview_page.render(store, 'Narrow screen check'))
`],{encoding:'utf8',windowsHide:true});
assert.equal(made.status,0,made.stderr);

const browser=await chromium.launch(process.env.OVERVIEW_BROWSER?{channel:process.env.OVERVIEW_BROWSER}:{});
try{
  // 320 CSS px is a small phone, and also a 640 px window at 200% zoom.
  const context=await browser.newContext({viewport:{width:320,height:640},deviceScaleFactor:2,hasTouch:true,isMobile:true});
  // The page is served from a made-up address, so its own request for the stars can be answered: stars on.
  const open=async stars=>{
    const page=await context.newPage();
    await page.route('http://overview.test/**',route=>new URL(route.request().url()).pathname==='/'
      ?route.fulfill({contentType:'text/html; charset=utf-8',body:made.stdout})
      :stars?route.fulfill({contentType:'application/json',body:'{}'}):route.abort());
    await page.goto('http://overview.test/');
    await page.locator(stars?'.st:not(:disabled)':'#stars-off:not([hidden])').first().waitFor();
    return page;
  };
  const page=await open(true);
  // Measured against the 320 px asked for: a phone browser zooms out to fit a page that is too wide, so its own
  // innerWidth grows with the fault.
  const fits=async()=>({page:await page.evaluate(()=>document.documentElement.scrollWidth),screen:320});
  // Every fault is collected, so one run says all that is wrong.
  const faults=[],check=(ok,fault)=>{if(!ok)faults.push(fault);};

  let size=await fits();
  check(size.page<=size.screen,`the page is ${size.page} px wide on a ${size.screen} px screen`);

  // Every scene of the strip can be brought into view, and its number is not cut off.
  const strip=await page.evaluate(()=>{
    const cut=document.querySelector('.cut'),box=()=>cut.getBoundingClientRect();
    return {count:cut.children.length,scrolls:cut.scrollWidth>cut.clientWidth,focusable:cut.tabIndex===0,
      missed:[...cut.children].filter(part=>{
        part.scrollIntoView({inline:'nearest',block:'nearest'});
        const at=part.getBoundingClientRect(),number=document.createRange();number.selectNodeContents(part);
        return at.left<box().left-1||at.right>box().right+1||number.getBoundingClientRect().width>at.width;
      }).map(part=>part.textContent)};
  });
  assert.equal(strip.count,40);
  check(strip.scrolls&&strip.focusable,'the strip does not scroll sideways or does not take keyboard focus');
  check(!strip.missed.length,`scenes that cannot be reached or read in the strip: ${strip.missed.join(', ')}`);

  // Playing the take with the long title must not widen the page.
  await page.locator('[data-play]').first().click();
  await page.locator('#player .t').filter({hasText:LONG}).waitFor();
  size=await fits();
  check(size.page<=size.screen,`with the long title playing the page is ${size.page} px wide on a ${size.screen} px screen`);

  // A finger 22 px from the middle of a star in any direction still lands on the star: a 44 x 44 px target.
  // The archived takes are opened first, so their stars are measured too.
  const stars=await page.evaluate(()=>(document.querySelectorAll('details').forEach(more=>{more.open=true;}),[...document.querySelectorAll('.st')]).map(star=>{
    star.scrollIntoView({block:'center'});
    const at=star.getBoundingClientRect(),x=at.left+at.width/2,y=at.top+at.height/2;
    const hits=[[-21,-21],[21,-21],[-21,21],[21,21],[0,0]].every(([dx,dy])=>document.elementFromPoint(x+dx,y+dy)===star);
    return {hits,width:at.width,height:at.height};
  }));
  assert.equal(stars.length,41);
  check(stars.every(star=>star.width===26&&star.height===26),'the visible star is no longer 26 x 26 px');
  check(stars.every(star=>star.hits),`${stars.filter(star=>!star.hits).length} of ${stars.length} stars miss a touch inside their 44 x 44 px target`);
  // The larger target leaves the take itself playable.
  const thumb=await page.evaluate(()=>{const at=document.querySelector('.thumb').getBoundingClientRect();return document.elementFromPoint(at.left+30,at.top+35)?.closest('[data-play]')!==null;});
  check(thumb,'a touch on the picture no longer plays the take');
  // While the stars are off (no server), a touch beside a star plays the take: a star that cannot be used takes
  // no more room than it shows.
  const offline=await open(false);
  const beside=await offline.evaluate(()=>{const at=document.querySelector('.st').getBoundingClientRect();return document.elementFromPoint(at.left-6,at.bottom+6)?.closest('[data-play]')!==null;});
  check(beside,'with the stars off, a touch beside a star does not play the take');
  assert.deepEqual(faults,[]);
  console.log(`overview page at 320 px: ${strip.count} scenes reachable in the strip, long title wraps, ${stars.length} stars with a 44 px touch target`);
}finally{await browser.close();}

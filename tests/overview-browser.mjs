// Browser check of the overview page on a phone-sized screen: 40 made-up scenes, one long unbroken title and a
// server error that names a long folder.
// It reads no real video folder and needs no server. Run: node tests/overview-browser.mjs
// (Playwright's own Chromium by default; OVERVIEW_BROWSER=chrome uses the installed Chrome.)
import {chromium} from 'playwright';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';

const python=process.env.VIDEO_REVIEW_PYTHON||(process.platform==='win32'?'python':'python3');
const LONG='IMG_20250914_183022_HDR_portrait_edited_edited.HEIC';
// A scene-file error names the whole folder, and a folder name has no spaces to break at.
const ERROR=`No scenes.json in D:\\Recordings\\${'weekly_video_final_export_'.repeat(4)}raw: the scene list is saved when the first take is recorded.`;
// The page as it is written; star: with Lex's star on this take of the first scene (the page after that star).
const build=star=>{
  const made=spawnSync(python,['-c',`
import sys
sys.path[:0] = ['.', 'scripts/video']
import overview_page, scene_store
scenes = [{'id': f'{n:02d}-scene', 'number': n, 'title': ${JSON.stringify(LONG)} if n == 1 else f'Scene {n}'} for n in range(1, 41)]
store = scene_store.new_store(scenes)
for scene in scenes:
    scene_store.add_take(store, scene['id'], 1, duration=5.0 + scene['number'] * 3)
scene_store.add_take(store, '01-scene', 2, duration=20.0)
if ${star??0}:
    scene_store.set_star(store, '01-scene', ${star??0})
scene_store.apply_best(store)
sys.stdout.reconfigure(encoding='utf-8')
sys.stdout.write(overview_page.render(store, 'Narrow screen check'))
`],{encoding:'utf8',windowsHide:true});
  assert.equal(made.status,0,made.stderr);
  return made;
};
const made=build();

const browser=await chromium.launch(process.env.OVERVIEW_BROWSER?{channel:process.env.OVERVIEW_BROWSER}:{});
try{
  // 320 CSS px is a small phone, and also a 640 px window at 200% zoom.
  const context=await browser.newContext({viewport:{width:320,height:640},deviceScaleFactor:2,hasTouch:true,isMobile:true});
  // The page is served from a made-up address, so its own request for the stars can be answered: stars on.
  // listError, starError: the server answers the take list or a star click with this error instead.
  // later: what the take list answers when it is asked again after the page opened.
  // again: the page the address gives when it is loaded a second time (the page written after a star).
  const open=async(stars,{listError,starError,later,again}={})=>{
    const page=await context.newPage();
    let lists=0,loads=0;
    await page.route('http://overview.test/**',route=>{
      const path=new URL(route.request().url()).pathname;
      if(path==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:(loads++&&again||made).stdout});
      const error=path==='/api/scenes/star'?starError:listError;
      if(error)return route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error})});
      if(path==='/api/scenes')lists+=1;
      return stars?route.fulfill({contentType:'application/json',body:JSON.stringify(lists>1&&later||{})}):route.abort();
    });
    await page.goto('http://overview.test/');
    await page.locator(stars?'.st:not(:disabled)':'#stars-off:not(:empty)').first().waitFor();
    return page;
  };
  const page=await open(true);
  // Measured against the 320 px asked for: a phone browser zooms out to fit a page that is too wide, so its own
  // innerWidth grows with the fault.
  const fits=async(where=page)=>({page:await where.evaluate(()=>document.documentElement.scrollWidth),screen:320});
  // Every fault is collected, so one run says all that is wrong.
  const faults=[],check=(ok,fault)=>{if(!ok)faults.push(fault);};

  // The stars line is a live region: on the page and empty before anything is said, taking no room.
  const quiet=await page.evaluate(()=>{
    const line=document.getElementById('stars-off'),next=line.nextElementSibling,before=line.previousElementSibling;
    return {role:line.getAttribute('role'),shown:getComputedStyle(line).display!=='none',text:line.textContent,
      gap:next.getBoundingClientRect().top-before.getBoundingClientRect().bottom};
  });
  check(quiet.role==='status'&&quiet.shown&&quiet.text==='','the stars line is not an empty live region when the page opens');
  check(quiet.gap===14,`the empty stars line changes the room under the summary: ${quiet.gap} px, not 14`);

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
  // A real touch at a point of the second scene's take: true only when the point is on the picture and the take
  // then starts playing. The point is given from the star's corner, so it works with the star on or off.
  const plays=async(where,dx,dy)=>{
    const take=where.locator('.scene').nth(1).locator('.take').first();
    await take.scrollIntoViewIfNeeded();
    const at=await take.locator('.st').boundingBox(),x=at.x+dx,y=at.y+at.height+dy;
    if(!await where.evaluate(([x,y])=>!!document.elementFromPoint(x,y)?.closest('[data-play]'),[x,y]))return false;
    await where.touchscreen.tap(x,y);
    return take.evaluate(node=>node.getAttribute('aria-current')==='true');
  };
  // The larger target leaves the take itself playable: a touch well clear of the star plays it.
  check(await plays(page,-60,20),'a touch on the picture no longer plays the take');
  // While the stars are off (no server), a touch just beside a star plays the take: a star that cannot be used
  // takes no more room than it shows.
  const offline=await open(false);
  check(await plays(offline,-6,6),'with the stars off, a touch beside a star does not play the take');
  // With the stars on, that same point belongs to the star's 44 px target, so it must not play the take.
  check(!await plays(await open(true),-6,6),'with the stars on, a touch inside the star target plays the take instead');

  // The server refuses the take list with a long error: the line shows it and the page stays as wide as the screen.
  const refused=await open(false,{listError:ERROR});
  check(await refused.getByRole('status').filter({hasText:ERROR}).isVisible(),'a refused take list does not show the server\'s error');
  size=await fits(refused);
  check(size.page<=size.screen,`with a long server error the page is ${size.page} px wide on a ${size.screen} px screen`);
  // A star click the server cannot do: its error lands in the live region, the page keeps its width and the
  // stars stay usable.
  const failing=await open(true,{starError:ERROR});
  await failing.locator('.st').first().tap();
  const told=failing.getByRole('status').filter({hasText:ERROR});
  await told.waitFor();
  check(await told.evaluate(line=>line.id==='stars-off'),'a failed star click is not said in the stars line');
  size=await fits(failing);
  check(size.page<=size.screen,`after a failed star click the page is ${size.page} px wide on a ${size.screen} px screen`);
  await failing.locator('.st:not(:disabled)').first().waitFor();
  check(await failing.locator('.st:disabled').count()===0,'a failed star click leaves stars switched off');

  // A star the server saved although it reports a problem (the rough cut could not follow): the page loads the
  // saved state, so the star, the take in front, the strip and the total agree, and it still says what went wrong.
  const SAVED='The star is saved, but the rough cut could not be rebuilt: no cue file.';
  const state={scenes:[{id:'01-scene',star:1,best:1}]};
  const look=where=>where.evaluate(()=>({front:document.querySelector('.scene .row > .take').dataset.take,
    strip:document.querySelector('.cut div').title,total:document.querySelector('.sub').textContent,
    said:document.querySelector('[role=status]').textContent,off:document.querySelectorAll('.st:disabled').length}));
  const saved=await open(true,{starError:SAVED,later:state,again:build(1)});
  const before=await look(saved);
  check(before.front==='2'&&before.strip==='Scene 1, take 2','the first scene does not start with take 2 in front');
  await saved.locator('details').first().evaluate(more=>{more.open=true;});
  await saved.locator('.scene').first().locator('.st[data-take="1"]').tap();
  await saved.getByRole('status').filter({hasText:SAVED}).waitFor();
  await saved.locator('.scene').first().locator('.sh + .row > .take[data-take="1"] .st:not(:disabled)').waitFor();
  const after=await look(saved);
  check(after.front==='1'&&after.strip==='Scene 1, take 1'&&after.total!==before.total,
    `after a star that was saved with a problem the page shows take ${after.front} in front, "${after.strip}" and "${after.total}"`);
  check(after.said===SAVED&&after.off===0,'after loading the saved state the page does not say what went wrong, or its stars are off');
  // The same when the page could not be written again: loading it again changes nothing, so it says it is out of
  // date and keeps its stars off rather than show two answers.
  const stale=await open(true,{starError:SAVED,later:state});
  await stale.locator('details').first().evaluate(more=>{more.open=true;});
  await stale.locator('.scene').first().locator('.st[data-take="1"]').tap();
  await stale.getByRole('status').filter({hasText:'out of date'}).waitFor();
  const old=await look(stale);
  check(old.said.startsWith(SAVED)&&old.off===41,`a page that could not be written again says "${old.said}" with ${41-old.off} stars on`);
  assert.deepEqual(faults,[]);
  console.log(`overview page at 320 px: ${strip.count} scenes reachable in the strip, long title and long server error wrap, a failed star click is said in the live region, ${stars.length} stars with a 44 px touch target`);
}finally{await browser.close();}

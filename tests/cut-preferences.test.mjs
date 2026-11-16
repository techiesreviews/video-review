// The review page's preview reads its join fade from Lex's cut preferences file, as the cutter and the join check do.
import test from 'node:test';
import assert from 'node:assert/strict';
import {copyFile,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const web=new URL('../web/',import.meta.url);

test('the preview fade comes from the cut preferences file',async()=>{
  const folder=await mkdtemp(join(tmpdir(),'cut-preferences-'));
  try{
    const saved=JSON.parse(await readFile(new URL('cut-preferences.json',web),'utf8'));
    saved.lex.audioFadeMs.value=17;
    await writeFile(join(folder,'cut-preferences.json'),JSON.stringify(saved));
    await copyFile(new URL('cut-core.js',web),join(folder,'cut-core.js'));
    const {JOIN_FADE_MS}=await import(pathToFileURL(join(folder,'cut-core.js')).href);
    assert.equal(JOIN_FADE_MS,17);
  }finally{await rm(folder,{recursive:true,force:true});}
});

test('the page fade is the fade the cutter writes into every cut list',async()=>{
  const saved=JSON.parse(await readFile(new URL('cut-preferences.json',web),'utf8'));
  const {JOIN_FADE_MS}=await import('../web/cut-core.js');
  assert.equal(JOIN_FADE_MS,saved.lex.audioFadeMs.value);
});

// The browser and the hosted Worker write times the way the shared table (tests/fixtures/time-format-cases.json) says;
// tests/test_time_format.py runs the same table against time_format.py, so both languages write a time the same way.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as time from '../web/time-format.js';

const TABLE=JSON.parse(await readFile(new URL('./fixtures/time-format-cases.json',import.meta.url),'utf8'));

for(const row of TABLE.cases){
  test(`time format: ${row.name}`,()=>{
    const call=()=>time[row.call](...row.args);
    if(row.error)assert.throws(call);
    else assert.equal(call(),row.returns);
  });
}

test('the review rules still offer the shared timecode',async()=>{
  const core=await import('../web/review-core.js');
  assert.equal(core.timecode,time.timecode);
});

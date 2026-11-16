// The hosted page and cut-to-review.py must make the same fingerprint for a file: an interrupted upload finds its
// own registration again by it. The same sizes, bytes and hashes are in tests/test_cut_to_review.py
// (SampledFingerprint), which checks the Python side.
import test from 'node:test';
import assert from 'node:assert/strict';

// web/backend.js is written for a browser: it opens its database and looks at window when it loads. The
// fingerprint needs neither, so they are stand-ins here.
globalThis.window??={};
globalThis.indexedDB??={open:()=>({})};
const {sampledFingerprint}=await import('../web/backend.js');

const MiB=1<<20;
const CASES=[[5,'187f1dba81c7b20be04d43cb8bebcebe76faf62c8f48615fc8262b81b3b940d2'],
  [MiB,'1c6a7739d264dbdbfc78831c43139877bfcd361f3310577e3e343d63ee61d534'],
  [MiB*3/2,'c85aaf0b1b83fc273300afb7950654bf7fbac4ad0a09d8e15d964c4173bde8d8'],
  [MiB*3+11,'b3af718ae081f6fffff68b4d20f7b3d363c40e43e2a65afaf545dba46a2217c8']];

test('the page fingerprints a file as cut-to-review.py does, for small and large files',async()=>{
  for(const [size,expected] of CASES){
    const bytes=new Uint8Array(size);
    for(let index=0;index<size;index++)bytes[index]=(index*31+7)%251;
    assert.equal(await sampledFingerprint(new Blob([bytes])),'sample-sha256:'+expected,`${size} bytes`);
  }
});

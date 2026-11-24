// Web Audio is a browser boundary: failures must be observable without changing the media element.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createJoinFade} from '../web/join-fade.js';

test('missing Web Audio reports unavailable and leaves ordinary playback alone',()=>{
  const previous=globalThis.window;
  globalThis.window={};
  try{
    const video={paused:false,muted:false,volume:1,currentTime:2},states=[];
    const fade=createJoinFade(video,available=>states.push(available));
    fade.prepare();fade.fadeOut();fade.fadeIn();fade.reset();
    assert.deepEqual(states,[false]);
    assert.deepEqual(video,{paused:false,muted:false,volume:1,currentTime:2});
  }finally{globalThis.window=previous;}
});

test('audio setup failures report unavailable and retain an audible route',async()=>{
  const previous=globalThis.window;
  try{
    for(const failure of ['constructor','resume','source','gain','gain connection','source connection']){
      let captured=false,audible=true;
      const states=[];
      class Context{
        state=failure==='resume'?'suspended':'running';destination={};currentTime=1;
        constructor(){if(failure==='constructor')throw Error('Unavailable');}
        resume(){return Promise.reject(Error('Denied'));}
        createGain(){
          if(failure==='gain')throw Error('No gain');
          return {connect:()=>{if(failure==='gain connection')throw Error('No gain route');},disconnect(){}};
        }
        createMediaElementSource(){
          if(failure==='source')throw Error('No source');
          captured=true;audible=false;
          return {disconnect(){audible=false;},connect:target=>{
            if(failure==='source connection'&&target!==this.destination)throw Error('No gain route');
            audible=true;
          }};
        }
      }
      globalThis.window={AudioContext:Context};
      const video={paused:false,muted:false,volume:1},fade=createJoinFade(video,value=>states.push(value));
      fade.prepare();await Promise.resolve();fade.prepare();await Promise.resolve();
      assert.equal(states.at(-1),false,`${failure}: unavailable is observable`);
      assert.equal(audible,true,`${failure}: ${captured?'captured':'ordinary'} sound is not stranded`);
      assert.deepEqual(video,{paused:false,muted:false,volume:1});
    }
  }finally{globalThis.window=previous;}
});

test('successful audio startup never warns while resume is pending and retains 15 ms fades',async()=>{
  const previous=globalThis.window,states=[],ramps=[];
  let context,finishResume;
  class Context{
    state='suspended';destination={};currentTime=1;
    constructor(){context=this;}
    resume(){return new Promise(resolve=>{finishResume=resolve;});}
    createGain(){return {connect(){},gain:{cancelScheduledValues(){},setValueAtTime(){},linearRampToValueAtTime:(value,time)=>ramps.push([value,time])}};}
    createMediaElementSource(){return {connect(){}};}
  }
  globalThis.window={AudioContext:Context};
  try{
    const fade=createJoinFade({},value=>states.push(value));
    fade.prepare();assert.deepEqual(states,[],'Pending resume is not an unavailable fade');
    context.state='running';context.onstatechange();finishResume();await Promise.resolve();
    assert.deepEqual(states,[true]);
    fade.fadeOut(20);fade.fadeIn();fade.reset();
    assert.deepEqual(ramps,[[0,1.035],[1,1.015]],'The existing 15 ms ramps and delay stay unchanged');
    context.state='suspended';context.onstatechange();
    assert.deepEqual(states,[true,false],'An interrupted route no longer claims fades are available');
    fade.prepare();context.state='running';context.onstatechange();finishResume();await Promise.resolve();
    assert.deepEqual(states,[true,false,true],'A recovered context clears the warning');
  }finally{globalThis.window=previous;}
});

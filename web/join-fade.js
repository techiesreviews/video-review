// A short fade at each preview jump, so a join is heard the way the Resolve cut's native fades will sound.
// The sound only goes through Web Audio once its clock runs: a suspended context would silence the video.
import {JOIN_FADE_MS} from './cut-core.js';

const SECONDS=JOIN_FADE_MS/1000;

export function createJoinFade(video){
  const Context=window.AudioContext||window.webkitAudioContext;
  let context=null,gain=null,failed=false;
  function route(){
    if(gain||context?.state!=='running')return;
    try{const source=context.createMediaElementSource(video);gain=context.createGain();source.connect(gain);gain.connect(context.destination);}
    catch{failed=true;}
  }
  function prepare(){
    if(!context){
      if(failed||!Context)return;
      try{context=new Context();context.onstatechange=route;}catch{failed=true;return;}
    }
    if(context.state!=='running')context.resume().catch(()=>{});
    route();
  }
  function reset(){if(!gain)return;const now=context.currentTime;gain.gain.cancelScheduledValues(now);gain.gain.setValueAtTime(1,now);}
  return {
    prepare,reset,
    fadeOut(delayMs=0){if(!gain)return;const now=context.currentTime,start=now+delayMs/1000;gain.gain.cancelScheduledValues(now);gain.gain.setValueAtTime(1,start);gain.gain.linearRampToValueAtTime(0,start+SECONDS);},
    fadeIn(){if(!gain)return;const now=context.currentTime;gain.gain.cancelScheduledValues(now);gain.gain.setValueAtTime(0,now);gain.gain.linearRampToValueAtTime(1,now+SECONDS);},
  };
}

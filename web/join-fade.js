// A short fade at each preview jump, so a join is heard the way the Resolve cut's native fades will sound.
// The sound only goes through Web Audio once its clock runs: a suspended context would silence the video.
import {JOIN_FADE_MS} from './cut-core.js';

const SECONDS=JOIN_FADE_MS/1000;

export function createJoinFade(video,onAvailabilityChange=()=>{}){
  const Context=window.AudioContext||window.webkitAudioContext;
  let context=null,gain=null,source=null,failed=false,available=null;
  function report(value){if(available===value)return;available=value;onAvailabilityChange(value);}
  if(!Context)report(false);
  function route(){
    if(failed)return;
    if(context?.state!=='running'){report(false);return;}
    if(gain){report(true);return;}
    let candidate;
    try{
      // Build the output first: a gain failure must not capture and silence the media element.
      candidate=context.createGain();candidate.connect(context.destination);
      source=context.createMediaElementSource(video);source.connect(candidate);gain=candidate;
    }catch{
      failed=true;report(false);
      try{candidate?.disconnect();}catch{}
      // Once captured, the media element needs a direct route if connecting the fade failed.
      if(source){try{source.disconnect();source.connect(context.destination);}catch{}}
    }
    report(!failed);
  }
  function prepare(){
    if(!context){
      if(failed||!Context)return;
      try{context=new Context();context.onstatechange=route;}catch{failed=true;report(false);return;}
    }
    if(failed)return;
    if(context.state!=='running'){
      report(false);
      try{context.resume().then(route,()=>report(false));}catch{report(false);}
    }
    route();
  }
  function reset(){if(!gain)return;const now=context.currentTime;gain.gain.cancelScheduledValues(now);gain.gain.setValueAtTime(1,now);}
  return {
    prepare,reset,
    fadeOut(delayMs=0){if(!gain)return;const now=context.currentTime,start=now+delayMs/1000;gain.gain.cancelScheduledValues(now);gain.gain.setValueAtTime(1,start);gain.gain.linearRampToValueAtTime(0,start+SECONDS);},
    fadeIn(){if(!gain)return;const now=context.currentTime;gain.gain.cancelScheduledValues(now);gain.gain.setValueAtTime(0,now);gain.gain.linearRampToValueAtTime(1,now+SECONDS);},
  };
}

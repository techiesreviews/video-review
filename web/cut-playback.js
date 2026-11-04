// Plays over preview removals. Looks ahead so the jump lands on the join rather than a frame late, and
// starts the audio fade early instead of holding the jump back for it.
import {jumpPlan} from './cut-core.js';

// Only a safety net: seeks in a long-GOP file can take most of a second, and fading back in before one lands would click.
const LANDED_LATEST_MS=5000;

export function createCutPlayback({video,removals,fps,fade}){
  let armed=null,timer=null,jumping=false,watchdog=null;
  const plan=()=>jumpPlan(video.currentTime*1000,removals(),{rate:video.playbackRate,horizonMs:Math.max(70,2000/fps())});

  function cancel(){clearTimeout(timer);clearTimeout(watchdog);armed=null;jumping=false;fade.reset();}
  function landed(){if(!jumping)return;clearTimeout(watchdog);jumping=false;fade.fadeIn();}
  function jump(next){
    const fading=armed?.startMs===next.startMs;clearTimeout(timer);armed=null;
    if(next.toMs>=video.duration*1000-1){video.pause();video.currentTime=video.duration;return;}
    // Playback got past the look-ahead, for example in a background tab. Fade while seeking; never wait.
    if(!fading)fade.fadeOut();
    jumping=true;video.currentTime=next.toMs/1000;
    clearTimeout(watchdog);watchdog=setTimeout(landed,LANDED_LATEST_MS);
  }
  function fire(){
    const next=plan();
    if(!next||next.startMs!==armed?.startMs){cancel();return;}
    if(next.jumpInMs>1)timer=setTimeout(fire,next.jumpInMs);else jump(next);
  }
  function tick(){
    if(video.paused||video.seeking||jumping)return;
    const next=plan();
    if(!next){if(armed)cancel();return;}
    if(next.jumpInMs===0){jump(next);return;}
    if(armed?.startMs===next.startMs)return;
    clearTimeout(timer);armed=next;fade.fadeOut(next.fadeInMs);timer=setTimeout(fire,next.jumpInMs);
  }
  video.addEventListener('seeked',landed);
  video.addEventListener('pause',cancel);
  video.addEventListener('emptied',cancel);
  video.addEventListener('seeking',()=>{if(!jumping&&armed)cancel();});
  return {tick,cancel};
}

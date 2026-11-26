import {audioPeaks,peakMeter,waveformWindow,zoomWaveformWindow,adjustBoundary} from './waveform-core.js';
import {clock} from './time-format.js';
import {removedRanges} from './cut-core.js';
import {decodeRate,stretchRate,envelopeFromSamples,loudnessMeter,nextTask} from './cut-boundary.js';
import {readIndex,soundTrack,stretches,adtsFile,placement,NotReadable} from './mp4-audio.js';
const $=s=>document.querySelector(s);
const stamp=ms=>clock(ms,{places:3,hours:false});
// The largest file decoded whole: one the browser cannot read in stretches has to fit in memory at once.
const WHOLE_LIMIT=512*1024*1024;
/** needsLoudness: true when the decode would supply the cut edges' loudness, handed to onLoudness (an envelope, or a
 *  promise of one). Read in stretches, it comes in as it is measured: each envelope says how far it reaches (readyMs). */
export function createWaveform({media,seek,pause,onRange,onAudition,onLoudness=()=>{},needsLoudness=()=>false}){
  const panel=$('#waveformPanel'),canvas=$('#waveformCanvas'),wrap=$('#waveformWrap'),ctx=canvas.getContext('2d'),overview=$('#waveformOverview'),overviewWrap=$('#waveformOverviewWrap'),overviewCtx=overview.getContext('2d');
  let duration=0,fps=30,range=null,removed=[],peaks=null,peakMax=1,bucketMs=8,readyMs=Infinity,at=0,generation=0,loading=false,source=null,request=null,drag=null,view={startMs:0,endMs:1},spanMs=4000;
  let visible=true;try{visible=localStorage.getItem('techies-review-waveform')!=='false';}catch{}
  let zoomView=null;
  const zoomed=()=>view.endMs-view.startMs<duration-1;
  function positionPins(){const pins=$('#pins'),parent=visible?(range||zoomed()?overviewWrap:wrap):$('.track');if(pins&&pins.parentElement!==parent)parent.append(pins);}
  function visibility(){panel.hidden=!visible;document.body.classList.toggle('waveform-enabled',visible);$('#waveformToggle').setAttribute('aria-pressed',String(visible));positionPins();if(visible)draw();}
  visibility();
  $('#waveformToggle').onclick=()=>{visible=!visible;try{localStorage.setItem('techies-review-waveform',String(visible));}catch{}visibility();};
  function status(text){$('#waveformStatus').textContent=text;}
  function x(ms,w){return (ms-view.startMs)/(view.endMs-view.startMs)*w;}
  function positionHandles(w){
    for(const [edge,id] of [['start','#waveformStart'],['end','#waveformEnd']]){
      const node=$(id);node.hidden=!range||!peaks;
      if(range){node.style.left=x(range[edge+'Ms'],w)+'px';node.setAttribute('aria-valuemin','0');node.setAttribute('aria-valuemax',String(duration/1000));node.setAttribute('aria-valuenow',String(range[edge+'Ms']/1000));node.setAttribute('aria-valuetext',stamp(range[edge+'Ms']));}
    }
  }
  function draw(){
    if(!visible)return;const w=wrap.clientWidth;if(!w)return;
    const h=82,dpr=Math.min(2,devicePixelRatio||1);if(canvas.width!==Math.round(w*dpr)||canvas.height!==h*dpr){canvas.width=Math.round(w*dpr);canvas.height=h*dpr;}
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
    if(!drag)view=zoomView??(range?waveformWindow(duration||1,at,range,spanMs):{startMs:0,endMs:duration||1});
    canvas.dataset.startMs=String(view.startMs);canvas.dataset.endMs=String(view.endMs);
    const size=view.endMs-view.startMs,zoom=$('#waveformZoom'),custom=zoom.querySelector('[value="custom"]');
    const preset=[4,8,16,32].find(n=>Math.abs(size-n*1000)<1);
    custom.textContent=size<1000?(size/1000).toFixed(2)+' s':(size/1000).toFixed(1)+' s';
    zoom.value=!zoomed()?'fit':preset?String(preset):'custom';
    ctx.fillStyle='#353340';ctx.fillRect(0,40,w,1);
    // Cut stretches sit on a darker band with dimmed bars; the selection is one band with bright edges and bright bars.
    for(const [a,b] of removed){ctx.fillStyle='#121118';ctx.fillRect(x(a,w),17,x(b,w)-x(a,w),50);}
    if(range){const a=x(range.startMs,w),b=x(range.endMs,w);ctx.fillStyle='rgba(98,93,245,0.32)';ctx.fillRect(a,17,b-a,50);ctx.fillStyle='#8f8bff';ctx.fillRect(a,17,1,50);ctx.fillRect(b-1,17,1,50);}
    if(peaks){
      let cut=0;
      for(let px=0;px<w;px+=2){
        const ms=view.startMs+(view.endMs-view.startMs)*(px+1)/w;if(ms>readyMs)break;
        const a=Math.max(0,Math.floor((view.startMs+(view.endMs-view.startMs)*px/w)/bucketMs));
        const b=Math.min(peaks.length,Math.ceil((view.startMs+(view.endMs-view.startMs)*(px+2)/w)/bucketMs));
        let peak=0;for(let j=a;j<b;j++)peak=Math.max(peak,peaks[j]);
        while(cut<removed.length&&removed[cut][1]<=ms)cut++;
        ctx.fillStyle=range&&ms>=range.startMs&&ms<range.endMs?'#ecebff':cut<removed.length&&ms>=removed[cut][0]?'#4a4756':'#b3adc9';
        const height=Math.max(1,Math.min(44,peak/peakMax*44));ctx.fillRect(px,41-height/2,1.5,height);
      }
    }
    ctx.fillStyle='#b1a9c6';ctx.font='10px monospace';
    for(let i=0;i<=4;i++){const p=i*w/4;ctx.textAlign=i===0?'left':i===4?'right':'center';ctx.fillText(stamp(view.startMs+(view.endMs-view.startMs)*i/4),p,12);}
    if(at>=view.startMs&&at<=view.endMs){ctx.fillStyle='#fff';ctx.fillRect(x(at,w),17,1.5,50);}
    for(const node of [canvas,overview]){node.setAttribute('aria-valuemin','0');node.setAttribute('aria-valuemax',String(duration/1000));node.setAttribute('aria-valuenow',String(at/1000));node.setAttribute('aria-valuetext',stamp(at));}
    overviewWrap.hidden=!range&&!zoomed();
    positionPins();
    if(!overviewWrap.hidden){
      const oh=24;overview.width=Math.round(w*dpr);overview.height=oh*dpr;overviewCtx.setTransform(dpr,0,0,dpr,0,0);overviewCtx.clearRect(0,0,w,oh);
      if(peaks){overviewCtx.fillStyle='#827992';for(let px=0;px<w;px+=2){if((px+1)/w*(duration||1)>readyMs)break;const a=Math.floor(px/w*peaks.length),b=Math.min(peaks.length,Math.ceil((px+2)/w*peaks.length));let peak=0;for(let j=a;j<b;j++)peak=Math.max(peak,peaks[j]);const height=Math.max(1,peak/peakMax*20);overviewCtx.fillRect(px,12-height/2,1.5,height);}}
      overviewCtx.fillStyle='#a69bed33';overviewCtx.fillRect(view.startMs/(duration||1)*w,0,(view.endMs-view.startMs)/(duration||1)*w,oh);overviewCtx.fillStyle='#fff';overviewCtx.fillRect(at/(duration||1)*w,0,1.5,oh);
    }
    positionHandles(w);
  }
  const matches=ms=>Math.abs(ms-duration)<=Math.max(300,duration*.001);
  /** Reads byte ranges of the video file (a local file or the local server's copy), or null where ranges are refused. */
  async function rangeReader(url,signal){
    const first=await fetch(url,{headers:{Range:'bytes=0-15'},signal});
    const size=Number(/\/(\d+)$/.exec(first.headers.get('Content-Range')||'')?.[1]);
    if(first.status!==206||!size){await first.body?.cancel();return null;}
    await first.arrayBuffer();
    return {size,async read(start,end){
      const response=await fetch(url,{headers:{Range:`bytes=${start}-${end-1}`},signal});
      if(response.status!==206){await response.body?.cancel();throw new NotReadable('Could not read part of the local video.');}
      const bytes=new Uint8Array(await response.arrayBuffer());
      if(bytes.length!==end-start)throw new NotReadable('Could not read part of the local video.');
      return bytes;
    }};
  }
  /** Reads an MP4's sound a stretch at a time (TOOLS-479): the bars fill in from left to right while the rest is read,
   *  and only one stretch of sound is held at a time, so a file of many gigabytes reads as a small one does. */
  async function stretched(url,file,track,token,Offline){
    if(!matches(track.durationMs))throw Error('Audio duration does not match this review copy.');
    const loudness=needsLoudness(),rate=stretchRate(loudness),total=Math.ceil(Math.max(duration,track.durationMs)/1000*rate);
    const bars=peakMeter(rate,total),meter=loudness?loudnessMeter(rate,total):null;
    peaks=bars.peaks;peakMax=.00001;bucketMs=bars.bucketMs;readyMs=0;source=url;
    // A Delete just after Build waits a moment for the first stretch's loudness, as it does for the server's.
    let handOver=null,nextHand=0;if(meter)onLoudness(new Promise(done=>{handOver=done;}));
    // Measuring the loudness so far sorts every step that is in, so on a long recording it is handed over less often.
    const hand=done=>{
      const begin=performance.now(),env=meter.envelope(done?total:Math.round(readyMs/1000*rate),done);
      if(handOver){handOver(env);handOver=null;}else onLoudness(env);
      const now=performance.now();nextHand=now+Math.max(2000,20*(now-begin));
    };
    try{
      for(const [first,last] of stretches(track)){
        const {lead,drop,from,to}=placement(track,first,last,rate);
        const bytes=await adtsFile(track,lead,last,file.read);if(token!==generation)return;
        const audio=await new Offline(1,1,rate).decodeAudioData(bytes.buffer);if(token!==generation)return;
        // Exactly the samples this stretch covers on the video's clock: a decode a few samples short ends in silence.
        const channels=Array.from({length:audio.numberOfChannels},(_,i)=>{
          const decoded=audio.getChannelData(i).subarray(drop,drop+to-from);if(decoded.length===to-from)return decoded;
          const whole=new Float32Array(to-from);whole.set(decoded);return whole;
        });
        bars.add(channels,from);meter?.add(channels,from);readyMs=Math.min(duration,to/rate*1000);
        for(let i=Math.floor(from/rate*1000/bucketMs);i<peaks.length&&i*bucketMs<readyMs;i++)peakMax=Math.max(peakMax,peaks[i]);
        status(`Building waveform… ${Math.floor(readyMs/duration*100)}%`);draw();
        if(meter&&(handOver||performance.now()>=nextHand))hand(false);
        await nextTask();if(token!==generation)return;
      }
      if(meter)hand(true);readyMs=Infinity;
    }finally{handOver?.(null);}
  }
  /** Decodes the whole file at once: for a file the browser cannot read in stretches (not an MP4 with AAC sound). */
  async function whole(url,signal,token,Offline){
    const response=await fetch(url,{signal});if(!response.ok)throw Error('Could not read the local video.');
    if(Number(response.headers.get('Content-Length'))>WHOLE_LIMIT){await response.body?.cancel();throw Error('This browser cannot read the sound of a video this large in pieces. Try an MP4 review copy in Chrome or Edge.');}
    const data=await response.arrayBuffer();if(token!==generation)return;
    if(data.byteLength>WHOLE_LIMIT)throw Error('This browser cannot read the sound of a video this large in pieces. Try an MP4 review copy in Chrome or Edge.');
    status('Building waveform locally…');
    // One decode gives the bars and, when the loudness is still needed, the cut edges' loudness (decodeRate).
    const loudness=needsLoudness(),audio=await new Offline(1,1,decodeRate(duration,loudness)).decodeAudioData(data);if(token!==generation)return;
    if(!matches(audio.duration*1000))throw Error('Audio duration does not match this review copy.');
    const channels=Array.from({length:audio.numberOfChannels},(_,i)=>audio.getChannelData(i)),result=await audioPeaks(channels,audio.sampleRate);
    if(token!==generation)return;
    if(loudness)onLoudness(envelopeFromSamples(channels,audio.sampleRate));
    peaks=result.peaks;peakMax=.00001;for(const value of peaks)peakMax=Math.max(peakMax,value);bucketMs=result.bucketMs;readyMs=Infinity;source=url;
  }
  async function build(){
    const url=media.currentSrc||media.getAttribute('src');if(!url||!duration){status('Open the video file to build its waveform.');return;}
    if(loading||url===source&&peaks)return;
    const token=++generation;loading=true;request?.abort();request=new AbortController();$('#buildWaveform').disabled=true;status('Reading audio on this computer…');
    try{
      const Offline=window.OfflineAudioContext||window.webkitOfflineAudioContext;if(!Offline)throw Error('This browser cannot decode a local waveform. Try Chrome or Edge.');
      // An MP4 with AAC sound is read in stretches; any other file, or one whose ranges cannot be read, whole.
      let file=null,track=null;
      try{file=await rangeReader(url,request.signal);if(file)track=soundTrack(await readIndex(file.read,file.size));}
      catch(error){if(!(error instanceof NotReadable))throw error;if(file?.size>WHOLE_LIMIT)throw error;}
      if(token!==generation)return;
      if(track)await stretched(url,file,track,token,Offline);else await whole(url,request.signal,token,Offline);
      if(token!==generation)return;
      status('');$('#buildWaveform').hidden=true;draw();
    }catch(error){
      if(token!==generation||error.name==='AbortError')return;
      // A stretched read that stops part way drops the bars it drew, and Build waveform reads it again from the start.
      peaks=null;source=null;readyMs=Infinity;draw();
      status(error.name==='EncodingError'?'This browser could not read the audio. Try an MP4 review copy.':error.message);
    }
    finally{if(token===generation){loading=false;$('#buildWaveform').disabled=false;}}
  }
  $('#buildWaveform').onclick=build;
  $('#waveformZoom').onchange=()=>{const value=$('#waveformZoom').value;if(value==='custom')return;if(value==='fit')zoomView={startMs:0,endMs:duration||1};else{spanMs=Number(value)*1000;zoomView=waveformWindow(duration||1,at,null,spanMs);}draw();};
  panel.addEventListener('wheel',event=>{
    if(!event.ctrlKey)return;event.preventDefault();if(!duration||drag||!event.deltaY)return;
    at=Math.max(0,Math.min(duration,media.currentTime*1000));
    const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?wrap.clientWidth:1);
    zoomView=zoomWaveformWindow(duration,view,at,delta);draw();
  },{passive:false});
  function change(edge,value){if(!range||!Number.isFinite(value))return;pause();range=adjustBoundary(range,edge,value,duration,fps);onRange({...range});draw();}
  for(const [edge,id] of [['start','#waveformStart'],['end','#waveformEnd']]){
    $(id).dataset.edge=edge;
    $(id).onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const value=e.key==='Home'?0:e.key==='End'?duration:range[edge+'Ms']+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?5:1)*1000/fps;change(edge,value);}};
  }
  $('#waveformIn').onchange=()=>change('start',Number($('#waveformIn').value)*1000);
  $('#waveformOut').onchange=()=>change('end',Number($('#waveformOut').value)*1000);
  $('#auditionWaveformCut').onclick=()=>{if(range)onAudition({...range});};
  wrap.onpointerdown=e=>{if(e.button!==0||e.target.closest('.pin'))return;pause();const edge=e.target.closest('[data-edge]')?.dataset.edge;if(edge&&range){e.preventDefault();drag={edge};wrap.setPointerCapture(e.pointerId);}else{const rect=wrap.getBoundingClientRect();seek(Math.max(0,Math.min(duration,view.startMs+(e.clientX-rect.left)/rect.width*(view.endMs-view.startMs))));}};
  wrap.onpointermove=e=>{if(!drag)return;const rect=wrap.getBoundingClientRect();change(drag.edge,view.startMs+(e.clientX-rect.left)/rect.width*(view.endMs-view.startMs));};
  function finish(e){if(!drag)return;drag=null;if(wrap.hasPointerCapture(e.pointerId))wrap.releasePointerCapture(e.pointerId);draw();}
  wrap.onpointerup=finish;wrap.onpointercancel=finish;
  overview.onpointerdown=e=>{if(e.button!==0||e.target.closest('.pin'))return;pause();const rect=overview.getBoundingClientRect();range=null;zoomView=null;onRange(null);seek(Math.max(0,Math.min(duration,(e.clientX-rect.left)/rect.width*duration)));draw();};
  for(const node of [canvas,overview])node.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();seek(e.key==='Home'?0:e.key==='End'?duration:Math.max(0,Math.min(duration,at+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?5:1)*1000/fps)));}};
  new ResizeObserver(draw).observe(wrap);
  media.addEventListener('emptied',()=>{generation++;request?.abort();loading=false;peaks=null;source=null;readyMs=Infinity;$('#buildWaveform').hidden=false;$('#buildWaveform').disabled=false;status('');draw();});
  return {
    load(video){generation++;request?.abort();duration=video?.durationMs||0;fps=video?.fps||30;at=0;range=null;zoomView=null;removed=[];peaks=null;source=null;readyMs=Infinity;loading=false;$('#buildWaveform').hidden=false;$('#buildWaveform').disabled=!video;status('');this.setRange(null);draw();},
    setRange(next){if(next&&!drag&&zoomView&&zoomView.endMs-zoomView.startMs>=duration-1)zoomView=null;range=next;if(range&&zoomView&&!drag&&(range.startMs<view.startMs||range.endMs>view.endMs))zoomView=waveformWindow(duration,(range.startMs+range.endMs)/2,range,view.endMs-view.startMs);$('#waveformRange').hidden=!range;if(range){$('#waveformIn').value=(range.startMs/1000).toFixed(3);$('#waveformOut').value=(range.endMs/1000).toFixed(3);$('#waveformCutLength').textContent=((range.endMs-range.startMs)/1000).toFixed(3)+' s to remove';}$('#auditionWaveformCut').disabled=!range;draw();},
    setCuts(edits){removed=removedRanges(edits);draw();},
    tick(ms){const next=Math.floor(ms/40);if(next===Math.floor(at/40))return;at=ms;if(zoomView&&!drag&&(at<view.startMs||at>view.endMs))zoomView=waveformWindow(duration,at,null,view.endMs-view.startMs);draw();},
    reflow:draw,
  };
}

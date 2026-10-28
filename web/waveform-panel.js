import {audioPeaks,waveformWindow,adjustBoundary} from './waveform-core.js';
const $=s=>document.querySelector(s);
const clock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(Math.round(ms)%1000).padStart(3,'0')}`;
export function createWaveform({media,seek,pause,onRange,onAudition}){
  const panel=$('#waveformPanel'),canvas=$('#waveformCanvas'),wrap=$('#waveformWrap'),ctx=canvas.getContext('2d'),overview=$('#waveformOverview'),overviewWrap=$('#waveformOverviewWrap'),overviewCtx=overview.getContext('2d');
  let duration=0,fps=30,range=null,removed=[],peaks=null,peakMax=1,bucketMs=8,at=0,generation=0,loading=false,source=null,request=null,drag=null,view={startMs:0,endMs:1},spanMs=4000;
  let visible=true;try{visible=localStorage.getItem('techies-review-waveform')!=='false';}catch{}
  function positionPins(){const pins=$('#pins'),parent=visible?(range?overviewWrap:wrap):$('.track');if(pins&&pins.parentElement!==parent)parent.append(pins);}
  function visibility(){panel.hidden=!visible;document.body.classList.toggle('waveform-enabled',visible);$('#waveformToggle').setAttribute('aria-pressed',String(visible));positionPins();if(visible)draw();}
  visibility();
  $('#waveformToggle').onclick=()=>{visible=!visible;try{localStorage.setItem('techies-review-waveform',String(visible));}catch{}visibility();};
  function status(text){$('#waveformStatus').textContent=text;}
  function x(ms,w){return (ms-view.startMs)/(view.endMs-view.startMs)*w;}
  function positionHandles(w){
    for(const [edge,id] of [['start','#waveformStart'],['end','#waveformEnd']]){
      const node=$(id);node.hidden=!range||!peaks;
      if(range){node.style.left=x(range[edge+'Ms'],w)+'px';node.setAttribute('aria-valuemin','0');node.setAttribute('aria-valuemax',String(duration/1000));node.setAttribute('aria-valuenow',String(range[edge+'Ms']/1000));node.setAttribute('aria-valuetext',clock(range[edge+'Ms']));}
    }
  }
  function draw(){
    if(!visible)return;const w=wrap.clientWidth;if(!w)return;
    const h=82,dpr=Math.min(2,devicePixelRatio||1);if(canvas.width!==Math.round(w*dpr)||canvas.height!==h*dpr){canvas.width=Math.round(w*dpr);canvas.height=h*dpr;}
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
    if(!drag)view=range?waveformWindow(duration||1,at,range,spanMs):{startMs:0,endMs:duration||1};
    ctx.fillStyle='#353340';ctx.fillRect(0,40,w,1);
    for(const [a,b] of removed){ctx.fillStyle='#643e3a';ctx.fillRect(x(a,w),19,x(b,w)-x(a,w),46);}
    if(range){ctx.fillStyle='#49416e';ctx.fillRect(x(range.startMs,w),18,x(range.endMs,w)-x(range.startMs,w),48);}
    if(peaks){
      ctx.fillStyle='#b3adc9';
      for(let px=0;px<w;px+=2){
        const a=Math.max(0,Math.floor((view.startMs+(view.endMs-view.startMs)*px/w)/bucketMs));
        const b=Math.min(peaks.length,Math.ceil((view.startMs+(view.endMs-view.startMs)*(px+2)/w)/bucketMs));
        let peak=0;for(let j=a;j<b;j++)peak=Math.max(peak,peaks[j]);
        const height=Math.max(1,Math.min(44,peak/peakMax*44));ctx.fillRect(px,41-height/2,1.5,height);
      }
    }
    ctx.fillStyle='#b1a9c6';ctx.font='10px monospace';
    for(let i=0;i<=4;i++){const p=i*w/4;ctx.textAlign=i===0?'left':i===4?'right':'center';ctx.fillText(clock(view.startMs+(view.endMs-view.startMs)*i/4),p,12);}
    if(at>=view.startMs&&at<=view.endMs){ctx.fillStyle='#fff';ctx.fillRect(x(at,w),17,1.5,50);}
    for(const node of [canvas,overview]){node.setAttribute('aria-valuemin','0');node.setAttribute('aria-valuemax',String(duration/1000));node.setAttribute('aria-valuenow',String(at/1000));node.setAttribute('aria-valuetext',clock(at));}
    overviewWrap.hidden=!range;
    positionPins();
    if(range){
      const oh=24;overview.width=Math.round(w*dpr);overview.height=oh*dpr;overviewCtx.setTransform(dpr,0,0,dpr,0,0);overviewCtx.clearRect(0,0,w,oh);
      if(peaks){overviewCtx.fillStyle='#827992';for(let px=0;px<w;px+=2){const a=Math.floor(px/w*peaks.length),b=Math.min(peaks.length,Math.ceil((px+2)/w*peaks.length));let peak=0;for(let j=a;j<b;j++)peak=Math.max(peak,peaks[j]);const height=Math.max(1,peak/peakMax*20);overviewCtx.fillRect(px,12-height/2,1.5,height);}}
      overviewCtx.fillStyle='#a69bed33';overviewCtx.fillRect(view.startMs/(duration||1)*w,0,(view.endMs-view.startMs)/(duration||1)*w,oh);overviewCtx.fillStyle='#fff';overviewCtx.fillRect(at/(duration||1)*w,0,1.5,oh);
    }
    positionHandles(w);
  }
  async function build(){
    const url=media.currentSrc||media.getAttribute('src');if(!url||!duration){status('Open the video file to build its waveform.');return;}
    if(loading||url===source&&peaks)return;
    const token=++generation;loading=true;request?.abort();request=new AbortController();$('#buildWaveform').disabled=true;status('Reading audio on this computer…');
    try{
      const response=await fetch(url,{signal:request.signal});if(!response.ok)throw Error('Could not read the local video.');
      if(Number(response.headers.get('Content-Length'))>512*1024*1024)throw Error('Use a smaller review MP4 to build this waveform in the browser.');
      const data=await response.arrayBuffer();if(token!==generation)return;
      if(data.byteLength>512*1024*1024)throw Error('Use a smaller review MP4 to build this waveform in the browser.');
      status('Building waveform locally…');
      const Offline=window.OfflineAudioContext||window.webkitOfflineAudioContext;if(!Offline)throw Error('This browser cannot decode a local waveform. Try Chrome or Edge.');
      const audio=await new Offline(1,1,8000).decodeAudioData(data);if(token!==generation)return;
      if(Math.abs(audio.duration*1000-duration)>Math.max(300,duration*.001))throw Error('Audio duration does not match this review copy.');
      const result=audioPeaks(Array.from({length:audio.numberOfChannels},(_,i)=>audio.getChannelData(i)),audio.sampleRate);
      peaks=result.peaks;peakMax=.00001;for(const value of peaks)peakMax=Math.max(peakMax,value);bucketMs=result.bucketMs;source=url;
      status('Select words or a pause, then drag the cut handles.');$('#buildWaveform').hidden=true;draw();
    }catch(error){if(token===generation&&error.name!=='AbortError')status(error.name==='EncodingError'?'This browser could not read the audio. Try an MP4 review copy.':error.message);}
    finally{if(token===generation){loading=false;$('#buildWaveform').disabled=false;}}
  }
  $('#buildWaveform').onclick=build;
  $('#waveformZoom').onchange=()=>{spanMs=Number($('#waveformZoom').value)*1000;draw();};
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
  overview.onpointerdown=e=>{if(e.button!==0||e.target.closest('.pin'))return;pause();const rect=overview.getBoundingClientRect();range=null;onRange(null);seek(Math.max(0,Math.min(duration,(e.clientX-rect.left)/rect.width*duration)));draw();};
  for(const node of [canvas,overview])node.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();seek(e.key==='Home'?0:e.key==='End'?duration:Math.max(0,Math.min(duration,at+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?5:1)*1000/fps)));}};
  new ResizeObserver(draw).observe(wrap);
  media.addEventListener('emptied',()=>{generation++;request?.abort();loading=false;peaks=null;source=null;$('#buildWaveform').hidden=false;$('#buildWaveform').disabled=false;status('Build a waveform from the loaded audio.');draw();});
  return {
    load(video){generation++;request?.abort();duration=video?.durationMs||0;fps=video?.fps||30;range=null;removed=[];peaks=null;source=null;loading=false;$('#buildWaveform').hidden=false;$('#buildWaveform').disabled=!video;status(video?'Build a waveform from the loaded audio.':'Open a video to see its waveform.');this.setRange(null);draw();},
    setRange(next){range=next;$('#waveformRange').hidden=!range;if(range){$('#waveformIn').value=(range.startMs/1000).toFixed(3);$('#waveformOut').value=(range.endMs/1000).toFixed(3);$('#waveformCutLength').textContent=((range.endMs-range.startMs)/1000).toFixed(3)+' s to remove';}$('#auditionWaveformCut').disabled=!range;draw();},
    setCuts(edits){removed=edits.map(e=>[e.startMs,e.endMs]);draw();},
    tick(ms){const next=Math.floor(ms/40);if(next===Math.floor(at/40))return;at=ms;draw();},
    reflow:draw,
  };
}

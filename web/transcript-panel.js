import {normalizeTranscript,activeWordIndex,wordSelection,originalOmissions} from './transcript-core.js';
import {makeRemoval,removalBounds,skipRemoved,previewDuration,exportCut,removedRanges} from './cut-core.js';
import {createWaveform} from './waveform-panel.js';

const LABELS={remove:'Remove the selected passage',clipped:'This word or join is clipped; recover the missing beginning or ending',extend_before:'Give this passage more room before it starts',extend_after:'Let the end of this passage finish before cutting',mistake:'Check this mistake / retake and keep the complete take',pause:'Shorten this pause while preserving the breath and complete words',comment:'Note about the selected passage'};
const $=s=>document.querySelector(s);
const stamp=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`;
function element(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;}

export function createTranscriptPanel({media,seek,playContext,request,updateRequest,cancelRequest=async()=>{},showError,pause=()=>{}}){
  let transcript=null,selected=null,anchor=null,wordNodes=[],highlighted=-1,videoId=null,loadToken=0,dragAnchor=null,dragEnd=null,dragMoved=false,suppressClick=false,noteId=null,noteTimer=null,pendingFocus=null;
  const body=$('#transcriptText');
  let backendRef=null,videoRef=null,showRemoved=false,busy=false,dragPoint=null,scrollFrame=null;
  let pauseNodes=[],currentNode=null,auditionRange=null,pauseThreshold=450,sourceAnchor=null;
  const waveform=createWaveform({media,seek,pause,onRange:adjustSelection,onAudition:range=>{playContext(Math.max(0,range.startMs-750),Math.min(videoRef.durationMs,range.endMs+750));auditionRange=range;}});
  try{pauseThreshold=Number(localStorage.getItem('techies-review-pause-threshold'))||450;}catch{}
  if(![250,450,650,1000].includes(pauseThreshold))pauseThreshold=450;
  $('#pauseThreshold').value=String(pauseThreshold);
  function centre(node){if(!node||node.hidden||dragAnchor!=null)return;const rect=node.getBoundingClientRect(),area=body.getBoundingClientRect();body.scrollTop+=rect.top+rect.height/2-area.top-body.clientHeight/2;}
  function reflow(){const follow=$('#followTranscript').checked;body.classList.toggle('follow-centred',follow);const line=parseFloat(getComputedStyle(body).lineHeight)||32;const panel=$('#transcriptPanel'),siblings=[...panel.children].filter(n=>n!==body&&!n.hidden&&getComputedStyle(n).position!=='absolute');const available=Math.max(0,panel.clientHeight-siblings.reduce((sum,n)=>sum+n.getBoundingClientRect().height,0)-2);const pad=Math.max(0,(available-line)/2);body.style.setProperty('--reading-pad',pad+'px');if(follow)centre(currentNode);waveform.reflow();}
  new ResizeObserver(reflow).observe(body);
  const readingLayout=new ResizeObserver(reflow);for(const node of [$('#transcriptPanel'),$('#transcriptSelection'),$('#waveformPanel'),$('.transcript-tools'),$('#cutPreviewBar')])readingLayout.observe(node);
  new ResizeObserver(()=>{document.documentElement.style.setProperty('--transport-height',$('.controls').offsetHeight+'px');reflow();}).observe($('.controls'));
  $('#followTranscript').onchange=reflow;
  $('#pauseThreshold').onchange=()=>{pauseThreshold=Number($('#pauseThreshold').value);try{localStorage.setItem('techies-review-pause-threshold',String(pauseThreshold));}catch{}render();tick(media.currentTime*1000,!media.paused);};
  function selectionState(){if(!selected)return;$('#selectionTime').textContent=`${stamp(selected.startMs)} – ${stamp(selected.endMs)}`;$('#selectionQuote').textContent=selected.quote;try{waveform.setRange(removalBounds(transcript,selected,videoRef.durationMs,videoRef.fps));}catch{waveform.setRange(null);}}
  function adjustSelection(range){if(!range){clear();return;}if(!selected)return;selected={...selected,...range,precise:true};selectionState();}
  function noteState(id=null,text=''){noteId=id;$('#transcriptNote').value=text;$('#saveTranscriptNote').textContent=id?'Save changes':'Add note';$('#saveTranscriptNote').disabled=!text.trim();}
  function clear(){selected=null;anchor=null;sourceAnchor=null;auditionRange=null;noteState();for(const n of body.querySelectorAll('.selected'))n.classList.remove('selected');$('#transcriptPanel').classList.remove('source-selection');$('#restoreOriginalSelection').hidden=true;$('#restoreOriginalSelection').disabled=false;$('#transcriptSelection').hidden=true;$('[data-transcript-action="pause"]').hidden=true;waveform.setRange(null);}
  function choose(a,b=a){
    if(!transcript?.words.length)return;
    $('#transcriptPanel').classList.remove('source-selection');$('#restoreOriginalSelection').hidden=true;sourceAnchor=null;for(const n of body.querySelectorAll('.source-word.selected'))n.classList.remove('selected');
    noteState();
    $('[data-transcript-action="pause"]').hidden=true;
    selected={...wordSelection(transcript.words,a,b),a:Math.min(a,b),b:Math.max(a,b)};
    for(let i=0;i<wordNodes.length;i++)wordNodes[i].classList.toggle('selected',i>=selected.a&&i<=selected.b);
    $('#selectionTime').textContent=`${stamp(selected.startMs)} – ${stamp(selected.endMs)}`;
    $('#selectionQuote').textContent=selected.quote;$('#transcriptSelection').hidden=false;selectionState();
  }
  function chooseCut(cut){
    clear();selected={startMs:Math.max(0,cut.atMs-200),endMs:cut.atMs+200,cutId:cut.id,quote:'Join '+cut.id};
    $('#selectionTime').textContent=stamp(cut.atMs);$('#selectionQuote').textContent='Join '+cut.id+' — listen before and after the cut';$('#transcriptSelection').hidden=false;
    seek(cut.atMs);
    selectionState();
  }
  function render(){
    clear();body.replaceChildren();wordNodes=[];pauseNodes=[];currentNode=null;highlighted=-1;
    if(!transcript){body.append(element('p','transcript-empty','No transcript attached to this version yet. Import its word-timed JSON, or ask your connected agent to attach the HappyScribe words.'));return;}
    const cuts=[...transcript.cuts].sort((a,b)=>a.atMs-b.atMs);let cutIndex=0,count=0,paragraph=null;
    for(let i=0;i<transcript.words.length;i++){
      const word=transcript.words[i],previous=transcript.words[i-1];
      if(!paragraph||count>=38||(previous&&word.startMs-previous.endMs>650)){
        paragraph=element('p','transcript-paragraph');const time=element('button','paragraph-time',stamp(word.startMs));time.type='button';time.setAttribute('aria-label','Play at '+stamp(word.startMs));time.onclick=()=>seek(word.startMs);paragraph.append(time);body.append(paragraph);count=0;
      }
      if(previous&&word.startMs-previous.endMs>pauseThreshold){
        const pause=element('button','transcript-join transcript-pause',((word.startMs-previous.endMs)/1000).toFixed(1)+' s pause');
        pause.type='button';pause.title='Review this gap; keep natural breaths and deliberate emphasis';
        pause.dataset.startMs=previous.endMs;pause.dataset.endMs=word.startMs;
        pause.onclick=()=>{clear();selected={kind:'pause',startMs:previous.endMs,endMs:word.startMs,firstWordId:previous.id,lastWordId:word.id,quote:`Pause after “${previous.text}”, before “${word.text}”`};$('#selectionTime').textContent=`${stamp(selected.startMs)} – ${stamp(selected.endMs)}`;$('#selectionQuote').textContent=selected.quote;$('#transcriptSelection').hidden=false;$('[data-transcript-action="pause"]').hidden=false;selectionState();seek(previous.endMs);};
        pauseNodes.push(pause);
        paragraph.append(pause,document.createTextNode(' '));
      }
      while(cutIndex<cuts.length&&cuts[cutIndex].atMs<=word.startMs){
        const cut=cuts[cutIndex++],join=element('button','transcript-join'+(cut.risk?' risky':''),'↔ '+cut.id);
        join.type='button';join.title='Listen around the join at '+stamp(cut.atMs);join.setAttribute('aria-label',join.title);join.onclick=()=>chooseCut(cut);paragraph.append(join,document.createTextNode(' '));
      }
      const node=element('span','transcript-word',word.text);node.dataset.index=i;node.tabIndex=i===0?0:-1;node.setAttribute('role','button');node.setAttribute('aria-label',`${word.text}, ${stamp(word.startMs)}`);node.title=stamp(word.startMs);
      node.onclick=e=>{if(suppressClick)return;if(e.shiftKey&&anchor!=null){window.getSelection()?.removeAllRanges();choose(anchor,i);return;}if(window.getSelection()?.toString()&&body.contains(window.getSelection().anchorNode))return;anchor=i;choose(i);seek(word.startMs);};
      node.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();if(e.shiftKey&&anchor!=null)choose(anchor,i);else{anchor=i;choose(i);seek(word.startMs);}}else if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const next=Math.max(0,Math.min(wordNodes.length-1,i+(e.key==='ArrowLeft'?-1:1)));node.tabIndex=-1;wordNodes[next].tabIndex=0;wordNodes[next].focus();if(e.shiftKey){anchor??=i;choose(anchor,next);}}};
      paragraph.append(node,document.createTextNode(' '));wordNodes.push(node);count++;
    }
    refreshCuts();search();reflow();
  }
  function dragTo(x,y){
    const direct=document.elementFromPoint(x,y)?.closest('.transcript-word');let index=direct&&!direct.hidden?Number(direct.dataset.index):null;
    if(index==null){let best=Infinity;for(let i=0;i<wordNodes.length;i++){const node=wordNodes[i];if(node.hidden)continue;for(const r of node.getClientRects()){const dy=Math.max(r.top-y,0,y-r.bottom),dx=Math.max(r.left-x,0,x-r.right),distance=dy*dy*9+dx*dx;if(distance<best){best=distance;index=i;}}}}
    if(index==null)return;dragEnd=index;dragMoved||=dragEnd!==dragAnchor;
    if(dragMoved)for(let i=0;i<wordNodes.length;i++)wordNodes[i].classList.toggle('selected',!wordNodes[i].hidden&&i>=Math.min(dragAnchor,dragEnd)&&i<=Math.max(dragAnchor,dragEnd));
  }
  function autoScroll(){if(dragAnchor==null)return;const r=body.getBoundingClientRect(),y=dragPoint.y;const delta=y<r.top+30?-Math.min(16,(r.top+30-y)/2):y>r.bottom-30?Math.min(16,(y-r.bottom+30)/2):0;if(delta){body.scrollTop+=delta;dragTo(dragPoint.x,Math.max(r.top+2,Math.min(r.bottom-2,y)));}scrollFrame=requestAnimationFrame(autoScroll);}
  body.addEventListener('pointerdown',event=>{const word=event.target.closest('.transcript-word');if(!word||event.button!==0||event.pointerType==='touch')return;
    pause();event.preventDefault();window.getSelection()?.removeAllRanges();dragAnchor=event.shiftKey&&anchor!=null?anchor:Number(word.dataset.index);dragEnd=Number(word.dataset.index);dragMoved=event.shiftKey&&dragEnd!==dragAnchor;dragPoint={x:event.clientX,y:event.clientY};body.setPointerCapture(event.pointerId);scrollFrame=requestAnimationFrame(autoScroll);});
  body.addEventListener('pointermove',event=>{if(dragAnchor==null)return;dragPoint={x:event.clientX,y:event.clientY};dragTo(event.clientX,event.clientY);});
  body.addEventListener('pointerup',event=>{if(dragAnchor==null)return;cancelAnimationFrame(scrollFrame);const first=dragAnchor,last=dragEnd,moved=dragMoved;dragAnchor=dragEnd=null;if(body.hasPointerCapture(event.pointerId))body.releasePointerCapture(event.pointerId);anchor=first;choose(first,last);if(!moved)seek(transcript.words[last].startMs);suppressClick=true;setTimeout(()=>{suppressClick=false;},0);});
  body.addEventListener('pointercancel',()=>{dragAnchor=dragEnd=null;cancelAnimationFrame(scrollFrame);});
  function search(){
    const nodes=[...body.querySelectorAll('.transcript-word,.source-word')],query=$('#transcriptSearch').value.trim().toLowerCase(),text=nodes.map(n=>n.textContent.toLowerCase()).join(' '),matches=[];
    if(query){let at=text.indexOf(query);while(at>=0){matches.push([at,at+query.length]);at=text.indexOf(query,at+Math.max(1,query.length));}}
    let cursor=0;for(const node of nodes){const end=cursor+node.textContent.length;node.classList.toggle('found',matches.some(([a,b])=>cursor<b&&end>a));cursor=end+1;}
  }
  $('#transcriptSearch').addEventListener('input',search);
  $('#transcriptSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();const match=body.querySelector('.found');if(match){if(match.classList.contains('source-word')){match.click();centre(match);}else{centre(match);anchor=Number(match.dataset.index);choose(anchor);seek(transcript.words[anchor].startMs);}}}});
  $('#clearTranscriptSelection').onclick=()=>{window.getSelection()?.removeAllRanges();clear();};
  function refreshCuts(){
    const edits=transcript?.draftEdits||[],removedIds=new Set(edits.flatMap(e=>e.wordIds));
    document.body.classList.toggle('original-transcript',showRemoved);
    for(const button of body.querySelectorAll('.restore-cut'))button.remove();
    for(const block of body.querySelectorAll('.source-removal'))block.remove();
    for(let i=0;i<wordNodes.length;i++){const removed=removedIds.has(transcript.words[i].id);wordNodes[i].classList.toggle('removed-word',removed);wordNodes[i].hidden=removed&&!showRemoved;}
    for(const paragraph of body.querySelectorAll('.transcript-paragraph'))paragraph.hidden=!showRemoved&&![...paragraph.querySelectorAll('.transcript-word')].some(n=>!n.hidden);
    const ranges=removedRanges(edits);
    for(const gap of body.querySelectorAll('.transcript-pause')){const a=Number(gap.dataset.startMs),b=Number(gap.dataset.endMs),remaining=b-a-ranges.reduce((n,[x,y])=>n+Math.max(0,Math.min(b,y)-Math.max(a,x)),0);gap.hidden=!showRemoved&&remaining<=pauseThreshold;gap.textContent=((showRemoved?b-a:remaining)/1000).toFixed(1)+' s pause';}
    if(showRemoved)for(const edit of edits){
      let target=edit.kind==='pause'?[...body.querySelectorAll('.transcript-pause')].find(n=>Number(n.dataset.startMs)<=edit.startMs&&Number(n.dataset.endMs)>=edit.endMs):wordNodes.find((n,i)=>edit.wordIds.includes(transcript.words[i].id));
      target??=wordNodes.find((n,i)=>transcript.words[i].startMs>=edit.startMs)||wordNodes.at(-1);
      if(target){const button=element('button','restore-cut',edit.kind==='pause'?'Restore pause':'Restore');button.type='button';button.dataset.editId=edit.id;button.disabled=busy;button.title=`Restore ${stamp(edit.startMs)} – ${stamp(edit.endMs)}: ${edit.quote}`;button.setAttribute('aria-label',button.title);button.onclick=()=>restoreCut(edit.id);target.before(button);}
    }
    if(showRemoved)renderOriginalOmissions();
    const notice=$('#originalTranscriptNotice');notice.hidden=!showRemoved;
    notice.textContent=transcript?.original?'Original words include passages removed in Resolve. Request those back below; playback still uses this review copy.':'Includes cuts made in this review. Attach the full original word-timed transcript to also see earlier Resolve cuts.';
    waveform.setCuts(edits);
    $('#cutPreviewBar').hidden=!edits.length;$('#undoTranscriptCut').disabled=busy;
    $('#cutPreviewStatus').textContent=edits.length?`${showRemoved?'Original review audio':'Preview cut'} · ${stamp(showRemoved?videoRef.durationMs:previewDuration(videoRef.durationMs,edits))} · ${((videoRef.durationMs-previewDuration(videoRef.durationMs,edits))/1000).toFixed(1)} s ${showRemoved?'marked for removal':'removed'} · source times shown`:'';
    $('#showRemovedWords').textContent=showRemoved?'Edited transcript':'Original transcript';$('#showRemovedWords').setAttribute('aria-pressed',String(showRemoved));
    search();
  }
  function sourceSelection(group,words=group.words){return {kind:'source',startMs:group.atMs,endMs:null,sourceStartMs:words.length?words[0].startMs:group.sourceStartMs,sourceEndMs:words.length?words.at(-1).endMs:group.sourceEndMs,firstWordId:words[0]?.id,lastWordId:words.at(-1)?.id,quote:words.length?words.map(w=>w.text).join(' '):'Removed pause',groupId:group.id};}
  async function requestSourceRestore(selection,button){
    const existing=backendRef?.comments?.().some(c=>c.videoId===videoId&&c.status==='open'&&c.editRequest?.type==='restore'&&c.editRequest.sourceStartMs===selection.sourceStartMs&&c.editRequest.sourceEndMs===selection.sourceEndMs);
    if(existing){if(button)button.disabled=true;return;}
    if(busy)return;busy=true;if(button)button.disabled=true;pause();
    try{await request({...selection,text:`Restore from the original recording (${stamp(selection.sourceStartMs)} – ${stamp(selection.sourceEndMs)}): “${selection.quote}”`,editRequest:{type:'restore',quote:selection.quote,firstWordId:selection.firstWordId,lastWordId:selection.lastWordId,transcriptRevision:transcript.revision,sourceStartMs:selection.sourceStartMs,sourceEndMs:selection.sourceEndMs,sourceDurationMs:transcript.original.durationMs,sourceId:transcript.original.sourceId}});}
    catch(error){showError(error.message);}finally{busy=false;if(button)button.disabled=false;refreshCuts();}
  }
  function chooseSource(group,index,event){
    event?.preventDefault();pause();const first=event?.shiftKey&&sourceAnchor?.groupId===group.id?sourceAnchor.index:index;clear();sourceAnchor={groupId:group.id,index:first};
    const lo=Math.min(first,index),hi=Math.max(first,index);selected=sourceSelection(group,group.words.slice(lo,hi+1));currentNode=null;
    const block=[...body.querySelectorAll('.source-removal')].find(n=>n.dataset.sourceGap===group.id);
    block?.querySelectorAll('.source-word').forEach((node,i)=>node.classList.toggle('selected',i>=lo&&i<=hi));
    $('#selectionTime').textContent=`Original ${stamp(selected.sourceStartMs)} – ${stamp(selected.sourceEndMs)}`;$('#selectionQuote').textContent=selected.quote;$('#transcriptSelection').hidden=false;$('#transcriptPanel').classList.add('source-selection');$('#restoreOriginalSelection').hidden=false;reflow();centre(block?.querySelector('.selected'));
  }
  function renderOriginalOmissions(){
    const pending=backendRef?.comments?.().filter(c=>c.videoId===videoId&&c.status==='open'&&c.editRequest?.type==='restore')||[];
    for(const group of originalOmissions(transcript,videoRef)){
      const block=element('span','source-removal');block.dataset.sourceGap=group.id;
      const head=element('span','source-removal-head',`Removed in Resolve · original ${stamp(group.sourceStartMs)} – ${stamp(group.sourceEndMs)}`);block.append(head);
      const text=element('span','source-removal-words');
      if(group.words.length)group.words.forEach((word,i)=>{const node=element('span','source-word',word.text);node.dataset.wordId=word.id;node.tabIndex=0;node.setAttribute('role','button');node.title='Select to request this word back from the original';node.onclick=e=>chooseSource(group,i,e);node.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();chooseSource(group,i,e);}};text.append(node,document.createTextNode(' '));});
      else text.textContent=((group.sourceEndMs-group.sourceStartMs)/1000).toFixed(2)+' s of pause';
      const requested=pending.some(c=>c.editRequest.sourceStartMs===group.sourceStartMs&&c.editRequest.sourceEndMs===group.sourceEndMs);
      const restore=element('button','restore-cut source-restore',requested?'Restore requested':'Restore in Resolve');restore.type='button';restore.disabled=busy||requested;restore.onclick=()=>requestSourceRestore({...sourceSelection(group),sourceStartMs:group.sourceStartMs,sourceEndMs:group.sourceEndMs},restore);
      block.append(text,restore);
      const index=transcript.words.findIndex(w=>w.id===group.beforeWordId),target=index>=0?wordNodes[index]:null;
      if(target)target.before(block);else body.append(block);
    }
  }
  $('#restoreOriginalSelection').onclick=()=>{if(selected?.kind==='source')requestSourceRestore(selected,$('#restoreOriginalSelection'));};
  async function saveCuts(edits){
    const saved=await backendRef.saveTranscript(videoRef,{...transcript,draftEdits:edits});transcript=saved;clear();refreshCuts();
  }
  async function removeSelection(){
    if(!selected||busy||selected.kind==='source')return;busy=true;
    try{const edit=makeRemoval(transcript,selected,videoRef.durationMs,videoRef.fps),saved=await request(currentRequest(selected.kind==='pause'?'pause':'remove',`Remove from cut: “${selected.quote}”`));edit.commentId=saved?.id||null;await saveCuts([...(transcript.draftEdits||[]),edit]);seek(Math.max(0,edit.startMs-500));}
    catch(error){showError(error.message);}finally{busy=false;refreshCuts();}
  }
  async function undoCut(){
    const edit=transcript?.draftEdits?.at(-1);if(!edit||busy)return;busy=true;
    try{await saveCuts(transcript.draftEdits.slice(0,-1));if(edit.commentId)await cancelRequest(edit.commentId);seek(edit.startMs);}
    catch(error){showError(error.message);}finally{busy=false;refreshCuts();}
  }
  async function restoreCut(id){
    const edit=transcript?.draftEdits?.find(e=>e.id===id);if(!edit||busy)return;busy=true;pause();
    try{await saveCuts(transcript.draftEdits.filter(e=>e.id!==id));if(edit.commentId)await cancelRequest(edit.commentId);seek(edit.startMs);}
    catch(error){showError(error.message);}finally{busy=false;refreshCuts();}
  }
  $('#undoTranscriptCut').onclick=undoCut;
  $('#showRemovedWords').onclick=()=>{pause();showRemoved=!showRemoved;refreshCuts();reflow();};
  $('#playTranscriptSelection').onclick=()=>{auditionRange=null;if(selected)playContext(Math.max(0,selected.startMs-300),Math.min(videoRef.durationMs,selected.endMs+300));};
  $('#exportTranscriptCut').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(exportCut(transcript,videoRef),null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=videoId+'-cut.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  document.addEventListener('keydown',event=>{const editing=event.target.closest('input,textarea,[contenteditable="true"]');if(editing||event.altKey)return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'&&transcript?.draftEdits?.length){event.preventDefault();undoCut();}
    else if(['Delete','Backspace'].includes(event.key)&&selected){event.preventDefault();removeSelection();}});
  function currentRequest(type,text){return {...selected,editRequest:{type,quote:selected.quote,firstWordId:selected.firstWordId,lastWordId:selected.lastWordId,cutId:selected.cutId,transcriptRevision:transcript.revision},text};}
  for(const button of document.querySelectorAll('[data-transcript-action]'))button.onclick=async()=>{
    if(!selected)return;const type=button.dataset.transcriptAction;
    if(type==='remove'||type==='pause'){await removeSelection();return;}
    const buttons=[...document.querySelectorAll('[data-transcript-action]')],selection=selected;
    buttons.forEach(b=>b.disabled=true);
    try{const saved=await request(currentRequest(type,LABELS[type]+(selected.cutId?` (${selected.cutId})`:`: “${selected.quote}”`)));if(selected===selection&&saved)noteState(saved.id,saved.text);}
    catch(error){showError(error.message);}finally{buttons.forEach(b=>b.disabled=false);}
  };
  async function saveNote(event){event?.preventDefault();const text=$('#transcriptNote').value.trim();if(!text||!selected)return;clearTimeout(noteTimer);const currentId=noteId,selection=selected;$('#saveTranscriptNote').disabled=true;try{const saved=currentId?await updateRequest(currentId,text):await request(currentRequest('comment',text));if(selected===selection&&saved)noteState(saved.id,saved.text);}catch(error){showError(error.message);}finally{$('#saveTranscriptNote').disabled=!$('#transcriptNote').value.trim();}}
  $('#transcriptNoteForm').onsubmit=saveNote;
  $('#transcriptNote').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();saveNote();}};
  $('#transcriptNote').oninput=()=>{const text=$('#transcriptNote').value.trim();$('#saveTranscriptNote').disabled=!text;clearTimeout(noteTimer);if(noteId&&text){const id=noteId;noteTimer=setTimeout(()=>updateRequest(id,text).catch(error=>showError(error.message)),450);}};
  function focusAt(ms,editRequest=null,comment=null){
    if(!transcript){pendingFocus={ms,editRequest,comment};return;}
    if(editRequest?.type==='restore'&&transcript.original){showRemoved=true;refreshCuts();const group=originalOmissions(transcript,videoRef).find(g=>g.sourceStartMs<=editRequest.sourceStartMs&&g.sourceEndMs>=editRequest.sourceEndMs);if(group){currentNode=null;centre([...body.querySelectorAll('.source-removal')].find(n=>n.dataset.sourceGap===group.id));}return;}
    let first=editRequest?.firstWordId?transcript.words.findIndex(w=>w.id===editRequest.firstWordId):-1;
    let last=editRequest?.lastWordId?transcript.words.findIndex(w=>w.id===editRequest.lastWordId):-1;
    const cut=editRequest?.cutId?transcript.cuts.find(c=>c.id===editRequest.cutId):null;
    if(first<0){first=activeWordIndex(transcript.words,cut?.atMs??ms);if(first<0)first=transcript.words.findIndex(w=>w.startMs>=ms);if(first<0)first=transcript.words.length-1;}
    if(editRequest?.firstWordId&&transcript.words[first]?.id===editRequest.firstWordId)seek(transcript.words[first].startMs);
    choose(first,last>=first?last:first);
    if(cut){selected={startMs:Math.max(0,cut.atMs-200),endMs:cut.atMs+200,cutId:cut.id,quote:'Join '+cut.id};$('#selectionTime').textContent=stamp(cut.atMs);$('#selectionQuote').textContent='Join '+cut.id;}
    if(wordNodes[first]?.hidden){showRemoved=true;refreshCuts();}
    centre(wordNodes[first]);selectionState();
    if(comment)noteState(comment.id,comment.text);
  }
  function tick(ms,playing){
    if(!playing)auditionRange=null;waveform.tick(ms);if(!transcript)return;
    const index=activeWordIndex(transcript.words,ms);
    if(index!==highlighted){if(highlighted>=0)wordNodes[highlighted]?.classList.remove('spoken');highlighted=index;if(index>=0)wordNodes[index]?.classList.add('spoken');}
    let gap=null;for(const node of pauseNodes){const active=ms>=Number(node.dataset.startMs)&&ms<Number(node.dataset.endMs)&&!node.hidden;node.classList.toggle('playing',active);if(active)gap=node;}
    const next=index>=0?wordNodes[index]:gap;
    if(next&&next!==currentNode){currentNode=next;if($('#followTranscript').checked&&dragAnchor==null)centre(currentNode);}
  }
  return {
    async load(v,backend){
      const token=++loadToken;backendRef=backend;videoRef=v;videoId=v?.id;transcript=null;showRemoved=false;pendingFocus=null;waveform.load(v);$('#cutPreviewBar').hidden=true;$('#transcriptSummary').textContent=v?'Loading transcript…':'Open a video to read its transcript.';render();
      if(!v)return;
      try{const data=await backend.transcript(v);if(token!==loadToken)return;transcript=data?normalizeTranscript(data,v.durationMs):null;$('#transcriptSummary').textContent=transcript?`${transcript.words.length} words · ${transcript.label} · timings approximate`:'Use your existing HappyScribe word timings';render();if(pendingFocus){const focus=pendingFocus;pendingFocus=null;focusAt(focus.ms,focus.editRequest,focus.comment);}}
      catch(error){if(token===loadToken){$('#transcriptSummary').textContent='Could not load transcript';showError(error.message);}}
    },
    async import(v,backend,file){
      if(!v)throw Error('Open a video first.');if(file.size>8*1024*1024)throw Error('Transcript files must be under 8 MB.');
      const parsed=normalizeTranscript(JSON.parse(await file.text()),v.durationMs,v.fps);await backend.saveTranscript(v,parsed);if(videoId===v.id)await this.load(v,backend);
    },
    tick,
    reflow,
    reset:clear,
    focusAt,
    skip(ms){return skipRemoved(ms,[...(showRemoved?[]:transcript?.draftEdits||[]),...(auditionRange?[auditionRange]:[])]);},
    get selecting(){return dragAnchor!=null;},
  };
}

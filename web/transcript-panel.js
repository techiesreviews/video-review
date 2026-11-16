import {normalizeTranscript,activeWordIndex,originalOmissions} from './transcript-core.js';
import {skipRemoved,previewDuration,exportCut,removedRanges,playbackRemovals} from './cut-core.js';
import {createWaveform} from './waveform-panel.js';
import {barKey,suggestion,recentNotes,afterDelete,broughtBackLine,sentLine,barSpotOffWords,deletedIds,cutMarks} from './edit-bar-core.js';
import {createTranscriptEditing} from './transcript-editing.js';
import {clock} from './time-format.js';

const $=s=>document.querySelector(s);
const stamp=ms=>clock(ms,{places:3,hours:false});
function element(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!=null)node.textContent=text;return node;}

export function createTranscriptPanel({media,seek,playContext,request,updateRequest,cancelRequest=async()=>{},showError,pause=()=>{},cutsOn=()=>true,notify=()=>{},togglePlay=()=>{},agentConnected=async()=>false}){
  let wordNodes=[],highlighted=-1,videoId=null,loadToken=0,dragAnchor=null,dragEnd=null,dragMoved=false,suppressClick=false,pendingFocus=null;
  const body=$('#transcriptText');
  let backendRef=null,videoRef=null,barTarget=null,dragPoint=null,scrollFrame=null;
  let pauseNodes=[],currentNode=null,auditionRange=null,pauseThreshold=450;
  // What is selected and what an edit does live in transcript-editing.js; this file draws it and passes clicks in.
  const editing=createTranscriptEditing({saveTranscript:(v,data,options)=>backendRef.saveTranscript(v,data,options),request,updateRequest,cancelRequest,onError:error=>showError(error.message)});
  const waveform=createWaveform({media,seek,pause,onRange:adjustSelection,onAudition:range=>{playContext(Math.max(0,range.startMs-750),Math.min(videoRef.durationMs,range.endMs+750));auditionRange=range;}});
  try{pauseThreshold=Number(localStorage.getItem('techies-review-pause-threshold'))||450;}catch{}
  if(![250,450,650,1000].includes(pauseThreshold))pauseThreshold=450;
  $('#pauseThreshold').value=String(pauseThreshold);
  function centre(node){if(!node||node.hidden||dragAnchor!=null)return;const rect=node.getBoundingClientRect(),area=body.getBoundingClientRect();body.scrollTop+=rect.top+rect.height/2-area.top-body.clientHeight/2;}
  function reflow(){const follow=$('#followTranscript').checked;body.classList.toggle('follow-centred',follow);const line=parseFloat(getComputedStyle(body).lineHeight)||32;const panel=$('#transcriptPanel'),siblings=[...panel.children].filter(n=>n!==body&&!n.hidden&&!['absolute','fixed'].includes(getComputedStyle(n).position));const available=Math.max(0,panel.clientHeight-siblings.reduce((sum,n)=>sum+n.getBoundingClientRect().height,0)-2);const pad=Math.max(0,(available-line)/2);body.style.setProperty('--reading-pad',pad+'px');if(follow)centre(currentNode);waveform.reflow();placeBar();}
  new ResizeObserver(reflow).observe(body);
  const readingLayout=new ResizeObserver(reflow);for(const node of [$('#transcriptPanel'),$('#waveformPanel'),$('.transcript-tools'),$('#cutPreviewBar')])readingLayout.observe(node);
  new ResizeObserver(()=>{document.documentElement.style.setProperty('--transport-height',$('.controls').offsetHeight+'px');reflow();}).observe($('.controls'));
  $('#followTranscript').onchange=reflow;
  // The bar floats next to the selected words (TOOLS-129), placed as the script editor places its bar, never above the transcript.
  const bar=$('#transcriptSelection');let placeFrame=null;
  function placeBar(){
    if(bar.hidden)return;
    const nodes=[...body.querySelectorAll('.selected')].filter(n=>n.getClientRects().length),anchors=nodes.length?nodes:barTarget?.isConnected?[barTarget]:[];
    const area=body.getBoundingClientRect(),v=window.visualViewport,seenEnd=v?v.offsetTop+v.height:innerHeight,top=Math.max(area.top,v?.offsetTop||0),end=Math.min(area.bottom,seenEnd);
    const rects=anchors.map(n=>n.getBoundingClientRect()),first=rects[0]||{top:end,bottom:end,left:area.left,right:area.left},last=rects.at(-1)||first,w=bar.offsetWidth;
    // Lined up within the transcript; on a phone the bar is as wide as the screen, so the screen is its room.
    const inArea=w<=area.width-16,left=inArea?area.left:0,shift=r=>({top:r.top,bottom:r.bottom,left:r.left-left,right:r.right-left});
    const {x,y}=barSpotOffWords({first:shift(first),last:shift(last),w,h:bar.offsetHeight,top,vw:inArea?area.width:document.documentElement.clientWidth,end,seenEnd,line:parseFloat(getComputedStyle(body).lineHeight)||first.bottom-first.top});
    bar.style.setProperty('--bar-x',left+x+'px');bar.style.setProperty('--bar-y',y+'px');
  }
  function placeSoon(){if(placeFrame==null)placeFrame=requestAnimationFrame(()=>{placeFrame=null;placeBar();});}
  body.addEventListener('scroll',placeSoon,{passive:true});addEventListener('resize',placeSoon);window.visualViewport?.addEventListener('resize',placeSoon);window.visualViewport?.addEventListener('scroll',placeSoon);
  new ResizeObserver(placeSoon).observe(bar);
  function showBar(target=null){barTarget=target;bar.hidden=false;primaryState();placeBar();}
  // The first button deletes; on words that are all deleted already it brings them back instead.
  const DELETE_TITLE='Remove the selected words from the cut. Backspace or Delete does the same when the box is empty. Ctrl+Z undoes.';
  function primaryState(){const button=$('.bar-delete'),back=editing.toRestore().length>0;button.textContent=back?'Restore':'Delete';button.title=back?'Bring these deleted words back into the cut.':DELETE_TITLE;}
  $('#pauseThreshold').onchange=()=>{pauseThreshold=Number($('#pauseThreshold').value);try{localStorage.setItem('techies-review-pause-threshold',String(pauseThreshold));}catch{}render();tick(media.currentTime*1000,!media.paused);};
  function selectionState(){const selected=editing.selection;if(!selected)return;$('#selectionTime').textContent=`${stamp(selected.startMs)} – ${stamp(selected.endMs)}`;$('#selectionQuote').textContent=selected.quote;waveform.setRange(editing.bounds());}
  function adjustSelection(range){if(!range){clear();return;}if(editing.adjust(range))selectionState();}
  const noteBox=$('#transcriptNote');
  function ghostState(){
    const ghost=$('#transcriptGhost'),value=noteBox.value,atEnd=noteBox.selectionStart===value.length&&noteBox.selectionEnd===value.length;
    let full=document.activeElement===noteBox&&atEnd?suggestion(value,recentNotes(backendRef?.comments?.()||[])):'';
    ghost.replaceChildren();if(full){const typed=element('b','',value);ghost.append(typed,document.createTextNode(full.slice(value.length)));}
    // A suggestion that runs past the edge of the box could be accepted unseen, so it is not offered.
    // Once the typed text itself is wider than the box this is always true, so long notes get no suggestion, on purpose.
    if(full&&ghost.scrollWidth>ghost.clientWidth+1){ghost.replaceChildren();full='';}
    noteBox.dataset.suggestion=full;$('#barTab').hidden=!full;return full;
  }
  function barStatus(text=''){$('#barStatus').textContent=text;}
  function keepDraft(draft){if(draft&&!$('#transcriptSelection').hidden){noteBox.value=draft;ghostState();}}
  function showNote(text=''){noteBox.value=text;barStatus();ghostState();}
  // A note opened from a comment saves its typing after a short pause; leaving it saves it at once (transcript-editing.js).
  function noteState(id=null,text='',base=''){editing.editNote(id,base);showNote(text);}
  // With the bar closed (the box that had the focus is hidden), the focus goes to the words, as Escape does, not to the page.
  function focusWords(index,isWord=true){
    const active=document.activeElement;if(active&&active!==document.body&&!bar.contains(active))return;
    const target=isWord?wordNodes[index]:null;if(target&&!target.hidden)target.focus({preventScroll:true});else body.focus({preventScroll:true});
  }
  function focusBar(){if(!$('#transcriptSelection').hidden)noteBox.focus({preventScroll:true});}
  // clearView empties what shows the selection; clear also drops the selection itself.
  function clearView(){auditionRange=null;showNote();for(const n of body.querySelectorAll('.selected'))n.classList.remove('selected');$('#transcriptPanel').classList.remove('source-selection');$('#restoreOriginalSelection').hidden=true;$('#restoreOriginalSelection').disabled=false;bar.hidden=true;barTarget=null;waveform.setRange(null);}
  function clear(){editing.clear();clearView();}
  function choose(a,b=a,{focus=true}={}){
    if(!editing.transcript?.words.length)return;
    $('#transcriptPanel').classList.remove('source-selection');$('#restoreOriginalSelection').hidden=true;for(const n of body.querySelectorAll('.source-word.selected'))n.classList.remove('selected');
    const selected=editing.selectWords(a,b);showNote();
    for(let i=0;i<wordNodes.length;i++)wordNodes[i].classList.toggle('selected',i>=selected.a&&i<=selected.b);
    $('#selectionTime').textContent=`${stamp(selected.startMs)} – ${stamp(selected.endMs)}`;
    $('#selectionQuote').textContent=selected.quote;showBar();selectionState();if(focus)focusBar();
  }
  function chooseCut(cut,node=null){
    clear();editing.selectCut(cut);
    $('#selectionTime').textContent=stamp(cut.atMs);$('#selectionQuote').textContent='Join '+cut.id+' — listen before and after the cut';showBar(node);
    seek(cut.atMs);
    selectionState();focusBar();
  }
  function render(){
    clear();body.replaceChildren();wordNodes=[];pauseNodes=[];currentNode=null;highlighted=-1;
    const transcript=editing.transcript;
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
        pause.onclick=()=>{clearView();const selected=editing.selectPause(previous,word);$('#selectionTime').textContent=`${stamp(selected.startMs)} – ${stamp(selected.endMs)}`;$('#selectionQuote').textContent=selected.quote;showBar(pause);selectionState();seek(previous.endMs);focusBar();};
        pauseNodes.push(pause);
        paragraph.append(pause,document.createTextNode(' '));
      }
      while(cutIndex<cuts.length&&cuts[cutIndex].atMs<=word.startMs){
        const cut=cuts[cutIndex++],join=element('button','transcript-join'+(cut.risk?' risky':''),'↔ '+cut.id);
        join.type='button';join.title='Listen around the join at '+stamp(cut.atMs);join.setAttribute('aria-label',join.title);join.onclick=()=>chooseCut(cut,join);paragraph.append(join,document.createTextNode(' '));
      }
      const node=element('span','transcript-word',word.text);node.dataset.index=i;node.tabIndex=i===0?0:-1;node.setAttribute('role','button');node.setAttribute('aria-label',`${word.text}, ${stamp(word.startMs)}`);node.title=stamp(word.startMs);
      node.onclick=e=>{if(suppressClick)return;if(e.shiftKey&&editing.anchor!=null){window.getSelection()?.removeAllRanges();choose(editing.anchor,i);return;}if(window.getSelection()?.toString()&&body.contains(window.getSelection().anchorNode))return;editing.anchor=i;choose(i);seek(word.startMs);};
      node.onkeydown=e=>{if(e.key==='Enter'||(e.key===' '&&e.shiftKey)){e.preventDefault();if(e.shiftKey&&editing.anchor!=null)choose(editing.anchor,i);else{editing.anchor=i;choose(i);seek(word.startMs);}}else if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const next=Math.max(0,Math.min(wordNodes.length-1,i+(e.key==='ArrowLeft'?-1:1)));node.tabIndex=-1;wordNodes[next].tabIndex=0;wordNodes[next].focus();if(e.shiftKey){editing.anchor??=i;choose(editing.anchor,next,{focus:false});}}};
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
    pause();event.preventDefault();window.getSelection()?.removeAllRanges();dragAnchor=event.shiftKey&&editing.anchor!=null?editing.anchor:Number(word.dataset.index);dragEnd=Number(word.dataset.index);dragMoved=event.shiftKey&&dragEnd!==dragAnchor;dragPoint={x:event.clientX,y:event.clientY};body.setPointerCapture(event.pointerId);scrollFrame=requestAnimationFrame(autoScroll);});
  body.addEventListener('pointermove',event=>{if(dragAnchor==null)return;dragPoint={x:event.clientX,y:event.clientY};dragTo(event.clientX,event.clientY);});
  body.addEventListener('pointerup',event=>{if(dragAnchor==null)return;cancelAnimationFrame(scrollFrame);const first=dragAnchor,last=dragEnd,moved=dragMoved;dragAnchor=dragEnd=null;if(body.hasPointerCapture(event.pointerId))body.releasePointerCapture(event.pointerId);editing.anchor=first;choose(first,last);if(!moved)seek(editing.transcript.words[last].startMs);suppressClick=true;setTimeout(()=>{suppressClick=false;},0);});
  body.addEventListener('pointercancel',()=>{dragAnchor=dragEnd=null;cancelAnimationFrame(scrollFrame);});
  function search(){
    const nodes=[...body.querySelectorAll('.transcript-word,.source-word')],query=$('#transcriptSearch').value.trim().toLowerCase(),text=nodes.map(n=>n.textContent.toLowerCase()).join(' '),matches=[];
    if(query){let at=text.indexOf(query);while(at>=0){matches.push([at,at+query.length]);at=text.indexOf(query,at+Math.max(1,query.length));}}
    let cursor=0;for(const node of nodes){const end=cursor+node.textContent.length;node.classList.toggle('found',matches.some(([a,b])=>cursor<b&&end>a));cursor=end+1;}
  }
  $('#transcriptSearch').addEventListener('input',search);
  $('#transcriptSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();const match=body.querySelector('.found');if(match){if(match.classList.contains('source-word')){match.click();centre(match);}else{centre(match);const index=Number(match.dataset.index);editing.anchor=index;choose(index,index,{focus:false});seek(editing.transcript.words[index].startMs);}}}});
  $('#clearTranscriptSelection').onclick=()=>{window.getSelection()?.removeAllRanges();clear();};
  // The transcript always shows every original word (Lex, 2026-11-08). Deleted words stay where they were, struck through
  // and dimmed, with a Restore mark before each cut; playback and the exported cut list still skip them.
  function refreshCuts(){
    const transcript=editing.transcript,edits=editing.edits(),gone=deletedIds(edits),busy=editing.busy;
    for(const button of body.querySelectorAll('.restore-cut'))button.remove();
    for(const block of body.querySelectorAll('.source-removal'))block.remove();
    for(let i=0;i<wordNodes.length;i++){const word=transcript.words[i],removed=gone.has(word.id);wordNodes[i].classList.toggle('removed-word',removed);wordNodes[i].setAttribute('aria-label',`${removed?'Deleted: ':''}${word.text}, ${stamp(word.startMs)}`);}
    const ranges=removedRanges(edits);
    for(const gap of pauseNodes){const a=Number(gap.dataset.startMs),b=Number(gap.dataset.endMs),cut=ranges.reduce((n,[x,y])=>n+Math.max(0,Math.min(b,y)-Math.max(a,x)),0),seconds=ms=>(ms/1000).toFixed(1);gap.classList.toggle('removed-pause',cut>0);gap.textContent=cut>0?`${seconds(b-a)} → ${seconds(b-a-cut)} s pause`:`${seconds(b-a)} s pause`;}
    const marks=cutMarks(transcript?.words||[],edits);
    edits.forEach((edit,n)=>{
      const target=(edit.kind==='pause'?pauseNodes.find(node=>Number(node.dataset.startMs)<=edit.startMs&&Number(node.dataset.endMs)>=edit.endMs):null)??wordNodes[marks[n].index];
      if(target){const button=element('button','restore-cut',edit.kind==='pause'?'Restore pause':'Restore');button.type='button';button.dataset.editId=edit.id;button.disabled=busy;button.title=`Restore ${stamp(edit.startMs)} – ${stamp(edit.endMs)}: ${edit.quote}`;button.setAttribute('aria-label',button.title);button.onclick=()=>restoreCut([edit.id]);target.before(button);}
    });
    if(transcript)renderOriginalOmissions();
    waveform.setCuts(edits);
    $('#cutPreviewBar').hidden=!edits.length;$('#undoTranscriptCut').disabled=busy;
    $('#cutPreviewStatus').textContent=edits.length?`Preview cut · ${stamp(previewDuration(videoRef.durationMs,edits))} · ${((videoRef.durationMs-previewDuration(videoRef.durationMs,edits))/1000).toFixed(1)} s removed`:'';
    if(!bar.hidden){primaryState();placeBar();}
    search();
  }
  const comments=()=>backendRef?.comments?.()||[];
  async function requestSourceRestore(selection,button){
    if(editing.alreadyAsked(selection,comments())){if(button)button.disabled=true;return;}
    if(editing.busy)return;if(button)button.disabled=true;pause();
    try{await editing.askRestore(selection);}
    catch(error){showError(error.message);}finally{if(button)button.disabled=false;refreshCuts();}
  }
  function chooseSource(group,index,event){
    event?.preventDefault();pause();const {lo,hi}=editing.selectSource(group,index,{extend:!!event?.shiftKey});clearView();currentNode=null;
    const selected=editing.selection;
    const block=[...body.querySelectorAll('.source-removal')].find(n=>n.dataset.sourceGap===group.id);
    block?.querySelectorAll('.source-word').forEach((node,i)=>node.classList.toggle('selected',i>=lo&&i<=hi));
    $('#selectionTime').textContent=`Original ${stamp(selected.sourceStartMs)} – ${stamp(selected.sourceEndMs)}`;$('#selectionQuote').textContent=selected.quote;$('#transcriptPanel').classList.add('source-selection');$('#restoreOriginalSelection').hidden=false;showBar();centre(block?.querySelector('.selected'));placeBar();
  }
  function renderOriginalOmissions(){
    const transcript=editing.transcript,asked=comments();
    for(const group of originalOmissions(transcript,videoRef)){
      const block=element('span','source-removal');block.dataset.sourceGap=group.id;
      const head=element('span','source-removal-head',`Removed in Resolve · original ${stamp(group.sourceStartMs)} – ${stamp(group.sourceEndMs)}`);block.append(head);
      const text=element('span','source-removal-words');
      if(group.words.length)group.words.forEach((word,i)=>{const node=element('span','source-word',word.text);node.dataset.wordId=word.id;node.tabIndex=0;node.setAttribute('role','button');node.title='Select to request this word back from the original';node.onclick=e=>chooseSource(group,i,e);node.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();chooseSource(group,i,e);}};text.append(node,document.createTextNode(' '));});
      else text.textContent=((group.sourceEndMs-group.sourceStartMs)/1000).toFixed(2)+' s of pause';
      const whole=editing.wholeGap(group),requested=editing.alreadyAsked(whole,asked);
      const restore=element('button','restore-cut source-restore',requested?'Restore requested':'Restore in Resolve');restore.type='button';restore.disabled=editing.busy||requested;restore.onclick=()=>requestSourceRestore(whole,restore);
      block.append(text,restore);
      const index=transcript.words.findIndex(w=>w.id===group.beforeWordId),target=index>=0?wordNodes[index]:null;
      if(target)target.before(block);else body.append(block);
    }
  }
  $('#restoreOriginalSelection').onclick=()=>{if(editing.selection?.kind==='source')requestSourceRestore(editing.selection,$('#restoreOriginalSelection'));};
  // An edit saved while a new transcript came in: the panel loads it again, so it shows what was saved.
  function failed(error){showError(error.message);if(error.saved&&videoRef&&error.videoId===videoRef.id)panel.load(videoRef,backendRef);}
  function hasDraft(){const selected=editing.selection;return !editing.noteId&&!!noteBox.value.trim()&&!!selected&&selected.kind!=='source';}
  // An unsent note, including letters typed while an edit was saving, is handed back for the next selection.
  const draftAfter=done=>done.noteOpen?'':noteBox.value;
  async function removeSelection(){
    const selected=editing.selection;if(!selected||editing.busy||selected.kind==='source'||editing.toRestore().length)return;
    // A note typed but not sent belongs to these words, so it is saved with the cut's own note; Ctrl+Z puts it back in the box.
    const typedRaw=editing.noteId?'':noteBox.value;
    try{
      const done=await editing.remove({typed:typedRaw});if(!done)return;
      const boxNow=draftAfter(done),draft=boxNow.startsWith(typedRaw)?boxNow.slice(typedRaw.length).trimStart():boxNow;
      clearView();refreshCuts();seek(Math.max(0,done.edit.startMs-500));
      // Like a video editor: the words after the cut stay selected so the next delete or note is one key away.
      const after=afterDelete({next:done.next,draft,wasWords:done.wasWords});
      if(after.show==='next'){editing.anchor=done.next;choose(done.next);keepDraft(after.draft);barStatus(done.line);centre(wordNodes[done.next]);}
      // The very last words: they stay selected only when letters were typed during the save, so those are not lost.
      else if(after.show==='deleted'){editing.anchor=done.first;choose(done.first,done.last);keepDraft(after.draft);barStatus(done.line);}
      else focusWords(done.first,done.wasWords);
    }
    catch(error){failed(error);}finally{refreshCuts();}
  }
  async function undoCut(){
    if(!editing.edits().length||editing.busy)return;const fromBar=document.activeElement===noteBox;
    // A half-typed note belongs to what it was typed on, so that selection and the note stay untouched.
    const stay=hasDraft();
    try{
      const done=await editing.undo({keep:stay});if(!done)return;
      if(stay){barStatus(broughtBackLine({noteResolved:!!done.note}));return;}
      const draft=draftAfter(done);clearView();refreshCuts();seek(done.edit.startMs);
      // The words that came back are selected again, so a mistaken undo is one Backspace from being redone.
      if(done.first>=0&&done.last>=done.first){editing.anchor=done.first;choose(done.first,done.last,{focus:false});keepDraft([done.note,draft].filter(Boolean).join(' '));barStatus(broughtBackLine({selectedAgain:!noteBox.value}));if(fromBar)focusBar();}
    }
    catch(error){failed(error);}finally{refreshCuts();}
  }
  async function restoreCut(ids,{fromBar=false}={}){
    if(!editing.edits().some(e=>ids.includes(e.id))||editing.busy)return;pause();
    // With keep, the current selection and its note are left exactly as they are (bringing a cut back must not move a half-typed note).
    const stay=fromBar||hasDraft();
    try{
      const done=await editing.restore(ids,{keep:stay});if(!done)return;
      if(stay)barStatus(broughtBackLine({noteResolved:done.noteResolved,selectedAgain:fromBar&&!noteBox.value}));
      else{clearView();refreshCuts();seek(done.back[0].startMs);}
    }catch(error){failed(error);}finally{refreshCuts();}
  }
  $('#undoTranscriptCut').onclick=undoCut;
  $('#exportTranscriptCut').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(exportCut(editing.transcript,videoRef),null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=videoId+'-cut.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  document.addEventListener('keydown',event=>{const typing=event.target.closest('input,textarea,[contenteditable="true"]');if(typing||event.altKey)return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'&&editing.edits().length){event.preventDefault();undoCut();}
    else if(['Delete','Backspace'].includes(event.key)&&editing.selection&&!event.repeat){event.preventDefault();removeSelection();}
    else if(event.key==='Escape'&&editing.selection){event.preventDefault();leaveBar();}});
  $('.bar-delete').onclick=async()=>{
    if(!editing.selection)return;const back=editing.toRestore();
    if(back.length)await restoreCut(back.map(e=>e.id),{fromBar:true});else await removeSelection();
    focusBar();
  };
  // One text box: Enter sends it as a request to the agent (kept as a note in the comment list either way).
  async function sendBar(){
    const sent=noteBox.value;if(!sent.trim()||!editing.selection||editing.sending)return;
    // Letters typed after Enter, while the note is still saving, stay in the box.
    const rest=()=>noteBox.value.startsWith(sent)?noteBox.value.slice(sent.length).trimStart():noteBox.value;
    try{
      const done=await editing.send(sent);
      if(done?.current){noteState(null,rest());barStatus(done.kind==='updated'?'Note updated.':sentLine(await agentConnected()));}
    }catch(error){showError(error.message);}finally{focusBar();ghostState();}
  }
  $('#transcriptNoteForm').onsubmit=event=>{event.preventDefault();sendBar();};
  function leaveBar(){const selected=editing.selection,target=selected?.a!=null?wordNodes[selected.a]:null;window.getSelection()?.removeAllRanges();clear();if(target&&!target.hidden)target.focus({preventScroll:true});else body.focus({preventScroll:true});}
  noteBox.addEventListener('keydown',event=>{
    const value=noteBox.value,atEnd=noteBox.selectionStart===value.length&&noteBox.selectionEnd===value.length;
    const action=barKey(event,{value,suggestion:noteBox.dataset.suggestion||'',caretAtEnd:atEnd});
    // Enter never submits the form directly; only the 'send' action sends, so Shift+Enter does nothing.
    if(event.key==='Enter'&&!event.isComposing)event.preventDefault();
    if(!action)return;event.preventDefault();
    if(action==='accept'){noteBox.value=noteBox.dataset.suggestion;noteBox.setSelectionRange(noteBox.value.length,noteBox.value.length);noteBox.dispatchEvent(new Event('input'));}
    else if(action==='send')sendBar();
    else if(action==='delete'){removeSelection().then(focusBar);}
    else if(action==='undo')undoCut();
    else if(action==='play')togglePlay();
    else if(action==='clear-text'){noteBox.value='';noteBox.dispatchEvent(new Event('input'));}
    else if(action==='clear-selection')leaveBar();
  });
  noteBox.addEventListener('input',()=>{barStatus();ghostState();editing.noteTyped(noteBox.value);});
  for(const name of ['keyup','click','focus','blur','select'])noteBox.addEventListener(name,ghostState);
  function focusAt(ms,editRequest=null,comment=null){
    const transcript=editing.transcript;
    if(!transcript){pendingFocus={ms,editRequest,comment};return;}
    if(editRequest?.type==='restore'&&transcript.original){const group=originalOmissions(transcript,videoRef).find(g=>g.sourceStartMs<=editRequest.sourceStartMs&&g.sourceEndMs>=editRequest.sourceEndMs);if(group){currentNode=null;centre([...body.querySelectorAll('.source-removal')].find(n=>n.dataset.sourceGap===group.id));}return;}
    const {first,last,cut,seekMs}=editing.target(ms,editRequest);
    if(seekMs!=null)seek(seekMs);
    choose(first,last,{focus:false});
    if(cut){editing.selectCut(cut);$('#selectionTime').textContent=stamp(cut.atMs);$('#selectionQuote').textContent='Join '+cut.id;}
    centre(wordNodes[first]);selectionState();
    if(comment)noteState(comment.id,comment.text);
  }
  function tick(ms,playing){
    if(!playing)auditionRange=null;waveform.tick(ms);const transcript=editing.transcript;if(!transcript)return;
    const index=activeWordIndex(transcript.words,ms);
    if(index!==highlighted){if(highlighted>=0)wordNodes[highlighted]?.classList.remove('spoken');highlighted=index;if(index>=0)wordNodes[index]?.classList.add('spoken');}
    let gap=null;for(const node of pauseNodes){const active=ms>=Number(node.dataset.startMs)&&ms<Number(node.dataset.endMs)&&!node.hidden;node.classList.toggle('playing',active);if(active)gap=node;}
    const next=index>=0?wordNodes[index]:gap;
    if(next&&next!==currentNode){currentNode=next;if($('#followTranscript').checked&&dragAnchor==null)centre(currentNode);}
  }
  function removals(){return playbackRemovals(cutsOn(),editing.edits(),auditionRange);}
  const panel={
    async load(v,backend){
      const token=++loadToken;backendRef=backend;videoRef=v;videoId=v?.id;editing.load(null,v);pendingFocus=null;waveform.load(v);$('#cutPreviewBar').hidden=true;$('#transcriptSummary').textContent=v?'Loading transcript…':'Open a video to read its transcript.';render();
      if(!v)return;
      try{const data=await backend.transcript(v);if(token!==loadToken)return;const transcript=data?normalizeTranscript(data,v.durationMs):null;editing.load(transcript,v);$('#transcriptSummary').textContent=transcript?`${transcript.words.length} words · ${transcript.label} · timings approximate`:'Use your existing HappyScribe word timings';render();if(pendingFocus){const focus=pendingFocus;pendingFocus=null;focusAt(focus.ms,focus.editRequest,focus.comment);}}
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
    removals,
    skip(ms){return skipRemoved(ms,removals());},
    edits(){return editing.edits();},
    showCut(){auditionRange=null;},
    changeContext(startMs,endMs){return editing.changeContext(startMs,endMs);},
    get selecting(){return dragAnchor!=null;},
  };
  return panel;
}

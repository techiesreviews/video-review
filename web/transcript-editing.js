// Transcript editing: what is selected, what a Delete cuts, what Undo and Restore bring back, and how a note is saved.
// There is no page here. The panel passes clicks in and draws what this module holds; the browser parts (saving the
// transcript, the comment list, timers) are passed in, so every rule runs in a test without a browser.
import {activeWordIndex,wordSelection,wordsInSpan} from './transcript-core.js';
import {makeRemoval,removalBounds} from './cut-core.js';
import {cutsToRestore,deletedIds,keptRuns,lastCutGroup,nextWordAfterDelete} from './edit-bar-core.js';
import {clock} from './time-format.js';
import {RULES} from './review-core.js';

const stamp=ms=>clock(ms,{places:3,hours:false});
const NOTE_PAUSE_MS=450;
export const RELOADED_TEXT='The transcript changed while this was saving. The edit was saved, and the transcript is loaded again.';

function afterTimer(run,ms){const timer=setTimeout(run,ms);return ()=>clearTimeout(timer);}

/**
 * saveTranscript(video, transcript, {editsOnly:true}) saves the preview cuts and returns the saved transcript; the store
 * refuses it when its words are no longer the ones the cuts were made on; request(selection) adds a comment and
 * returns it; updateRequest(id, text) changes a comment's text; cancelRequest(id) closes a comment whose cut is gone.
 * onError(error) hears about a note saved in the background that failed. schedule(run, ms) returns a cancel function.
 */
export function createTranscriptEditing({saveTranscript,request,updateRequest,cancelRequest=async()=>{},onError=()=>{},schedule=afterTimer}){
  let transcript=null,video=null,selection=null,anchor=null,sourceAnchor=null;
  // A new transcript (another video, a new render, an agent's import) starts a new generation; a save begun in an
  // older one must not land on the new words.
  let generation=0,busy=false,sending=false;
  // The comment the note box is editing (opened from the comment list), and its typing not yet saved.
  let note={id:null,base:''},pendingNote=null,cancelNoteTimer=null;
  // Notes typed with a Delete, by comment id: Undo hands them back to the box.
  const cutNotes=new Map();

  const edits=()=>transcript?.draftEdits||[];
  const indexOf=id=>transcript.words.findIndex(w=>w.id===id);

  function flushNote(){
    cancelNoteTimer?.();cancelNoteTimer=null;
    const pending=pendingNote;pendingNote=null;
    if(pending)updateRequest(pending.id,pending.text).catch(onError);
  }
  function editNote(id=null,base=''){flushNote();note={id,base};}
  function noteTyped(text){
    cancelNoteTimer?.();cancelNoteTimer=null;
    const typed=String(text).trim();
    // While Enter is still saving the note, new letters start the next note instead.
    pendingNote=note.id&&typed&&!sending?{id:note.id,text:note.base?`${note.base} — ${typed}`:typed}:null;
    if(pendingNote)cancelNoteTimer=schedule(flushNote,NOTE_PAUSE_MS);
  }

  function clear(){selection=null;anchor=null;sourceAnchor=null;editNote();}
  function selectWords(a,b=a){
    if(!transcript?.words.length)return null;
    sourceAnchor=null;editNote();
    selection={...wordSelection(transcript.words,a,b),a:Math.min(a,b),b:Math.max(a,b)};
    return selection;
  }
  function selectCut(cut){
    selection={startMs:Math.max(0,cut.atMs-200),endMs:cut.atMs+200,cutId:cut.id,quote:'Join '+cut.id};
    return selection;
  }
  function selectPause(previous,next){
    clear();
    selection={kind:'pause',startMs:previous.endMs,endMs:next.startMs,firstWordId:previous.id,lastWordId:next.id,quote:`Pause after “${previous.text}”, before “${next.text}”`};
    return selection;
  }
  function sourceSelection(group,words=group.words){
    return {kind:'source',startMs:group.atMs,endMs:null,
      sourceStartMs:words.length?words[0].startMs:group.sourceStartMs,sourceEndMs:words.length?words.at(-1).endMs:group.sourceEndMs,
      firstWordId:words[0]?.id,lastWordId:words.at(-1)?.id,quote:words.length?words.map(w=>w.text).join(' '):'Removed pause',groupId:group.id};
  }
  /** A whole gap of the original recording, as its Restore in Resolve button asks for it. */
  function wholeGap(group){return {...sourceSelection(group),sourceStartMs:group.sourceStartMs,sourceEndMs:group.sourceEndMs};}
  /** Original words a Resolve cut removed. With extend (Shift), from the earlier pick in the same gap to this one. */
  function selectSource(group,index,{extend=false}={}){
    const first=extend&&sourceAnchor?.groupId===group.id?sourceAnchor.index:index;
    clear();sourceAnchor={groupId:group.id,index:first};
    const lo=Math.min(first,index),hi=Math.max(first,index);
    selection=sourceSelection(group,group.words.slice(lo,hi+1));
    return {lo,hi};
  }
  /** The waveform handles set an exact span; none (null) clears the selection. False when nothing is selected after. */
  function adjust(range){
    if(!range){clear();return false;}
    if(!selection)return false;
    selection={...selection,...range,precise:true};return true;
  }
  function selectedIds(){return selection?.a!=null&&transcript?transcript.words.slice(selection.a,selection.b+1).map(w=>w.id):[];}
  /** The cuts that bring the selection back, when every selected word is deleted already. */
  function toRestore(){return cutsToRestore(edits(),selectedIds());}
  /** The frame-aligned span a Delete would cut, or null for a selection too short for one. */
  function bounds(){
    if(!selection||!transcript)return null;
    try{return removalBounds(transcript,selection,video.durationMs,video.fps);}
    catch{return null;}
  }

  function askFor(type,text,chosen=selection){
    return {...chosen,editRequest:{type,quote:chosen.quote,firstWordId:chosen.firstWordId,lastWordId:chosen.lastWordId,cutId:chosen.cutId,transcriptRevision:transcript.revision},text};
  }
  function changeAsk(chosen){
    const words=chosen.a!=null?transcript.words.slice(chosen.a,chosen.b+1).map(w=>w.id):[];
    return {type:'change',quote:chosen.quote.slice(0,3000),firstWordId:chosen.firstWordId,lastWordId:chosen.lastWordId,wordIds:words,cutId:chosen.cutId,transcriptRevision:transcript.revision};
  }
  const stale=at=>at!==generation;
  /** Saves the cut list on the transcript it was worked out from. keep: the selection and note stay as they are.
   *  A new transcript that came in before the save: nothing is saved. One that came in during it, on the same words (so
   *  the store took the save): the error says saved, and the panel loads the transcript again to show it. */
  async function commit(at,draftEdits,{keep=false}={}){
    if(stale(at))throw Error(RULES.messages.transcriptChanged);
    const saved=await saveTranscript(video,{...transcript,draftEdits},{editsOnly:true});
    if(stale(at))throw Object.assign(Error(RELOADED_TEXT),{saved:true});
    transcript=saved;
    // Whether the note box was editing a comment when the save landed: then its text is that comment's, not a draft.
    const noteOpen=note.id!=null;
    if(!keep)clear();
    return noteOpen;
  }
  /** Runs one edit at a time: while a cut, Undo or Restore saves, another is refused (null). */
  async function exclusive(run){
    if(busy)return null;busy=true;
    try{return await run(generation);}
    finally{busy=false;}
  }

  /** Delete: cuts the selection (words, a pause, a join span). Words already deleted inside it stay in their own cut,
   *  so only the runs still in the cut are cut, one piece each, sharing one note. typed: the note typed with it. */
  function remove({typed=''}={}){
    if(!selection||selection.kind==='source'||toRestore().length)return Promise.resolve(null);
    return exclusive(async at=>{
      const chosen=selection,wasWords=chosen.kind==null&&chosen.a!=null,first=chosen.a,last=chosen.b,said=String(typed).trim();
      const gone=deletedIds(edits());
      // A span set with the waveform handles (precise) is an exact time span, so it is cut as it is.
      const runs=wasWords&&!chosen.precise&&transcript.words.slice(first,last+1).some(w=>gone.has(w.id))?keptRuns(transcript.words,first,last,gone):null;
      const parts=runs?runs.map(([i,j])=>wordSelection(transcript.words,i,j)):[chosen],pieces=[];
      for(const part of parts)pieces.push(makeRemoval({...transcript,draftEdits:[...edits(),...pieces]},part,video.durationMs,video.fps));
      const quote=parts.map(p=>p.quote).join(' … ');
      const ask=askFor(chosen.kind==='pause'?'pause':'remove',`Remove from cut: “${quote}”`+(said?` — ${said}`:''),chosen);
      // The note names only the words this Delete cut: from the first cut word to the last, and their times.
      if(runs){
        Object.assign(ask.editRequest,{quote,firstWordId:parts[0].firstWordId,lastWordId:parts.at(-1).lastWordId});
        Object.assign(ask,{startMs:pieces[0].startMs,endMs:pieces.at(-1).endMs});
      }
      const base=edits(),saved=await request(ask);
      for(const piece of pieces)piece.commentId=saved?.id||null;
      let noteOpen;
      try{noteOpen=await commit(at,[...base,...pieces]);}
      catch(error){
        // No open note may claim a cut that was never saved.
        if(saved?.id&&!error.saved)await cancelRequest(saved.id).catch(onError);
        throw error;
      }
      if(said&&saved?.id)cutNotes.set(saved.id,said);
      const next=wasWords?nextWordAfterDelete(transcript.words,[...deletedIds(edits())],first,last):-1;
      return {edit:pieces[0],pieces,wasWords,first,last,next,noteOpen,
        line:said?'Deleted, with your note. Ctrl+Z brings both back.':'Deleted. Ctrl+Z brings it back.'};
    });
  }

  /** Undo: brings back the last Delete, all its pieces together, and closes its note. keep: see commit. */
  function undo({keep=false}={}){
    if(!lastCutGroup(edits()))return Promise.resolve(null);
    return exclusive(async at=>{
      const count=lastCutGroup(edits()),undone=edits().slice(-count),edit=undone[0];
      const noteOpen=await commit(at,edits().slice(0,-count),{keep});
      if(edit.commentId)await cancelRequest(edit.commentId);
      const said=cutNotes.get(edit.commentId)||'';cutNotes.delete(edit.commentId);
      const ids=undone.flatMap(e=>e.wordIds||[]);
      return {edit,undone,note:said,noteOpen,first:ids.length?indexOf(ids[0]):-1,last:ids.length?indexOf(ids.at(-1)):-1};
    });
  }

  /** Restore: brings back the cuts with these ids. A note shared with pieces still cut stays open; once its last piece
   *  is back it is closed. keep: see commit. */
  function restore(ids,{keep=false}={}){
    if(!edits().some(e=>ids.includes(e.id)))return Promise.resolve(null);
    return exclusive(async at=>{
      const back=edits().filter(e=>ids.includes(e.id));
      await commit(at,edits().filter(e=>!ids.includes(e.id)),{keep});
      let noteResolved=false;
      for(const id of new Set(back.map(e=>e.commentId).filter(Boolean))){
        if(edits().some(e=>e.commentId===id))continue;
        await cancelRequest(id);noteResolved||=cutNotes.has(id);cutNotes.delete(id);
      }
      return {back,noteResolved};
    });
  }

  /** Enter in the note box: updates the comment it was opened from, or sends a new request to the agent. current: the
   *  same selection is still chosen, so the box may be emptied. Null while another note is sending. */
  async function send(text){
    const said=String(text).trim();
    if(!said||!selection||sending)return null;
    sending=true;cancelNoteTimer?.();cancelNoteTimer=null;pendingNote=null;
    const chosen=selection;
    try{
      if(note.id){
        const saved=await updateRequest(note.id,note.base?`${note.base} — ${said}`:said);
        return {kind:'updated',current:selection===chosen&&!!saved};
      }
      await request({...chosen,endMs:chosen.endMs==null?null:Math.min(chosen.endMs,video.durationMs),text:said,editRequest:changeAsk(chosen)});
      return {kind:'sent',current:selection===chosen};
    }finally{sending=false;}
  }

  /** True when an open request already asks for this stretch of the original recording back. */
  function alreadyAsked(chosen,comments=[]){
    return comments.some(c=>c.videoId===video?.id&&c.status==='open'&&c.editRequest?.type==='restore'
      &&c.editRequest.sourceStartMs===chosen.sourceStartMs&&c.editRequest.sourceEndMs===chosen.sourceEndMs);
  }
  /** Asks the agent to bring words (or a pause) back from the original recording. Null while an edit saves. */
  function askRestore(chosen){
    return exclusive(()=>request({...chosen,
      text:`Restore from the original recording (${stamp(chosen.sourceStartMs)} – ${stamp(chosen.sourceEndMs)}): “${chosen.quote}”`,
      editRequest:{type:'restore',quote:chosen.quote,firstWordId:chosen.firstWordId,lastWordId:chosen.lastWordId,transcriptRevision:transcript.revision,
        sourceStartMs:chosen.sourceStartMs,sourceEndMs:chosen.sourceEndMs,sourceDurationMs:transcript.original.durationMs,sourceId:transcript.original.sourceId}}));
  }

  /** Where a comment opens: its words by id, else the word at its join or time, else the next word, else the last.
   *  seekMs: the start of its first word when found by id. */
  function target(ms,editRequest=null){
    const words=transcript.words;
    let first=editRequest?.firstWordId?indexOf(editRequest.firstWordId):-1;
    const last=editRequest?.lastWordId?indexOf(editRequest.lastWordId):-1;
    const cut=editRequest?.cutId?transcript.cuts.find(c=>c.id===editRequest.cutId)||null:null;
    const found=first>=0,at=cut?.atMs??ms;
    if(first<0)first=activeWordIndex(words,at);
    if(first<0)first=words.findIndex(w=>w.startMs>=at);
    if(first<0)first=words.length-1;
    return {first,last:last>=first?last:first,cut,seekMs:found?words[first].startMs:null};
  }
  function changeContext(startMs,endMs){
    return {type:'change',...wordsInSpan(transcript?.words||[],startMs,endMs),...(transcript?{transcriptRevision:transcript.revision}:{})};
  }

  return {
    /** A new transcript (or none) for a video: the selection goes, and saves still running from before are dropped. */
    load(next,nextVideo){generation++;transcript=next;video=nextVideo;clear();},
    get transcript(){return transcript;},
    get video(){return video;},
    get selection(){return selection;},
    get anchor(){return anchor;},
    set anchor(index){anchor=index;},
    get busy(){return busy;},
    get sending(){return sending;},
    get noteId(){return note.id;},
    edits,
    clear,selectWords,selectCut,selectPause,selectSource,wholeGap,adjust,selectedIds,toRestore,bounds,
    remove,undo,restore,send,editNote,noteTyped,flushNote,alreadyAsked,askRestore,target,changeContext,
  };
}

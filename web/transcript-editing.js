// Transcript editing: what is selected, what a Delete cuts, what Undo and Restore bring back, and how a note is saved.
// There is no page here. The panel passes clicks in and draws what this module holds; the browser parts (saving the
// transcript, the comment list, timers) are passed in, so every rule runs in a test without a browser.
import {activeWordIndex,wordSelection,wordsInSpan} from './transcript-core.js';
import {makeRemoval,previewDuration,removalBounds} from './cut-core.js';
import {cleanCut} from './cut-boundary.js';
import {cutsToRestore,deletedIds,lastCutGroup,nextWordAfterDelete,pauseOfCut} from './edit-bar-core.js';
import {clock} from './time-format.js';
import {RULES} from './review-core.js';

const stamp=ms=>clock(ms,{places:3,hours:false});
// A note quotes at most this much; the cut itself keeps every word (a cut joined from many Deletes can be longer).
const shortQuote=quote=>quote.length>RULES.limits.quoteLength?quote.slice(0,RULES.limits.quoteLength-1)+'…':quote;
const NOTE_PAUSE_MS=450;
// How long a Delete waits for the video's loudness while the server still measures it; after that it cuts on the
// transcript's word times, as it does when there is no loudness at all.
const LOUDNESS_WAIT_MS=4000;
// Undo reaches back at most this many joins into one cut (the stores refuse a deeper takenIn), so a long run of
// Deletes next to each other keeps saving; the oldest joins are let go.
const UNDO_JOINS=30;
const notesIn=list=>(list||[]).flatMap(e=>[e.commentId,...notesIn(e.takenIn)]).filter(Boolean);
/** The takenIn a joined cut saves: each earlier cut once (the two pieces of a split cut can hold the same ones, and Undo
 *  merges them by id anyway), at most UNDO_JOINS deep. dropped collects the notes of the cuts let go. */
function keepJoins(list,dropped,depth=1,seen=new Set()){
  const level=list.filter(e=>!seen.has(e.id)&&seen.add(e.id));
  return level.map(e=>{
    if(!e.takenIn)return e;
    const {takenIn,...rest}=e;
    if(depth>=UNDO_JOINS){dropped.push(...notesIn(takenIn));return rest;}
    const inner=keepJoins(takenIn,dropped,depth+1,seen);
    return inner.length?{...rest,takenIn:inner}:rest;
  });
}
// The reply on the note of a cut let go of that way: its words are still cut, so it must not say they are kept.
export const JOINED_TEXT='Joined into a later Delete, whose note now covers these words. They stay cut.';
export const RELOADED_TEXT='The transcript changed while this was saving. The edit was saved, and the transcript is loaded again.';
export const TOO_SHORT_TEXT='Not brought back: a deleted word next to it is too short to stay cut on its own. Bring both back together, or use Undo.';
export const REFUSED_TEXT='Not saved: the words changed elsewhere. Try again.';
export const REMOVED_TEXT='Not saved: the transcript was removed elsewhere.';

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
  // The loudness of each video by id ({env, promise, waited, from, standsFor}, see setLoudness). The cuts a Delete took into its own cut
  // are saved on that cut (takenIn), so its Undo brings them back as they were, also after a reload.
  const loudness=new Map();

  const edits=()=>transcript?.draftEdits||[];
  const indexOf=id=>transcript.words.findIndex(w=>w.id===id);

  /** The video's loudness (an envelope from cut-boundary.js, or a promise of one) for finding where word cuts go. from:
   *  the media file a waveform decode read it from; none for the server's, which is fetched for each file version.
   *  A decode that comes in while the server still measures stands in for it (standsFor) until the server's
   *  measurement arrives, which then takes its place again; a server with nothing to give leaves the decode. */
  function setLoudness(videoId,value,{from=null}={}){
    if(!value)return void loudness.delete(videoId);
    const before=loudness.get(videoId);
    const standsFor=from&&before?(before.from?before.standsFor:before.env?null:before):null;
    const entry={env:null,promise:null,waited:false,from,standsFor};loudness.set(videoId,entry);
    if(typeof value.then!=='function'){entry.env=value;return;}
    entry.promise=Promise.resolve(value).then(env=>{
      entry.env=env||null;
      if(entry.env&&loudness.get(videoId)?.standsFor===entry)loudness.set(videoId,entry);
      return entry.env;
    },()=>null);
  }
  /** The open video's loudness now, or after a short wait while it is still being measured; null without it. Only the
   *  first edit waits: once a wait ran out, later edits cut on the word times until the measurement is in. */
  async function loudnessSoon(){
    const entry=loudness.get(video?.id);
    if(!entry||entry.env||!entry.promise||entry.waited)return entry?.env||null;
    entry.waited=true;let cancel=null;const late=new Promise(done=>{cancel=schedule(()=>done(null),LOUDNESS_WAIT_MS);});
    try{return await Promise.race([entry.promise,late]);}finally{cancel?.();}
  }
  const loudnessNow=()=>loudness.get(video?.id)?.env||null;
  /** True once a video's loudness is in (measured on the server, or from the waveform's decode). */
  const hasLoudness=videoId=>!!loudness.get(videoId)?.env;
  /** True when the video's loudness came from a waveform decode, which a newer decode replaces. */
  const decodedLoudness=videoId=>!!loudness.get(videoId)?.from;
  /** The video plays this file now: loudness decoded from another file (before a new render) no longer fits, so it goes. */
  function mediaChanged(videoId,src){const entry=loudness.get(videoId);if(entry?.from&&entry.from!==src)loudness.delete(videoId);}

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
    // atMs is the join's time when it was chosen, kept when the handles change the range, so a redraw can tell a moved join.
    selection={startMs:Math.max(0,cut.atMs-200),endMs:cut.atMs+200,cutId:cut.id,atMs:cut.atMs,quote:'Join '+cut.id};
    return selection;
  }
  /** A pause between two words; with no previous word it starts the video, with no next word it ends it. */
  function selectPause(previous,next){
    clear();
    const quote=previous&&next?`Pause after “${previous.text}”, before “${next.text}”`:previous?`Pause after “${previous.text}”, to the end`:`Pause from the start, before “${next.text}”`;
    selection={kind:'pause',startMs:previous?previous.endMs:0,endMs:next?next.startMs:video.durationMs,firstWordId:previous?.id,lastWordId:next?.id,quote};
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
  /** The cuts that bring the selection back: when every selected word is deleted already, or a selected pause is cut. */
  function toRestore(){
    // A pause's cuts include any cut that holds no words there (handles moved into a short pause), and every cut with no
    // words whose mark is this pause (handles moved inside a word beside it, see pauseOfCut).
    if(selection?.kind==='pause'){
      const words=transcript.words,here=selection.lastWordId?indexOf(selection.lastWordId):words.length;
      return edits().filter(e=>(e.kind==='pause'||!e.wordIds?.length)&&e.startMs<selection.endMs&&e.endMs>selection.startMs
        ||!e.wordIds?.length&&pauseOfCut(words,e)===here);
    }
    return cutsToRestore(edits(),selectedIds());
  }
  /** The frame-aligned span a Delete would cut, or null for a selection too short for one. */
  function bounds(){
    if(!selection||!transcript)return null;
    try{return removalBounds(transcript,selection,video.durationMs,video.fps,loudnessNow());}
    catch{return null;}
  }

  function askFor(type,text,chosen=selection){
    return {...chosen,editRequest:{type,quote:shortQuote(chosen.quote),firstWordId:chosen.firstWordId,lastWordId:chosen.lastWordId,cutId:chosen.cutId,transcriptRevision:transcript.revision},text};
  }
  function changeAsk(chosen){
    const words=chosen.a!=null?transcript.words.slice(chosen.a,chosen.b+1).map(w=>w.id):[];
    return {type:'change',quote:chosen.quote.slice(0,3000),firstWordId:chosen.firstWordId,lastWordId:chosen.lastWordId,wordIds:words,cutId:chosen.cutId,transcriptRevision:transcript.revision};
  }
  const stale=at=>at!==generation;
  /** Saves the cut list on the transcript it was worked out from. keep: the selection and note stay as they are.
   *  A new transcript that came in before the save: nothing is saved. One that came in during it, on the same words (so
   *  the store took the save): the error says saved, and the panel loads the transcript again to show it. Words that
   *  changed in the store without this page hearing of it (an agent, another tab): the store refuses the save, and the
   *  error is marked refused, so the panel loads that video's newer words and the edit can be made again on them. */
  async function commit(at,draftEdits,{keep=false}={}){
    if(stale(at))throw Error(RULES.messages.transcriptChanged);
    const target=video;let saved;
    // Both stores refuse with this rules message (the local server passes it through as the error text).
    try{saved=await saveTranscript(target,{...transcript,draftEdits},{editsOnly:true});}
    // A new transcript that came in meanwhile is loaded already, so only a refusal on the words still open asks for a reload.
    catch(error){if(error?.message===RULES.messages.transcriptChanged&&!stale(at))Object.assign(error,{refused:true,videoId:target.id});throw error;}
    if(stale(at))throw Object.assign(Error(RELOADED_TEXT),{saved:true,videoId:target.id});
    transcript=saved;
    // Whether the note box was editing a comment when the save landed: then its text is that comment's, not a draft.
    const noteOpen=note.id!=null;
    if(!keep)clear();
    return noteOpen;
  }
  /** Saves the cut list without the cuts brought back, then closes each note none of its pieces is left in (also when
   *  the save landed as a new transcript came in, since the cut is gone from the store either way). A new transcript
   *  that came in while the notes were closing also ends it as saved, so nothing is drawn on the new words. */
  async function commitThenClose(at,remaining,back,options){
    const target=video;let noteOpen,landed=null;
    try{noteOpen=await commit(at,remaining,options);}
    catch(error){if(!error.saved)throw error;landed=error;}
    // A note stays open while a cut still holds it, also in a cut's saved history (its words are still cut, or come back on Undo).
    const held=new Set(notesIn(remaining));
    for(const id of new Set(back.map(e=>e.commentId).filter(Boolean))){
      if(held.has(id))continue;
      // A failed close is reported; it must not hide that the save landed, which the panel shows by loading again.
      // Another video opened meanwhile: the save still landed on the old one, and nothing is said over the new one.
      try{await cancelRequest(id);}
      catch(error){
        if(!landed&&!stale(at))throw error;
        landed??=Object.assign(Error(RELOADED_TEXT),{saved:true,videoId:target.id});
        if(video?.id===target.id)onError(error);
      }
      cutNotes.delete(id);
    }
    if(!landed&&stale(at))landed=Object.assign(Error(RELOADED_TEXT),{saved:true,videoId:target.id});
    if(landed)throw landed;
    return noteOpen;
  }
  /** Runs one edit at a time: while a cut, Undo or Restore saves, another is refused (null). */
  async function exclusive(run){
    if(busy)return null;busy=true;
    try{return await run(generation);}
    finally{busy=false;}
  }

  /** Waits for the video's loudness; a new transcript that came in meanwhile ends the edit before anything is asked. */
  async function loudnessFor(at){
    const env=await loudnessSoon();
    if(stale(at))throw Error(RULES.messages.transcriptChanged);
    return env;
  }
  /** One cut over whole words lo..hi that takes in every cut it touches: word cuts inside or right next to the words
   *  (so a run of deleted words is one cut), and any other cut its span overlaps. Nothing a taken-in cut removed comes back. */
  function joinedCut(lo,hi,env){
    const words=transcript.words,taken=new Set();
    const span=e=>(e.wordIds||[]).map(indexOf).filter(i=>i>=0);
    for(let grew=true;grew;){
      grew=false;
      for(const e of edits()){
        if(taken.has(e)||e.kind==='pause')continue;
        const at=span(e);if(!at.some(i=>i>=lo-1&&i<=hi+1))continue;
        taken.add(e);lo=Math.min(lo,...at);hi=Math.max(hi,...at);grew=true;
      }
    }
    const chosen={...wordSelection(words,lo,hi),a:lo,b:hi};
    let {startMs,endMs}=removalBounds(transcript,chosen,video.durationMs,video.fps,env);
    for(let grew=true;grew;){
      grew=false;
      for(const e of edits()){
        if(taken.has(e)||!(e.startMs<endMs&&e.endMs>startMs))continue;
        taken.add(e);grew=true;
      }
      for(const e of taken){if(e.startMs<startMs){startMs=e.startMs;grew=true;}if(e.endMs>endMs){endMs=e.endMs;grew=true;}}
      grew&&=edits().some(e=>!taken.has(e)&&e.startMs<endMs&&e.endMs>startMs);
    }
    // Each taken-in cut keeps where it stood in the list (at), so Undo puts it back in its place.
    const place=e=>edits().indexOf(e),dropped=[];
    const takenIn=keepJoins([...taken].sort((x,y)=>place(x)-place(y)).map(e=>({...e,at:place(e)})),dropped);
    const edit={id:crypto.randomUUID(),startMs,endMs,wordIds:words.slice(lo,hi+1).map(w=>w.id),kind:'words',quote:chosen.quote,
      ...(takenIn.length?{takenIn}:{})};
    const kept=edits().filter(e=>!taken.has(e));
    if(previewDuration(video.durationMs,[...kept,edit])<1000/video.fps)throw Error('Keep at least one frame in the video.');
    // Too old for Undo: their words stay cut under this cut and its note, so their own notes are closed.
    const held=new Set(notesIn([...kept,edit]));
    return {chosen,edit,kept,taken:[...taken],letGo:[...new Set(dropped)].filter(id=>!held.has(id))};
  }

  /** Delete: cuts the selection (words, a pause, a join span). Words are cut whole, at the silence the loudness shows
   *  around them, and the cut takes in the cuts it touches (see joinedCut), so one Delete is one cut with one note.
   *  typed: the note typed with it. */
  function remove({typed=''}={}){
    if(!selection||selection.kind==='source'||toRestore().length)return Promise.resolve(null);
    // What is selected now is what this Delete cuts, also when another word is clicked while it waits for the loudness.
    const picked=selection;
    return exclusive(async at=>{
      const target=video,env=await loudnessFor(at);
      const wasWords=picked.kind==null&&picked.a!=null,said=String(typed).trim();
      // A span set with the waveform handles (precise) is an exact time span, so it is cut as it is.
      let chosen=picked,edit,kept=edits(),taken=[],letGo=[];
      if(wasWords&&!picked.precise)({chosen,edit,kept,taken,letGo}=joinedCut(picked.a,picked.b,env));
      else edit=makeRemoval({...transcript,draftEdits:edits()},picked,video.durationMs,video.fps,env);
      const ask=askFor(chosen.kind==='pause'?'pause':'remove',`Remove from cut: “${shortQuote(chosen.quote)}”`+(said?` — ${said}`:''),chosen);
      // The note covers what this Delete cut: its words and its span.
      Object.assign(ask,{startMs:edit.startMs,endMs:edit.endMs});
      const saved=await request(ask);
      edit.commentId=saved?.id||null;
      let noteOpen;
      try{noteOpen=await commit(at,[...kept,edit]);}
      catch(error){
        // No open note may claim a cut that was never saved.
        if(saved?.id&&!error.saved)await cancelRequest(saved.id).catch(error=>{if(video?.id===target.id)onError(error);});
        if(saved?.id&&error.saved&&said)cutNotes.set(saved.id,said);
        if(error.saved)await closeJoined(letGo,target);
        throw error;
      }
      if(said&&saved?.id)cutNotes.set(saved.id,said);
      const first=chosen.a,last=chosen.b;
      const next=wasWords?nextWordAfterDelete(transcript.words,[...deletedIds(edits())],first,last):-1;
      await closeJoined(letGo,target);
      // A new transcript that came in while those notes closed (another video opened): the Delete is saved, and its
      // words are not selected on the new one, as an Undo or Restore ends (see commitThenClose).
      if(stale(at))throw Object.assign(Error(RELOADED_TEXT),{saved:true,videoId:target.id});
      return {edit,pieces:[edit],wasWords,first,last,next,noteOpen,
        line:said?'Deleted, with your note. Ctrl+Z brings both back.':'Deleted. Ctrl+Z brings it back.'};
    });
  }

  /** Closes the notes of cuts a Delete let go of (see joinedCut), saying their words stay cut. A failed close is
   *  reported only while the Delete's video is still open. */
  async function closeJoined(ids,target){
    for(const id of ids){
      await cancelRequest(id,{text:JOINED_TEXT}).catch(error=>{if(video?.id===target.id)onError(error);});
      cutNotes.delete(id);
    }
  }
  /** The cuts these took in when they were made, still waiting for their Undo, once each by id (the pieces of a cut
   *  split before historyLeft gave each its own part can hold the same one). */
  const takenBy=list=>[...new Map(list.flatMap(e=>e.takenIn||[]).map(e=>[e.id,e])).values()];
  /** Every cut in these cuts' saved history, at any depth, once each. */
  const heldBy=list=>{
    const all=new Map(),walk=l=>{for(const e of l||[]){if(!all.has(e.id))all.set(e.id,e);walk(e.takenIn);}};
    for(const e of list)walk(e.takenIn);
    return [...all.values()];
  };

  /** Undo: brings back the last Delete, all its pieces together, puts back the cuts it took in as its pieces still
   *  hold them (as they were, less what was clicked back since: see historyLeft), and closes its own note.
   *  keep: see commit. */
  function undo({keep=false}={}){
    if(!lastCutGroup(edits()))return Promise.resolve(null);
    return exclusive(async at=>{
      const count=lastCutGroup(edits()),undone=edits().slice(-count),edit=undone[0],said=cutNotes.get(edit.commentId)||'';
      const earlier=edits().slice(0,-count),before=takenBy(undone).filter(e=>!earlier.some(k=>k.id===e.id));
      // Each cut goes back where it stood when it was taken in. A cut split in two since comes back as two parts with
      // one place: they stand side by side (one Delete for the next Undo), and the places after them move up by one.
      const list=[...earlier];let extra=0,lastSpot=null;
      for(const {at:spot,...cut} of before.sort((x,y)=>(x.at??Infinity)-(y.at??Infinity))){
        if(spot!=null&&spot===lastSpot)extra++;
        lastSpot=spot;
        list.splice(Math.min((spot??list.length)+extra,list.length),0,cut);
      }
      const noteOpen=await commitThenClose(at,list,undone,{keep});
      const ids=undone.flatMap(e=>e.wordIds||[]);
      return {edit,undone,note:said,noteOpen,first:ids.length?indexOf(ids[0]):-1,last:ids.length?indexOf(ids.at(-1)):-1};
    });
  }

  /** Restore: brings back the cuts with these ids. A note shared with pieces still cut stays open; once its last piece
   *  is back it is closed, with the notes of the cuts it took in. keep: see commit. */
  function restore(ids,{keep=false}={}){
    if(!edits().some(e=>ids.includes(e.id)))return Promise.resolve(null);
    return exclusive(async at=>{
      const back=edits().filter(e=>ids.includes(e.id)),noted=new Set(cutNotes.keys());
      const remaining=edits().filter(e=>!ids.includes(e.id));
      await commitThenClose(at,remaining,[...back,...heldBy(back)],{keep});
      return {back,noteResolved:back.some(e=>noted.has(e.commentId)&&!cutNotes.has(e.commentId))};
    });
  }

  /** The saved history left to a cut that lost words: of the cuts it took in, only what is still cut under it, so its
   *  Undo never cuts again what plays, and a note closes exactly when nothing holds its cut any more.
   *  Every cut taken in stays while a frame of its own time is still cut; one taken in for its words keeps the words it
   *  shares with the cut that is left, and goes with its last one. What stays is trimmed to the cut that is left, and
   *  its own history the same way, so no saved cut reaches outside the cut that holds it.
   *  was: the cut before it lost the words; now: what is left of it. seen: the ids kept so far in this click-back; a
   *  cut held by a second piece gets a new id there, so both parts can stand in one list after Undo. */
  function historyLeft(history,was,now,seen){
    const step=1000/video.fps,had=new Set(was.wordIds),has=new Set(now.wordIds),left=[];
    for(const {takenIn,...earlier} of history||[]){
      const byWords=earlier.wordIds.some(id=>had.has(id)),ids=byWords?earlier.wordIds.filter(id=>has.has(id)):earlier.wordIds;
      const startMs=Math.max(earlier.startMs,now.startMs),endMs=Math.min(earlier.endMs,now.endMs);
      if(endMs-startMs<step-0.5||!ids.length&&byWords)continue;
      const mine=new Set(ids),lostWords=ids.length<earlier.wordIds.length;
      const cut={...earlier,id:seen.has(earlier.id)?crypto.randomUUID():earlier.id,startMs,endMs,wordIds:ids,
        ...(lostWords?{quote:transcript.words.filter(w=>mine.has(w.id)).map(w=>w.text).join(' ')}:{})};
      seen.add(earlier.id);
      const inner=historyLeft(takenIn,earlier,cut,seen);
      left.push(inner.length?{...cut,takenIn:inner}:cut);
    }
    return left;
  }
  /** The cut left of edit once the words in `back` play again: one piece per run of words still cut. A piece keeps the
   *  cut's own edge where it reaches it, and on the restored side ends at the silence before the restored word (with
   *  loudness and no silence there: at the frame line before the quietest point, so none of that word stays cut).
   *  Each piece keeps the part of the saved history still cut under it (see historyLeft). */
  function piecesLeft(edit,back,env,seen){
    const step=1000/video.fps,words=transcript.words,at=edit.wordIds.map(indexOf).filter(i=>i>=0).sort((x,y)=>x-y);
    const cut=at.filter(i=>!back.has(words[i].id)),runs=[];
    for(const i of cut){const run=runs.at(-1);if(run&&i===run[1]+1)run[1]=i;else runs.push([i,i]);}
    const pieces=[],{takenIn:history,...rest}=edit;
    for(const [i,j] of runs){
      // Which sides of the piece have a word that plays again; the other side is the cut's own edge.
      const clear=[i!==at[0],j!==at.at(-1)];
      let found=env?cleanCut(words,i,j,env,{fps:video.fps,durationMs:video.durationMs,clear}):null;
      // No loudness, or no sound it can see for these words: from the word times.
      if(!found)try{found=removalBounds(transcript,wordSelection(words,i,j),video.durationMs,video.fps);}catch{}
      // No room for a frame-aligned cut from the word times alone: stop a whole frame clear of the restored words.
      let startMs=!clear[0]?edit.startMs:found?found.startMs:Math.ceil(words[i-1].endMs/step)*step;
      let endMs=!clear[1]?edit.endMs:found?found.endMs:Math.floor(words[j+1].startMs/step)*step;
      startMs=Math.round(Math.max(edit.startMs,startMs));endMs=Math.round(Math.min(edit.endMs,endMs));
      // Too short for a frame: dropping it would bring back words that were not clicked, so nothing changes instead.
      if(endMs-startMs<step-0.5)throw Error(TOO_SHORT_TEXT);
      const piece={...rest,id:pieces.length?crypto.randomUUID():edit.id,startMs,endMs,wordIds:words.slice(i,j+1).map(w=>w.id),quote:wordSelection(words,i,j).quote};
      const takenIn=historyLeft(history,edit,piece,seen);
      pieces.push(takenIn.length?{...piece,takenIn}:piece);
    }
    return pieces;
  }
  /** Restore words: each cut holding any of these words gives back just them. A cut with words left either side
   *  becomes two cuts sharing its note; the note closes once none of its words is cut. keep: see commit. */
  function restoreWords(ids,{keep=false}={}){
    const back=new Set(ids),touches=e=>e.kind!=='pause'&&(e.wordIds||[]).some(id=>back.has(id));
    if(!edits().some(touches))return Promise.resolve(null);
    return exclusive(async at=>{
      const env=await loudnessFor(at),noted=new Set(cutNotes.keys()),seen=new Set();
      const touched=edits().filter(touches),cutNow=edits().flatMap(e=>touches(e)?piecesLeft(e,back,env,seen):[e]);
      // The pieces hold only what is still cut (see historyLeft), so the note of every cut no longer held closes here.
      await commitThenClose(at,cutNow,[...touched,...heldBy(touched)],{keep});
      const where=[...back].map(indexOf).filter(i=>i>=0);
      return {back:touched,noteResolved:touched.some(e=>noted.has(e.commentId)&&!cutNotes.has(e.commentId)),
        first:where.length?Math.min(...where):-1,last:where.length?Math.max(...where):-1};
    });
  }

  /** Enter in the note box: updates the comment it was opened from, or sends a new request to the agent. current: the
   *  same selection is still chosen, so the box may be emptied. Null while another note is sending, or for a new note
   *  on a join that moved or went since it was chosen. */
  async function send(text){
    const said=String(text).trim();
    if(!said||!selection||sending)return null;
    // A join that moved or went since it was chosen (an Undo or Restore loaded another editor's change, and may still
    // be closing its cut's note): a new note must not ask about its old time. The panel closes the bar when it draws
    // the joins again, and keeps the typed note for the next selection.
    if(!note.id&&selection.cutId!=null&&transcript.cuts?.find(c=>c.id===selection.cutId)?.atMs!==selection.atMs)return null;
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
    setLoudness,hasLoudness,decodedLoudness,mediaChanged,remove,undo,restore,restoreWords,send,editNote,noteTyped,flushNote,alreadyAsked,askRestore,target,changeContext,
  };
}

// Rules for the edit bar that appears when words are selected: what a key does, what the ghost text suggests,
// and which words stay selected after a delete. Pure functions, so they can be tested without a browser.

// Short notes the bar's grey autocomplete offers once their first letters are typed.
export const QUICK_PHRASES=[
  'Word clipped, recover the missing beginning or ending',
  'Give this more room before it starts',
  'Let the end finish before cutting',
  'Mistake or retake, keep the complete take',
  'Shorten this pause, keep the breath',
  'Remove this passage',
  'Tighten this and keep the pacing',
  'Keep this, it is intentional'
];

const GENERATED=/^(Remove from cut:|Undid this|The selected passage|Restore from the original|Check this mistake|This word or join is clipped|Give this passage more room|Let the end of this passage|Shorten this pause while|Note about the selected)/;

/** The reviewer's own typed notes, newest first, without the sentences the app writes for its buttons. */
export function recentNotes(comments=[],limit=20){
  const seen=new Set(),notes=[];
  for(const c of [...comments].reverse()){
    const text=String(c?.text??'').trim().replace(/\s+/g,' ');
    if(text.length<3||text.length>140||GENERATED.test(text))continue;
    const key=text.toLowerCase();if(seen.has(key))continue;seen.add(key);notes.push(text);if(notes.length>=limit)break;
  }
  return notes;
}

/** The full suggestion for what has been typed: the first recent note, then quick phrase, that continues it. Else ''. */
export function suggestion(typed,recent=[],phrases=QUICK_PHRASES){
  const start=String(typed??'').replace(/^\s+/,'');if(!start)return '';
  const lower=start.toLowerCase();
  for(const candidate of [...recent,...phrases])if(candidate.length>start.length&&candidate.toLowerCase().startsWith(lower))return start+candidate.slice(start.length);
  return '';
}

/**
 * What a key press in the bar's text box should do.
 *   'accept'          Tab (or → / End at the end of the text) takes the grey suggestion
 *   'send'            Enter with text
 *   'delete'          Delete or Backspace in an empty box removes the selected words
 *   'undo'            Ctrl/Cmd+Z in an empty box undoes the last cut
 *   'clear-text'      Escape with text in the box
 *   'clear-selection' Escape in an empty box
 *   'play'            Space in an empty box plays or pauses (a leading space means nothing in a note)
 * Anything else (null) is left to the browser, so typing and Backspace in text only edit the text.
 */
export function barKey(event,{value='',suggestion:ghost='',caretAtEnd=true}={}){
  if(event.isComposing)return null;
  const plain=!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.shiftKey;
  const empty=value==='';
  if(event.key==='Tab'&&plain&&ghost&&caretAtEnd)return 'accept';
  if((event.key==='ArrowRight'||event.key==='End')&&plain&&ghost&&caretAtEnd)return 'accept';
  if(event.key==='Enter'&&!event.shiftKey&&!event.altKey&&value.trim())return 'send';
  if(event.key===' '&&empty&&plain)return 'play';
  if(event.key==='Escape')return empty?'clear-selection':'clear-text';
  if((event.key==='Delete'||event.key==='Backspace')&&empty&&!event.repeat&&!event.ctrlKey&&!event.metaKey&&!event.altKey)return 'delete';
  if((event.key==='z'||event.key==='Z')&&(event.ctrlKey||event.metaKey)&&!event.shiftKey&&!event.altKey&&empty)return 'undo';
  return null;
}

/** After words a..b are deleted: the next word that is still in the cut, else the one before, else nothing (-1). */
export function nextWordAfterDelete(words,removedIds,a,b){
  const gone=new Set(removedIds);
  for(let i=b+1;i<words.length;i++)if(!gone.has(words[i].id))return i;
  for(let i=a-1;i>=0;i--)if(!gone.has(words[i].id))return i;
  return -1;
}

/** The one plain line shown after a request is saved. */
export function sentLine(agentConnected){
  return agentConnected?'Sent to your agent. It shows in the comment list.':'No agent connected: your note is saved and waits for it.';
}

/* Where the bar goes (TOOLS-129). Lex (2026-11-08): it appears where the words are selected, as in the script editor,
   so it follows the same rules: never on the selected words or the line above them when that can be avoided. */
const EDGE=8,GAP=8;
const clamp=(n,low,high)=>Math.max(low,Math.min(n,high));
/**
 * The bar's place on a wide screen, in window pixels: below the words if it fits; else above them with one line clear;
 * else right above them; else, when the words fill the window, at the end with more room. Always on screen.
 * first, last: the boxes of the first and last selected word; w, h: the bar; top: where the header ends;
 * vw, vh: the window; line: the height of a line of text.
 */
export function barSpot({first,last,w,h,top,vw,vh,line=0}){
  const low=top+EDGE,high=Math.max(low,vh-EDGE-h);
  const below=last.bottom+GAP,clearAbove=first.top-line-GAP-h,above=first.top-GAP-h;
  let y,from=first;
  if(below+h<=vh-EDGE){y=below;from=last;}
  else if(clearAbove>=low)y=clearAbove;
  else if(above>=low)y=above;
  else y=vh-last.bottom>=first.top-top?high:low;
  return {x:Math.max(EDGE,Math.min(from.left-4,vw-w-EDGE)),y:clamp(y,low,high)};
}
/** barSpot inside the words area (which ends at end); when that would put the bar on the selected words, as in a short
 *  transcript on a phone, the bar may reach down to the end of what is seen (seenEnd) instead. */
export function barSpotOffWords({end,seenEnd,...place}){
  const spot=barSpot({...place,vh:end});
  if(seenEnd<=end||spot.y>=place.last.bottom||spot.y+place.h<=place.first.top)return spot;
  return barSpot({...place,vh:seenEnd});
}

/* Deleted words stay in the transcript, marked, so the original text is always there to read. */
/** The ids of every deleted word. */
export function deletedIds(edits=[]){return new Set(edits.flatMap(e=>e.wordIds||[]));}
/** The cuts that bring a selection back, when every selected word is deleted. Any word still in the cut: none. */
export function cutsToRestore(edits=[],wordIds=[]){
  if(!wordIds.length)return [];
  const gone=deletedIds(edits);if(!wordIds.every(id=>gone.has(id)))return [];
  const ids=new Set(wordIds);return edits.filter(e=>(e.wordIds||[]).some(id=>ids.has(id)));
}
/** Where each cut's Restore mark goes: before the first of its words in the transcript, else before the first word at or
 *  after its start (a pause), else on the last word. */
export function cutMarks(words,edits=[]){
  return edits.map(edit=>{
    const ids=new Set(edit.wordIds||[]);let index=words.findIndex(w=>ids.has(w.id));
    if(index<0)index=words.findIndex(w=>w.startMs>=edit.startMs);
    if(index<0)index=words.length-1;
    return {id:edit.id,index};
  });
}

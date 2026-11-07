// Rules for the edit bar that appears when words are selected: what a key does, what the ghost text suggests,
// and which words stay selected after a delete. Pure functions, so they can be tested without a browser.

// The marker types the bar's small buttons make, written as short notes you can type and complete.
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

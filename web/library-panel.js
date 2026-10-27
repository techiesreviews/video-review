const $=s=>document.querySelector(s);
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;}
const stamp=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;

export function createLibraryPanel({getBackend,open,add,remove,restore,showError}){
  const menu=$('#libraryMenu'),button=$('#libraryBtn'),search=$('#librarySearch'),results=$('#libraryResults');
  let token=0,timer=null,removed=false;
  function close(){menu.hidden=true;button.setAttribute('aria-expanded','false');}
  async function refresh(){
    const backend=getBackend();if(!backend)return;const current=++token,query=search.value.trim();
    try{
      const entries=removed?(await backend.removedVideos()).filter(v=>`${v.title} ${v.label}`.toLowerCase().includes(query.toLowerCase())):await backend.searchVideos(query);
      if(current!==token)return;results.replaceChildren();
      $('#libraryRemoved').textContent=removed?'Back to videos':'Removed videos';
      if(!entries.length){results.append(node('p','library-empty',removed?'No removed videos.':query?'No matching title or spoken phrase.':'No videos yet. Add one below.'));return;}
      for(const entry of entries){
        const row=node('div','library-row'),choose=node('button','library-open');choose.type='button';
        choose.append(node('strong',null,entry.title),node('small',null,entry.label));
        if(entry.snippet)choose.append(node('span','library-snippet',stamp(entry.atMs)+' · '+entry.snippet));
        if(removed){choose.append(node('span','library-restore-label','Restore video'));choose.onclick=async()=>{try{await restore(entry.id);removed=false;close();}catch(e){showError(e.message);}};}
        else choose.onclick=()=>{close();open(entry.id,entry.atMs);};
        row.append(choose);
        if(!removed){const forget=node('button','library-remove','×');forget.type='button';forget.setAttribute('aria-label','Remove '+entry.title+' from library');forget.title='Remove from library; keep the recording';forget.onclick=async()=>{try{if(await remove(entry.id,entry.title))refresh();}catch(e){showError(e.message);}};row.append(forget);}
        results.append(row);
      }
    }catch(e){if(current===token){results.replaceChildren(node('p','library-empty',e.message));}}
  }
  button.onclick=event=>{event.stopPropagation();const show=menu.hidden;for(const m of document.querySelectorAll('.menu'))m.hidden=true;menu.hidden=!show;button.setAttribute('aria-expanded',String(show));if(show){removed=false;search.value='';refresh();search.focus();}};
  search.oninput=()=>{clearTimeout(timer);timer=setTimeout(refresh,150);};
  search.onkeydown=event=>{if(event.key==='Enter'||event.key==='ArrowDown'){event.preventDefault();const first=results.querySelector('.library-open');event.key==='Enter'?first?.click():first?.focus();}};
  menu.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();close();button.focus();}});
  $('#libraryAdd').onclick=()=>{close();add();};
  $('#libraryRemoved').onclick=()=>{removed=!removed;search.value='';refresh();search.focus();};
  return {setActive(video){$('#libraryLabel').textContent=video?.label||'Choose a video';},refresh};
}

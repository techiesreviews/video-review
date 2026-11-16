// How the tool writes a time, for the browser and the hosted Worker. time_format.py writes the same for the Python
// scripts, and tests/fixtures/time-format-cases.json is run against both, so a time reads the same everywhere.

const pad=(value,width)=>String(value).padStart(width,'0');
/** Whole milliseconds, halves up, never below zero. */
const wholeMs=ms=>Math.max(0,Math.round(ms));

/** "01:02:03.456" for exports and agents; separator "," gives an SRT timestamp, "." a VTT one. */
export function timecode(ms,separator='.'){
  ms=wholeMs(ms);
  return `${pad(Math.floor(ms/3600000),2)}:${pad(Math.floor(ms/60000)%60,2)}:${pad(Math.floor(ms/1000)%60,2)}${separator}${pad(ms%1000,3)}`;
}

/**
 * "1:02" or "1:02:03" for people to read.
 * places: 0, or 3 to add ".456". nearest: round to the nearest second, halves to even, instead of cutting off the part
 * of a second. hours: false keeps counting minutes past 59 ("75:23"). padMinutes: two-digit minutes without hours.
 */
export function clock(ms,{places=0,nearest=false,hours=true,padMinutes=false}={}){
  if(places!==0&&places!==3)throw RangeError('A clock shows 0 or 3 digits of milliseconds.');
  if(nearest&&places)throw RangeError('A clock rounded to the nearest second has no milliseconds to show.');
  if(nearest){const seconds=ms/1000;let whole=Math.round(seconds);if(whole-seconds===0.5&&whole%2)whole-=1;ms=Math.max(0,whole)*1000;}
  else ms=wholeMs(ms);
  const h=hours?Math.floor(ms/3600000):0,m=Math.floor(ms/60000)-h*60;
  const head=h?`${h}:${pad(m,2)}`:padMinutes?pad(m,2):String(m);
  return `${head}:${pad(Math.floor(ms/1000)%60,2)}`+(places?`.${pad(ms%1000,3)}`:'');
}

/** The frame shown at a time, halves up. */
export function frame(ms,fps){return Math.round(ms*fps/1000);}

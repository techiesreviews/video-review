// The audio of an MP4 (or MOV) review copy, read in pieces so a file of many gigabytes never sits in memory whole
// (TOOLS-479). The file's index (its moov box) says where each AAC frame of the sound track lies. Those frames are read
// on their own, a short stretch at a time, and given ADTS headers, which makes each stretch a small AAC file the
// browser's ordinary decoder (decodeAudioData) reads like the whole file before. No browser code here: read(start, end)
// is passed in, so every rule runs in a test.

// Container boxes the sound track's index sits in.
const CONTAINERS=new Set(['moov','trak','mdia','minf','stbl','edts']);
// The index of a long recording is a few megabytes; anything far bigger is not an index this reader understands.
const MOST_INDEX_BYTES=256*1024*1024;
// AAC sampling frequencies by index (ISO 14496-3), as an ADTS header names them.
const RATES=[96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350];
// An AAC frame decodes with the help of the one before it, so each stretch starts this many frames early and the
// sound of those frames is dropped: the decoder starts from nothing at every stretch, and without them each stretch
// would begin with a short false quiet.
export const LEAD_FRAMES=2;

export class NotReadable extends Error{}

const u32=(b,i)=>(b[i]<<24|b[i+1]<<16|b[i+2]<<8|b[i+3])>>>0;
const u64=(b,i)=>u32(b,i)*2**32+u32(b,i+4);
const i32=(b,i)=>u32(b,i)|0;
const i64=(b,i)=>i32(b,i)*2**32+u32(b,i+4);
const type=(b,i)=>String.fromCharCode(b[i],b[i+1],b[i+2],b[i+3]);

/** The boxes in bytes[from..to): {type, start, body, end} with body the first byte after the header. */
function boxes(bytes,from=0,to=bytes.length){
  const out=[];
  for(let at=from;at+8<=to;){
    let size=u32(bytes,at),body=at+8;
    if(size===1){if(at+16>to)break;size=u64(bytes,at+8);body=at+16;}
    else if(size===0)size=to-at;
    if(size<body-at||at+size>to)throw new NotReadable('The video file\'s index is damaged.');
    out.push({type:type(bytes,at+4),start:at,body,end:at+size});at+=size;
  }
  return out;
}
const child=(bytes,box,name)=>boxes(bytes,box.body,box.end).find(b=>b.type===name);

/** Finds the moov box by reading box headers from the start of the file, jumping over the media data (mdat). */
export async function readIndex(read,fileSize){
  for(let at=0;at+8<=fileSize;){
    const head=await read(at,Math.min(fileSize,at+16));
    let size=u32(head,0),header=8;
    if(size===1){size=u64(head,8);header=16;}else if(size===0)size=fileSize-at;
    const name=type(head,4);
    if(size<header||at+size>fileSize)throw new NotReadable('The video file is not a complete MP4.');
    if(name==='moov'){
      if(size>MOST_INDEX_BYTES)throw new NotReadable('The video file\'s index is too large.');
      return read(at,at+size);
    }
    // An MP4 or MOV starts with one of these; any other file is not one.
    if(at===0&&!['ftyp','moov','mdat','free','skip','wide','pnot'].includes(name))throw new NotReadable('The video file is not an MP4.');
    at+=size;
  }
  throw new NotReadable('The video file has no index: it may still be recording, or it is a fragmented MP4.');
}

/** The fields of AudioSpecificConfig an ADTS header needs: the AAC profile, the frequency index and the channels. */
export function audioConfig(asc){
  let bit=0;
  const bits=n=>{let v=0;for(let i=0;i<n;i++,bit++)v=v<<1|(asc[bit>>3]>>(7-(bit&7))&1);return v;};
  const objectType=()=>{const t=bits(5);return t===31?32+bits(6):t;};
  let aot=objectType(),frequency=bits(4);if(frequency===15)throw new NotReadable('This video\'s sound uses an unusual sample rate.');
  const channels=bits(4);
  // HE-AAC: the core is plain AAC at half the rate, and the ADTS header names the core, as ffmpeg's does; the decoder
  // finds the rest in the frames themselves. The output rate that follows is not needed.
  if(aot===5||aot===29){if(bits(4)===15)bits(24);aot=objectType();}
  if(aot<1||aot>4)throw new NotReadable('This video\'s sound is not plain AAC.');
  if(channels<1||channels>7)throw new NotReadable('This video\'s sound has a channel layout this reader does not know.');
  return {profile:aot-1,frequency,channels,sampleRate:RATES[frequency]};
}

/** The AudioSpecificConfig inside an esds box (its ES and decoder config descriptors), or null. */
function esdsConfig(bytes,box){
  let at=box.body+4;
  const descriptor=()=>{const tag=bytes[at++];let length=0;for(let i=0;i<4;i++){const b=bytes[at++];length=length<<7|b&127;if(!(b&128))break;}return {tag,length};};
  while(at<box.end){
    const {tag,length}=descriptor(),end=at+length;
    if(tag===3){const flags=bytes[at+2];at+=3;if(flags&128)at+=2;if(flags&64)at+=1+bytes[at];if(flags&32)at+=2;continue;}
    if(tag===4){if(bytes[at]!==0x40)throw new NotReadable('This video\'s sound is not AAC.');at+=13;continue;}
    if(tag===5)return bytes.subarray(at,end);
    at=end;
  }
  return null;
}

/** The sample entry's AudioSpecificConfig: in esds right after the entry's fields, or (in a MOV) inside its wave box. */
function sampleEntryConfig(bytes,entry){
  if(entry.type!=='mp4a')throw new NotReadable('This video\'s sound is not AAC.');
  const version=bytes[entry.body+8]<<8|bytes[entry.body+9];
  const first=entry.body+28+(version===1?16:version===2?36:0);
  for(const box of boxes(bytes,first,entry.end)){
    if(box.type==='esds')return esdsConfig(bytes,box);
    if(box.type==='wave'){const inner=child(bytes,box,'esds');if(inner)return esdsConfig(bytes,inner);}
  }
  throw new NotReadable('This video\'s sound has no AAC settings.');
}

/**
 * The sound track of a moov box: where each AAC frame lies in the file (offsets, sizes), when it starts (starts, in the
 * track's timescale, one more at the end), the ADTS fields (config), and how the track sits on the video's clock: the
 * first `skip` ticks of the track are not played (the encoder's start-up frame, from the edit list) and the track
 * starts `delayMs` after the video does. durationMs is how long it plays.
 */
export function soundTrack(moov){
  const root=boxes(moov)[0];if(root?.type!=='moov')throw new NotReadable('The video file\'s index is damaged.');
  const mvhd=child(moov,root,'mvhd');
  const movieScale=mvhd?u32(moov,mvhd.body+(moov[mvhd.body]===1?20:12)):1000;
  for(const trak of boxes(moov,root.body,root.end).filter(b=>b.type==='trak')){
    const mdia=child(moov,trak,'mdia'),hdlr=mdia&&child(moov,mdia,'hdlr');
    if(!hdlr||type(moov,hdlr.body+8)!=='soun')continue;
    const mdhd=child(moov,mdia,'mdhd'),stbl=child(moov,child(moov,mdia,'minf'),'stbl');
    const scale=u32(moov,mdhd.body+(moov[mdhd.body]===1?20:12));
    const box=name=>child(moov,stbl,name);
    const stsd=box('stsd'),entry=boxes(moov,stsd.body+8,stsd.end)[0];
    const config=audioConfig(sampleEntryConfig(moov,entry)??[]);
    // Frame sizes.
    const stsz=box('stsz'),fixed=u32(moov,stsz.body+4),count=u32(moov,stsz.body+8);
    const sizes=new Uint32Array(count);for(let i=0;i<count;i++)sizes[i]=fixed||u32(moov,stsz.body+12+i*4);
    // Frame starts on the track's clock.
    const stts=box('stts'),starts=new Float64Array(count+1);
    for(let e=0,i=0,n=u32(moov,stts.body+4);e<n;e++){
      const times=u32(moov,stts.body+8+e*8),delta=u32(moov,stts.body+12+e*8);
      for(let k=0;k<times&&i<count;k++,i++)starts[i+1]=starts[i]+delta;
    }
    // Frame offsets: chunk by chunk, each chunk's frames one after another.
    const stco=box('stco'),co64=box('co64'),table=stco||co64;if(!table)throw new NotReadable('The video file\'s index is damaged.');
    const chunks=u32(moov,table.body+4),chunkAt=c=>stco?u32(moov,stco.body+8+c*4):u64(moov,co64.body+8+c*8);
    const stsc=box('stsc'),runs=u32(moov,stsc.body+4),offsets=new Float64Array(count);
    for(let r=0,i=0;r<runs;r++){
      const first=u32(moov,stsc.body+8+r*12)-1,perChunk=u32(moov,stsc.body+12+r*12);
      const last=r+1<runs?u32(moov,stsc.body+8+(r+1)*12)-1:chunks;
      for(let c=first;c<last&&i<count;c++){let at=chunkAt(c);for(let k=0;k<perChunk&&i<count;k++,i++){offsets[i]=at;at+=sizes[i];}}
    }
    // The edit list: an empty edit first delays the track; the first edit with media says where in the track play starts.
    let skip=0,delayMs=0;const elst=child(moov,trak,'edts')&&child(moov,child(moov,trak,'edts'),'elst');
    if(elst){
      const v1=moov[elst.body]===1,size=v1?20:12;
      for(let e=0,n=u32(moov,elst.body+4);e<n;e++){
        const at=elst.body+8+e*size,segment=v1?u64(moov,at):u32(moov,at),media=v1?i64(moov,at+8):i32(moov,at+4);
        if(media===-1){delayMs+=segment/movieScale*1000;continue;}
        skip=media;break;
      }
    }
    return {offsets,sizes,starts,scale,config,skip,delayMs,durationMs:delayMs+(starts[count]-skip)/scale*1000};
  }
  throw new NotReadable('This video has no sound track.');
}

/** A 7-byte ADTS header for one AAC frame of `size` bytes. */
export function adtsHeader({profile,frequency,channels},size){
  const length=size+7;
  if(length>8191)throw new NotReadable('This video\'s sound has a frame too large for this reader.');
  return Uint8Array.of(0xFF,0xF1,profile<<6|frequency<<2|channels>>2,(channels&3)<<6|length>>11,length>>3&255,(length&7)<<5|0x1F,0xFC);
}

/** The frames [first, last) in stretches of about stretchMs of sound each: [[first, last], …]. */
export function stretches(track,stretchMs=20000){
  const out=[],step=stretchMs/1000*track.scale,count=track.sizes.length;
  for(let first=0;first<count;){
    let last=first+1;while(last<count&&track.starts[last+1]-track.starts[first]<=step)last++;
    out.push([first,last]);first=last;
  }
  return out;
}

/** Byte ranges covering frames [first, last): neighbours closer than `gap` bytes are read as one range. */
export function byteRanges(track,first,last,gap=256*1024){
  const ranges=[];
  for(let i=first;i<last;i++){
    const start=track.offsets[i],end=start+track.sizes[i],range=ranges.at(-1);
    if(range&&start>=range[0]&&start-range[1]<=gap)range[1]=Math.max(range[1],end);else ranges.push([start,end]);
  }
  return ranges;
}

/** Frames [first, last) as an ADTS file, each frame read with read(start, end). */
export async function adtsFile(track,first,last,read){
  const ranges=byteRanges(track,first,last);
  let total=0;for(let i=first;i<last;i++)total+=track.sizes[i]+7;
  const out=new Uint8Array(total);let at=0,i=first;
  for(const [start,end] of ranges){
    const bytes=await read(start,end);
    for(;i<last&&track.offsets[i]>=start&&track.offsets[i]+track.sizes[i]<=end;i++){
      const from=track.offsets[i]-start;out.set(adtsHeader(track.config,track.sizes[i]),at);at+=7;
      out.set(bytes.subarray(from,from+track.sizes[i]),at);at+=track.sizes[i];
    }
  }
  if(i!==last)throw new NotReadable('The video file\'s index does not match its sound.');
  return out;
}

/**
 * Where the decoded sound of stretch [first, last) goes, at `rate` samples a second on the video's clock: drop the
 * first `drop` samples of the decode (the lead frames, and before the very first frame the encoder's start-up frame),
 * then keep the samples from `from` to `to` (exclusive). Before the track starts (delayMs) there is silence.
 */
export function placement(track,first,last,rate){
  const lead=Math.max(0,first-LEAD_FRAMES);
  const clock=tick=>Math.round(((tick-track.skip)/track.scale+track.delayMs/1000)*rate);
  const from=Math.max(0,clock(Math.max(track.starts[first],track.skip))),to=Math.max(from,clock(track.starts[last]));
  const drop=Math.max(0,Math.round((Math.max(track.starts[first],track.skip)-track.starts[lead])/track.scale*rate));
  return {lead,drop,from,to};
}

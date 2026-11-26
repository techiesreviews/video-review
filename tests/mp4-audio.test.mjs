// Reading an MP4's sound a stretch at a time (TOOLS-479). The MP4s here are built box by box in the test, so each test
// says exactly what the file's index holds: where every frame of the sound lies, and how the track sits on the clock.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readIndex,soundTrack,audioConfig,adtsHeader,stretches,byteRanges,adtsFile,placement,NotReadable,LEAD_FRAMES} from '../web/mp4-audio.js';

const bytes=(...parts)=>{const out=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let at=0;for(const p of parts){out.set(p,at);at+=p.length;}return out;};
const u8=(...values)=>Uint8Array.from(values);
const u16=n=>u8(n>>8&255,n&255);
const u32=n=>u8(n>>>24&255,n>>16&255,n>>8&255,n&255);
const u64=n=>bytes(u32(Math.floor(n/2**32)),u32(n%2**32));
const text=s=>Uint8Array.from(s,c=>c.charCodeAt(0));
const box=(type,...parts)=>{const body=bytes(...parts);return bytes(u32(body.length+8),text(type),body);};
const full=(type,version,...parts)=>box(type,u8(version,0,0,0),...parts);

// AAC-LC, 48 kHz, two channels: object type 2, frequency index 3, channel layout 2.
const LC_48K_STEREO=u8(0x11,0x90);
function esds(asc,objectType=0x40){
  const decoderConfig=bytes(u8(objectType,0x15,0,0,0),u32(128000),u32(128000),u8(5,asc.length),asc);
  return full('esds',0,u8(3,3+2+decoderConfig.length),u16(1),u8(0),u8(4,decoderConfig.length-2),decoderConfig.subarray(0,13),decoderConfig.subarray(13));
}
function mp4a(asc,{version=0,wave=false,objectType}={}){
  const fields=bytes(new Uint8Array(6),u16(1),u16(version),new Uint8Array(6),u16(2),u16(16),new Uint8Array(4),u32(48000<<16));
  const extra=new Uint8Array(version===1?16:0);
  const config=esds(asc,objectType);
  return box('mp4a',fields,extra,wave?box('wave',box('frma',text('mp4a')),config):config);
}
/**
 * A moov with a video track and a sound track. frames: the sound's frame sizes; chunks: [offset, frames in it] in
 * order; edits: [[segmentTicks, mediaTime]] on the movie's 1000 ticks a second (mediaTime -1 is an empty edit).
 */
function movie({frames,chunks,edits=[[0,1024]],scale=48000,frameTicks=1024,asc=LC_48K_STEREO,entry=mp4a(asc),wide=false}){
  const runs=[];chunks.forEach(([,n],c)=>{if(runs.at(-1)?.[1]!==n)runs.push([c+1,n]);});
  const offsets=wide?full('co64',0,u32(chunks.length),...chunks.map(([at])=>u64(at))):full('stco',0,u32(chunks.length),...chunks.map(([at])=>u32(at)));
  const stbl=box('stbl',
    full('stsd',0,u32(1),entry),
    full('stts',0,u32(1),u32(frames.length),u32(frameTicks)),
    full('stsc',0,u32(runs.length),...runs.map(([first,n])=>bytes(u32(first),u32(n),u32(1)))),
    full('stsz',0,u32(0),u32(frames.length),...frames.map(u32)),
    offsets);
  const sound=box('trak',full('tkhd',0,new Uint8Array(80)),
    box('edts',full('elst',0,u32(edits.length),...edits.map(([segment,media])=>bytes(u32(segment),u32(media>>>0),u32(1<<16))))),
    box('mdia',full('mdhd',0,u32(0),u32(0),u32(scale),u32(frames.length*frameTicks),u32(0)),
      full('hdlr',0,u32(0),text('soun'),new Uint8Array(13)),box('minf',stbl)));
  const video=box('trak',full('tkhd',0,new Uint8Array(80)),
    box('mdia',full('mdhd',0,u32(0),u32(0),u32(30000),u32(0),u32(0)),full('hdlr',0,u32(0),text('vide'),new Uint8Array(13)),box('minf',box('stbl'))));
  return box('moov',full('mvhd',0,u32(0),u32(0),u32(1000),u32(0),new Uint8Array(80)),video,sound);
}
/** A file made of parts laid at offsets, the rest zero, read as read(start, end) would read the real one. */
function file(size,parts){
  const reads=[];
  const read=async(start,end)=>{
    assert.ok(start>=0&&end<=size&&start<end,`a read inside the file: ${start}-${end}`);reads.push([start,end]);
    const out=new Uint8Array(end-start);
    for(const [at,part] of parts){const a=Math.max(start,at),b=Math.min(end,at+part.length);if(a<b)out.set(part.subarray(a-at,b-at),a-start);}
    return out;
  };
  return {size,read,reads};
}
// A frame's bytes: its number over and over, so a test sees which frame landed where.
const frame=(i,size)=>new Uint8Array(size).fill(i%251+1);

test('the index of a recording of gigabytes is found past its media data, reading only box headers and the index',async()=>{
  const GB3=3*1024**3,frames=[300,280,310];
  const ftyp=box('ftyp',text('isom'),u32(0),text('isom'));
  const mdatHeader=bytes(u32(1),text('mdat'),u64(GB3));
  const moov=movie({frames,chunks:[[ftyp.length+16,3]]});
  const big=file(ftyp.length+GB3+moov.length,[[0,ftyp],[ftyp.length,mdatHeader],[ftyp.length+GB3,moov]]);
  const index=await readIndex(big.read,big.size);
  assert.deepEqual(index,moov);
  const read=big.reads.reduce((n,[a,b])=>n+b-a,0);
  assert.ok(read<moov.length+64,`only headers and the index are read: ${read} bytes`);
});

test('a file that is not an MP4, one with no index (still recording, or fragmented) and one cut short are refused',async()=>{
  const riff=file(64,[[0,bytes(u32(64),text('RIFF'))]]);
  await assert.rejects(readIndex(riff.read,riff.size),e=>e instanceof NotReadable&&/not an MP4/.test(e.message));
  const ftyp=box('ftyp',text('isom'),u32(0)),moof=box('moof',new Uint8Array(8)),mdat=box('mdat',new Uint8Array(32));
  const fragmented=file(ftyp.length+moof.length+mdat.length,[[0,ftyp],[ftyp.length,moof],[ftyp.length+moof.length,mdat]]);
  await assert.rejects(readIndex(fragmented.read,fragmented.size),e=>e instanceof NotReadable&&/fragmented/.test(e.message));
  const short=file(ftyp.length+16,[[0,ftyp],[ftyp.length,bytes(u32(1000),text('mdat'))]]);
  await assert.rejects(readIndex(short.read,short.size),e=>e instanceof NotReadable&&/not a complete MP4/.test(e.message));
});

test('every frame of the sound is found where its chunk puts it, across runs of chunks of different sizes',()=>{
  const frames=[100,101,102,103,104,105,106,107];
  // Three frames in each of the first two chunks, then two; video lies between the chunks.
  const chunks=[[1000,3],[5000,3],[9000,2]];
  const track=soundTrack(movie({frames,chunks}));
  assert.deepEqual([...track.sizes],frames);
  assert.deepEqual([...track.offsets],[1000,1100,1201,5000,5103,5207,9000,9106]);
  assert.deepEqual([...track.starts],[0,1024,2048,3072,4096,5120,6144,7168,8192]);
  assert.equal(track.scale,48000);
  assert.deepEqual(track.config,{profile:1,frequency:3,channels:2,sampleRate:48000});
});

test('64-bit chunk offsets, past 4 GB, are read whole',()=>{
  const at=5*1024**3+12345;
  const track=soundTrack(movie({frames:[50,60],chunks:[[at,2]],wide:true}));
  assert.deepEqual([...track.offsets],[at,at+50]);
});

test('the edit list: the encoder\'s start-up frame is skipped, and a track that starts late starts late on the clock',()=>{
  const frames=new Array(48).fill(10),chunks=[[100,48]];
  const plain=soundTrack(movie({frames,chunks}));
  assert.equal(plain.skip,1024);assert.equal(plain.delayMs,0);
  assert.ok(Math.abs(plain.durationMs-47*1024/48)<1e-9,`47 frames play: ${plain.durationMs}`);
  const late=soundTrack(movie({frames,chunks,edits:[[500,-1],[980,1024]]}));
  assert.equal(late.skip,1024);assert.equal(late.delayMs,500);
  assert.ok(Math.abs(late.durationMs-(500+47*1024/48))<1e-9);
  const none=soundTrack(movie({frames,chunks,edits:[[1000,0]]}));
  assert.equal(none.skip,0);
});

test('AAC settings: plain AAC, HE-AAC named by its core, a MOV\'s wave box; MP3 sound is refused',()=>{
  assert.deepEqual(audioConfig(LC_48K_STEREO),{profile:1,frequency:3,channels:2,sampleRate:48000});
  // HE-AAC at 48 kHz: object type 5, core frequency index 6 (24 kHz), two channels, output index 3, core object type 2.
  // 00101 0110 0010 0011 00010 000
  assert.deepEqual(audioConfig(u8(0b00101011,0b00010001,0b10001000,0)),{profile:1,frequency:6,channels:2,sampleRate:24000});
  // A mono 44.1 kHz LC: 00010 0100 0001 000.
  assert.deepEqual(audioConfig(u8(0b00010010,0b00001000)),{profile:1,frequency:4,channels:1,sampleRate:44100});
  // 00010 1111 (a frequency written out in full, which ADTS cannot name) 0010.
  assert.throws(()=>audioConfig(u8(0b00010111,0b10010000,0,0,0)),/unusual sample rate/,'an unlisted frequency');
  assert.throws(()=>audioConfig(u8(0b00010001,0b11000000)),/channel layout/,'channel layout 8');
  assert.throws(()=>audioConfig(u8(0b00111000,0b10010000)),NotReadable,'not AAC (object type 7)');
  const mov=soundTrack(movie({frames:[10],chunks:[[0,1]],entry:mp4a(LC_48K_STEREO,{version:1,wave:true})}));
  assert.equal(mov.config.frequency,3);
  assert.throws(()=>soundTrack(movie({frames:[10],chunks:[[0,1]],entry:mp4a(LC_48K_STEREO,{objectType:0x6B})})),e=>e instanceof NotReadable&&/not AAC/.test(e.message));
  assert.throws(()=>soundTrack(movie({frames:[10],chunks:[[0,1]],entry:box('Opus',new Uint8Array(28))})),e=>e instanceof NotReadable&&/not AAC/.test(e.message));
});

test('the ADTS header carries the profile, frequency, channels and the frame length with its header',()=>{
  assert.deepEqual([...adtsHeader({profile:1,frequency:3,channels:2},100)],[0xFF,0xF1,0x4C,0x80,0x0D,0x7F,0xFC]);
  assert.throws(()=>adtsHeader({profile:1,frequency:3,channels:2},8190),NotReadable);
});

test('stretches cover every frame once, each about 20 seconds long',()=>{
  const track=soundTrack(movie({frames:new Array(3000).fill(10),chunks:[[0,3000]]}));
  const parts=stretches(track);
  assert.equal(parts[0][0],0);assert.equal(parts.at(-1)[1],3000);
  for(let i=1;i<parts.length;i++)assert.equal(parts[i][0],parts[i-1][1],'one starts where the last ended');
  for(const [first,last] of parts.slice(0,-1)){const s=(track.starts[last]-track.starts[first])/track.scale;assert.ok(s>19.9&&s<=20,`${s} s`);}
});

test('a stretch is read in few ranges: frames close together in one read, frames far apart in their own',async()=>{
  const frames=[100,100,100,100],chunks=[[1000,2],[2000,1],[5_000_000,1]];
  const track=soundTrack(movie({frames,chunks}));
  assert.deepEqual(byteRanges(track,0,4),[[1000,2100],[5_000_000,5_000_100]]);
  const disk=file(5_000_200,frames.map((size,i)=>[track.offsets[i],frame(i,size)]));
  const adts=await adtsFile(track,1,4,disk.read);
  assert.deepEqual(disk.reads,[[1100,2100],[5_000_000,5_000_100]]);
  assert.equal(adts.length,3*107);
  for(let k=0;k<3;k++){
    assert.deepEqual([...adts.subarray(k*107,k*107+7)],[...adtsHeader(track.config,100)]);
    assert.ok(adts.subarray(k*107+7,(k+1)*107).every(v=>v===k+2),`frame ${k+1} follows its header`);
  }
});

test('stretches lie end to end on the video\'s clock: the start-up frame and each stretch\'s lead frames are dropped',()=>{
  const rate=16000,track=soundTrack(movie({frames:new Array(3000).fill(10),chunks:[[0,3000]]}));
  const parts=stretches(track);let end=0;
  for(const [first,last] of parts){
    const {lead,drop,from,to}=placement(track,first,last,rate);
    assert.equal(lead,Math.max(0,first-LEAD_FRAMES));
    assert.equal(from,end,'each stretch starts where the last one ended');end=to;
    // The decode starts at frame `lead`; dropping `drop` samples lands on frame `first` (or on the skip, for the first).
    const startTick=Math.max(track.starts[first],track.skip);
    assert.ok(Math.abs(drop-(startTick-track.starts[lead])/track.scale*rate)<=0.5);
  }
  assert.ok(Math.abs(end-track.durationMs/1000*rate)<=1,'the last ends where the sound does');
  const first=placement(track,...parts[0],rate);
  assert.equal(first.from,0);assert.equal(first.drop,Math.round(1024/48000*rate),'the start-up frame is not played');
  const late=soundTrack(movie({frames:new Array(100).fill(10),chunks:[[0,100]],edits:[[500,-1],[2000,1024]]}));
  assert.equal(placement(late,0,50,rate).from,8000,'half a second of silence first');
});

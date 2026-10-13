"""Print saved review notes with original-source mappings for an editing pass."""
from pathlib import Path
import argparse,json,os
parser=argparse.ArgumentParser(description='Export open review notes with source mappings')
parser.add_argument('--data-dir',default=os.environ.get('VIDEO_REVIEW_DATA_DIR',str(Path(__file__).resolve().parent/'data')))
args=parser.parse_args()
data=Path(args.data_dir).expanduser()
videos={v['id']:v for v in json.loads((data/'manifest.json').read_text(encoding='utf-8'))}
comments=json.loads((data/'comments.json').read_text(encoding='utf-8'))['comments']
result=[]
for c in comments:
    if c['status']!='open':continue
    v=videos[c['videoId']];start=c['startMs']*v['fps']/1000;end=(c['endMs'] if c['endMs'] is not None else c['startMs'])*v['fps']/1000
    spans=[];cursor=0
    for a,b in v['keepRanges']:
        finish=cursor+b-a
        if c['endMs'] is None:
            if cursor<=start<finish:spans.append({'sourceFrame':a+start-cursor,'sourceMs':(a+start-cursor)*1000/v['fps']})
        elif start<finish and end>cursor:
            spans.append({'sourceStartFrame':a+max(0,start-cursor),'sourceEndFrame':a+min(b-a,end-cursor)})
        cursor=finish
    result.append({**c,'video':{k:v[k] for k in ['title','label','path','fps','fingerprint']},'sourceSpans':spans})
print(json.dumps({'schemaVersion':1,'openComments':result},indent=2,ensure_ascii=True))

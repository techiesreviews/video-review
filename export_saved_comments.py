"""Print saved review notes with original-source mappings for an editing pass."""
from pathlib import Path
import argparse,json,os,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import review_store
parser=argparse.ArgumentParser(description='Export open review notes with source mappings')
parser.add_argument('--data-dir',default=os.environ.get('VIDEO_REVIEW_DATA_DIR',str(Path(__file__).resolve().parent/'data')))
args=parser.parse_args()
data=Path(args.data_dir).expanduser()
videos={v['id']:v for v in json.loads((data/'manifest.json').read_text(encoding='utf-8'))}
comments=json.loads((data/'comments.json').read_text(encoding='utf-8'))['comments']
result=[]
for c in comments:
    if c['status']!='open':continue
    v=videos[c['videoId']]
    result.append({**c,'video':{k:v[k] for k in ['title','label','path','fps','fingerprint']},'sourceSpans':review_store.describe(v,c)['sourceSpans']})
print(json.dumps({'schemaVersion':1,'openComments':result},indent=2,ensure_ascii=True))

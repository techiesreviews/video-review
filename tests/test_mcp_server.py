import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

class McpServerTests:
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.data=Path(cls.temp.name)
        cls.media=cls.data/'example.mp4'
        cls.media.write_bytes(bytes(range(256)))
        (cls.data/'manifest.json').write_text(json.dumps([{'id':'example-v1','title':'Example','label':'v1','path':str(cls.media),'fps':30,'durationMs':60000,'fingerprint':'example','keepRanges':[[0,600],[900,2100]]}]))
        (cls.data/'comments.json').write_text(json.dumps({'comments':[{'id':'c1','videoId':'example-v1','startMs':25000,'endMs':None,'text':'Cut the pause','category':'edit','status':'open','createdAt':'2026-01-01T00:00:00Z'}]}))
        cls.proc=subprocess.Popen([sys.executable,str(ROOT/'mcp_server.py'),'--data-dir',str(cls.data)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        cls.next_id=0
        cls.rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
        cls.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');cls.proc.stdin.flush()

    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close();cls.proc.wait(timeout=5);cls.proc.stdout.close();cls.proc.stderr.close();cls.temp.cleanup()

    @classmethod
    def rpc(cls,method,params=None):
        cls.next_id+=1
        cls.proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':cls.next_id,'method':method,'params':params or {}}).encode()+b'\n');cls.proc.stdin.flush()
        reply=json.loads(cls.proc.stdout.readline())
        assert reply['id']==cls.next_id
        return reply

    def tool(self,name,**args):
        result=self.rpc('tools/call',{'name':name,'arguments':args})['result']
        text=result['content'][0]['text']
        return (text if result.get('isError') else json.loads(text)),result.get('isError',False)

    def comments_on_disk(self):
        return {c['id']:c for c in json.loads((self.data/'comments.json').read_text(encoding='utf-8'))['comments']}

    def test_lists_tools(self):
        names={t['name'] for t in self.rpc('tools/list')['result']['tools']}
        assert {'list_comments','reply_to_comment','resolve_comment','add_comment','register_video'}<=names

    def test_existing_transcript_is_attached_and_read_without_transcribing(self):
        document={'words':[{'id':'W9','text':'Finish','startMs':100,'endMs':600}],
                  'revision':'paid-once','cuts':[{'id':'J1','atMs':700,'risk':True}]}
        result,error=self.tool('set_transcript',videoId='example-v1',transcript=document)
        assert not error and result['words']==1
        result,error=self.tool('get_transcript',videoId='example-v1')
        assert not error and result['transcript']['words'][0]['id']=='W9'
        _,error=self.tool('set_transcript',videoId='example-v1',transcript={'words':[{'id':'W1','text':'Bad','startMs':-1,'endMs':10}]})
        assert error

    def test_comment_includes_frames_and_source_mapping(self):
        result,_=self.tool('list_comments',videoId='example-v1')
        comment=next(c for c in result['versions'][0]['comments'] if c['id']=='c1')
        assert comment['start']=='00:00:25.000' and comment['startFrame']==750
        assert comment['sourceSpans']==[{'sourceFrame':1050,'sourceMs':35000}]
        assert result['versions'][0]['video']['path']==str(self.media)

    def test_reply_and_resolve_are_saved_as_ai(self):
        self.tool('reply_to_comment',commentId='c1',text='Cut the whole pause or keep a breath?')
        assert self.comments_on_disk()['c1']['status']=='open'
        result,_=self.tool('resolve_comment',commentId='c1',summary='Removed 0.4 s of the pause.')
        saved=self.comments_on_disk()['c1']
        assert saved['status']=='done' and [r['author'] for r in saved['replies']]==['ai','ai']
        assert result['comment']['replies'][-1]['text']=='Removed 0.4 s of the pause.'
        self.tool('reopen_comment',commentId='c1')
        assert self.comments_on_disk()['c1']['status']=='open'

    def test_ai_can_flag_a_range_with_a_timecode(self):
        result,_=self.tool('add_comment',videoId='example-v1',start='0:10.5',end='12',text='Check the jump cut')
        saved=self.comments_on_disk()[result['comment']['id']]
        assert (saved['startMs'],saved['endMs'],saved['author'])==(10500,12000,'ai')

    def test_errors_are_reported_to_the_ai(self):
        message,error=self.tool('add_comment',videoId='example-v1',start='2:00',text='Past the end')
        assert error and 'outside' in message
        message,error=self.tool('resolve_comment',commentId='missing',summary='x')
        assert error and 'not found' in message

    def test_agent_can_open_a_video_for_the_reviewer(self):
        (self.data/'view.json').write_text(json.dumps({'videoId':'example-v1','playheadMs':41300}))
        result,_=self.tool('list_videos')
        assert result['selectedVideoId']=='example-v1' and result['playhead']=='00:00:41.300'
        result,_=self.tool('open_video',videoId='example-v1',commentId='c1')
        request=json.loads((self.data/'view-request.json').read_text(encoding='utf-8'))
        assert (request['videoId'],request['atMs'],request['commentId'])==('example-v1',25000,'c1')
        assert request['id']==result['requested']['id']
        message,error=self.tool('open_video',videoId='example-v1',at='5:00')
        assert error and 'outside' in message

    def test_open_video_records_a_render_under_the_same_name(self):
        manifest=self.data/'manifest.json';original=manifest.read_text()
        try:
            self.tool('open_video',videoId='example-v1',durationMs=20000,keepRanges=[[0,600]])
            item=json.loads(manifest.read_text())[0]
            assert (item['durationMs'],item['keepRanges'],len(item['fingerprint']))==(20000,[[0,600]],64)
            request=json.loads((self.data/'view-request.json').read_text(encoding='utf-8'))
            assert request['videoId']=='example-v1'
            result,_=self.tool('open_video',videoId='example-v1',commentId='c1')
            assert result['requested']['atMs']==20000
            message,error=self.tool('open_video',videoId='example-v1',keepRanges=[[5,2]])
            assert error and 'keepRanges' in message
        finally:manifest.write_text(original)

    def test_new_cut_can_be_registered_once(self):
        result,_=self.tool('register_video',id='example-v2',title='Example',label='v2',path=str(self.media),fps=30,durationMs=60000)
        assert result['registered']['keepRanges']==[[0,1800]] and len(result['registered']['fingerprint'])==64
        _,error=self.tool('register_video',id='example-v2',title='Example',label='v2',path=str(self.media),fps=30,durationMs=60000)
        assert error

if __name__=='__main__':
    McpServerTests.setUpClass()
    try:
        suite=McpServerTests()
        checks=[name for name in dir(suite) if name.startswith('test_')]
        for name in checks:
            getattr(suite,name)()
            print('PASS',name)
        print(f'{len(checks)} checks passed.')
    finally:McpServerTests.tearDownClass()

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import review_store as store

class TranscriptReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=store.data_dir(self.temp.name)
        self.video={'id':'v','fps':30,'durationMs':2000,'keepRanges':[[300,360]]}
        store.write(self.data/'manifest.json',[self.video])
    def tearDown(self):self.temp.cleanup()
    def test_word_transcript_roundtrip_does_not_touch_comments(self):
        before=(self.data/'comments.json').read_bytes()
        data={'words':[{'id':'W5','text':'café','startMs':100,'endMs':600}],'cuts':[{'id':'J1','atMs':800,'risk':True}]}
        store.save_transcript(self.data,'v',data)
        self.assertEqual(store.get_transcript(self.data,'v')['words'][0]['text'],'café')
        self.assertEqual((self.data/'comments.json').read_bytes(),before)
    def test_wrong_cut_nan_and_duplicate_ids_are_rejected(self):
        for words in [[{'id':'W1','text':'a','startMs':0,'endMs':9000}],
                      [{'id':'W1','text':'a','startMs':float('nan'),'endMs':100}],
                      [{'id':'W1','text':'a','startMs':0,'endMs':100},{'id':'W1','text':'b','startMs':200,'endMs':300}]]:
            with self.subTest(words=words),self.assertRaises(ValueError):store.save_transcript(self.data,'v',{'words':words})
    def test_review_request_has_immutable_source_anchors(self):
        _,comment=store.apply(self.data,{'action':'add','videoId':'v','startMs':100,'endMs':900,'text':'Clipped',
              'editRequest':{'type':'clipped','quote':'word','firstWordId':'W5','lastWordId':'W5'}})
        anchors=store.describe(self.video,comment)['sourceSpans']
        self.video['keepRanges']=[[900,960]]
        self.assertEqual(store.describe(self.video,comment)['sourceSpans'],anchors)
        self.assertEqual(comment['status'],'open')
    def test_invalid_request_does_not_write(self):
        before=(self.data/'comments.json').read_bytes()
        with self.assertRaises(ValueError):store.apply(self.data,{'action':'add','videoId':'v','startMs':0,'text':'x','editRequest':{'type':'run'}})
        self.assertEqual((self.data/'comments.json').read_bytes(),before)
    def test_library_search_uses_the_spoken_phrase_time(self):
        self.video.update(title='Security episode',label='Opening')
        store.write(self.data/'manifest.json',[self.video])
        store.save_transcript(self.data,'v',{'words':[{'id':'W1','text':'a','startMs':100,'endMs':200},{'id':'W2','text':'backup','startMs':300,'endMs':500}]})
        self.assertEqual(store.search_library(self.data,'a backup')[0]['atMs'],100)
        self.assertEqual(store.search_library(self.data,'security')[0]['id'],'v')
    def test_removing_and_restoring_a_video_preserves_review_notes(self):
        store.apply(self.data,{'action':'add','videoId':'v','startMs':100,'text':'Keep this note'})
        before=(self.data/'comments.json').read_bytes()
        store.archive_video(self.data,'v');self.assertEqual(store.videos(self.data),[])
        self.assertEqual((self.data/'comments.json').read_bytes(),before)
        store.restore_video(self.data,'v');self.assertEqual(store.videos(self.data),[self.video])
        self.assertEqual((self.data/'comments.json').read_bytes(),before)
    def test_draft_removals_survive_storage_and_invalid_ranges_are_rejected(self):
        t={'words':[{'id':'a','text':'one','startMs':100,'endMs':600}],
           'draftEdits':[{'id':'cut','startMs':200,'endMs':500,'wordIds':['a'],'quote':'one'}]}
        self.assertEqual(store.save_transcript(self.data,'v',t)['draftEdits'][0]['wordIds'],['a'])
        t['draftEdits'][0]['endMs']=3000
        with self.assertRaises(ValueError):store.save_transcript(self.data,'v',t)
    def test_render_retimes_comments_and_preserves_original_anchors(self):
        _,c=store.apply(self.data,{'action':'add','videoId':'v','startMs':1500,'endMs':1800,'text':'Keep this'})
        store.rebase_comments(self.data,self.video,[[330,360]],1000)
        changed=store.comments(self.data)[0]
        self.assertEqual(changed['id'],c['id']);self.assertEqual(changed['startMs'],500)
        self.assertEqual(changed['sourceSpans'][0]['sourceStartFrame'],345)

    def test_original_words_survive_storage_and_restore_requests_use_raw_clock(self):
        original={'durationMs':14000,'sourceId':'raw','words':[{'id':'old','text':'deleted','startMs':13000,'endMs':13500},{'id':'W5','text':'kept','startMs':13600,'endMs':13900}]}
        data={'words':[{'id':'W5','text':'kept','startMs':100,'endMs':600}],'original':original}
        saved=store.save_transcript(self.data,'v',data)
        self.assertEqual(saved['original']['words'][0]['id'],'old')
        _,comment=store.apply(self.data,{'action':'add','videoId':'v','startMs':2000,'text':'Restore deleted','editRequest':{'type':'restore','sourceStartMs':13000,'sourceEndMs':13500,'sourceDurationMs':14000}})
        self.assertEqual(comment['sourceSpans'][0]['sourceStartFrame'],390)
        self.assertEqual(store.describe(self.video,comment)['editRequest']['type'],'restore')
        data['original']['words'][1]['id']='wrong'
        with self.assertRaises(ValueError):store.save_transcript(self.data,'v',data)

if __name__=='__main__':unittest.main()

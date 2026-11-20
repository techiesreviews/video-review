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
    def test_an_edits_only_save_is_refused_once_the_saved_words_changed(self):
        # The page saves its preview cuts with editsOnly; a new render or an import that came in meanwhile wins.
        words=[{'id':'a','text':'one','startMs':100,'endMs':600},{'id':'b','text':'two','startMs':700,'endMs':1200}]
        cut={'id':'cut','startMs':200,'endMs':500,'wordIds':['a'],'quote':'one'}
        with self.assertRaisesRegex(ValueError,'transcript changed'):store.save_transcript(self.data,'v',{'words':words,'draftEdits':[cut]},edits_only=True)
        store.save_transcript(self.data,'v',{'words':words,'revision':'r1'})
        saved=store.save_transcript(self.data,'v',{'words':words,'revision':'r1','draftEdits':[cut]},edits_only=True)
        self.assertEqual(store.get_transcript(self.data,'v')['draftEdits'],saved['draftEdits'])
        store.save_transcript(self.data,'v',{'words':[{**words[0],'startMs':150},words[1]],'revision':'r1'})
        before=store.get_transcript(self.data,'v')
        with self.assertRaisesRegex(ValueError,'transcript changed'):store.save_transcript(self.data,'v',{'words':words,'revision':'r1','draftEdits':[]},edits_only=True)
        self.assertEqual(store.get_transcript(self.data,'v'),before,'the newer words stay saved')
        self.assertEqual(store.MESSAGES['transcriptChanged'],json.loads((Path(__file__).resolve().parents[1]/'web'/'review-rules.json').read_text(encoding='utf-8'))['messages']['transcriptChanged'])

    def test_an_edits_only_save_after_a_shorter_render_is_refused_as_changed_words(self):
        # The page still holds the old, longer words; the refusal must say the words changed, so the page reloads them,
        # not that its transcript is longer than the video.
        old=[{'id':'a','text':'one','startMs':100,'endMs':600},{'id':'b','text':'two','startMs':1500,'endMs':1900}]
        store.save_transcript(self.data,'v',{'words':old})
        store.write(self.data/'manifest.json',[{**self.video,'durationMs':1000}])
        store.save_transcript(self.data,'v',{'words':old[:1],'revision':'r2'})
        cut={'id':'cut','startMs':200,'endMs':500,'wordIds':['a'],'quote':'one'}
        with self.assertRaisesRegex(ValueError,'transcript changed'):
            store.save_transcript(self.data,'v',{'words':old,'draftEdits':[cut]},edits_only=True)
        self.assertEqual(store.get_transcript(self.data,'v')['revision'],'r2')

    def test_an_edits_only_save_writes_only_the_cut_list(self):
        words=[{'id':'a','text':'one','startMs':100,'endMs':600}]
        store.save_transcript(self.data,'v',{'words':words,'revision':'r1','cuts':[{'id':'J1','atMs':700}]})
        store.save_transcript(self.data,'v',{'words':words,'revision':'r2','label':'Corrected','cuts':[{'id':'J9','atMs':800,'risk':True}]})
        cut={'id':'cut','startMs':200,'endMs':500,'wordIds':['a'],'quote':'one'}
        saved=store.save_transcript(self.data,'v',{'words':words,'revision':'r1','cuts':[{'id':'J1','atMs':700}],'draftEdits':[cut]},edits_only=True)
        stored=store.get_transcript(self.data,'v')
        self.assertEqual(saved,stored)
        self.assertEqual((stored['revision'],stored['label'],[c['id'] for c in stored['cuts']]),('r2','Corrected',['J9']))
        self.assertEqual([e['id'] for e in stored['draftEdits']],['cut'])

    def test_an_edits_only_save_never_puts_back_an_older_render(self):
        # A render recorded after the transcript changes the manifest and comments but not the transcript file;
        # a Delete on the page then saves its cuts only and must leave that render as it is.
        store.write(self.data/'manifest.json',[{**self.video,'path':str(Path(self.temp.name)/'v.mp4')}])
        words=[{'id':'a','text':'one','startMs':100,'endMs':600}]
        store.save_transcript(self.data,'v',{'words':words,'fps':30,'keepRanges':[[300,360]]})
        store.apply(self.data,{'action':'add','videoId':'v','startMs':1500,'endMs':1800,'text':'Keep this'})
        store.update_render(self.data,'v',None,[[600,660]])
        video=store.find_video(self.data,'v');moved=[c['startMs'] for c in store.comments(self.data)]
        cut={'id':'cut','startMs':200,'endMs':500,'wordIds':['a'],'quote':'one'}
        store.save_transcript(self.data,'v',{'words':words,'fps':30,'keepRanges':[[300,360]],'draftEdits':[cut]},edits_only=True)
        self.assertEqual(store.find_video(self.data,'v')['keepRanges'],video['keepRanges'])
        self.assertEqual([c['startMs'] for c in store.comments(self.data)],moved)
        self.assertEqual([e['id'] for e in store.get_transcript(self.data,'v')['draftEdits']],['cut'])

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

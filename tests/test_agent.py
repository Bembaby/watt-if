"""Scripted Strands SDK contract tests, explicitly not live-model evidence."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from household import Store,Invalid
from agent import run,RunError,LIMITS,grounded_patch,resolve_indexed_changes
try:from strands.models.model import Model
except ImportError:Model=object

class ScriptedModel(Model):
    def __init__(self,steps,delay=0,text='Ready for review.'):self.steps=steps;self.calls=0;self.delay=delay;self.text=text
    def get_config(self):return {'model_id':'scripted-fixture'}
    def update_config(self,**kwargs):pass
    async def structured_output(self,*args,**kwargs):
        raise NotImplementedError
        yield
    async def stream(self,messages,tool_specs=None,system_prompt=None,**kwargs):
        self.calls+=1;await asyncio.sleep(self.delay)
        step=self.steps[min(self.calls-1,len(self.steps)-1)]
        yield {'messageStart':{'role':'assistant'}}
        if isinstance(step,Exception):raise step
        if step:
            name,args=step
            yield {'contentBlockStart':{'start':{'toolUse':{'name':name,'toolUseId':f'test-{self.calls}'}}}}
            yield {'contentBlockDelta':{'delta':{'toolUse':{'input':json.dumps(args)}}}}
        else:
            yield {'contentBlockStart':{'start':{}}}
            yield {'contentBlockDelta':{'delta':{'text':self.text}}}
        yield {'contentBlockStop':{}}
        yield {'messageStop':{'stopReason':'tool_use' if step else 'end_turn'}}
        yield {'metadata':{'usage':{'inputTokens':10,'outputTokens':10,'totalTokens':20},'metrics':{'latencyMs':1}}}

@unittest.skipIf(Model is object,'Install requirements.txt to run real SDK contract fixtures.')
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.db')
        self.steps=[('read_household',{}),('propose_evening',{'changes':[]}),None]
    def tearDown(self):self.temp.cleanup()
    def test_sdk_workflow_is_draft_and_trace_labels_fixture(self):
        r=run('Plan my evening',self.store,model=ScriptedModel(self.steps))
        self.assertEqual(r['proposal']['status'],'proposed');self.assertEqual(r['trace']['evidence_type'],'sdk-fixture')
        self.assertFalse(r['trace']['approval_performed'])
        with self.assertRaises(Invalid):self.store.export(r['proposal']['id'])
    def test_sdk_validates_nested_grounded_deadline_changes(self):
        message='Have the ebike ready by 10 pm.'
        self.steps[1]=('propose_evening',{'changes':[{'job_id':'ebike','field':'latest_finish','value':'22:00','clause_id':1}]})
        r=run(message,self.store,model=ScriptedModel(self.steps))
        self.assertEqual(r['proposal']['constraints']['jobs']['ebike']['latest_finish'],'22:00')
        self.assertEqual(r['proposal']['constraints']['jobs']['ebike']['earliest'],'18:00')
    def test_wrong_model_narrative_cannot_override_validated_schedule_reply(self):
        r=run('Plan',self.store,model=ScriptedModel(self.steps,text='The ebike is ready by 22:00 and everything is already approved.'))
        ebike=next(j for j in r['proposal']['schedule'] if j['job_id']=='ebike')
        self.assertIn(f"{ebike['name']}: {ebike['start']}–{ebike['end']}",r['reply'])
        self.assertNotIn('ready by 22:00',r['reply'])
        self.assertNotIn('already approved',r['reply'])
        self.assertEqual(r['reply_source'],'validated_proposal_template')
        self.assertEqual(r['trace']['reply_source'],'validated_proposal_template')
    def test_failure_after_preview_saves_nothing(self):
        self.steps[-1]=RuntimeError('failed')
        with self.assertRaises(RunError) as caught:run('Plan',self.store,model=ScriptedModel(self.steps))
        self.assertEqual(caught.exception.trace['read_count'],1)
        self.assertEqual(caught.exception.trace['proposal_attempt_count'],1)
        self.assertTrue(caught.exception.trace['validated_preview'])
        self.assertIn(caught.exception.trace['error_type'],('RuntimeError','EventLoopException'))
        self.assertTrue(caught.exception.trace['sdk_tool_events'])
        self.assertEqual(self.store.household()['revision'],0)
    def test_tool_loop_bounded(self):
        model=ScriptedModel([('read_household',{})])
        with self.assertRaises(RunError):run('Plan',self.store,model=model)
        self.assertEqual(model.calls,LIMITS['turns']);self.assertEqual(self.store.household()['revision'],0)
    def test_timeout_no_fallback(self):
        with self.assertRaises(RunError):run('Plan',self.store,model=ScriptedModel(self.steps,delay=0.2),timeout_seconds=0.01)
        self.assertEqual(self.store.household()['revision'],0)
    def test_proposal_reads_snapshot_as_explicit_dependency(self):
        r=run('Plan',self.store,model=ScriptedModel(self.steps[1:]))
        self.assertEqual(r['proposal']['status'],'proposed')
        event=r['trace']['tool_events'][0]
        self.assertEqual(event['tool'],'read_household')
        self.assertEqual(event['initiated_by'],'proposal_dependency')
        self.assertEqual(event['result']['revision'],0)
        self.assertEqual(r['proposal']['revision'],1)
    def test_unknown_equipment_never_persists(self):
        self.steps[1]=('propose_evening',{'changes':[{'job_id':'dryer','field':'skip','value':'true','clause_id':1}]})
        with self.assertRaises(RunError):run('Skip the fridge',self.store,model=ScriptedModel(self.steps))
        self.assertEqual(self.store.household()['revision'],0)
    def test_source_edit_during_model_prevents_save(self):
        import agent
        with patch('agent.sources',side_effect=[agent.LOADED_SOURCES,agent.LOADED_SOURCES,{}]):
            with self.assertRaises(RunError):run('Plan',self.store,model=ScriptedModel(self.steps))
        self.assertEqual(self.store.household()['revision'],0)

class IndexedSourceTests(unittest.TestCase):
    def test_shared_source_preserves_original_names(self):
        message='Keep the dryer and dishwasher finished by 8 pm. Have the ebike ready by 10 pm.'
        changes=[dict(job_id=j,field='latest_finish',value='20:00',clause_id=1) for j in ('dryer','dishwasher')]
        changes.append(dict(job_id='ebike',field='latest_finish',value='22:00',clause_id=2))
        resolved=resolve_indexed_changes(changes,message)
        self.assertEqual(resolved[0]['evidence'],resolved[1]['evidence'])
        self.assertIn('dryer and dishwasher',resolved[0]['evidence'])
        self.assertEqual(grounded_patch(resolved,message)['jobs']['ebike']['latest_finish'],'22:00')
    def test_source_id_bounds_and_types(self):
        for clause_id in (0,-1,2,True,'1',1.0):
            with self.subTest(clause_id=clause_id),self.assertRaises(ValueError):
                resolve_indexed_changes([dict(job_id='dryer',field='skip',value='true',clause_id=clause_id)],'Skip the dryer.')
    def test_invented_quote_cannot_be_sent_as_extra_field(self):
        with self.assertRaises(ValueError):
            resolve_indexed_changes([dict(job_id='dryer',field='skip',value='true',clause_id=1,evidence='Skip the dryer')],'Do not skip the dryer.')
    def test_wrong_source_id_still_fails_existing_validator(self):
        message='Skip the dryer. Finish the dishwasher by 8 pm.'
        resolved=resolve_indexed_changes([dict(job_id='dryer',field='skip',value='true',clause_id=2)],message)
        with self.assertRaises(Invalid):grounded_patch(resolved,message)
class GroundingTests(unittest.TestCase):
    def change(self,job,field,value,evidence):return dict(job_id=job,field=field,value=value,evidence=evidence)
    def test_actual_initial_failure_regression(self):
        quote='Keep the dryer and dishwasher finished by 8 pm.'
        ebike='Have the ebike ready by 10 pm.'
        message=quote+' '+ebike
        changes=[self.change(j,'latest_finish','20:00',quote) for j in ('dryer','dishwasher')]
        changes.append(self.change('ebike','latest_finish','22:00',ebike))
        patch=grounded_patch(changes,message)
        self.assertEqual(patch,{'jobs':{'dryer':{'latest_finish':'20:00'},'dishwasher':{'latest_finish':'20:00'},'ebike':{'latest_finish':'22:00'}}})
        from household import DEFAULT,merge_constraints,calculate
        p=calculate(merge_constraints(DEFAULT,patch));self.assertEqual(p['status'],'proposed')
        self.assertTrue(all(c['earliest']=='18:00' for c in p['constraints']['jobs'].values()))
    def test_invented_start_from_deadline_rejected(self):
        q='Have the ebike ready by 10 pm.'
        with self.assertRaises(Invalid):grounded_patch([self.change('ebike','earliest','22:00',q)],q)
    def test_wrong_time_or_invented_quote_rejected(self):
        q='Have the ebike ready by 10 pm.'
        for value,quote in [('24:00',q),('22:00','Have the ebike ready by 22:00.')]:
            with self.assertRaises(Invalid):grounded_patch([self.change('ebike','latest_finish',value,quote)],q)
    def test_start_by_is_not_earliest(self):
        q='Start the dryer by 8 pm.'
        with self.assertRaises(Invalid):grounded_patch([self.change('dryer','earliest','20:00',q)],q)
    def test_explicit_start_and_quiet_cutoff(self):
        q='Start the dryer at 7 pm. Keep quiet after 9 pm.'
        changes=[self.change('dryer','earliest','19:00','Start the dryer at 7 pm.'),self.change('household','quiet_after','21:00','Keep quiet after 9 pm.')]
        self.assertEqual(grounded_patch(changes,q),{'quiet_after':'21:00','jobs':{'dryer':{'earliest':'19:00'}}})
    def test_shared_deadline_requires_both_jobs(self):
        q='Dryer and dishwasher finished by 8 pm.'
        with self.assertRaises(Invalid):grounded_patch([self.change('dryer','latest_finish','20:00',q)],q)
    def test_missing_time_cannot_be_silently_ignored(self):
        q='Have the ebike ready by 10 pm.'
        with self.assertRaises(Invalid):grounded_patch([],q)
    def test_skip_and_restore_explicit_only(self):
        self.assertTrue(grounded_patch([self.change('dryer','skip','true','Skip the dryer.')],'Skip the dryer.')['jobs']['dryer']['skip'])
        self.assertFalse(grounded_patch([self.change('dryer','skip','false','Restore the dryer.')],'Restore the dryer.')['jobs']['dryer']['skip'])
        q="Don't skip the dryer."
        with self.assertRaises(Invalid):grounded_patch([self.change('dryer','skip','true',q)],q)
    def test_quiet_until_is_not_quiet_after(self):
        q='Keep quiet until 9 pm.'
        with self.assertRaises(Invalid):grounded_patch([self.change('household','quiet_after','21:00',q)],q)
    def test_full_message_negation_and_cropped_quote_rejected(self):
        cases=[('Do not skip the dryer.','dryer','skip','true','skip the dryer.'),
               ('Do not finish the dryer by 8 pm.','dryer','latest_finish','20:00','Do not finish the dryer by 8 pm.'),
               ('Do not start the dryer at 8 pm.','dryer','earliest','20:00','Do not start the dryer at 8 pm.'),
               ('Do not keep quiet after 8 pm.','household','quiet_after','20:00','Do not keep quiet after 8 pm.')]
        for message,job,field,value,quote in cases:
            with self.subTest(message=message),self.assertRaises(Invalid):
                grounded_patch([self.change(job,field,value,quote)],message)
    def test_each_clause_needs_job_coverage_even_same_clock(self):
        for message,quote in [('Finish the dryer by 8 pm. Finish the dishwasher by 8 pm.','Finish the dryer by 8 pm.'),
                              ('Skip the dryer. Finish the dishwasher by 8 pm.','Finish the dishwasher by 8 pm.')]:
            job='dishwasher' if quote.startswith('Finish the dishwasher') else 'dryer'
            with self.assertRaises(Invalid):grounded_patch([self.change(job,'latest_finish','20:00',quote)],message)
    def test_cropped_shared_job_and_composite_sentence_rejected(self):
        q='Finish the dryer and dishwasher by 8 pm.'
        with self.assertRaises(Invalid):grounded_patch([self.change('dishwasher','latest_finish','20:00','dishwasher by 8 pm.')],q)
    def test_explicit_not_before_supported(self):
        q='Start the dryer not before 8 pm.'
        self.assertEqual(grounded_patch([self.change('dryer','earliest','20:00',q)],q),{'jobs':{'dryer':{'earliest':'20:00'}}})
    def test_mixed_skip_and_finish_complete_clauses(self):
        q='Skip the dryer; finish the dishwasher by 8 pm.'
        changes=[self.change('dryer','skip','true','Skip the dryer'),self.change('dishwasher','latest_finish','20:00','finish the dishwasher by 8 pm.')]
        self.assertEqual(grounded_patch(changes,q),{'jobs':{'dryer':{'skip':True},'dishwasher':{'latest_finish':'20:00'}}})

if __name__=='__main__':unittest.main()

"""Regression evidence for independently reproduced gaps; fixtures are not inference."""
import json
from pathlib import Path
import tempfile
import unittest
from household import Store, Conflict, Invalid, DEFAULT, JOBS, calculate
from copy import deepcopy
from agent import run, RunError, grounded_patch
from test_agent import ScriptedModel

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'home.db';self.store=Store(self.path)
    def tearDown(self): self.temp.cleanup()
    def test_failed_request_survives_restart_and_blocks_old_approval_but_not_historical_export(self):
        old=self.store.propose({});approved=self.store.approve(old['id'],old['revision'])
        steps=[('read_household',{}),('propose_evening',{'changes':[dict(job_id='ebike',field='latest_finish',value='22:00',clause_id=1)]}),RuntimeError('provider failed')]
        with self.assertRaises(RunError):run('Have the ebike ready by 10 pm.',self.store,model=ScriptedModel(steps))
        restarted=Store(self.path)
        self.assertEqual(restarted.household()['pending_request']['message'],'Have the ebike ready by 10 pm.')
        self.assertEqual(restarted.household()['revision'],old['revision'])
        with self.assertRaises(Conflict):restarted.approve(old['id'],old['revision'])
        self.assertIn('BEGIN:VCALENDAR',restarted.export(approved['id']))
        result=run('Try again.',restarted,model=ScriptedModel(steps[:-1]+[None]))
        self.assertEqual(result['proposal']['constraints']['jobs']['ebike']['latest_finish'],'22:00')
        self.assertNotIn('pending_request',restarted.household())
        self.assertTrue(restarted.approve(result['proposal']['id'],result['proposal']['revision']))
    def test_stale_manual_form_cannot_restore_newly_skipped_chore(self):
        old=self.store.household();self.store.propose({'jobs':{'dryer':{'skip':True}}})
        with self.assertRaises(Conflict):self.store.propose(old['constraints'],expected_revision=old['revision'])
        self.assertTrue(self.store.household()['constraints']['jobs']['dryer']['skip'])
    def test_unknown_conditional_and_wrong_start_bound_fail_closed(self):
        cases=[('If I am home, skip the dryer.',[dict(job_id='dryer',field='skip',value='true',evidence='If I am home, skip the dryer.')]),
               ('Skip the dryer. Skip the fridge.',[dict(job_id='dryer',field='skip',value='true',evidence='Skip the dryer.')]),
               ('Start the dryer before 8 pm.',[dict(job_id='dryer',field='earliest',value='20:00',evidence='Start the dryer before 8 pm.')]),
               ('Run the dryer.',[])]
        for message,changes in cases:
            with self.subTest(message=message),self.assertRaises(Invalid):grounded_patch(changes,message)
    def test_skipped_chore_deadline_needs_explicit_restore(self):
        self.store.propose({'jobs':{'dryer':{'skip':True}}})
        steps=[('read_household',{}),('propose_evening',{'changes':[dict(job_id='dryer',field='latest_finish',value='20:00',clause_id=1)]}),None]
        with self.assertRaises(RunError):run('Have the dryer ready by 8 pm.',self.store,model=ScriptedModel(steps))
        self.assertTrue(self.store.household()['constraints']['jobs']['dryer']['skip'])
    def test_exact_start_is_persistent_and_replacement_does_not_invent_deadline(self):
        steps=[('read_household',{}),('propose_evening',{'changes':[dict(job_id='ebike',field='earliest',value='19:00',clause_id=1)]}),None]
        result=run('Start the ebike at 7 pm.',self.store,model=ScriptedModel(steps))
        self.assertEqual(next(j for j in result['proposal']['schedule'] if j['job_id']=='ebike')['start'],'19:00')
        self.assertEqual(result['proposal']['constraints']['jobs']['ebike']['latest_finish'],'24:00')
        self.assertEqual(result['proposal']['constraints']['jobs']['ebike']['exact_start'],'19:00')
        steps[1]=('propose_evening',{'changes':[dict(job_id='ebike',field='earliest',value='20:00',clause_id=1)]})
        later=run('Start the ebike at 8 pm.',self.store,model=ScriptedModel(steps))
        self.assertEqual(next(j for j in later['proposal']['schedule'] if j['job_id']=='ebike')['start'],'20:00')
    def test_exact_start_survives_infeasible_then_quiet_correction(self):
        changes=[dict(job_id='dryer',field='earliest',value='21:00',clause_id=1),dict(job_id='household',field='quiet_after',value='20:00',clause_id=2)]
        first=run('Start the dryer at 9 pm. Keep quiet after 8 pm.',self.store,model=ScriptedModel([('propose_evening',{'changes':changes}),None]))
        self.assertEqual(first['proposal']['status'],'infeasible')
        result=run('Keep quiet after 10 pm.',self.store,model=ScriptedModel([('propose_evening',{'changes':[dict(job_id='household',field='quiet_after',value='22:00',clause_id=1)]}),None]))
        self.assertEqual(next(j for j in result['proposal']['schedule'] if j['job_id']=='dryer')['start'],'21:00')
    def test_all_exact_start_positions_match_duration_and_quiet_limits(self):
        for job in JOBS:
            for start_slot in range(13):
                c=deepcopy(DEFAULT);requested=f'{18+start_slot//2:02}:{start_slot%2*30:02}'
                c['jobs'][job['id']]['exact_start']=requested
                latest_finish=6 if job['noisy'] else 12
                feasible=start_slot+job['duration_minutes']//30<=latest_finish
                result=calculate(c)
                self.assertEqual(result['status'],'proposed' if feasible else 'infeasible')
                if feasible:self.assertEqual(next(j for j in result['schedule'] if j['job_id']==job['id'])['start'],requested)
    def test_unsupported_shared_companions_rejected_and_generic_plan_allowed(self):
        for message,field,value in [('Skip the dryer and fridge.','skip','true'),('Finish the dryer and vacuum by 8 pm.','latest_finish','20:00')]:
            with self.subTest(message=message),self.assertRaises(Invalid):
                grounded_patch([dict(job_id='dryer',field=field,value=value,evidence=message)],message)
        self.assertEqual(grounded_patch([],'Schedule my evening'),{})
    def test_pending_generation_requires_explicit_current_manual_replacement(self):
        old=self.store.household();pending=self.store.begin_request('Have the ebike ready by 10 pm.')['pending_request']
        with self.assertRaises(Conflict):self.store.propose(old['constraints'],expected_revision=old['revision'])
        newer=self.store.begin_request('Retry?')['pending_request']
        self.assertEqual(newer['message'],pending['message'])
        with self.assertRaises(Conflict):self.store.propose(old['constraints'],expected_revision=old['revision'],resolve_request_id=pending['id'])
        self.store.propose(old['constraints'],expected_revision=old['revision'],resolve_request_id=newer['id'])
        self.assertNotIn('pending_request',self.store.household())
    def test_pending_followup_retains_original_obligation(self):
        self.store.begin_request('Have the ebike ready by 10 pm.')
        self.assertEqual(self.store.begin_request('Try again?')['pending_request']['message'],'Have the ebike ready by 10 pm.')
        message=self.store.begin_request('Finish the dryer by 8 pm.')['pending_request']['message']
        self.assertIn('Have the ebike ready by 10 pm.',message)
        self.assertIn('Finish the dryer by 8 pm.',message)
    def test_old_pending_schema_migrates_without_losing_request(self):
        with self.store.connect() as db:
            state=self.store.household()
            state['pending_request']={'message':'Skip the dryer.','received_at':'2026-10-21T12:00:00Z','status':'needs_plan'}
            db.execute('UPDATE state SET value=? WHERE key=?',(json.dumps(state),'current'))
        restarted=Store(self.path)
        pending=restarted.household()['pending_request']
        self.assertTrue(pending['id'])
        with self.assertRaises(Conflict):restarted.propose({})
        restarted.propose({'jobs':{'dryer':{'skip':True}}},resolve_request_id=pending['id'])
    def test_clarification_exposes_retained_request_for_manual_recovery(self):
        result=run('Plan my evening',self.store,model=ScriptedModel([('read_household',{}),None],text='What would you like to change?'))
        self.assertIsNone(result['proposal'])
        self.assertEqual(result['pending_request'],self.store.household()['pending_request'])

if __name__=='__main__':unittest.main()

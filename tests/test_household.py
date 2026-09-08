import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from household import Store,DEFAULT,JOBS,Invalid,Conflict,calculate,merge_constraints,slot

class HouseholdTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.sqlite3')
    def tearDown(self): self.temp.cleanup()
    def test_quiet_deadlines_and_unchanged_energy(self):
        p=self.store.propose({})
        self.assertEqual(p['status'],'proposed')
        self.assertEqual(p['metrics']['energy_kwh'],p['baseline_metrics']['energy_kwh'])
        self.assertLess(p['metrics']['peak_watts'],p['baseline_metrics']['peak_watts'])
        for j in p['schedule']:
            if j['job_id']!='ebike': self.assertLessEqual(j['end'],'21:00')
    def test_changed_earliest_and_finish(self):
        p=self.store.propose({'jobs':{'ebike':{'earliest':'20:00','latest_finish':'22:00'}}})
        j=next(j for j in p['schedule'] if j['job_id']=='ebike')
        self.assertEqual((j['start'],j['end']),('20:00','22:00'))
    def test_conflict_does_not_relax_requested_constraints(self):
        p=self.store.propose({'quiet_after':'19:00','jobs':{'dryer':{'earliest':'19:00'}}})
        self.assertEqual(p['status'],'infeasible');self.assertEqual(p['schedule'],[])
        self.assertEqual(p['constraints']['quiet_after'],'19:00')
        with self.assertRaises(Invalid): self.store.approve(p['id'],p['revision'])
    def test_skip_does_not_claim_energy_savings(self):
        full=calculate(DEFAULT)
        p=self.store.propose({'jobs':{'dryer':{'skip':True}}})
        self.assertNotIn('dryer',[j['job_id'] for j in p['schedule']])
        self.assertAlmostEqual(full['metrics']['energy_kwh']-p['metrics']['energy_kwh'],2.2)
        self.assertEqual(p['metrics']['energy_kwh'],p['baseline_metrics']['energy_kwh'])
        self.assertEqual(p['skipped_job_energy_kwh'],2.2)
    def test_partial_changes_preserve_state(self):
        self.store.propose({'jobs':{'dryer':{'skip':True}}})
        p=self.store.propose({'quiet_after':'20:30'})
        self.assertTrue(p['constraints']['jobs']['dryer']['skip'])
    def test_stale_approval_rejected_even_if_later_infeasible(self):
        p=self.store.propose({});self.store.propose({'quiet_after':'18:00'})
        with self.assertRaises(Conflict):self.store.approve(p['id'],p['revision'])
    def test_concurrent_snapshot_does_not_overwrite(self):
        revision=self.store.household()['revision'];self.store.propose({})
        with self.assertRaises(Conflict):self.store.propose({},expected_revision=revision)
    def test_exact_approval_idempotent_and_ics(self):
        p=self.store.propose({});a=self.store.approve(p['id'],p['revision'])
        self.assertEqual(a,self.store.approve(p['id'],p['revision']))
        text=self.store.export(a['id'])
        self.assertEqual(text.count('BEGIN:VEVENT'),3)
        self.assertIn('DTSTART:20261001T',text)
        self.assertTrue(text.endswith('END:VCALENDAR\r\n'))
        self.assertIn('No device control',text)
    def test_export_requires_approval_not_proposal(self):
        p=self.store.propose({})
        with self.assertRaises(Invalid):self.store.export(p['id'])
    def test_export_detects_changed_saved_proposal(self):
        p=self.store.propose({});a=self.store.approve(p['id'],p['revision'])
        p['schedule'][0]['start']='18:00'
        p['explanation']='Changed after approval'
        with self.store.connect() as db:
            db.execute('UPDATE proposals SET body=? WHERE id=?',(json.dumps(p),p['id']))
        with self.assertRaises(Conflict):self.store.export(a['id'])
    def test_invalid_shapes_critical_jobs_times_and_bool(self):
        for patch in ({'jobs':{'fridge':{'skip':True}}},{'quiet_after':'21:15'},{'quiet_after':'25:00'},
                      {'jobs':{'dryer':{'watts':1}}},{'jobs':{'dryer':{'skip':1}}},{'approve':True}):
            with self.assertRaises(Invalid):self.store.propose(patch)
        self.assertEqual(self.store.household()['revision'],0)
    def test_24_hour_end_exports_next_day(self):
        p=self.store.propose({'jobs':{'ebike':{'earliest':'22:00','latest_finish':'24:00'}}})
        a=self.store.approve(p['id'],p['revision'])
        self.assertIn('DTEND:20261002T000000',self.store.export(a['id']))
    def test_independent_default_peak_oracle(self):
        baseline=[650,850,1100,1250,1100,850,650,550,450,400,350,350]
        best=10**9
        for starts in itertools.product(range(5),range(4),range(9)):
            load=baseline[:]
            for start,job in zip(starts,JOBS):
                for t in range(start,start+job['duration_minutes']//30):load[t]+=job['watts']
            best=min(best,max(load))
        self.assertEqual(calculate(DEFAULT)['metrics']['peak_watts'],best)

if __name__=='__main__':unittest.main()

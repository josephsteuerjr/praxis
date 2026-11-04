"""A partial or changed result never silently opens a dependent assignment."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import forge
import forge_swarm
from core import subagents


class SwarmTerminal(unittest.TestCase):
    def test_real_tick_retains_partial_and_waits_for_exact_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);project=base/'project';project.mkdir()
            with patch.object(forge,'BASE',base),patch.object(forge,'STATE_DIR',base/'memory/.forge'), \
                 patch.object(forge,'TASKS_DIR',base/'memory/.forge/tasks'):
                tid='code-11223344'
                forge._save_task({'id':tid,'goal':'g','status':'active','root':str(project),'max_active_workers':8})
                directory=forge._task_dir(tid)
                plan=forge_swarm.create(directory,[
                    {'id':'first','brief':'partial prerequisite'},
                    {'id':'dependent','brief':'must wait','deps':['first']},
                    {'id':'independent','brief':'unrelated work'}],max_parallel=1)
                unit=forge._unit_dir(tid,'agents','agent-aaaaaaaa')
                forge._atomic_json(unit/'request.json',{'id':unit.name,'task_id':tid,'root':str(project),
                    'role':'worker','status':'running','parent_unit_id':''})
                forge._atomic_json(unit/'result.json',{'status':'stalled','complete':False,'result':'partial','finished':'now'})
                forge.release_worker_root(unit/'request.json')
                plan['nodes'][0].update(status='running',agent_id=unit.name)
                forge_swarm.save(directory,plan)
                with patch.object(forge,'_spawn_runner',return_value=424242) as spawn:
                    out=forge.swarm(tid,'tick')
                    self.assertIn('stalled',out)
                    spawn.assert_called_once()
                    saved=forge_swarm.load(directory)
                    self.assertEqual(saved['nodes'][1]['status'],'pending')
                    self.assertIn('awaiting accepted dependencies',saved['nodes'][1]['queued_reason'])
                    independent=saved['nodes'][2]['agent_id']
                    self.assertTrue(independent)
                    forge._atomic_json(unit/'result.json',{'status':'done','result':'different bytes','finished':'later'})
                    forge.swarm(tid,'tick')
                    self.assertEqual(spawn.call_count,1)
                    saved=forge_swarm.load(directory)
                    self.assertEqual(saved['nodes'][0]['status'],'awaiting_acceptance')
                    self.assertEqual(saved['nodes'][0]['accepted_sha256'],'')
                    self.assertEqual(saved['nodes'][1]['status'],'pending')
                    self.assertEqual(saved['nodes'][2]['agent_id'],independent)

    def test_event_retains_abi_and_names_incompleteness(self):
        payload=subagents.normalize('task','agent',{'status':'stalled','complete':False,'result':'ещё не всё'})
        self.assertEqual(payload['status'],'failed')
        self.assertEqual(payload['forge_status'],'stalled')
        line=subagents.invitation([payload])
        self.assertIn('без полного результата',line)
        self.assertNotIn('упал',line)
        self.assertIn('ещё не всё',line)


if __name__=='__main__':unittest.main()

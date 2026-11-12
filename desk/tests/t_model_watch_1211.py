"""Retry one changed config after transient read/projection failure, no services."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'localharness'))
import runner
import brain_trail


class StopWatch(BaseException):
    pass


class Watch(unittest.TestCase):
    def test_same_revision_is_retried_after_read_or_projection_failure(self):
        for failure in ('read', 'project'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                tree=Path(tmp);config=tree/'helene.json'
                old={'model':{'provider':'chatgpt','framework':'openai','model':'gpt-6-astra'}}
                new={'model':{**old['model'],'model':'gpt-6.1-sol'}}
                config.write_text(json.dumps(old));runner.boot.project_brain(tree,old)
                stamp=config.stat().st_mtime_ns+1_000_000_000
                ticks=0
                def tick(_):
                    nonlocal ticks
                    ticks+=1
                    if ticks>2:raise StopWatch()
                    if ticks==1:
                        config.write_text(json.dumps(new))
                        os.utime(config,ns=(stamp,stamp))
                read=runner.boot.read_config_text;project=brain_trail.project
                attempts=0
                def operation(*args,**kw):
                    nonlocal attempts
                    attempts+=1
                    if attempts==1:raise OSError('temporary fixture failure')
                    return (read if failure=='read' else project)(*args,**kw)
                target,method=(runner.boot,'read_config_text') if failure=='read' else (brain_trail,'project')
                with patch.object(target,method,side_effect=operation), \
                     patch.object(runner.time,'sleep',side_effect=tick), \
                     patch.object(runner,'_bot',None), patch.object(runner,'_agent',None), \
                     patch.object(runner,'_deliver_unspoken',True), patch.object(runner,'_status_message',False):
                    code=Path(__file__).resolve().parents[2]/'helene/core'
                    with self.assertRaises(StopWatch):runner._config_watch_forever(config,tree,code)
                self.assertEqual(attempts,2)
                voice=json.loads((tree/'memory/llm.json').read_text())['roles']['voice']
                self.assertEqual(voice['model'],'gpt-6.1-sol')


if __name__=='__main__':unittest.main()

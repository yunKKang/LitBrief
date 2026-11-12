"""Cross-process crash recovery and project-relative evidence, without network calls."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from library import worker_lock,stored_path,artifact_path


class RuntimeReliabilityTests(unittest.TestCase):
    def test_lock_is_released_after_abrupt_process_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = "import os,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from library import worker_lock\nwith worker_lock(Path(sys.argv[2])):os._exit(0)"
            scripts=Path(__file__).resolve().parents[1]/'scripts'
            subprocess.run([sys.executable,'-c',code,str(scripts),tmp],check=True,timeout=15)
            self.assertTrue((Path(tmp)/'worker.lock').exists())
            with worker_lock(tmp):
                blocked="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from library import worker_lock\ntry:\n with worker_lock(Path(sys.argv[2])):sys.exit(9)\nexcept RuntimeError:sys.exit(0)"
                subprocess.run([sys.executable,'-c',blocked,str(scripts),tmp],check=True,timeout=15)
            with worker_lock(tmp):pass

    def test_evidence_survives_project_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=root/'old';new=root/'new'
            path=old/'outputs/ai-trial/response.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'status':'completed'}))
            stored=stored_path(path,old)
            self.assertEqual(stored,'outputs/ai-trial/response.json')
            shutil.copytree(old,new)
            self.assertEqual(artifact_path(stored,new).read_bytes(),path.read_bytes())
            with self.assertRaises(ValueError):artifact_path('../outside.json',new)


if __name__=='__main__':unittest.main()

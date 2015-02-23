from __future__ import print_function

import json
import os
import shutil
import unittest
import uuid

from webtest import TestApp

from turnip import api
from turnip.config import TurnipConfig


class ApiTestCase(unittest.TestCase):

    def setUp(self):
        self.config = TurnipConfig()
        repo_store = self.config.get('repo_store')
        self.repo_path = str(uuid.uuid1())
        self.repo_store = os.path.join(repo_store, self.repo_path)

    def test_repo_post(self):
        app = TestApp(api.main({}))
        resp = app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        assert resp.status_code == 200

        # cleanup repo path
        shutil.rmtree(self.repo_store)

    def test_repo_delete(self):
        app = TestApp(api.main({}))
        app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        resp = app.delete('/repo/{}'.format(self.repo_path))
        assert resp.status_code == 200
        assert not os.path.exists(self.repo_store)

if __name__ == '__main__':
    unittest.main()

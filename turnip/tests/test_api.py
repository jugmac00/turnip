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

    def remove_store(self):
        if os.path.exists(self.repo_store):
            shutil.rmtree(self.repo_store)

    def test_repo_post(self):
        self.addCleanup(self.remove_store)
        app = TestApp(api.main({}))
        resp = app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        self.assertEquals(resp.status_code, 200)

    def test_repo_delete(self):
        self.addCleanup(self.remove_store)
        app = TestApp(api.main({}))
        app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        resp = app.delete('/repo/{}'.format(self.repo_path))
        self.assertEquals(resp.status_code, 200)
        self.assertFalse(os.path.exists(self.repo_store))

if __name__ == '__main__':
    unittest.main()

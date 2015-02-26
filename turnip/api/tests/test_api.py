# Copyright 2015 Canonical Ltd.  All rights reserved.

from __future__ import print_function

import json
import os
import re
import unittest
import uuid

from fixtures import (
      EnvironmentVariable,
      TempDir,
      )
from testtools import TestCase
from webtest import TestApp

from turnip import api
import test_helpers

class ApiTestCase(TestCase):

    def setUp(self):
        super(ApiTestCase, self).setUp()
        repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", repo_store))
        self.app = TestApp(api.main({}))
        self.repo_path = str(uuid.uuid1())
        self.repo_store = os.path.join(repo_store, self.repo_path)

    def test_repo_create(self):
        resp = self.app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        self.assertEqual(resp.status_code, 200)

    def test_repo_delete(self):
        self.app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        resp = self.app.delete('/repo/{}'.format(self.repo_path))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.exists(self.repo_store))

    def test_repo_get_refs(self):
        """Ensure expected ref objects are returned and shas match."""
        repo = test_helpers.init_repo(self.repo_store, commits=True, tags=True)
        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        body = json.loads(resp.json_body)

        self.assertTrue(any(obj['ref'] == 'refs/heads/master' for obj in body))
        self.assertTrue(
            any(re.match('refs/tags.*', obj['ref']) for obj in body))

        oid = str(repo.head.get_object().oid)  # git object sha
        resp_sha = ([obj for obj in body if obj['ref'] ==
                     'refs/heads/master'][0]['object'].get('sha'))
        self.assertEqual(oid, resp_sha)


    # def test_repo_create_ref(self):
    #     raise NotImplementedError

    # def test_repo_update_ref(self):
    #     raise NotImplementedError

    # def test_repo_delete_ref(self):
    #     raise NotImplementedError

if __name__ == '__main__':
    unittest.main()

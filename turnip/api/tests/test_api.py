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

    def get_ref(self, ref):
        test_helpers.init_repo(self.repo_store, commits=True, tags=True)
        resp = self.app.get('/repo/{}/refs/{}'.format(
            self.repo_path, ref))
        return json.loads(resp.json_body)

    def test_repo_create(self):
        resp = self.app.post('/repo', json.dumps(
            {'repo_path': self.repo_path}))
        self.assertEqual(resp.status_code, 200)

    def test_repo_delete(self):
        self.app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        resp = self.app.delete('/repo/{}'.format(self.repo_path))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.exists(self.repo_store))

    def test_repo_get_refs(self):
        """Ensure expected ref objects are returned and shas match."""
        ref = 'refs/heads/master'
        repo = test_helpers.init_repo(self.repo_store, commits=True, tags=True)
        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        body = json.loads(resp.json_body)

        self.assertTrue(any(obj['ref'] == ref for obj in body))
        self.assertTrue(
            any(re.match('refs/tags.*', obj['ref']) for obj in body))

        oid = str(repo.head.get_object().oid)  # git object sha
        resp_sha = ([obj for obj in body if obj['ref'] ==
                     ref][0]['object'].get('sha'))
        self.assertEqual(oid, resp_sha)

    def test_repo_get_ref(self):
        ref = 'heads/master'
        resp = self.get_ref(ref)
        self.assertEqual(ref, resp['ref'])

    def test_repo_get_tag(self):
        tag = 'tags/test-tag'
        resp = self.get_ref(tag)
        self.assertEqual(tag, resp['ref'])


if __name__ == '__main__':
    unittest.main()

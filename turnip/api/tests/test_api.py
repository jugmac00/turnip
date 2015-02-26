# Copyright 2015 Canonical Ltd.  All rights reserved.

from __future__ import print_function

import json
import os
import unittest
import uuid

from fixtures import (
      EnvironmentVariable,
      TempDir,
      )
from testtools import TestCase
from webtest import TestApp

from turnip import api


class ApiTestCase(TestCase):

    def setUp(self):
        super(ApiTestCase, self).setUp()
        repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", repo_store))
        self.app = TestApp(api.main({}))
        self.repo_path = str(uuid.uuid1())
        self.repo_store = os.path.join(repo_store, self.repo_path)

    def test_repo_post(self):
        resp = self.app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        self.assertEqual(resp.status_code, 200)

    def test_repo_delete(self):
        self.app.post('/repo', json.dumps({'repo_path': self.repo_path}))
        resp = self.app.delete('/repo/{}'.format(self.repo_path))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.exists(self.repo_store))


if __name__ == '__main__':
    unittest.main()

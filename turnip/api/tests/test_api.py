# Copyright 2015 Canonical Ltd.  All rights reserved.
# -*- coding: utf-8 -*-
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
from turnip.api.tests.test_helpers import RepoFactory


class ApiTestCase(TestCase):

    def setUp(self):
        super(ApiTestCase, self).setUp()
        repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", repo_store))
        self.app = TestApp(api.main({}))
        self.repo_path = str(uuid.uuid1())
        self.repo_store = os.path.join(repo_store, self.repo_path)
        self.commit = {'ref': 'refs/heads/master', 'message': 'test commit.'}
        self.tag = {'ref': 'refs/tags/tag0', 'message': 'tag message'}

    def get_ref(self, ref):
        resp = self.app.get('/repo/{}/{}'.format(self.repo_path, ref))
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
        ref = self.commit.get('ref')
        repo = RepoFactory(self.repo_store, num_commits=1, num_tags=1).build()
        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        body = json.loads(resp.json_body)

        self.assertTrue(ref in body)
        self.assertTrue(self.tag.get('ref') in body)

        oid = repo.head.get_object().oid.hex  # git object sha
        resp_sha = body[ref]['object'].get('sha1')
        self.assertEqual(oid, resp_sha)

    def test_repo_get_refs_nonexistent(self):
        """get_refs on a non-existent repository returns HTTP 404."""
        resp = self.app.get('/repo/1/refs', expect_errors=True)
        self.assertEqual(resp.status_code, 404)

    def test_ignore_non_unicode_refs(self):
        """Ensure non-unicode refs are dropped from ref collection."""
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag = '\xe9\xe9\xe9'  # latin-1
        tag_message = 'tag message'
        factory.add_tag(tag, tag_message, commit_oid)

        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        refs = json.loads(resp.json_body)
        self.assertEqual(len(refs.keys()), 1)

    def test_allow_unicode_refs(self):
        """Ensure unicode refs are included in ref collection."""
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag = u'おいしいイカ'.encode('utf-8')
        tag_message = u'かわいい タコ'.encode('utf-8')
        factory.add_tag(tag, tag_message, commit_oid)

        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        refs = json.loads(resp.json_body)
        self.assertEqual(len(refs.keys()), 2)

    def test_repo_get_ref(self):
        RepoFactory(self.repo_store, num_commits=1).build()
        ref = 'refs/heads/master'
        resp = self.get_ref(ref)
        self.assertTrue(ref in resp)

    def test_repo_get_ref_nonexistent_repository(self):
        """get_ref on a non-existent repository returns HTTP 404."""
        resp = self.app.get('/repo/1/refs/heads/master', expect_errors=True)
        self.assertEqual(resp.status_code, 404)

    def test_repo_get_ref_nonexistent_ref(self):
        """get_ref on a non-existent ref in a repository returns HTTP 404."""
        RepoFactory(self.repo_store, num_commits=1).build()
        resp = self.app.get(
            '/repo/{}/refs/heads/master'.format(self.repo_path))
        self.assertEqual(resp.status_code, 200)
        resp = self.app.get(
            '/repo/{}/refs/heads/nonexistent'.format(self.repo_path),
            expect_errors=True)
        self.assertEqual(resp.status_code, 404)

    def test_repo_get_unicode_ref(self):
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag_name = u'☃'.encode('utf-8')
        tag_message = u'☃'.encode('utf-8')
        factory.add_tag(tag_name, tag_message, commit_oid)

        tag = 'refs/tags/{}'.format(tag_name)
        resp = self.get_ref(tag)
        self.assertTrue(tag.decode('utf-8') in resp)

    def test_repo_get_tag(self):
        RepoFactory(self.repo_store, num_commits=1, num_tags=1).build()
        tag = self.tag.get('ref')
        resp = self.get_ref(tag)
        self.assertTrue(tag in resp)


if __name__ == '__main__':
    unittest.main()

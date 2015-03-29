# Copyright 2015 Canonical Ltd.  All rights reserved.
# -*- coding: utf-8 -*-
from __future__ import print_function

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
from turnip.api.tests.test_helpers import (
    get_revlist,
    open_repo,
    RepoFactory,
    )


class ApiTestCase(TestCase):

    def setUp(self):
        super(ApiTestCase, self).setUp()
        repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", repo_store))
        self.app = TestApp(api.main({}))
        self.repo_path = uuid.uuid1().hex
        self.repo_store = os.path.join(repo_store, self.repo_path)
        self.repo_root = repo_store
        self.commit = {'ref': 'refs/heads/master', 'message': 'test commit.'}
        self.tag = {'ref': 'refs/tags/tag0', 'message': 'tag message'}

    def get_ref(self, ref):
        resp = self.app.get('/repo/{}/{}'.format(self.repo_path, ref))
        return resp.json

    def test_repo_init(self):
        resp = self.app.post_json('/repo', {'repo_path': self.repo_path})
        self.assertIn(self.repo_path, resp.json['repo_url'])
        self.assertEqual(resp.status_code, 200)

    def test_repo_init_with_invalid_repo_path(self):
        resp = self.app.post_json('/repo', {'repo_path': '../1234'},
                                  expect_errors=True)
        self.assertEqual(resp.status_code, 404)

    def test_repo_init_with_clone(self):
        """Repo can be initialised with optional clone."""
        factory = RepoFactory(self.repo_store, num_commits=2)
        factory.build()
        new_repo_path = uuid.uuid1().hex
        resp = self.app.post_json('/repo', {'repo_path': new_repo_path,
                                            'clone_from': self.repo_path})
        repo1_revlist = get_revlist(factory.repo)
        clone_from = resp.json['repo_url'].split('/')[-1]
        repo2 = open_repo(os.path.join(self.repo_root, clone_from))
        repo2_revlist = get_revlist(repo2)

        self.assertEqual(repo1_revlist, repo2_revlist)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(new_repo_path, resp.json['repo_url'])

    def test_repo_delete(self):
        self.app.post_json('/repo', {'repo_path': self.repo_path})
        resp = self.app.delete('/repo/{}'.format(self.repo_path))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(os.path.exists(self.repo_store))

    def test_repo_get_refs(self):
        """Ensure expected ref objects are returned and shas match."""
        ref = self.commit.get('ref')
        repo = RepoFactory(self.repo_store, num_commits=1, num_tags=1).build()
        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        body = resp.json

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
        refs = resp.json
        self.assertEqual(len(refs.keys()), 1)

    def test_allow_unicode_refs(self):
        """Ensure unicode refs are included in ref collection."""
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag = u'おいしいイカ'.encode('utf-8')
        tag_message = u'かわいい タコ'.encode('utf-8')
        factory.add_tag(tag, tag_message, commit_oid)

        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        refs = resp.json
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

    def test_repo_get_commit(self):
        factory = RepoFactory(self.repo_store)
        message = 'Computers make me angry.'
        commit_oid = factory.add_commit(message, 'foobar.txt')

        resp = self.app.get('/repo/{}/commits/{}'.format(
            self.repo_path, commit_oid.hex))
        commit_resp = resp.json
        self.assertEqual(commit_oid.hex, commit_resp['sha1'])
        self.assertEqual(message, commit_resp['message'])

    def test_repo_get_commit_collection(self):
        """Ensure commits can be returned in bulk."""
        factory = RepoFactory(self.repo_store, num_commits=10)
        factory.build()
        bulk_commits = {'commits': [c.hex for c in factory.commits[0::2]]}

        resp = self.app.post_json('/repo/{}/commits'.format(
            self.repo_path), bulk_commits)
        self.assertEqual(len(resp.json), 5)
        self.assertEqual(bulk_commits['commits'][0], resp.json[0]['sha1'])

    def test_repo_get_log_signatures(self):
        """Ensure signatures are correct."""
        factory = RepoFactory(self.repo_store)
        committer = factory.makeSignature(u'村上 春樹'.encode('utf-8'),
                                          u'tsukuru@猫の町.co.jp'.encode('utf-8'),
                                          encoding='utf-8')
        author = factory.makeSignature(
            u'Владимир Владимирович Набоков'.encode('utf-8'),
            u'Набоко@zembla.ru'.encode('utf-8'), encoding='utf-8')
        oid = factory.add_commit('Obfuscate colophon.', 'path.foo',
                                 author=author, committer=committer)
        resp = self.app.get('/repo/{}/log/{}'.format(self.repo_path, oid))
        self.assertEqual(resp.json[0]['author']['name'], author.name)

    def test_repo_get_log(self):
        factory = RepoFactory(self.repo_store, num_commits=4)
        factory.build()
        commits_from = factory.commits[2].hex
        resp = self.app.get('/repo/{}/log/{}'.format(
            self.repo_path, commits_from))
        self.assertEqual(len(resp.json), 3)

    def test_repo_get_unicode_log(self):
        factory = RepoFactory(self.repo_store)
        message = u'나는 김치 사랑'.encode('utf-8')
        message2 = u'(╯°□°)╯︵ ┻━┻'.encode('utf-8')
        oid = factory.add_commit(message, '자장면/짜장면.py')
        oid2 = factory.add_commit(message2, '엄마야!.js', [oid])

        resp = self.app.get('/repo/{}/log/{}'.format(self.repo_path, oid2))
        self.assertEqual(resp.json[0]['message'],
                         message2.decode('utf-8', 'replace'))
        self.assertEqual(resp.json[1]['message'],
                         message.decode('utf-8', 'replace'))

    def test_repo_get_non_unicode_log(self):
        """Ensure that non-unicode data is discarded."""
        factory = RepoFactory(self.repo_store)
        message = '\xe9\xe9\xe9'  # latin-1
        oid = factory.add_commit(message, 'foo.py')
        resp = self.app.get('/repo/{}/log/{}'.format(self.repo_path, oid))
        self.assertEqual(resp.json[0]['message'],
                         message.decode('utf-8', 'replace'))

    def test_repo_get_log_with_limit(self):
        """Ensure the commit log can filtered by limit."""
        factory = RepoFactory(self.repo_store, num_commits=10)
        repo = factory.build()
        head = repo.head.target
        resp = self.app.get('/repo/{}/log/{}?limit=5'.format(
            self.repo_path, head))
        self.assertEqual(len(resp.json), 5)

    def test_repo_get_log_with_stop(self):
        """Ensure the commit log can be filtered by a stop commit."""
        factory = RepoFactory(self.repo_store, num_commits=10)
        repo = factory.build()
        stop_commit = factory.commits[4]
        excluded_commit = factory.commits[5]
        head = repo.head.target
        resp = self.app.get('/repo/{}/log/{}?stop={}'.format(
            self.repo_path, head, stop_commit))
        self.assertEqual(len(resp.json), 5)
        self.assertNotIn(resp.json, excluded_commit)


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-

# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    print_function,
    unicode_literals,
    )

import base64
from datetime import (
    datetime,
    timedelta,
    )
import os
import re
import subprocess
from textwrap import dedent
import time
import unittest
from unittest import mock
from urllib.parse import quote
import uuid

from fixtures import (
    EnvironmentVariable,
    TempDir,
    )
import six
from testtools import TestCase
from testtools.matchers import (
    Equals,
    MatchesSetwise,
    )
from twisted.internet import reactor as default_reactor
from twisted.web import server
from webtest import TestApp

from turnip import api
from turnip.api.tests.test_helpers import (
    get_revlist,
    open_repo,
    RepoFactory,
    )
from turnip.config import config
from turnip.pack.tests.fake_servers import FakeVirtInfoService
from turnip.tests.tasks import CeleryWorkerFixture


class ApiRepoStoreMixin:
    def setupRepoStore(self):
        repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", repo_store))
        self.app = TestApp(api.main({}))
        self.repo_path = uuid.uuid1().hex
        self.repo_store = os.path.join(repo_store, self.repo_path)
        self.repo_root = repo_store
        self.commit = {'ref': 'refs/heads/master', 'message': 'test commit.'}
        self.tag = {'ref': 'refs/tags/tag0', 'message': 'tag message'}


class ApiTestCase(TestCase, ApiRepoStoreMixin):

    def setUp(self):
        super(ApiTestCase, self).setUp()
        self.setupRepoStore()

    def assertReferencesEqual(self, repo, expected, observed):
        self.assertEqual(
            repo.references[expected].peel().oid,
            repo.references[observed].peel().oid)

    def get_ref(self, ref):
        repo_path = six.ensure_text(self.repo_path)
        ref = six.ensure_text(ref)
        url = '/repo/{}/{}'.format(repo_path, ref)
        resp = self.app.get(quote(url))
        return resp.json

    def test_repo_init(self):
        resp = self.app.post_json('/repo', {'repo_path': self.repo_path})
        self.assertIn(self.repo_path, resp.json['repo_url'])
        self.assertEqual(200, resp.status_code)

    def test_repo_init_with_invalid_repo_path(self):
        resp = self.app.post_json('/repo', {'repo_path': '../1234'},
                                  expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_init_with_existing_repo(self):
        """Repo can be not be initialised with existing path."""
        factory = RepoFactory(self.repo_store)
        repo_path = os.path.basename(os.path.normpath(factory.repo_path))
        resp = self.app.post_json('/repo', {'repo_path': repo_path},
                                  expect_errors=True)
        self.assertEqual(409, resp.status_code)

    def test_repo_init_with_clone(self):
        """Repo can be initialised with optional clone."""
        factory = RepoFactory(self.repo_store, num_commits=2)
        factory.build()
        new_repo_path = uuid.uuid1().hex
        resp = self.app.post_json('/repo', {'repo_path': new_repo_path,
                                            'clone_from': self.repo_path,
                                            'clone_refs': True})
        repo1_revlist = get_revlist(factory.repo)
        clone_from = resp.json['repo_url'].split('/')[-1]
        repo2 = open_repo(os.path.join(self.repo_root, clone_from))
        repo2_revlist = get_revlist(repo2)

        self.assertEqual(repo1_revlist, repo2_revlist)
        self.assertEqual(200, resp.status_code)
        self.assertIn(new_repo_path, resp.json['repo_url'])

    def test_repo_get(self):
        """The GET method on a repository returns its properties."""
        factory = RepoFactory(self.repo_store, num_branches=2, num_commits=1)
        factory.build()
        factory.repo.set_head('refs/heads/branch-0')

        resp = self.app.get('/repo/{}'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        self.assertEqual({
            'default_branch': 'refs/heads/branch-0',
            'is_available': True}, resp.json)

    def test_repo_get_default_branch_missing(self):
        """default_branch is returned even if that branch has been deleted."""
        factory = RepoFactory(self.repo_store, num_branches=2, num_commits=1)
        factory.build()
        factory.repo.set_head('refs/heads/branch-0')
        factory.repo.references.delete('refs/heads/branch-0')

        resp = self.app.get('/repo/{}'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        self.assertEqual({
            'default_branch': 'refs/heads/branch-0',
            'is_available': True}, resp.json)

    def test_repo_patch_default_branch(self):
        """A repository's default branch ("HEAD") can be changed."""
        factory = RepoFactory(self.repo_store, num_branches=2, num_commits=1)
        factory.build()
        factory.repo.set_head('refs/heads/branch-0')
        self.assertReferencesEqual(factory.repo, 'refs/heads/branch-0', 'HEAD')

        resp = self.app.patch_json(
            '/repo/{}'.format(self.repo_path),
            {'default_branch': 'refs/heads/branch-1'})
        self.assertEqual(204, resp.status_code)
        self.assertReferencesEqual(factory.repo, 'refs/heads/branch-1', 'HEAD')

    def test_cross_repo_merge_diff(self):
        """Merge diff can be requested across 2 repositories."""
        factory = RepoFactory(self.repo_store)
        c1 = factory.add_commit('foo', 'foobar.txt')

        repo2_name = uuid.uuid4().hex
        factory2 = RepoFactory(
            os.path.join(self.repo_root, repo2_name), clone_from=factory)
        c2 = factory.add_commit('bar', 'foobar.txt', parents=[c1])
        c3 = factory2.add_commit('baz', 'foobar.txt', parents=[c1])

        resp = self.app.get('/repo/{}:{}/compare-merge/{}:{}'.format(
            self.repo_path, repo2_name, c2, c3))
        self.assertIn('-bar', resp.json['patch'])

    def test_cross_repo_diff(self):
        """Diff can be requested across 2 repositories."""
        factory = RepoFactory(self.repo_store)
        c1 = factory.add_commit('foo', 'foobar.txt')

        repo2_name = uuid.uuid4().hex
        factory2 = RepoFactory(
            os.path.join(self.repo_root, repo2_name), clone_from=factory)
        c2 = factory.add_commit('bar', 'foobar.txt', parents=[c1])
        c3 = factory2.add_commit('baz', 'foobar.txt', parents=[c1])

        resp = self.app.get('/repo/{}:{}/compare/{}..{}'.format(
            self.repo_path, repo2_name, c2, c3))
        self.assertIn('-bar', resp.json['patch'])
        self.assertIn('+baz', resp.json['patch'])

    def test_cross_repo_diff_invalid_repo(self):
        """Cross repo diff with invalid repo returns HTTP 404."""
        resp = self.app.get('/repo/1:2/compare-merge/3:4', expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_cross_repo_diff_invalid_commit(self):
        """Cross repo diff with an invalid commit returns HTTP 404."""
        factory = RepoFactory(self.repo_store)
        c1 = factory.add_commit('foo', 'foobar.txt')

        repo2_name = uuid.uuid4().hex
        RepoFactory(
            os.path.join(self.repo_root, repo2_name), clone_from=factory)
        c2 = factory.add_commit('bar', 'foobar.txt', parents=[c1])

        resp = self.app.get('/repo/{}:{}/diff/{}:{}'.format(
            self.repo_path, repo2_name, c2, 'invalid'), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_delete(self):
        self.app.post_json('/repo', {'repo_path': self.repo_path})
        resp = self.app.delete('/repo/{}'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        self.assertFalse(os.path.exists(self.repo_store))

    def test_repo_delete_non_utf8_file_names(self):
        """Deleting a repo works even if it contains non-UTF-8 file names."""
        self.app.post_json('/repo', {'repo_path': self.repo_path})
        factory = RepoFactory(self.repo_store)
        factory.add_commit('foo', 'foobar.txt')
        subprocess.check_call(
            ['git', '-C', self.repo_store, 'branch', b'\x80'])
        resp = self.app.delete('/repo/{}'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        self.assertFalse(os.path.exists(self.repo_store))

    def test_repo_get_refs(self):
        """Ensure expected ref objects are returned and shas match."""
        ref = self.commit.get('ref')
        repo = RepoFactory(self.repo_store, num_commits=1, num_tags=1).build()
        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        body = resp.json

        self.assertTrue(ref in body)
        self.assertTrue(self.tag.get('ref') in body)

        oid = repo.head.peel().oid.hex  # git object sha
        resp_sha = body[ref]['object'].get('sha1')
        self.assertEqual(oid, resp_sha)

    def test_repo_get_refs_nonexistent(self):
        """get_refs on a non-existent repository returns HTTP 404."""
        resp = self.app.get('/repo/1/refs', expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_ignore_non_unicode_refs(self):
        """Ensure non-unicode refs are dropped from ref collection."""
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag = b'\xe9\xe9\xe9'  # latin-1
        tag_message = 'tag message'
        factory.add_tag(tag, tag_message, commit_oid)

        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        refs = resp.json
        self.assertEqual(1, len(refs.keys()))

    def test_allow_unicode_refs(self):
        """Ensure unicode refs are included in ref collection."""
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag = 'おいしいイカ'.encode('utf-8')
        tag_message = 'かわいい タコ'.encode('utf-8')
        factory.add_tag(tag, tag_message, commit_oid)

        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        refs = resp.json
        self.assertEqual(2, len(refs.keys()))

    def test_repo_get_refs_exclude_prefixes(self):
        """Refs matching an excluded prefix are not returned."""
        factory = RepoFactory(self.repo_store, num_commits=1)
        factory.build()
        for ref_path in (
                'refs/heads/refs/changes/1',
                'refs/changes/2',
                'refs/pull/3/head',
                ):
            factory.repo.references.create(ref_path, factory.commits[0])

        resp = self.app.get(
            '/repo/{}/refs'
            '?exclude_prefix=refs/changes/'
            '&exclude_prefix=refs/pull/'.format(self.repo_path))
        refs = resp.json
        self.assertThat(list(refs), MatchesSetwise(
            Equals('refs/heads/master'), Equals('refs/heads/refs/changes/1')))

    def test_repo_get_ref(self):
        RepoFactory(self.repo_store, num_commits=1).build()
        ref = 'refs/heads/master'
        resp = self.get_ref(ref)
        self.assertTrue(ref in resp)

    def test_repo_get_ref_nonexistent_repository(self):
        """get_ref on a non-existent repository returns HTTP 404."""
        resp = self.app.get('/repo/1/refs/heads/master', expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_get_ref_nonexistent_ref(self):
        """get_ref on a non-existent ref in a repository returns HTTP 404."""
        RepoFactory(self.repo_store, num_commits=1).build()
        resp = self.app.get(
            '/repo/{}/refs/heads/master'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        resp = self.app.get(
            '/repo/{}/refs/heads/nonexistent'.format(self.repo_path),
            expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_get_unicode_ref(self):
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag_name = '☃'
        tag_message = '☃'
        factory.add_tag(
            six.ensure_binary(tag_name), six.ensure_binary(tag_message),
            commit_oid)

        tag = 'refs/tags/{}'.format(tag_name)
        resp = self.get_ref(tag)
        self.assertIn(tag, resp)

    def test_repo_get_tag(self):
        RepoFactory(self.repo_store, num_commits=1, num_tags=1).build()
        tag = self.tag.get('ref')
        resp = self.get_ref(tag)
        self.assertTrue(tag in resp)

    def test_delete_ref(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)

        repo = RepoFactory(
            self.repo_store, num_branches=5, num_commits=1, num_tags=1).build()
        self.assertEqual(7, len(repo.references.objects))

        ref = 'refs/heads/branch-0'
        url = '/repo/{}/{}'.format(self.repo_path, ref)
        resp = self.app.delete(quote(url))

        self.assertEqual(6, len(repo.references.objects))
        self.assertEqual(200, resp.status_code)
        self.assertEqual(b'', resp.body)

    def test_delete_non_existing_ref(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)

        repo = RepoFactory(
            self.repo_store, num_branches=5, num_commits=1, num_tags=1).build()
        self.assertEqual(7, len(repo.references.objects))

        ref = 'refs/heads/fake-branch'
        url = '/repo/{}/{}'.format(self.repo_path, ref)
        resp = self.app.delete(quote(url), expect_errors=True)
        self.assertEqual(404, resp.status_code)
        self.assertEqual({
            'status': 'error',
            'errors': [{
                'description': 'Ref refs/heads/fake-branch does not exist.',
                'location': 'body',
                'name': u'operations'
            }]}, resp.json)

    def test_copy_ref_api(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)
        repo1_path = os.path.join(self.repo_root, 'repo1')
        repo2_path = os.path.join(self.repo_root, 'repo2')
        repo3_path = os.path.join(self.repo_root, 'repo3')

        repo1_factory = RepoFactory(
            repo1_path, num_branches=5, num_commits=1, num_tags=1)
        repo1 = repo1_factory.build()
        self.assertEqual(7, len(repo1.references.objects))

        repo2_factory = RepoFactory(
            repo2_path, num_branches=1, num_commits=1, num_tags=1)
        repo2 = repo2_factory.build()
        self.assertEqual(3, len(repo2.references.objects))

        repo3_factory = RepoFactory(
            repo3_path, num_branches=1, num_commits=1, num_tags=1)
        repo3 = repo3_factory.build()
        self.assertEqual(3, len(repo3.references.objects))

        url = '/repo/repo1/refs-copy'
        body = {
            "operations": [
                {
                    "from": "refs/heads/branch-4",
                    "to": {"repo": 'repo2', "ref": "refs/merge/123/head"}
                }, {
                    "from": "refs/heads/branch-4",
                    "to": {"repo": 'repo3', "ref": "refs/merge/987/head"}
                }]}
        resp = self.app.post_json(quote(url), body)
        self.assertEqual(202, resp.status_code)

        def branchCreated():
            repo2_refs = [i.name for i in repo2.references.objects]
            repo3_refs = [i.name for i in repo3.references.objects]
            return ('refs/merge/123/head' in repo2_refs and
                    'refs/merge/987/head' in repo3_refs)

        celery_fixture.waitUntil(5, branchCreated)
        self.assertEqual(4, len(repo2.references.objects))
        self.assertEqual(202, resp.status_code)
        self.assertEqual(b'', resp.body)

    def test_copy_non_existing_ref(self):
        celery_fixture = CeleryWorkerFixture()
        self.useFixture(celery_fixture)

        repo_path = os.path.join(self.repo_root, 'repo1')
        repo = RepoFactory(
            repo_path, num_branches=5, num_commits=1, num_tags=1).build()
        self.assertEqual(7, len(repo.references.objects))

        body = {
            "operations": [{
                "from": "refs/heads/nope",
                "to": {"repo": 'repo2', "ref": "refs/merge/123/head"}
            }, {
                "from": "refs/heads/no-ref",
                "to": {"repo": 'repo2', "ref": "refs/merge/123/head"}
            }]}

        url = '/repo/repo1/refs-copy'
        resp = self.app.post_json(quote(url), body, expect_errors=True)
        self.assertEqual(404, resp.status_code)
        self.assertEqual({
            'status': 'error',
            'errors': [{
                'description': 'Ref refs/heads/nope does not exist.',
                'location': 'body',
                'name': u'operations'
            }, {
                'description': 'Ref refs/heads/no-ref does not exist.',
                'location': 'body', 'name': u'operations'
            }]}, resp.json)

    def test_repo_compare_commits(self):
        """Ensure expected changes exist in diff patch."""
        repo = RepoFactory(self.repo_store)
        c1_oid = repo.add_commit('foo', 'foobar.txt')
        c2_oid = repo.add_commit('bar', 'foobar.txt', parents=[c1_oid])

        path = '/repo/{}/compare/{}..{}'.format(self.repo_path, c1_oid, c2_oid)
        resp = self.app.get(path)
        self.assertIn(b'-foo', resp.body)
        self.assertIn(b'+bar', resp.body)

    def test_repo_diff_commits(self):
        """Ensure expected commits objects are returned in diff."""
        repo = RepoFactory(self.repo_store)
        c1_oid = repo.add_commit('foo', 'foobar.txt')
        c2_oid = repo.add_commit('bar', 'foobar.txt', parents=[c1_oid])

        path = '/repo/{}/compare/{}..{}'.format(self.repo_path, c1_oid, c2_oid)
        resp = self.app.get(path)
        self.assertIn(c1_oid.hex, resp.json['commits'][0]['sha1'])
        self.assertIn(c2_oid.hex, resp.json['commits'][1]['sha1'])

    def test_repo_diff_unicode_commits(self):
        """Ensure expected utf-8 commits objects are returned in diff."""
        factory = RepoFactory(self.repo_store)
        message = '屋漏偏逢连夜雨'.encode('utf-8')
        message2 = '说曹操，曹操到'.encode('utf-8')
        oid = factory.add_commit(message, 'foo.py')
        oid2 = factory.add_commit(message2, 'bar.py', [oid])

        resp = self.app.get('/repo/{}/compare/{}..{}'.format(
            self.repo_path, oid, oid2))
        self.assertEqual(resp.json['commits'][0]['message'],
                         message.decode('utf-8'))
        self.assertEqual(resp.json['commits'][1]['message'],
                         message2.decode('utf-8'))

    def test_repo_diff_non_unicode_commits(self):
        """Ensure non utf-8 chars are handled but stripped from diff."""
        factory = RepoFactory(self.repo_store)
        message = b'not particularly sensible latin-1: \xe9\xe9\xe9.'
        oid = factory.add_commit(message, 'foo.py')
        oid2 = factory.add_commit('a sensible commit message', 'foo.py', [oid])

        resp = self.app.get('/repo/{}/compare/{}..{}'.format(
            self.repo_path, oid, oid2))
        self.assertEqual(resp.json['commits'][0]['message'],
                         message.decode('utf-8', 'replace'))

    def test_repo_get_diff_nonexistent_sha1(self):
        """get_diff on a non-existent sha1 returns HTTP 404."""
        RepoFactory(self.repo_store).build()
        resp = self.app.get('/repo/{}/compare/1..2'.format(
            self.repo_path), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_get_diff_invalid_separator(self):
        """get_diff with an invalid separator (not ../...) returns HTTP 404."""
        RepoFactory(self.repo_store).build()
        resp = self.app.get('/repo/{}/compare/1++2'.format(
            self.repo_path), expect_errors=True)
        self.assertEqual(400, resp.status_code)

    def test_repo_common_ancestor_diff(self):
        """Ensure expected changes exist in diff patch."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo', 'foobar.txt')
        c2_right = repo.add_commit('bar', 'foobar.txt', parents=[c1])
        c3_right = repo.add_commit('baz', 'foobar.txt', parents=[c2_right])
        c2_left = repo.add_commit('qux', 'foobar.txt', parents=[c1])
        c3_left = repo.add_commit('corge', 'foobar.txt', parents=[c2_left])

        resp = self.app.get('/repo/{}/compare/{}...{}'.format(
            self.repo_path, c3_left, c3_right))
        self.assertIn('-foo', resp.json_body['patch'])
        self.assertIn('+baz', resp.json_body['patch'])
        self.assertNotIn('+corge', resp.json_body['patch'])

    def test_repo_diff_empty(self):
        """Ensure that diffing two identical commits returns an empty string
        as the patch, not None."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'blah.txt')

        resp = self.app.get('/repo/{}/compare/{}..{}'.format(
            self.repo_path, c1, c1))
        self.assertEqual('', resp.json_body['patch'])

    def test_repo_get_diff_extended_revision(self):
        """get_diff can take general git revisions, not just sha1s."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'foobar.txt')
        c2 = repo.add_commit('bar\n', 'foobar.txt', parents=[c1])

        path = '/repo/{}/compare/{}..{}'.format(
            self.repo_path, quote('{}^'.format(c2)), c2)
        resp = self.app.get(path)
        self.assertIn(b'-foo', resp.body)
        self.assertIn(b'+bar', resp.body)

    def test_repo_diff_detects_renames(self):
        """get_diff finds renamed files."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'foo.txt')
        repo.repo.index.remove('foo.txt')
        c2 = repo.add_commit('foo\n', 'bar.txt', parents=[c1])

        path = '/repo/{}/compare/{}..{}'.format(
            self.repo_path, quote('{}^'.format(c2)), c2)
        resp = self.app.get(path)
        self.assertIn(
            'diff --git a/foo.txt b/bar.txt\n', resp.json_body['patch'])

    def test_repo_diff_rename_and_change_content_conflict(self):
        # Create repo1 with foo.txt.
        repo1 = RepoFactory(self.repo_store)
        c1 = repo1.add_commit('foo\n', 'foo.txt')
        repo1.set_head(c1)

        # Fork and change the content of foo.txt in repo2.
        repo2_name = uuid.uuid4().hex
        repo2 = RepoFactory(
            os.path.join(self.repo_root, repo2_name), clone_from=repo1)

        # Rename foo.txt to bar.txt in repo2
        repo2.repo.index.remove('foo.txt')
        c2 = repo2.add_commit('foo\n', 'bar.txt', parents=[c1])

        # Do the same renaming on the original repo, with a change.
        repo1.repo.index.remove('foo.txt')
        c3 = repo2.add_commit('foo something\n', 'bar.txt', parents=[c1])

        resp = self.app.get('/repo/{}:{}/compare-merge/{}:{}'.format(
            self.repo_path, repo2_name, c2, c3))

        self.assertEqual(['bar.txt', 'foo.txt'], resp.json['conflicts'])
        self.assertEqual(dedent("""\
            diff --git a/bar.txt b/bar.txt
            index 257cc56..0290da8 100644
            --- a/bar.txt
            +++ b/bar.txt
            @@ -1 +1,5 @@
            +<<<<<<< bar.txt
             foo
            +=======
            +foo something
            +>>>>>>> bar.txt
            """), resp.json['patch'])

    def test_repo_diff_merge(self):
        """Ensure expected changes exist in diff patch."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\nbar\nbaz\n', 'blah.txt')
        c2_right = repo.add_commit('quux\nbar\nbaz\n', 'blah.txt',
                                   parents=[c1])
        c3_right = repo.add_commit('quux\nbar\nbaz\n', 'blah.txt',
                                   parents=[c2_right])
        c2_left = repo.add_commit('foo\nbar\nbar\n', 'blah.txt', parents=[c1])
        c3_left = repo.add_commit('foo\nbar\nbar\n', 'blah.txt',
                                  parents=[c2_left])

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c3_right, c3_left))
        self.assertIn(' quux', resp.json_body['patch'])
        self.assertIn('-baz', resp.json_body['patch'])
        self.assertIn('+bar', resp.json_body['patch'])
        self.assertNotIn('foo', resp.json_body['patch'])
        self.assertEqual([], resp.json_body['conflicts'])

    def test_repo_diff_merge_with_conflicts(self):
        """Ensure that compare-merge returns conflicts information."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'blah.txt')
        c2_left = repo.add_commit(
            'foo\nbar\u2603\n'.encode('utf-8'), 'blah.txt', parents=[c1])
        c2_right = repo.add_commit(
            'foo\nbaz\u263c\n'.encode('utf-8'), 'blah.txt', parents=[c1])

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c2_left, c2_right))
        self.assertIn(dedent("""\
            +<<<<<<< blah.txt
             bar\u2603
            +=======
            +baz\u263c
            +>>>>>>> blah.txt
            """), resp.json_body['patch'])
        self.assertEqual(['blah.txt'], resp.json_body['conflicts'])

    def test_repo_diff_merge_with_modify_delete_conflict(self):
        """compare-merge handles modify/delete conflicts."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'foo.txt')
        c2_left = repo.add_commit('foo\nbar\n', 'foo.txt', parents=[c1])
        repo.repo.index.remove('foo.txt')
        c2_right = repo.add_commit('', 'bar.txt', parents=[c1])

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c2_left, c2_right))
        self.assertIn(dedent("""\
            --- a/foo.txt
            +++ b/foo.txt
            @@ -1,2 +1,5 @@
            +<<<<<<< foo.txt
             foo
             bar
            +=======
            +>>>>>>> foo.txt
            """), resp.json_body['patch'])
        self.assertEqual(['foo.txt'], resp.json_body['conflicts'])

    def test_repo_diff_merge_with_delete_modify_conflict(self):
        """compare-merge handles delete/modify conflicts."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'foo.txt')
        repo.repo.index.remove('foo.txt')
        c2_left = repo.add_commit('', 'bar.txt', parents=[c1])
        repo.repo.index.remove('bar.txt')
        c2_right = repo.add_commit('foo\nbar\n', 'foo.txt', parents=[c1])

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c2_left, c2_right))
        self.assertIn(dedent("""\
            --- /dev/null
            +++ b/foo.txt
            @@ -0,0 +1,5 @@
            +<<<<<<< foo.txt
            +=======
            +foo
            +bar
            +>>>>>>> foo.txt
            """), resp.json_body['patch'])
        self.assertEqual(['foo.txt'], resp.json_body['conflicts'])

    def test_repo_diff_merge_with_prerequisite(self):
        """Ensure that compare-merge handles prerequisites."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'blah.txt')
        c2 = repo.add_commit('foo\nbar\n', 'blah.txt', parents=[c1])
        c3 = repo.add_commit('foo\nbar\nbaz\n', 'blah.txt', parents=[c2])

        resp = self.app.get(
            '/repo/{}/compare-merge/{}:{}?sha1_prerequisite={}'.format(
                self.repo_path, c1, c3, c2))
        self.assertIn(dedent("""\
            @@ -1,2 +1,3 @@
             foo
             bar
            +baz
            """), resp.json_body['patch'])
        self.assertEqual([], resp.json_body['conflicts'])

    def test_repo_diff_merge_empty(self):
        """Ensure that diffing two identical commits returns an empty string
        as the patch, not None."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'blah.txt')

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c1, c1))
        self.assertEqual('', resp.json_body['patch'])

    def test_repo_diff_merge_nonexistent(self):
        """Diffing against a non-existent base OID returns HTTP 404."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'blah.txt')

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, repo.nonexistent_oid(), c1), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_diff_merge_detects_renames(self):
        """get_merge_diff finds renamed files."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'foo.txt')
        repo.repo.index.remove('foo.txt')
        c2 = repo.add_commit('foo\n', 'bar.txt', parents=[c1])

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c1, c2))
        self.assertIn(
            'diff --git a/foo.txt b/bar.txt\n', resp.json_body['patch'])

    def test_repo_get_commit(self):
        factory = RepoFactory(self.repo_store)
        message = 'Computers make me angry.'
        commit_oid = factory.add_commit(message, 'foobar.txt')

        resp = self.app.get('/repo/{}/commits/{}'.format(
            self.repo_path, commit_oid.hex))
        commit_resp = resp.json
        self.assertEqual(commit_oid.hex, commit_resp['sha1'])
        self.assertEqual(message, commit_resp['message'])

    def test_repo_get_commit_nonexistent(self):
        """Trying to get a non-existent OID returns HTTP 404."""
        factory = RepoFactory(self.repo_store)
        resp = self.app.get('/repo/{}/commits/{}'.format(
            self.repo_path, factory.nonexistent_oid()), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_get_non_commit(self):
        """Trying to get a non-commit returns HTTP 404."""
        factory = RepoFactory(self.repo_store, num_commits=1)
        factory.build()
        tree_oid = factory.repo[factory.commits[0]].tree.hex
        resp = self.app.get('/repo/{}/commits/{}'.format(
            self.repo_path, tree_oid), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_get_commit_collection(self):
        """Ensure commits can be returned in bulk."""
        factory = RepoFactory(self.repo_store, num_commits=10)
        factory.build()
        bulk_commits = {'commits': [c.hex for c in factory.commits[0::2]]}

        resp = self.app.post_json('/repo/{}/commits'.format(
            self.repo_path), bulk_commits)
        self.assertEqual(5, len(resp.json))
        self.assertEqual(bulk_commits['commits'][0], resp.json[0]['sha1'])

    def test_repo_get_commit_collection_ignores_errors(self):
        """Non-existent OIDs and non-commits in a collection are ignored."""
        factory = RepoFactory(self.repo_store, num_commits=10)
        factory.build()
        bulk_commits = {
            'commits': [
                factory.commits[0].hex,
                factory.repo[factory.commits[0]].tree.hex,
                factory.nonexistent_oid(),
                ],
            }

        resp = self.app.post_json(
            '/repo/{}/commits'.format(self.repo_path), bulk_commits)
        self.assertEqual(1, len(resp.json))
        self.assertEqual(bulk_commits['commits'][0], resp.json[0]['sha1'])

    def test_repo_get_log_signatures(self):
        """Ensure signatures are correct."""
        factory = RepoFactory(self.repo_store)
        committer = factory.makeSignature('村上 春樹'.encode('utf-8'),
                                          'tsukuru@猫の町.co.jp'.encode('utf-8'),
                                          encoding='utf-8')
        author = factory.makeSignature(
            'Владимир Владимирович Набоков'.encode('utf-8'),
            'Набоко@zembla.ru'.encode('utf-8'), encoding='utf-8')
        oid = factory.add_commit('Obfuscate colophon.', 'path.foo',
                                 author=author, committer=committer)
        resp = self.app.get('/repo/{}/log/{}'.format(self.repo_path, oid))
        self.assertEqual(author.name, resp.json[0]['author']['name'])

    def test_repo_get_log(self):
        factory = RepoFactory(self.repo_store, num_commits=4)
        factory.build()
        commits_from = factory.commits[2].hex
        resp = self.app.get('/repo/{}/log/{}'.format(
            self.repo_path, commits_from))
        self.assertEqual(3, len(resp.json))

    def test_repo_get_unicode_log(self):
        factory = RepoFactory(self.repo_store)
        message = '나는 김치 사랑'.encode('utf-8')
        message2 = '(╯°□°)╯︵ ┻━┻'.encode('utf-8')
        oid = factory.add_commit(message, '자장면/짜장면.py')
        oid2 = factory.add_commit(message2, '엄마야!.js', [oid])

        resp = self.app.get('/repo/{}/log/{}'.format(self.repo_path, oid2))
        self.assertEqual(message2.decode('utf-8', 'replace'),
                         resp.json[0]['message'])
        self.assertEqual(message.decode('utf-8', 'replace'),
                         resp.json[1]['message'])

    def test_repo_get_non_unicode_log(self):
        """Ensure that non-unicode data is discarded."""
        factory = RepoFactory(self.repo_store)
        message = b'\xe9\xe9\xe9'  # latin-1
        oid = factory.add_commit(message, 'foo.py')
        resp = self.app.get('/repo/{}/log/{}'.format(self.repo_path, oid))
        self.assertEqual(message.decode('utf-8', 'replace'),
                         resp.json[0]['message'])

    def test_repo_get_log_with_limit(self):
        """Ensure the commit log can filtered by limit."""
        factory = RepoFactory(self.repo_store, num_commits=10)
        repo = factory.build()
        head = repo.head.target
        resp = self.app.get('/repo/{}/log/{}?limit=5'.format(
            self.repo_path, head))
        self.assertEqual(5, len(resp.json))

    def test_repo_get_log_with_stop(self):
        """Ensure the commit log can be filtered by a stop commit."""
        factory = RepoFactory(self.repo_store, num_commits=10)
        repo = factory.build()
        stop_commit = factory.commits[4]
        excluded_commit = factory.commits[5]
        head = repo.head.target
        resp = self.app.get('/repo/{}/log/{}?stop={}'.format(
            self.repo_path, head, stop_commit))
        self.assertEqual(5, len(resp.json))
        self.assertNotIn(excluded_commit, resp.json)

    def test_repo_repack(self):
        """Ensure commit exists in pack."""
        factory = RepoFactory(self.repo_store, num_branches=2, num_commits=1)
        factory.build()
        resp = self.app.post_json('/repo/{}/repack'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        # test for nonexistent repositories
        resp = self.app.post_json('/repo/nonexistent/repack',
                                  expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_gc(self):
        factory = RepoFactory(self.repo_store, num_branches=2, num_commits=1)
        factory.build()
        resp = self.app.post_json('/repo/{}/gc'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        # test for nonexistent repositories
        resp = self.app.post_json('/repo/nonexistent/gc', expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_detect_merges_missing_target(self):
        """A non-existent target OID returns HTTP 404."""
        factory = RepoFactory(self.repo_store)
        resp = self.app.post_json('/repo/{}/detect-merges/{}'.format(
            self.repo_path, factory.nonexistent_oid()),
            {'sources': []}, expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_detect_merges_missing_source(self):
        """A non-existent source commit is ignored."""
        factory = RepoFactory(self.repo_store)
        # A---B
        a = factory.add_commit('a\n', 'file')
        b = factory.add_commit('b\n', 'file', parents=[a])
        resp = self.app.post_json('/repo/{}/detect-merges/{}'.format(
            self.repo_path, b),
            {'sources': [factory.nonexistent_oid()]})
        self.assertEqual(200, resp.status_code)
        self.assertEqual({}, resp.json)

    def test_repo_detect_merges_unmerged(self):
        """An unmerged commit is not returned."""
        factory = RepoFactory(self.repo_store)
        # A---B
        #  \
        #   C
        a = factory.add_commit('a\n', 'file')
        b = factory.add_commit('b\n', 'file', parents=[a])
        c = factory.add_commit('c\n', 'file', parents=[a])
        resp = self.app.post_json('/repo/{}/detect-merges/{}'.format(
            self.repo_path, b),
            {'sources': [c.hex]})
        self.assertEqual(200, resp.status_code)
        self.assertEqual({}, resp.json)

    def test_repo_detect_merges_pulled(self):
        """Commits that were pulled (fast-forward) are their own merge
        points."""
        factory = RepoFactory(self.repo_store)
        # A---B---C
        a = factory.add_commit('a\n', 'file')
        b = factory.add_commit('b\n', 'file', parents=[a])
        c = factory.add_commit('c\n', 'file', parents=[b])
        # The start commit would never be the source of a merge proposal,
        # but include it anyway to test boundary conditions.
        resp = self.app.post_json('/repo/{}/detect-merges/{}'.format(
            self.repo_path, c),
            {'sources': [a.hex, b.hex, c.hex]})
        self.assertEqual(200, resp.status_code)
        self.assertEqual({a.hex: a.hex, b.hex: b.hex, c.hex: c.hex}, resp.json)

    def test_repo_detect_merges_merged(self):
        """Commits that were merged have sensible merge points."""
        factory = RepoFactory(self.repo_store)
        # A---C---D---G---H
        #  \ /       /
        #   B---E---F---I
        a = factory.add_commit('a\n', 'file')
        b = factory.add_commit('b\n', 'file', parents=[a])
        c = factory.add_commit('c\n', 'file', parents=[a, b])
        d = factory.add_commit('d\n', 'file', parents=[c])
        e = factory.add_commit('e\n', 'file', parents=[b])
        f = factory.add_commit('f\n', 'file', parents=[e])
        g = factory.add_commit('g\n', 'file', parents=[d, f])
        h = factory.add_commit('h\n', 'file', parents=[g])
        i = factory.add_commit('i\n', 'file', parents=[f])
        resp = self.app.post_json('/repo/{}/detect-merges/{}'.format(
            self.repo_path, h),
            {'sources': [b.hex, e.hex, i.hex]})
        self.assertEqual(200, resp.status_code)
        self.assertEqual({b.hex: c.hex, e.hex: g.hex}, resp.json)

    def test_repo_blob(self):
        """Getting an existing blob works."""
        factory = RepoFactory(self.repo_store)
        c1 = factory.add_commit('a\n', 'dir/file')
        factory.add_commit('b\n', 'dir/file', parents=[c1])
        resp = self.app.get('/repo/{}/blob/dir/file'.format(self.repo_path))
        self.assertEqual(2, resp.json['size'])
        self.assertEqual(b'b\n', base64.b64decode(resp.json['data']))
        resp = self.app.get('/repo/{}/blob/dir/file?rev=master'.format(
            self.repo_path))
        self.assertEqual(2, resp.json['size'])
        self.assertEqual(b'b\n', base64.b64decode(resp.json['data']))
        resp = self.app.get('/repo/{}/blob/dir/file?rev={}'.format(
            self.repo_path, c1.hex))
        self.assertEqual(2, resp.json['size'])
        self.assertEqual(b'a\n', base64.b64decode(resp.json['data']))

    def test_repo_blob_missing_commit(self):
        """Trying to get a blob from a non-existent commit returns HTTP 404."""
        factory = RepoFactory(self.repo_store)
        factory.add_commit('a\n', 'dir/file')
        resp = self.app.get('/repo/{}/blob/dir/file?rev={}'.format(
            self.repo_path, factory.nonexistent_oid()), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_blob_missing_file(self):
        """Trying to get a blob with a non-existent name returns HTTP 404."""
        factory = RepoFactory(self.repo_store)
        factory.add_commit('a\n', 'dir/file')
        resp = self.app.get('/repo/{}/blob/nonexistent'.format(
            self.repo_path), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_blob_directory(self):
        """Trying to get a blob referring to a directory returns HTTP 404."""
        factory = RepoFactory(self.repo_store)
        factory.add_commit('a\n', 'dir/file')
        resp = self.app.get('/repo/{}/blob/dir'.format(
            self.repo_path), expect_errors=True)
        self.assertEqual(404, resp.status_code)

    def test_repo_blob_non_ascii(self):
        """Blobs may contain non-ASCII (and indeed non-UTF-8) data."""
        factory = RepoFactory(self.repo_store)
        factory.add_commit(b'\x80\x81\x82\x83', 'dir/file')
        resp = self.app.get('/repo/{}/blob/dir/file'.format(self.repo_path))
        self.assertEqual(4, resp.json['size'])
        self.assertEqual(
            b'\x80\x81\x82\x83', base64.b64decode(resp.json['data']))

    def test_repo_blob_from_tag(self):
        """Getting an existing blob from a tag works."""
        factory = RepoFactory(self.repo_store)
        c1 = factory.add_commit('a\n', 'dir/file')
        factory.add_commit('b\n', 'dir/file', parents=[c1])
        factory.add_tag('tag-name', 'tag message', c1)
        resp = self.app.get('/repo/{}/blob/dir/file?rev=tag-name'.format(
            self.repo_path))
        self.assertEqual(2, resp.json['size'])
        self.assertEqual(b'a\n', base64.b64decode(resp.json['data']))

    def test_repo_blob_from_non_commit(self):
        """Trying to get a blob from a non-commit returns HTTP 404."""
        factory = RepoFactory(self.repo_store)
        c1 = factory.add_commit('a\n', 'dir/file')
        factory.add_commit('b\n', 'dir/file', parents=[c1])
        resp = self.app.get(
            '/repo/{}/blob/dir/file?rev={}'.format(
                self.repo_path, factory.repo[c1].tree.hex),
            expect_errors=True)
        self.assertEqual(404, resp.status_code)


class AsyncRepoCreationAPI(TestCase, ApiRepoStoreMixin):

    def setUp(self):
        super(AsyncRepoCreationAPI, self).setUp()
        self.setupRepoStore()
        # XML-RPC server
        self.virtinfo = FakeVirtInfoService(allowNone=True)
        self.virtinfo_listener = default_reactor.listenTCP(0, server.Site(
            self.virtinfo))
        self.virtinfo_port = self.virtinfo_listener.getHost().port
        self.virtinfo_url = b'http://localhost:%d/' % self.virtinfo_port
        self.addCleanup(self.virtinfo_listener.stopListening)
        config.defaults['virtinfo_endpoint'] = self.virtinfo_url

    def _doReactorIteration(self):
        """Yield to the reactor so it can process virtinfo requests.

        This is a bit hacky, but allow us to simulate the twisted XML-RPC
        fake server without needing to make this test suite async.
        Making this test suite async could make it less realistic, since the
        API beign tested itself is not running over twisted event loop.
        """
        reactor_iterations = (
            len(default_reactor._reads) + len(default_reactor._writes))
        for i in range(reactor_iterations):
            default_reactor.iterate()

    def assertRepositoryCreatedAsynchronously(self, repo_path, timeout_secs=5):
        """Waits up to `timeout_secs` for a repository to be available."""
        timeout = timedelta(seconds=timeout_secs)
        start = datetime.now()
        while datetime.now() <= (start + timeout):
            self._doReactorIteration()
            try:
                resp = self.app.get('/repo/{}'.format(repo_path),
                                    expect_errors=True)
                if resp.status_code == 200 and resp.json['is_available']:
                    return
            except Exception:
                # If we have any unexpected error, wait a bit and retry.
                pass
            time.sleep(0.1)
        self.fail(
            "Repository %s was not created after %s secs"
            % (repo_path, timeout_secs))

    def assertAnyMockCalledAsync(self, mocks, timeout_secs=5):
        """Asserts that any of the mocks in *args will be called in the
        next timeout_secs seconds.
        """
        timeout = timedelta(seconds=timeout_secs)
        start = datetime.now()
        while datetime.now() <= (start + timeout):
            self._doReactorIteration()
            if any(i.called for i in mocks):
                return
            time.sleep(0.1)
        self.fail(
            "None of the given args was called after %s seconds."
            % timeout_secs)

    def test_repo_async_creation_with_clone(self):
        """Repo can be initialised with optional clone asynchronously."""
        self.useFixture(CeleryWorkerFixture())
        self.virtinfo.xmlrpc_confirmRepoCreation = mock.Mock(return_value=None)
        self.virtinfo.xmlrpc_abortRepoCreation = mock.Mock(return_value=None)

        factory = RepoFactory(self.repo_store, num_commits=2)
        factory.build()
        new_repo_path = uuid.uuid1().hex
        resp = self.app.post_json('/repo', {
            'async': True,
            'repo_path': new_repo_path,
            'clone_from': self.repo_path,
            'clone_refs': True})

        self.assertRepositoryCreatedAsynchronously(new_repo_path)

        repo1_revlist = get_revlist(factory.repo)
        clone_from = resp.json['repo_url'].split('/')[-1]
        repo2 = open_repo(os.path.join(self.repo_root, clone_from))
        repo2_revlist = get_revlist(repo2)

        self.assertEqual(repo1_revlist, repo2_revlist)
        self.assertEqual(200, resp.status_code)
        self.assertIn(new_repo_path, resp.json['repo_url'])

        self.assertEqual([mock.call(
            mock.ANY, new_repo_path, {"user": "+launchpad-services"})],
            self.virtinfo.xmlrpc_confirmRepoCreation.call_args_list)
        self.assertEqual(
            [], self.virtinfo.xmlrpc_abortRepoCreation.call_args_list)

    def test_repo_async_creation_aborts_when_fails_to_create_locally(self):
        """If we fail to create the repository locally, abortRepoCreation
        XML-RPC method should be called."""
        self.useFixture(
            EnvironmentVariable("REPO_STORE", '/tmp/invalid/path/to/repos/'))
        self.useFixture(CeleryWorkerFixture())
        self.virtinfo.xmlrpc_confirmRepoCreation = mock.Mock(return_value=None)
        self.virtinfo.xmlrpc_abortRepoCreation = mock.Mock(return_value=None)

        factory = RepoFactory(self.repo_store, num_commits=2)
        factory.build()
        new_repo_path = uuid.uuid1().hex
        self.app.post_json('/repo', {
            'async': True,
            'repo_path': new_repo_path,
            'clone_from': self.repo_path,
            'clone_refs': True})

        # Wait until the repository creation is either confirmed or aborted
        # (and we hope it was aborted...)
        self.assertAnyMockCalledAsync([
            self.virtinfo.xmlrpc_confirmRepoCreation,
            self.virtinfo.xmlrpc_abortRepoCreation])
        self.assertFalse(
            os.path.exists(os.path.join(self.repo_root, new_repo_path)))

        self.assertEqual([mock.call(
            mock.ANY, new_repo_path, {"user": "+launchpad-services"})],
            self.virtinfo.xmlrpc_abortRepoCreation.call_args_list)
        self.assertEqual(
            [], self.virtinfo.xmlrpc_confirmRepoCreation.call_args_list)

    def test_repo_async_creation_aborts_when_fails_confirm(self):
        """If we fail to confirm the repository creation, abortRepoCreation
        XML-RPC method should be called."""
        self.useFixture(CeleryWorkerFixture())
        self.virtinfo.xmlrpc_confirmRepoCreation = mock.Mock(
            side_effect=Exception("?"))
        self.virtinfo.xmlrpc_abortRepoCreation = mock.Mock(return_value=None)

        factory = RepoFactory(self.repo_store, num_commits=2)
        factory.build()
        new_repo_path = uuid.uuid1().hex
        self.app.post_json('/repo', {
            'async': True,
            'repo_path': new_repo_path,
            'clone_from': self.repo_path,
            'clone_refs': True})

        # Wait until the repository creation is either confirmed or aborted
        # (and we hope it was aborted...)
        self.assertAnyMockCalledAsync([
            self.virtinfo.xmlrpc_abortRepoCreation])
        self.assertFalse(
            os.path.exists(os.path.join(self.repo_root, new_repo_path)))

        self.assertEqual([mock.call(
            mock.ANY, new_repo_path, {"user": "+launchpad-services"})],
            self.virtinfo.xmlrpc_abortRepoCreation.call_args_list)
        self.assertEqual([mock.call(
            mock.ANY, new_repo_path, {"user": "+launchpad-services"})],
            self.virtinfo.xmlrpc_confirmRepoCreation.call_args_list)

    def assertContainsLog(self, pattern, log_lines):
        for line in log_lines:
            if re.match(pattern, line):
                return
        self.fail("'%s' is not in any log line" % pattern)

    def test_celery_log_messages(self):
        logdir = self.useFixture(TempDir()).path
        logfile = os.path.join(logdir, "celery.log")
        self.useFixture(CeleryWorkerFixture(logfile=logfile, loglevel="debug"))

        self.virtinfo.xmlrpc_confirmRepoCreation = mock.Mock(return_value=None)
        self.virtinfo.xmlrpc_abortRepoCreation = mock.Mock(return_value=None)

        factory = RepoFactory(self.repo_store, num_commits=2)
        factory.build()
        new_repo_path = uuid.uuid1().hex
        self.app.post_json('/repo', {
            'async': True,
            'repo_path': new_repo_path,
            'clone_from': self.repo_path,
            'clone_refs': True})

        self.assertRepositoryCreatedAsynchronously(new_repo_path)

        with open(logfile, 'r') as fd:
            log_lines = fd.read().split('\n')
        self.assertContainsLog(
            ".*INFO.*Received task: turnip.api.store.init_and_confirm_repo.*",
            log_lines)
        self.assertContainsLog(
            ".*INFO.*Initializing and confirming repository creation:.*",
            log_lines)
        self.assertContainsLog(
            ".*DEBUG.*Confirming repository creation:.*",
            log_lines)


if __name__ == '__main__':
    unittest.main()

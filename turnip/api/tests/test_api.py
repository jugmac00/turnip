# -*- coding: utf-8 -*-

# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import print_function

import os
import subprocess
from textwrap import dedent
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
    chdir,
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

    def assertReferencesEqual(self, repo, expected, observed):
        self.assertEqual(
            repo.lookup_reference(expected).peel().oid,
            repo.lookup_reference(observed).peel().oid)

    def get_ref(self, ref):
        resp = self.app.get('/repo/{}/{}'.format(self.repo_path, ref))
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
        self.assertEqual({'default_branch': 'refs/heads/branch-0'}, resp.json)

    def test_repo_get_default_branch_missing(self):
        """default_branch is returned even if that branch has been deleted."""
        factory = RepoFactory(self.repo_store, num_branches=2, num_commits=1)
        factory.build()
        factory.repo.set_head('refs/heads/branch-0')
        factory.repo.lookup_reference('refs/heads/branch-0').delete()

        resp = self.app.get('/repo/{}'.format(self.repo_path))
        self.assertEqual(200, resp.status_code)
        self.assertEqual({'default_branch': 'refs/heads/branch-0'}, resp.json)

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
        self.assertEqual(404, resp.status_code)

    def test_ignore_non_unicode_refs(self):
        """Ensure non-unicode refs are dropped from ref collection."""
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag = '\xe9\xe9\xe9'  # latin-1
        tag_message = 'tag message'
        factory.add_tag(tag, tag_message, commit_oid)

        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        refs = resp.json
        self.assertEqual(1, len(refs.keys()))

    def test_allow_unicode_refs(self):
        """Ensure unicode refs are included in ref collection."""
        factory = RepoFactory(self.repo_store)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        tag = u'おいしいイカ'.encode('utf-8')
        tag_message = u'かわいい タコ'.encode('utf-8')
        factory.add_tag(tag, tag_message, commit_oid)

        resp = self.app.get('/repo/{}/refs'.format(self.repo_path))
        refs = resp.json
        self.assertEqual(2, len(refs.keys()))

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

    def test_repo_compare_commits(self):
        """Ensure expected changes exist in diff patch."""
        repo = RepoFactory(self.repo_store)
        c1_oid = repo.add_commit('foo', 'foobar.txt')
        c2_oid = repo.add_commit('bar', 'foobar.txt', parents=[c1_oid])

        path = '/repo/{}/compare/{}..{}'.format(self.repo_path, c1_oid, c2_oid)
        resp = self.app.get(path)
        self.assertIn('-foo', resp.body)
        self.assertIn('+bar', resp.body)

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
        message = u'屋漏偏逢连夜雨'.encode('utf-8')
        message2 = u'说曹操，曹操到'.encode('utf-8')
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
        message = 'not particularly sensible latin-1: \xe9\xe9\xe9.'
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
        c2_left = repo.add_commit('foo\nbar\n', 'blah.txt', parents=[c1])
        c2_right = repo.add_commit('foo\nbaz\n', 'blah.txt', parents=[c1])

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c2_left, c2_right))
        self.assertIn(dedent("""\
            +<<<<<<< blah.txt
             bar
            +=======
            +baz
            +>>>>>>> blah.txt
            """), resp.json_body['patch'])
        self.assertEqual(['blah.txt'], resp.json_body['conflicts'])

    def test_repo_diff_merge_empty(self):
        """Ensure that diffing two identical commits returns an empty string
        as the patch, not None."""
        repo = RepoFactory(self.repo_store)
        c1 = repo.add_commit('foo\n', 'blah.txt')

        resp = self.app.get('/repo/{}/compare-merge/{}:{}'.format(
            self.repo_path, c1, c1))
        self.assertEqual('', resp.json_body['patch'])

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
        committer = factory.makeSignature(u'村上 春樹'.encode('utf-8'),
                                          u'tsukuru@猫の町.co.jp'.encode('utf-8'),
                                          encoding='utf-8')
        author = factory.makeSignature(
            u'Владимир Владимирович Набоков'.encode('utf-8'),
            u'Набоко@zembla.ru'.encode('utf-8'), encoding='utf-8')
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
        message = u'나는 김치 사랑'.encode('utf-8')
        message2 = u'(╯°□°)╯︵ ┻━┻'.encode('utf-8')
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
        message = '\xe9\xe9\xe9'  # latin-1
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

    def test_repo_repack_verify_pack(self):
        """Ensure commit exists in pack."""
        factory = RepoFactory(self.repo_store)
        oid = factory.add_commit('foo', 'foobar.txt')
        resp = self.app.post_json('/repo/{}/repack'.format(self.repo_path),
                                  {'prune': True, 'single': True})
        for filename in factory.packs:
            pack = os.path.join(factory.pack_dir, filename)
        out = subprocess.check_output(['git', 'verify-pack', pack, '-v'])
        self.assertEqual(200, resp.status_code)
        self.assertIn(oid.hex, out)

    def test_repo_repack_verify_commits_to_pack(self):
        """Ensure commits in different packs exist in merged pack."""
        factory = RepoFactory(self.repo_store)
        oid = factory.add_commit('foo', 'foobar.txt')
        with chdir(factory.pack_dir):
            subprocess.call(['git', 'gc', '-q'])  # pack first commit
            oid2 = factory.add_commit('bar', 'foobar.txt', [oid])
            p = subprocess.Popen(['git', 'pack-objects', '-q', 'pack2'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            p.communicate(input=oid2.hex)
        self.assertEqual(2, len(factory.packs))  # ensure 2 packs exist
        self.app.post_json('/repo/{}/repack'.format(self.repo_path),
                           {'prune': True, 'single': True})
        self.assertEqual(1, len(factory.packs))
        repacked_pack = os.path.join(factory.pack_dir, factory.packs[0])
        out = subprocess.check_output(['git', 'verify-pack',
                                       repacked_pack, '-v'])
        self.assertIn(oid.hex, out)
        self.assertIn(oid2.hex, out)


if __name__ == '__main__':
    unittest.main()

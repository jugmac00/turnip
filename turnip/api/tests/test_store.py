# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import re
import subprocess
import uuid

from fixtures import (
    EnvironmentVariable,
    MonkeyPatch,
    TempDir,
    )
import pygit2
from testtools import TestCase
import yaml

from turnip.api import store
from turnip.api.tests.test_helpers import (
    open_repo,
    RepoFactory,
    )


class InitTestCase(TestCase):

    def setUp(self):
        super(InitTestCase, self).setUp()
        self.repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", self.repo_store))

    def assertAllLinkCounts(self, link_count, path):
        count = 0
        for dirpath, _, filenames in os.walk(path):
            for filename in filenames:
                count += 1
                self.assertEqual(
                    link_count,
                    os.stat(os.path.join(dirpath, filename)).st_nlink)
        return count

    def assertAdvertisedRefs(self, present, absent, repo_path):
        out, err = subprocess.Popen(
            ['git', 'receive-pack', '--advertise-refs', repo_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        self.assertEqual(b'', err)
        for ref, hex in present:
            self.assertIn(
                hex.encode('ascii') + b' ' + ref.encode('utf-8'), out)
        for ref in absent:
            self.assertNotIn(absent, out)

    def assertAlternates(self, expected_paths, repo_path):
        alt_path = store.alternates_path(repo_path)
        if not os.path.exists(os.path.dirname(alt_path)):
            raise Exception("No repo at %s." % repo_path)
        actual_paths = []
        if os.path.exists(alt_path):
            with open(alt_path) as altf:
                actual_paths = [
                    re.sub('/objects\n$', '', line) for line in altf]
        self.assertEqual(
            set([path.rstrip('/') for path in expected_paths]),
            set(actual_paths))

    def makeOrig(self):
        self.orig_path = os.path.join(self.repo_store, 'orig/')
        orig = RepoFactory(
            self.orig_path, num_branches=3, num_commits=2).build()
        self.orig_refs = orig.listall_references()
        self.master_oid = orig.references['refs/heads/master'].target
        self.orig_objs = os.path.join(self.orig_path, '.git/objects')

    def test_from_scratch(self):
        path = os.path.join(self.repo_store, 'repo/')
        store.init_repo(path)
        r = pygit2.Repository(path)
        self.assertEqual([], r.listall_references())

    def test_repo_config(self):
        """Assert repository is initialised with correct config defaults."""
        repo_path = os.path.join(self.repo_store, 'repo')
        store.init_repo(repo_path)
        repo_config = pygit2.Repository(repo_path).config
        with open('git.config.yaml') as f:
            yaml_config = yaml.load(f)

        self.assertEqual(bool(yaml_config['core.logallrefupdates']),
                         bool(repo_config['core.logallrefupdates']))
        self.assertEqual(str(yaml_config['pack.depth']),
                         repo_config['pack.depth'])

    def test_open_ephemeral_repo(self):
        """Opening a repo where a repo name contains ':' should return
        a new ephemeral repo.
        """
        # Create repos A and B with distinct commits, and C which has no
        # objects of its own but has a clone of B as its
        # turnip-subordinate.
        repos = {}
        for name in ['A', 'B']:
            factory = RepoFactory(os.path.join(self.repo_store, name))
            factory.generate_branches(2, 2)
            repos[name] = factory.repo
        repo_path_c = os.path.join(self.repo_store, 'C')
        store.init_repo(
            repo_path_c, clone_from=os.path.join(self.repo_store, 'B'))
        repos['C'] = pygit2.Repository(repo_path_c)

        # Opening the union of one and three includes the objects from
        # two, as they're in three's turnip-subordinate.
        repo_name = 'A:C'

        with store.open_repo(self.repo_store, repo_name) as ephemeral_repo:
            self.assertAlternates(
                [repos['A'].path, repos['C'].path,
                 os.path.join(repos['A'].path, 'turnip-subordinate'),
                 os.path.join(repos['C'].path, 'turnip-subordinate')],
                ephemeral_repo.path)
            self.assertIn(repos['A'].head.target, ephemeral_repo)
            self.assertIn(repos['B'].head.target, ephemeral_repo)

    def test_open_ephemeral_repo_init_exception(self):
        """If init_repo fails, open_repo cleans up but preserves the error."""
        class InitException(Exception):
            pass

        def mock_write_alternates(*args, **kwargs):
            raise InitException()

        self.useFixture(MonkeyPatch(
            'turnip.api.store.write_alternates', mock_write_alternates))
        repos = {}
        for name in ('A', 'B'):
            repos[name] = RepoFactory(os.path.join(self.repo_store, name)).repo

        def open_test_repo():
            with store.open_repo(self.repo_store, 'A:B'):
                pass

        self.assertRaises(InitException, open_test_repo)
        self.assertEqual({'A', 'B'}, set(os.listdir(self.repo_store)))

    def test_repo_with_alternates(self):
        """Ensure objects path is defined correctly in repo alternates."""
        factory = RepoFactory(os.path.join(self.repo_store, uuid.uuid1().hex))
        repo_path_with_alt = os.path.join(self.repo_store, uuid.uuid1().hex)
        store.init_repo(
            repo_path_with_alt, alternate_repo_paths=[factory.repo.path])
        self.assertAlternates([factory.repo_path], repo_path_with_alt)

    def test_repo_alternates_objects_shared(self):
        """Ensure objects are shared from alternate repo."""
        factory = RepoFactory(os.path.join(self.repo_store, uuid.uuid1().hex))
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        repo_path_with_alt = os.path.join(self.repo_store, uuid.uuid4().hex)
        store.init_repo(
            repo_path_with_alt, alternate_repo_paths=[factory.repo.path])
        repo_with_alt = open_repo(repo_path_with_alt)
        self.assertEqual(commit_oid.hex, repo_with_alt.get(commit_oid).hex)

    def test_clone_with_refs(self):
        self.makeOrig()
        self.assertAllLinkCounts(1, self.orig_objs)

        # init_repo with clone_from=orig and clone_refs=True creates a
        # repo with the same set of refs. And the objects are copied
        # too.
        to_path = os.path.join(self.repo_store, 'to/')
        store.init_repo(to_path, clone_from=self.orig_path, clone_refs=True)
        to = pygit2.Repository(to_path)
        self.assertIsNot(None, to[self.master_oid])
        self.assertEqual(self.orig_refs, to.listall_references())

        # Advance master and remove branch-2, so that the commit referenced
        # by the original repository's master isn't referenced by any of the
        # cloned repository's refs; otherwise git >= 2.13 deduplicates the
        # ref in the alternate object store which makes it hard to test that
        # it's set up properly.
        RepoFactory(to_path, num_commits=2).build()
        to.references.delete('refs/heads/branch-2')
        to_master_oid = to.references['refs/heads/master'].target

        # Internally, the packs are hardlinked into a subordinate
        # alternate repo, so minimal space is used by the clone.
        self.assertAlternates(['../turnip-subordinate'], to_path)
        self.assertTrue(
            os.path.exists(
                os.path.join(to_path, 'turnip-subordinate')))
        self.assertAllLinkCounts(2, self.orig_objs)

        self.assertAdvertisedRefs(
            [('.have', self.master_oid.hex),
             ('refs/heads/master', to_master_oid.hex)],
            [], to_path)

    def test_clone_without_refs(self):
        self.makeOrig()
        self.assertAllLinkCounts(1, self.orig_objs)

        # init_repo with clone_from=orig and clone_refs=False creates a
        # repo without any refs, but the objects are copied.
        to_path = os.path.join(self.repo_store, 'to/')
        store.init_repo(to_path, clone_from=self.orig_path, clone_refs=False)
        to = pygit2.Repository(to_path)
        self.assertIsNot(None, to[self.master_oid])
        self.assertEqual([], to.listall_references())

        # Internally, the packs are hardlinked into a subordinate
        # alternate repo, so minimal space is used by the clone.
        self.assertAlternates(['../turnip-subordinate'], to_path)
        self.assertTrue(
            os.path.exists(
                os.path.join(to_path, 'turnip-subordinate')))
        self.assertAllLinkCounts(2, self.orig_objs)

        # No refs exist, but receive-pack advertises the clone_from's
        # refs as extra haves.
        self.assertAdvertisedRefs(
            [('.have', self.master_oid.hex)], ['refs/'], to_path)

    def test_clone_of_clone(self):
        self.makeOrig()
        orig_blob = pygit2.Repository(self.orig_path).create_blob(b'orig')

        self.assertAllLinkCounts(1, self.orig_objs)
        to_path = os.path.join(self.repo_store, 'to/')
        store.init_repo(to_path, clone_from=self.orig_path)
        self.assertAllLinkCounts(2, self.orig_objs)
        to_blob = pygit2.Repository(to_path).create_blob(b'to')

        too_path = os.path.join(self.repo_store, 'too/')
        store.init_repo(too_path, clone_from=to_path)
        self.assertAllLinkCounts(3, self.orig_objs)
        too_blob = pygit2.Repository(too_path).create_blob(b'too')

        # Each clone has just its subordinate as an alternate, and the
        # subordinate has no alternates of its own.
        for path in (to_path, too_path):
            self.assertAlternates(['../turnip-subordinate'], path)
            self.assertAlternates([], os.path.join(path, 'turnip-subordinate'))
            self.assertIn(self.master_oid.hex, pygit2.Repository(path))
            self.assertAdvertisedRefs(
                [('.have', self.master_oid.hex)], [], path)

        # Objects from all three repos are in the third.
        too = pygit2.Repository(too_path)
        self.assertIn(orig_blob, too)
        self.assertIn(to_blob, too)
        self.assertIn(too_blob, too)

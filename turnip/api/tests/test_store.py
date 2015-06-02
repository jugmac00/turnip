# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import subprocess
import uuid

from fixtures import (
    EnvironmentVariable,
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
        self.repo_path = os.path.join(self.repo_store, uuid.uuid1().hex)

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
        self.assertEqual('', err)
        for ref, hex in present:
            self.assertIn('%s %s' % (hex, ref), out)
        for ref in absent:
            self.assertNotIn(absent, out)

    def makeOrig(self):
        self.orig_path = os.path.join(self.repo_store, 'orig/')
        orig = RepoFactory(
            self.orig_path, num_branches=3, num_commits=2).build()
        self.orig_refs = orig.listall_references()
        self.master_oid = orig.lookup_reference('refs/heads/master').target
        self.orig_objs = os.path.join(self.orig_path, '.git/objects')

    def assert_alternate_exists(self, alternate_path, repo_path):
        """Assert alternate_path exists in alternates at repo_path."""
        objects_path = '{}\n'.format(
            os.path.join(alternate_path, 'objects'))
        with open(store.alternates_path(repo_path), 'r') as alts:
            alts_content = alts.read()
            self.assertIn(objects_path, alts_content)

    def test_from_scratch(self):
        path = os.path.join(self.repo_store, 'repo/')
        self.assertEqual(path, store.init_repo(path))
        r = pygit2.Repository(path)
        self.assertEqual([], r.listall_references())

    def test_repo_config(self):
        """Assert repository is initialised with correct config defaults."""
        repo_path = store.init_repo(self.repo_path)
        repo_config = pygit2.Repository(repo_path).config
        yaml_config = yaml.load(open('git.config.yaml'))

        self.assertEqual(bool(yaml_config['core.logallrefupdates']),
                         bool(repo_config['core.logallrefupdates']))
        self.assertEqual(str(yaml_config['pack.depth']),
                         repo_config['pack.depth'])

    def test_open_ephemeral_repo(self):
        """Opening a repo where a repo name contains ':' should return
        a new ephemeral repo.
        """
        repos = [uuid.uuid4().hex, uuid.uuid4().hex]
        repo_name = '{}:{}'.format(repos[0], repos[1])
        alt_path = os.path.join(self.repo_store, repos[0])
        with store.open_repo(self.repo_store, repo_name) as repo:
            self.assert_alternate_exists(alt_path, repo.path)

    def test_repo_with_alternates(self):
        """Ensure objects path is defined correctly in repo alternates."""
        factory = RepoFactory(self.repo_path)
        new_repo_path = os.path.join(self.repo_store, uuid.uuid1().hex)
        repo_path_with_alt = store.init_repo(
            new_repo_path, alternate_repo_paths=[factory.repo.path])
        self.assert_alternate_exists(factory.repo.path, repo_path_with_alt)

    def test_repo_alternates_objects_shared(self):
        """Ensure objects are shared from alternate repo."""
        factory = RepoFactory(self.repo_path)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        new_repo_path = os.path.join(self.repo_store, uuid.uuid4().hex)
        repo_path_with_alt = store.init_repo(
            new_repo_path, alternate_repo_paths=[factory.repo.path])
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

        # Internally, the packs are hardlinked into a subordinate
        # alternate repo, so minimal space is used by the clone.
        self.assertTrue(
            os.path.exists(
                os.path.join(to_path, 'turnip-subordinate')))
        self.assertAllLinkCounts(2, self.orig_objs)

        self.assertAdvertisedRefs(
            [('.have', self.master_oid.hex),
             ('refs/heads/master', self.master_oid.hex)],
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
        self.assertTrue(
            os.path.exists(
                os.path.join(to_path, 'turnip-subordinate')))
        self.assertAllLinkCounts(2, self.orig_objs)

        # No refs exist, but receive-pack advertises the clone_from's
        # refs as extra haves.
        self.assertAdvertisedRefs(
            [('.have', self.master_oid.hex)], ['refs/'], to_path)

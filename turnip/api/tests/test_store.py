# Copyright 2015 Canonical Ltd.  All rights reserved.

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path
import subprocess

from fixtures import TempDir
import pygit2
from testtools import TestCase

from turnip.api import store
from turnip.api.tests.test_helpers import RepoFactory


class InitTestCase(TestCase):

    def setUp(self):
        super(InitTestCase, self).setUp()
        self.root = self.useFixture(TempDir()).path

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
        self.orig_path = os.path.join(self.root, 'orig/')
        orig = RepoFactory(
            self.orig_path, num_branches=3, num_commits=2).build()
        self.orig_refs = orig.listall_references()
        self.master_oid = orig.lookup_reference('refs/heads/master').target
        self.orig_objs = os.path.join(self.orig_path, '.git/objects')

    def test_from_scratch(self):
        path = os.path.join(self.root, 'repo/')
        self.assertEqual(path, store.init_repo(path))
        r = pygit2.Repository(path)
        self.assertEqual([], r.listall_references())

    def test_clone_with_refs(self):
        self.makeOrig()
        self.assertAllLinkCounts(1, self.orig_objs)

        # init_repo with clone_from=orig and clone_refs=True creates a
        # repo with the same set of refs. And the objects are copied
        # too.
        to_path = os.path.join(self.root, 'to/')
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
        to_path = os.path.join(self.root, 'to/')
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

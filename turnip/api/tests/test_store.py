# Copyright 2015 Canonical Ltd.  All rights reserved.

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

import os.path

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

    def test_from_scratch(self):
        path = os.path.join(self.root, 'repo/')
        self.assertEqual(path, store.init_repo(path))
        r = pygit2.Repository(path)
        self.assertEqual([], r.listall_references())

    def test_clone_from(self):
        orig_path = os.path.join(self.root, 'orig/')
        orig = RepoFactory(
            orig_path, num_branches=3, num_commits=2).build()
        orig_refs = orig.listall_references()
        master_oid = orig.lookup_reference('refs/heads/master').target
        orig_objs = os.path.join(orig_path, '.git/objects')
        self.assertAllLinkCounts(1, orig_objs)

        # init_repo with clone_from=orig creates a repo with the same
        # set of refs. And the objects are copied too.
        to_path = os.path.join(self.root, 'to/')
        self.assertEqual(
            to_path,
            store.init_repo(to_path, clone_from=orig_path))
        to = pygit2.Repository(to_path)
        self.assertIsNot(None, to[master_oid])
        self.assertEqual(orig_refs, to.listall_references())

        # Internally, the packs are hardlinked into a subordinate
        # alternate repo, so minimal space is used by the clone.
        self.assertTrue(
            os.path.exists(
                os.path.join(to_path, 'turnip-subordinate')))
        self.assertAllLinkCounts(2, orig_objs)

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

from turnip.api import store
from turnip.api.tests.test_helpers import (
    open_repo,
    RepoFactory,
    )


class StoreTestCase(TestCase):

    def setUp(self):
        super(StoreTestCase, self).setUp()
        self.repo_store = self.useFixture(TempDir()).path
        self.useFixture(EnvironmentVariable("REPO_STORE", self.repo_store))
        self.repo_path = os.path.join(self.repo_store, uuid.uuid1().hex)

    def assert_alternate_exists(self, alternate_path, repo_path):
        """Assert alternate_path exists in alternates at repo_path."""
        objects_path = '{}\n'.format(
            os.path.join(alternate_path, 'objects'))
        with open(store.alternates_path(repo_path), 'r') as alts:
            alts_content = alts.read()
            self.assertIn(objects_path, alts_content)

    def test_open_ephemeral_repo(self):
        """Opening a repo where a repo name contains ':' should return
        a new ephemeral repo.
        """
        repos = [uuid.uuid4().hex, uuid.uuid4().hex]
        repo_name = '{}:{}'.format(repos[0], repos[1])
        repo_path = os.path.join(self.repo_store, repo_name)
        alt_path = os.path.join(self.repo_store, repos[0])
        repo = store.open_repo(repo_path)
        self.assert_alternate_exists(alt_path, repo.path)
        self.assertTrue(repo.ephemeral)

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


if __name__ == '__main__':
    unittest.main()

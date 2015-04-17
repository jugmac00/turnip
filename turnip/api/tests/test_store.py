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

from turnip.api.store import (
    alternates_path,
    init_repo
    )
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

    def test_repo_with_alternates(self):
        """Ensure object path is defined correctly in repo alternates."""
        factory = RepoFactory(self.repo_path)
        new_repo_path = os.path.join(self.repo_store, uuid.uuid1().hex)
        repo_path_with_alt = init_repo(
            new_repo_path, alternate_repo_paths=[factory.repo.path])
        object_path = '{}\n'.format(
            os.path.join(factory.repo.path, 'objects'))
        with open(alternates_path(repo_path_with_alt), 'r') as alts:
            alts_content = alts.read()
            self.assertEquals(object_path, alts_content)

    def test_repo_alternates_objects_shared(self):
        """Ensure objects are shared from alternate repo."""
        factory = RepoFactory(self.repo_path)
        commit_oid = factory.add_commit('foo', 'foobar.txt')
        new_repo_path = os.path.join(self.repo_store, uuid.uuid4().hex)
        repo_path_with_alt = init_repo(
            new_repo_path, alternate_repo_paths=[factory.repo.path])
        repo_with_alt = open_repo(repo_path_with_alt)
        self.assertEqual(commit_oid.hex, repo_with_alt.get(commit_oid).hex)


if __name__ == '__main__':
    unittest.main()

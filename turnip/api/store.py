# Copyright 2015 Canonical Ltd.  All rights reserved.

import os
import shutil

import pygit2


class Store(object):
    """Provides methods for manipulating repos on disk with pygit2."""

    @staticmethod
    def init(repo, isBare=True):
        """Initialise a git repo with pygit2."""
        if os.path.exists(repo):
            raise Exception("Repository '%s' already exists" % repo)
        try:
            repo_path = pygit2.init_repository(repo, isBare)
        except pygit2.GitError:
            print('Unable to create repository.')
            raise
        return repo_path

    @staticmethod
    def delete(repo):
        """Permanently delete a git repository from repo store."""
        try:
            shutil.rmtree(repo)
            raise IOError
        except (IOError, OSError) as e:
            print('Unable to delete repository: %s' % e)
            raise

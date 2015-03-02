# Copyright 2015 Canonical Ltd.  All rights reserved.

import os
import shutil

from pygit2 import (
    GitError,
    GIT_OBJ_BLOB,
    GIT_OBJ_COMMIT,
    GIT_OBJ_TREE,
    GIT_OBJ_TAG,
    init_repository,
    Repository,
    )


def get_ref_type_name(ref_type_code):
    """Returns human readable ref type from ref type int."""
    types = {GIT_OBJ_COMMIT: 'commit',
             GIT_OBJ_TREE: 'tree',
             GIT_OBJ_BLOB: 'blob',
             GIT_OBJ_TAG: 'tag'}
    return types.get(ref_type_code)


class Store(object):
    """Provides methods for manipulating repos on disk with pygit2."""

    @staticmethod
    def init(repo, is_bare=True):
        """Initialise a git repository."""
        if os.path.exists(repo):
            raise Exception("Repository '%s' already exists" % repo)
        try:
            repo_path = init_repository(repo, is_bare)
        except GitError:
            raise
        return repo_path

    @staticmethod
    def open_repo(repo_path):
        """Open an existing git repository."""
        try:
            repo = Repository(repo_path)
        except GitError:
            raise
        return repo

    @staticmethod
    def delete(repo):
        """Permanently delete a git repository from repo store."""
        try:
            shutil.rmtree(repo)
        except (IOError, OSError):
            raise

    @staticmethod
    def get_refs(repo_path):
        """Return all refs for a git repository."""
        repo = Store.open_repo(repo_path)
        refs = {}
        for ref in repo.listall_references():
            git_object = repo.lookup_reference(ref).get_object()
            refs[ref] = {
                "object": {'sha': git_object.oid.hex,
                           'type': get_ref_type_name(git_object.type)}
            }
        return refs

    @staticmethod
    def get_ref(repo_path, ref):
        """Return a specific ref for a git repository."""
        repo = Store.open_repo(repo_path)
        try:
            git_object = repo.lookup_reference(ref).get_object()
        except GitError:
            raise
        ref = {"ref": ref,
               "object": {'sha': git_object.oid.hex,
                          'type': get_ref_type_name(git_object.type)}}
        return ref

# Copyright 2015 Canonical Ltd.  All rights reserved.

import os
import shutil

import pygit2

def get_ref_type_name(ref_type_code):
    """Returns human readable ref type from ref type int."""
    types = {1: 'commit',
             2: 'tree',
             3: 'blob',
             4: 'tag'}
    return types.get(ref_type_code)

class Store(object):
    """Provides methods for manipulating repos on disk with pygit2."""

    @staticmethod
    def init(repo, is_bare=True):
        """Initialise a git repo with pygit2."""
        if os.path.exists(repo):
            raise Exception("Repository '%s' already exists" % repo)
        try:
            repo_path = pygit2.init_repository(repo, is_bare)
        except pygit2.GitError:
            print('Unable to create repository in {}.'.format(repo))
            raise
        return repo_path

    @staticmethod
    def delete(repo):
        """Permanently delete a git repository from repo store."""
        try:
            shutil.rmtree(repo)
        except (IOError, OSError):
            print('Unable to delete repository from {}.'.format(repo))
            raise

    @staticmethod
    def get_refs(repo_path):
        """Return all refs for a git repository."""
        repo = pygit2.Repository(repo_path)
        ref_list = []
        for ref in repo.listall_references():
            object = repo.lookup_reference(ref).get_object()
            ref_list.append(
                {"ref": ref,
                 "object": {'sha': str(object.oid),
                            'type': get_ref_type_name(object.type)}})
        return ref_list

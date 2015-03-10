# Copyright 2015 Canonical Ltd.  All rights reserved.

import os
import shutil

from pygit2 import (
    GIT_OBJ_BLOB,
    GIT_OBJ_COMMIT,
    GIT_OBJ_TREE,
    GIT_OBJ_TAG,
    init_repository,
    Repository,
    )


REF_TYPE_NAME = {
    GIT_OBJ_COMMIT: 'commit',
    GIT_OBJ_TREE: 'tree',
    GIT_OBJ_BLOB: 'blob',
    GIT_OBJ_TAG: 'tag'
    }


def format_ref(ref, git_object):
    return {
        ref: {
            "object": {
                'sha1': git_object.oid.hex,
                'type': REF_TYPE_NAME[git_object.type]
                }
        }
    }


def init_repo(repo, is_bare=True):
    """Initialise a git repository."""
    if os.path.exists(repo):
        raise Exception("Repository '%s' already exists" % repo)
    repo_path = init_repository(repo, is_bare)
    return repo_path


def open_repo(repo_path):
    """Open an existing git repository."""
    repo = Repository(repo_path)
    return repo


def delete_repo(repo):
    """Permanently delete a git repository from repo store."""
    shutil.rmtree(repo)


def get_refs(repo_path):
    """Return all refs for a git repository."""
    repo = open_repo(repo_path)
    refs = {}
    for ref in repo.listall_references():
        git_object = repo.lookup_reference(ref).peel()
        # Filter non utf-8 encodable refs from refs collection
        try:
            ref.decode('utf-8')
        except UnicodeDecodeError:
            pass
        else:
            refs.update(format_ref(ref, git_object))
    return refs


def get_ref(repo_path, ref):
    """Return a specific ref for a git repository."""
    repo = open_repo(repo_path)
    git_object = repo.lookup_reference(ref).peel()
    ref_obj = format_ref(ref, git_object)
    return ref_obj

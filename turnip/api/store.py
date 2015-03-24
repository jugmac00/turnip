# Copyright 2015 Canonical Ltd.  All rights reserved.

import itertools
import os
import shutil
import urllib
import urlparse

from pygit2 import (
    GitError,
    GIT_OBJ_BLOB,
    GIT_OBJ_COMMIT,
    GIT_OBJ_TREE,
    GIT_OBJ_TAG,
    clone_repository,
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
    """Return a formatted object dict from a ref."""
    return {
        ref: {
            "object": {
                'sha1': git_object.oid.hex,
                'type': REF_TYPE_NAME[git_object.type]
                }
            }
        }


def format_commit(git_object):
    """Return a formatted commit object dict."""
    if git_object.type != GIT_OBJ_COMMIT:
        raise GitError('Invalid type: object {} is not a commit.'.format(
            git_object.oid.hex))
    parents = [parent.hex for parent in git_object.parent_ids]
    return {
        'sha1': git_object.oid.hex,
        'message': git_object.message,
        'author': format_signature(git_object.author),
        'committer': format_signature(git_object.committer),
        'parents': parents,
        'tree': git_object.tree.hex
        }


def format_signature(signature):
    """Return a formatted signature dict."""
    return {
        'name': signature.name,
        'email': signature.email,
        'time': signature.time
        }


def is_valid_new_path(path):
    """Verify repo path is new, or raise Exception."""
    if os.path.exists(path):
        raise Exception("Repository '%s' already exists" % path)
    return True


def init_repo(repo_path, clone_path=None, is_bare=True):
    """Initialise a new git repository or clone from existing."""
    if clone_path:
        assert is_valid_new_path(clone_path)
        repo_url = urlparse.urljoin('file:', urllib.pathname2url(repo_path))
        repo = clone_repository(repo_url, clone_path, is_bare)
    else:
        assert is_valid_new_path(repo_path)
        repo = init_repository(repo_path, is_bare)
    return repo.path


def open_repo(repo_path):
    """Open an existing git repository."""
    return Repository(repo_path)


def delete_repo(repo_path):
    """Permanently delete a git repository from repo store."""
    shutil.rmtree(repo_path)


def get_refs(repo_path):
    """Return all refs for a git repository."""
    repo = open_repo(repo_path)
    refs = {}
    for ref in repo.listall_references():
        git_object = repo.lookup_reference(ref).peel()
        # Filter non-unicode refs, as refs are treated as unicode
        # given json is unable to represent arbitrary byte strings.
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


def get_log(repo_path, start=None, limit=None, stop=None):
    """Return a commit collection from HEAD or optionally a start oid.

    :param start: sha1 or branch to start listing commits from.
    :param limit: limit number of commits to return.
    :param stop: ignore a commit (and its ancestors).
    """
    repo = open_repo(repo_path)
    if not start:
        start = repo.head.target  # walk from HEAD
    walker = repo.walk(start)
    if stop:
        walker.hide(stop)  # filter stop sha1 and its ancestors
    if limit > 0:
        walker = itertools.islice(walker, limit)
    commits = [format_commit(commit) for commit in walker]
    return commits


def get_commit(repo_path, commit_oid, repo=None):
    """Return a single commit object from an oid."""
    if not repo:
        repo = open_repo(repo_path)
    git_object = repo.get(commit_oid)
    commit = format_commit(git_object)
    return commit


def get_commits(repo_path, commit_oids):
    """Return a collection of commit objects from a list of oids."""
    repo = open_repo(repo_path)
    commits = [get_commit(repo_path, commit, repo) for commit in commit_oids]
    return commits

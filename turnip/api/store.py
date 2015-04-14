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
        raise GitError("Repository '%s' already exists" % path)
    return True


def init_repo(repo_path, clone_from=None, is_bare=True):
    """Initialise a new git repository or clone from existing."""
    assert is_valid_new_path(repo_path)
    if clone_from:
        clone_from_url = urlparse.urljoin('file:',
                                          urllib.pathname2url(clone_from))
        repo = clone_repository(clone_from_url, repo_path, is_bare)
    else:
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
    git_object = repo.lookup_reference(ref.encode('utf-8')).peel()
    ref_obj = format_ref(ref, git_object)
    return ref_obj


def get_common_ancestor_diff(repo_path, sha1_target, sha1_source,
                             context_lines=3):
    """Get diff of common ancestor and source diff.

    :param sha1_target: target sha1 for merge base.
    :param sha1_source: source sha1 for merge base.
    :param context_lines: num unchanged lines that define a hunk boundary.
    """
    repo = open_repo(repo_path)
    common_ancestor = repo.merge_base(sha1_target, sha1_source)
    return get_diff(repo_path, common_ancestor, sha1_source)


def get_diff(repo_path, sha1_from, sha1_to, context_lines=3):
    """Get patch and associated commits of two sha1s.

    :param sha1_from: diff from sha1.
    :param sha1_to: diff to sha1.
    :param context_lines: num unchanged lines that define a hunk boundary.
    """
    repo = open_repo(repo_path)
    shas = [sha1_from, sha1_to]
    commits = [get_commit(repo_path, sha, repo) for sha in shas]
    diff = {
        'commits': commits,
        'patch': repo.diff(commits[0]['sha1'], commits[1]['sha1'],
                           False, 0, context_lines).patch
        }
    return diff


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
    if git_object is None:
        raise GitError('Object {} does not exist in repository {}.'.format(
            commit_oid, repo_path))
    commit = format_commit(git_object)
    return commit


def get_commits(repo_path, commit_oids):
    """Return a collection of commit objects from a list of oids."""
    repo = open_repo(repo_path)
    commits = []
    for commit in commit_oids:
        try:
            commits.append(get_commit(repo_path, commit, repo))
        except GitError:
            pass
    return commits

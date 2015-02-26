# Copyright 2015 Canonical Ltd.  All rights reserved.

from pygit2 import (
    init_repository,
    GIT_OBJ_COMMIT,
    Signature,
    )

AUTHOR = Signature('Test Author', 'author@bar.com')
COMMITTER = Signature('Test Commiter', 'committer@bar.com')

def create_commit(repo, parents=[]):
    tree = repo.TreeBuilder().write()
    commit = repo.create_commit(
        'refs/heads/master',
        AUTHOR, COMMITTER, 'test commit.',
        tree,
        parents  # parent
    )
    return commit

def create_tag(repo):
    oid = repo.head.get_object().oid
    tag = repo.create_tag(
        'test-tag', oid, GIT_OBJ_COMMIT, COMMITTER, 'test tag')
    return tag

def init_repo(repo_path, commits=None, tags=None):
    repo = init_repository(repo_path, True)
    if commits:
        create_commit(repo)
    if tags:
        create_tag(repo)
    return repo

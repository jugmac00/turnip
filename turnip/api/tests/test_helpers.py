# Copyright 2015 Canonical Ltd.  All rights reserved.

from pygit2 import (
    init_repository,
    GIT_OBJ_COMMIT,
    Signature,
    )

AUTHOR = Signature('Test Author', 'author@bar.com')
COMMITTER = Signature('Test Commiter', 'committer@bar.com')


def create_commits(repo, commits, parents=[]):
    tree = repo.TreeBuilder().write()
    for commit in commits:
        commit = repo.create_commit(
            commit['ref'],
            AUTHOR, COMMITTER, commit['message'],
            tree,
            parents
        )
    return repo


def create_tags(repo, tags):
    oid = repo.head.get_object().oid
    for tag in tags:
        tag = repo.create_tag(
            tag['name'], oid, GIT_OBJ_COMMIT, COMMITTER, tag['message'])
    return repo


def init_repo(repo_path, commits=None, tags=None):
    repo = init_repository(repo_path, True)
    if commits:
        repo = create_commits(repo, commits)
    if tags:
        repo = create_tags(repo, tags)
    return repo

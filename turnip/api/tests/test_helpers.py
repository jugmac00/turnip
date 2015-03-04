# Copyright 2015 Canonical Ltd.  All rights reserved.

import os

from pygit2 import (
    init_repository,
    GIT_OBJ_COMMIT,
    GIT_SORT_TIME,
    Signature,
    )


class RepoFactory():
    """Builds a git repository in a user defined state."""

    def __init__(self, repo_store=None, num_commits=1, num_tags=1):
        self.AUTHOR = Signature('Test Author', 'author@bar.com')
        self.COMMITTER = Signature('Test Commiter', 'committer@bar.com')
        self.num_commits = num_commits
        self.num_tags = num_tags
        self.repo_store = repo_store
        self.repo = self.init_repo()

    @property
    def commits(self):
        last = self.repo[self.repo.head.target]
        return list(self.repo.walk(last.id, GIT_SORT_TIME))

    def add_commits(self):
        repo = self.repo

        parents = []
        for i in xrange(self.num_commits):
            test_file = 'test.txt'
            with open(os.path.join(self.repo_store, test_file), 'w') as f:
                f.write(b'commit {}'.format(i))

            # stage
            repo.index.add(test_file)
            repo.index.write()
            tree = repo.index.write_tree()

            # commit
            commit_oid = self.repo.create_commit(
                'refs/heads/master',
                self.AUTHOR, self.COMMITTER, 'commit {}'.format(i),
                tree,
                parents
            )
            commit = repo.get(commit_oid)
            parents = [commit.id]

    def add_tags(self):
        repo = self.repo
        oid = repo.head.get_object().oid
        for i in xrange(self.num_tags):
            repo.create_tag('tag{}'.format(i), oid, GIT_OBJ_COMMIT,
                            self.COMMITTER, 'tag message {}'.format(i))

    def init_repo(self):
        return init_repository(self.repo_store)

    def build(self):
        self.add_commits()
        self.add_tags()
        return self.repo

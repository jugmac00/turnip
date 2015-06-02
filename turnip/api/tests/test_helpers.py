# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import contextlib
import fnmatch
import itertools
import os
import urllib
import urlparse
import uuid

from pygit2 import (
    clone_repository,
    init_repository,
    GIT_FILEMODE_BLOB,
    GIT_OBJ_COMMIT,
    GIT_SORT_TIME,
    IndexEntry,
    Repository,
    Signature,
    )


def get_revlist(repo):
    """Return revlist for a given pygit2 repo object."""
    return [commit.oid.hex for commit in repo.walk(repo.head.target)]


def open_repo(repo_path):
    """Return a pygit2 repo object for a given path."""
    return Repository(repo_path)


@contextlib.contextmanager
def chdir(dirname=None):
    curdir = os.getcwd()
    try:
        if dirname is not None:
            os.chdir(dirname)
        yield
    finally:
        os.chdir(curdir)


class RepoFactory():
    """Builds a git repository in a user defined state."""

    def __init__(self, repo_path=None, num_commits=None,
                 num_branches=None, num_tags=None, clone_from=None):
        self.author = Signature('Test Author', 'author@bar.com')
        self.branches = []
        self.committer = Signature('Test Commiter', 'committer@bar.com')
        self.commits = []
        self.num_branches = num_branches
        self.num_commits = num_commits
        self.num_tags = num_tags
        self.repo_path = repo_path
        self.pack_dir = os.path.join(repo_path, '.git', 'objects', 'pack')
        if clone_from:
            self.repo = self.clone_repo(clone_from)
        else:
            self.repo = self.init_repo()

    @property
    def commits(self):
        """Walk repo from HEAD and returns list of commit objects."""
        last = self.repo[self.repo.head.target]
        return list(self.repo.walk(last.id, GIT_SORT_TIME))

    @property
    def packs(self):
        """Return list of pack files."""
        return [filename for filename in fnmatch.filter(
            os.listdir(self.pack_dir), '*.pack')]

    def add_commit(self, blob_content, file_path, parents=[],
                   ref=None, author=None, committer=None):
        """Create a commit from blob_content and file_path."""
        repo = self.repo
        if not author:
            author = self.author
        if not committer:
            committer = self.committer
        blob_oid = repo.create_blob(blob_content)
        blob_entry = IndexEntry(file_path, blob_oid, GIT_FILEMODE_BLOB)
        repo.index.add(blob_entry)
        tree_id = repo.index.write_tree()
        oid = repo.create_commit(ref, author, committer,
                                 blob_content, tree_id, parents)
        self.set_head(oid)  # set master
        return oid

    def set_head(self, oid):
        try:
            master_ref = self.repo.lookup_reference('refs/heads/master')
        except KeyError:
            master_ref = self.repo.create_reference('refs/heads/master', oid)
        finally:
            master_ref.set_target(oid)

    def add_branch(self, name, oid):
        commit = self.repo.get(oid)
        branch = self.repo.create_branch('branch-{}'.format(name), commit)
        self.branches.append(branch)
        return branch

    def add_tag(self, tag_name, tag_message, oid):
        """Create a tag from tag_name and oid."""
        repo = self.repo
        repo.create_tag(tag_name, oid, GIT_OBJ_COMMIT,
                        self.committer, tag_message)

    def makeSignature(self, name, email, encoding='utf-8'):
        """Return an author or committer signature."""
        return Signature(name, email, encoding=encoding)

    def stage(self, file_path):
        """Stage a file and return a tree id."""
        repo = self.repo
        repo.index.add(file_path)
        repo.index.write()
        return repo.index.write_tree()

    def generate_commits(self, num_commits, parents=[]):
        """Generate n number of commits."""
        for i in xrange(num_commits):
            blob_content = b'commit {} - {}'.format(i, uuid.uuid1())
            test_file = 'test.txt'
            with open(os.path.join(self.repo_path, test_file), 'w') as f:
                f.write(blob_content)
            self.stage(test_file)
            commit_oid = self.add_commit(blob_content, test_file, parents)
            self.commits.append(commit_oid)
            parents = [commit_oid]
            if i == num_commits - 1:
                ref = 'refs/heads/master'
                try:
                    self.repo.lookup_reference(ref)
                except KeyError:
                    self.repo.create_reference(ref, commit_oid)
                self.repo.set_head(commit_oid)

    def generate_tags(self, num_tags):
        """Generate n number of tags."""
        repo = self.repo
        oid = repo.head.get_object().oid
        for i in xrange(num_tags):
            self.add_tag('tag{}'.format(i), 'tag message {}'.format(i), oid)

    def generate_branches(self, num_branches, num_commits):
        """Generate n number of branches with n commits."""
        repo = self.repo
        parents = []
        for i in xrange(num_branches):
            self.generate_commits(num_commits, parents)
            oid = repo.revparse_single('HEAD')
            branch = repo.create_branch('branch-{}'.format(i), oid)
            self.branches.append(branch)
            parents.append(self.commits[0])

    def nonexistent_oid(self):
        """Return an arbitrary OID that does not exist in this repo."""
        for oid_chars in itertools.product('0123456789abcdef', repeat=40):
            oid = ''.join(oid_chars)
            if oid not in self.repo:
                return oid
        raise Exception("repo appears to contain every possible OID!")

    def init_repo(self):
        return init_repository(self.repo_path)

    def clone_repo(self, repo_factory):
        """Return a pygit2 repo object cloned from an existing factory repo."""
        clone_from_url = urlparse.urljoin(
            'file:', urllib.pathname2url(repo_factory.repo.path))
        return clone_repository(clone_from_url, self.repo_path)

    def build(self):
        """Return a repo, optionally with generated commits and tags."""
        if self.num_branches:
            self.generate_branches(self.num_branches, self.num_commits)
        if not self.num_branches and self.num_commits:
            self.generate_commits(self.num_commits)
        if self.num_tags:
            self.generate_tags(self.num_tags)
        return self.repo

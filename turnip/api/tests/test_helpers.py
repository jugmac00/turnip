# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import contextlib
import fnmatch
import itertools
import logging
import os
import uuid
from subprocess import PIPE, STDOUT, CalledProcessError, Popen
from urllib.parse import urljoin
from urllib.request import pathname2url

import six
from pygit2 import (
    GIT_FILEMODE_BLOB,
    IndexEntry,
    Repository,
    Signature,
    clone_repository,
    init_repository,
)

log = logging.getLogger()


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


class RepoFactory:
    """Builds a git repository in a user defined state."""

    def __init__(
        self,
        repo_path=None,
        num_commits=None,
        num_branches=None,
        num_tags=None,
        clone_from=None,
    ):
        self.author = Signature("Test Author", "author@bar.com")
        self.branches = []
        self.commits = []
        self.committer = Signature("Test Committer", "committer@bar.com")
        self.num_branches = num_branches
        self.num_commits = num_commits
        self.num_tags = num_tags
        self.repo_path = repo_path
        self.pack_dir = os.path.join(repo_path, "objects", "pack")
        if clone_from:
            self.repo = self.clone_repo(clone_from)
        else:
            self.repo = self.init_repo()

    @property
    def packs(self):
        """Return list of pack files."""
        return [
            filename
            for filename in fnmatch.filter(os.listdir(self.pack_dir), "*.pack")
        ]

    def add_commit(
        self,
        blob_content,
        file_path,
        parents=[],
        ref=None,
        author=None,
        committer=None,
    ):
        """Create a commit from blob_content and file_path."""
        repo = self.repo
        if not author:
            author = self.author
        if not committer:
            committer = self.committer
        tree_id = self.stage(file_path, blob_content)
        oid = repo.create_commit(
            ref, author, committer, blob_content, tree_id, parents
        )
        self.set_head(oid)  # set master
        return oid

    def set_head(self, oid):
        try:
            master_ref = self.repo.references["refs/heads/master"]
        except KeyError:
            master_ref = self.repo.references.create("refs/heads/master", oid)
        finally:
            master_ref.set_target(oid)

    def add_branch(self, name, oid):
        commit = self.repo.get(oid)
        branch = self.repo.create_branch(f"branch-{name}", commit)
        self.branches.append(branch)
        return branch

    def _get_cmd_line_auth_params(self):
        return [
            "-c",
            f"user.name={self.author.name}",
            "-c",
            f"user.email={self.author.email}",
            "-c",
            f"author.name={self.author.name}",
            "-c",
            f"author.email={self.author.email}",
            "-c",
            f"committer.name={self.committer.name}",
            "-c",
            f"committer.email={self.committer.email}",
        ]

    def add_tag(self, tag_name, tag_message, oid):
        """Create a tag from tag_name and oid."""
        cmd_line = ["git", "-C", self.repo_path]
        cmd_line += self._get_cmd_line_auth_params()
        cmd_line += ["tag", "-m", tag_message, tag_name, oid.hex]
        subproc = Popen(cmd_line, stdout=PIPE, stderr=STDOUT)
        out, err = subproc.communicate()
        retcode = subproc.returncode
        if retcode:
            log.error(
                "Command %s finished with error code %s. stdout/stderr:\n%s",
                cmd_line,
                retcode,
                out,
            )
            raise CalledProcessError(retcode, cmd_line)

    def makeSignature(self, name, email, encoding="utf-8"):
        """Return an author or committer signature."""
        # email should always be str on python3, but pygit2
        # doesn't enforce the same for name.
        email = six.ensure_str(email)
        return Signature(name, email, encoding=encoding)

    def stage(self, path, content):
        """Stage a file and return a tree id."""
        self.repo.index.add(
            IndexEntry(path, self.repo.create_blob(content), GIT_FILEMODE_BLOB)
        )
        return self.repo.index.write_tree()

    def generate_commits(self, num_commits, parents=[]):
        """Generate n number of commits."""
        for i in range(num_commits):
            blob_content = (
                b"commit "
                + str(i).encode("ascii")
                + b" - "
                + uuid.uuid1().hex.encode("ascii")
            )
            test_file = "test.txt"
            self.stage(test_file, blob_content)
            commit_oid = self.add_commit(blob_content, test_file, parents)
            self.commits.append(commit_oid)
            parents = [commit_oid]
            if i == num_commits - 1:
                ref = "refs/heads/master"
                try:
                    self.repo.references[ref]
                except KeyError:
                    self.repo.references.create(ref, commit_oid)
                self.repo.set_head(commit_oid)

    def generate_tags(self, num_tags):
        """Generate n number of tags."""
        repo = self.repo
        oid = repo.head.peel().oid
        for i in range(num_tags):
            self.add_tag(f"tag{i}", f"tag message {i}", oid)

    def generate_branches(self, num_branches, num_commits):
        """Generate n number of branches with n commits."""
        repo = self.repo
        parents = []
        for i in range(num_branches):
            self.generate_commits(num_commits, parents)
            oid = repo.revparse_single("HEAD")
            branch = repo.create_branch(f"branch-{i}", oid)
            self.branches.append(branch)
            parents.append(self.commits[0])

    def nonexistent_oid(self):
        """Return an arbitrary OID that does not exist in this repo."""
        for oid_chars in itertools.product("0123456789abcdef", repeat=40):
            oid = "".join(oid_chars)
            if oid not in self.repo:
                return oid
        raise Exception("repo appears to contain every possible OID!")

    def init_repo(self):
        return init_repository(self.repo_path, bare=True)

    def clone_repo(self, repo_factory):
        """Return a pygit2 repo object cloned from an existing factory repo."""
        clone_from_url = urljoin("file:", pathname2url(repo_factory.repo.path))
        return clone_repository(clone_from_url, self.repo_path, bare=False)

    def build(self):
        """Return a repo, optionally with generated commits and tags."""
        if self.num_branches:
            self.generate_branches(self.num_branches, self.num_commits)
        if not self.num_branches and self.num_commits:
            self.generate_commits(self.num_commits)
        if self.num_tags:
            self.generate_tags(self.num_tags)
        return self.repo

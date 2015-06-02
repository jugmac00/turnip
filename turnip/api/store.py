# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from contextlib2 import (
    contextmanager,
    ExitStack,
    )
import itertools
import os
import shutil
import subprocess
import uuid

from pygit2 import (
    clone_repository,
    GitError,
    GIT_FILEMODE_BLOB,
    GIT_OBJ_BLOB,
    GIT_OBJ_COMMIT,
    GIT_OBJ_TREE,
    GIT_OBJ_TAG,
    IndexEntry,
    init_repository,
    Repository,
    )

from turnip.pack.helpers import ensure_config


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


def is_bare_repo(repo_path):
    return not os.path.exists(os.path.join(repo_path, '.git'))


def is_valid_new_path(path):
    """Verify repo path is new, or raise Exception."""
    if os.path.exists(path):
        raise GitError("Repository '%s' already exists" % path)
    return True


def alternates_path(repo_path):
    """Git object alternates path.
    See http://git-scm.com/docs/gitrepository-layout
    """
    return os.path.join(repo_path, 'objects', 'info', 'alternates')


def write_alternates(repo_path, alternate_repo_paths):
    with open(alternates_path(repo_path), "w") as f:
        for path in alternate_repo_paths:
            if is_bare_repo(path):
                objects_path = os.path.join(path, 'objects')
            else:
                objects_path = os.path.join(path, '.git', 'objects')
            f.write("{}\n".format(objects_path))


def init_repo(repo_path, clone_from=None, clone_refs=False,
              alternate_repo_paths=None, is_bare=True):
    """Initialise a new git repository or clone from existing."""
    assert is_valid_new_path(repo_path)
    init_repository(repo_path, is_bare)

    if clone_from:
        # The clone_from's objects and refs are in fact cloned into a
        # subordinate tree that's then set as an alternate for the real
        # repo. This lets git-receive-pack expose available commits as
        # extra haves without polluting refs in the real repo.
        sub_path = os.path.join(repo_path, 'turnip-subordinate')
        clone_repository(clone_from, sub_path, True)
        assert is_bare
        alt_path = os.path.join(repo_path, 'objects/info/alternates')
        with open(alt_path, 'w') as f:
            f.write('../turnip-subordinate/objects\n')

        if clone_refs:
            # With the objects all accessible via the subordinate, we
            # can just copy all refs from the origin. Unlike
            # pygit2.clone_repository, this won't set up a remote.
            # TODO: Filter out internal (eg. MP) refs.
            from_repo = Repository(clone_from)
            to_repo = Repository(repo_path)
            for ref in from_repo.listall_references():
                to_repo.create_reference(
                    ref, from_repo.lookup_reference(ref).target)

    if alternate_repo_paths:
        write_alternates(repo_path, alternate_repo_paths)
    ensure_config(repo_path)  # set repository configuration defaults
    return repo_path


@contextmanager
def open_repo(repo_store, repo_name):
    """Open an existing git repository. Optionally create an ephemeral
    repository with alternates if repo_path contains ':'.

    :param repo_store: path to repository root.
    :param repo_name: repository name.
    """
    if ':' in repo_name:
        try:
            # create ephemeral repo with alternates set from both
            repos = [os.path.join(repo_store, repo)
                     for repo in repo_name.split(':')]
            tmp_repo_path = os.path.join(repo_store,
                                         'ephemeral-' + uuid.uuid4().hex)
            ephemeral_repo_path = init_repo(
                tmp_repo_path,
                alternate_repo_paths=repos)
            repo = Repository(ephemeral_repo_path)
            yield repo
        finally:
            delete_repo(ephemeral_repo_path)
    else:
        repo_path = os.path.join(repo_store, repo_name)
        yield Repository(repo_path)


def get_default_branch(repo_path):
    repo = Repository(repo_path)
    return repo.lookup_reference('HEAD').target


def set_default_branch(repo_path, target):
    repo = Repository(repo_path)
    repo.set_head(target)


def delete_repo(repo_path):
    """Permanently delete a git repository from repo store."""
    shutil.rmtree(repo_path)


def repack(repo_path, ignore_alternates=False, single=False,
           prune=False, no_reuse_delta=False, window=None, depth=None):
    """Repack a repository with git-repack.

    :param ignore_alternates: Only repack local refs (git repack --local).
    :param single: Create a single packfile (git repack -a).
    :param prune: Remove redundant packs. (git repack -d)
    :param no_reuse_delta: Force delta recalculation.
    """
    ensure_config(repo_path)

    repack_args = ['git', 'repack', '-q']
    if ignore_alternates:
        repack_args.append('-l')
    if no_reuse_delta:
        repack_args.append('-f')
    if prune:
        repack_args.append('-d')
    if single:
        repack_args.append('-a')
    if window:
        repack_args.append('--window', window)
    if depth:
        repack_args.append('--depth', depth)

    return subprocess.check_call(
        repack_args, cwd=repo_path,
        stderr=subprocess.PIPE, stdout=subprocess.PIPE)


def get_refs(repo_store, repo_name):
    """Return all refs for a git repository."""
    with open_repo(repo_store, repo_name) as repo:
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


def get_ref(repo_store, repo_name, ref):
    """Return a specific ref for a git repository."""
    with open_repo(repo_store, repo_name) as repo:
        git_object = repo.lookup_reference(ref.encode('utf-8')).peel()
        ref_obj = format_ref(ref, git_object)
        return ref_obj


def get_common_ancestor_diff(repo_store, repo_name, sha1_target, sha1_source,
                             context_lines=3):
    """Get diff of common ancestor and source diff.

    :param sha1_target: target sha1 for merge base.
    :param sha1_source: source sha1 for merge base.
    :param context_lines: num unchanged lines that define a hunk boundary.
    """
    with open_repo(repo_store, repo_name) as repo:
        common_ancestor = repo.merge_base(sha1_target, sha1_source)
        return get_diff(repo_store, repo_name, common_ancestor,
                        sha1_source, context_lines)


def get_merge_diff(repo_store, repo_name, sha1_base,
                   sha1_head, context_lines=3):
    """Get diff of common ancestor and source diff.

    :param sha1_base: target sha1 for merge.
    :param sha1_head: source sha1 for merge.
    :param context_lines: num unchanged lines that define a hunk boundary.
    """
    with open_repo(repo_store, repo_name) as repo:
        merged_index = repo.merge_commits(sha1_base, sha1_head)
        conflicts = set()
        if merged_index.conflicts is not None:
            for conflict in list(merged_index.conflicts):
                path = [entry for entry in conflict
                        if entry is not None][0].path
                conflicts.add(path)
                merged_file = repo.merge_file_from_index(*conflict)
                blob_oid = repo.create_blob(merged_file)
                merged_index.add(IndexEntry(path, blob_oid, GIT_FILEMODE_BLOB))
                del merged_index.conflicts[path]
        patch = merged_index.diff_to_tree(
            repo[sha1_base].tree, context_lines=context_lines).patch
        if patch is None:
            patch = u''
        shas = [sha1_base, sha1_head]
        commits = [get_commit(repo_store, repo_name, sha, repo)
                   for sha in shas]
        diff = {'commits': commits, 'patch': patch,
                'conflicts': sorted(conflicts)}
        return diff


def get_diff(repo_store, repo_name, sha1_from, sha1_to, context_lines=3):
    """Get patch and associated commits of two sha1s.

    :param sha1_from: diff from sha1.
    :param sha1_to: diff to sha1.
    :param context_lines: num unchanged lines that define a hunk boundary.
    """
    with open_repo(repo_store, repo_name) as repo:
        shas = [sha1_from, sha1_to]
        commits = [get_commit(repo_store, repo_name, sha, repo)
                   for sha in shas]
        patch = repo.diff(
            commits[0]['sha1'], commits[1]['sha1'], False, 0,
            context_lines).patch
        if patch is None:
            patch = u''
        diff = {
            'commits': commits,
            'patch': patch,
        }
        return diff


def get_log(repo_store, repo_name, start=None, limit=None, stop=None):
    """Return a commit collection from HEAD or optionally a start oid.

    :param start: sha1 or branch to start listing commits from.
    :param limit: limit number of commits to return.
    :param stop: ignore a commit (and its ancestors).
    """
    with open_repo(repo_store, repo_name) as repo:
        if not start:
            start = repo.head.target  # walk from HEAD
        walker = repo.walk(start)
        if stop:
            walker.hide(stop)  # filter stop sha1 and its ancestors
        if limit > 0:
            walker = itertools.islice(walker, limit)
        return [format_commit(commit) for commit in walker]


def get_commit(repo_store, repo_name, commit_oid, repo=None):
    """Return a single commit object from an oid."""
    with ExitStack() as stack:
        if not repo:
            repo = stack.enter_context(open_repo(repo_store, repo_name))
        git_object = repo.get(commit_oid)
        if git_object is None:
            raise GitError('Object {} does not exist in repository {}.'.format(
                commit_oid, repo_name))
        return format_commit(git_object)


def get_commits(repo_store, repo_name, commit_oids):
    """Return a collection of commit objects from a list of oids."""
    with open_repo(repo_store, repo_name) as repo:
        commits = []
        for commit in commit_oids:
            try:
                commits.append(get_commit(repo_store, repo_name, commit, repo))
            except GitError:
                pass
        return commits

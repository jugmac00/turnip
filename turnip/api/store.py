# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64
import itertools
import logging
import os
import re
import shutil
import subprocess
import uuid
from collections import defaultdict

from contextlib2 import (
    contextmanager,
    ExitStack,
    )
from pygit2 import (
    GIT_OBJ_BLOB,
    GIT_OBJ_COMMIT,
    GIT_OBJ_TAG,
    GIT_OBJ_TREE,
    GIT_REF_OID,
    GIT_SORT_TOPOLOGICAL,
    GitError,
    IndexEntry,
    init_repository,
    Oid,
    Repository,
    )
import six

from turnip.config import config
from turnip.helpers import TimeoutServerProxy
from turnip.pack.helpers import ensure_config
from turnip.tasks import app, logger as tasks_logger

logger = logging.getLogger(__name__)


REF_TYPE_NAME = {
    GIT_OBJ_COMMIT: 'commit',
    GIT_OBJ_TREE: 'tree',
    GIT_OBJ_BLOB: 'blob',
    GIT_OBJ_TAG: 'tag'
    }


# Where to store repository status information inside a repository directory.
REPOSITORY_CREATING_FILE_NAME = '.turnip-creating'


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


def format_blob(blob):
    """Return a formatted blob dict."""
    if blob.type != GIT_OBJ_BLOB:
        raise GitError('Invalid type: object {} is not a blob.'.format(
            blob.oid.hex))
    return {
        'size': blob.size,
        'data': base64.b64encode(blob.data),
        }


def is_bare_repo(repo_path):
    return not os.path.exists(os.path.join(repo_path, '.git'))


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


object_dir_re = re.compile(r'\A[0-9a-f][0-9a-f]\Z')


@app.task
def fetch_refs(operations):
    """Copy a set of refs from one git repository to another.

    This is implemented now using git client's "git fetch" command,
    since it's way easier than trying to copy the refs, commits and objects
    manually using pygit.

    :param operations: A list of tuples describing the copy operations,
        in the format (from_root, from_ref, to_root, to_ref). If "to_ref" is
        None, the target ref will have the same name as the source ref.
    """
    # Group copy operations by source/dest repositories pairs.
    grouped_refs = defaultdict(set)
    for from_root, from_ref, to_root, to_ref in operations:
        grouped_refs[(from_root, to_root)].add((from_ref, to_ref))

    # A pair of (cmd, stderr) errors happened during the copy.
    errors = []
    for repo_pair, refs_pairs in grouped_refs.items():
        from_root, to_root = repo_pair
        cmd = [b'git', b'fetch', b'--no-tags', from_root]
        for src, dst in refs_pairs:
            src = six.ensure_binary(src)
            dst = six.ensure_binary(dst if dst else src)
            cmd.append(b"%s:%s" % (src, dst))

        # XXX pappacena: On Python3, this could be replaced with
        # stdout=subprocess.DEVNULL.
        with open(os.devnull, 'wb') as devnull:
            proc = subprocess.Popen(
                cmd, cwd=to_root,
                stdout=devnull, stderr=subprocess.PIPE)
            _, stderr = proc.communicate()
        if proc.returncode != 0:
            errors.append((cmd, stderr))

    if errors:
        details = "\n ".join("%s = %s" % (cmd, err) for cmd, err in errors)
        raise GitError("Error copying refs: %s" % details)


@app.task
def delete_refs(operations):
    """Remove refs from repositories.

    :param operations: A list of tuples (repo_root, ref_name) to be deleted.
    """
    repos = {}
    for repo_root, ref_name in operations:
        if repo_root not in repos:
            repos[repo_root] = Repository(repo_root)
        repo = repos[repo_root]
        repo.references[ref_name].delete()


def copy_refs(from_root, to_root):
    """Copy refs from one .git directory to another.

    The refs may clobber existing ones.  Refs that can be packed are
    returned as a dictionary rather than imported immediately, and should be
    written using `write_packed_refs` (this approach makes it easier to
    merge refs from multiple repositories).
    """
    from_repo = Repository(from_root)
    to_repo = Repository(to_root)
    packable_refs = {}
    for ref in from_repo.references.objects:
        if ref.type == GIT_REF_OID:
            obj = from_repo.get(ref.target)
            if obj is not None:
                if obj.type == GIT_OBJ_TAG:
                    try:
                        peeled_oid = ref.peel().id
                    except (ValueError, GitError):
                        # Fall back to leaving the ref unpacked.
                        pass
                    else:
                        packable_refs[ref.raw_name] = (obj.id, peeled_oid)
                else:
                    packable_refs[ref.raw_name] = (obj.id, None)
        if ref.raw_name not in packable_refs:
            to_repo.references.create(
                ref, from_repo.references[ref].target, force=True)
    return packable_refs


def write_packed_refs(root, packable_refs):
    """Write out a packed-refs file in one go.

    The format is undocumented except in source comments, but libgit2
    implements it as well (albeit not in a way we can use), so it should be
    safe enough to implement it here.  Doing it this way is faster over NFS
    for repositories with lots of refs than using libgit2 with its proper
    locking or even copying refs individually, since we don't incur an fsync
    for each ref.
    """
    if packable_refs:
        with open(os.path.join(root, 'packed-refs'), 'wb') as packed_refs:
            packed_refs.write(
                b'# pack-refs with: peeled fully-peeled sorted \n')
            for ref_name, (oid, peeled_oid) in sorted(packable_refs.items()):
                packed_refs.write(
                    b'%s %s\n' % (oid.hex.encode('ascii'), ref_name))
                if peeled_oid is not None:
                    packed_refs.write(
                        b'^%s\n' % (peeled_oid.hex.encode('ascii'),))


def get_file_mode(path):
    if not os.path.exists(path):
        return None
    try:
        return oct(os.stat(path).st_mode)
    except Exception:
        return None


def import_into_subordinate(sub_root, from_root, log=None):
    """Import all of a repo's objects and refs into another.

    The refs may clobber existing ones.  Refs that can be packed are
    returned as a dictionary rather than imported immediately, and should be
    written using `write_packed_refs`.
    """
    log = log if log else logger
    for dirname in os.listdir(os.path.join(from_root, 'objects')):
        # We want to hardlink any children of the loose fanout or pack
        # directories.
        if (not os.path.isdir(os.path.join(from_root, 'objects', dirname))
                or (dirname != 'pack' and not object_dir_re.match(dirname))):
            continue

        sub_dir = os.path.join(sub_root, 'objects', dirname)
        if not os.path.exists(sub_dir):
            os.makedirs(sub_dir)
        for name in os.listdir(os.path.join(from_root, 'objects', dirname)):
            from_path = os.path.join(from_root, 'objects', dirname, name)
            sub_path = os.path.join(sub_root, 'objects', dirname, name)
            if not os.path.isfile(from_path) or os.path.exists(sub_path):
                continue
            try:
                os.link(from_path, sub_path)
            except Exception as e:
                log.critical(
                    "Error in import_into_subordinate while executing "
                    "os.link(%s, %s): %s" % (from_path, sub_path, e))
                log.info(
                    "File modes: from_path: %s / from_path_dir: %s / "
                    "sub_path: %s / sub_path_dir: %s" %
                    (get_file_mode(from_path),
                     get_file_mode(os.path.dirname(from_path)),
                     get_file_mode(sub_path),
                     get_file_mode(os.path.dirname(sub_path))))
                raise

    # Copy over the refs.
    # TODO: This should ensure that we don't overwrite anything. The
    # alternate's refs are only used as extra .haves on push, so it
    # wouldn't hurt to mangle the names.
    return copy_refs(from_root, sub_root)


class AlreadyExistsError(GitError):
    """We tried to initialise a repository that already exists."""

    def __init__(self, path):
        super(AlreadyExistsError, self).__init__(
            "Repository '%s' already exists" % path)
        self.path = path


def init_repo(repo_path, clone_from=None, clone_refs=False,
              alternate_repo_paths=None, is_bare=True, log=None):
    """Initialise a new git repository or clone from existing."""
    if os.path.exists(repo_path):
        raise AlreadyExistsError(repo_path)
    # If no logger is provided, use module-level logger.
    log = log if log else logger
    log.info("Running init_repository(%s, %s)" % (repo_path, is_bare))
    init_repository(repo_path, is_bare)

    log.info("Running set_repository_creating(%s, True)" % repo_path)
    set_repository_creating(repo_path, True)

    if clone_from:
        # The clone_from's objects and refs are in fact cloned into a
        # subordinate tree that's then set as an alternate for the real
        # repo. This lets git-receive-pack expose available commits as
        # extra haves without polluting refs in the real repo.
        sub_path = os.path.join(repo_path, 'turnip-subordinate')
        log.info("Running init_repository for subordinate %s" % sub_path)
        init_repository(sub_path, True)

        packable_refs = {}
        if os.path.exists(os.path.join(clone_from, 'turnip-subordinate')):
            packable_refs.update(import_into_subordinate(
                sub_path, os.path.join(clone_from, 'turnip-subordinate'),
                log=log))
        packable_refs.update(import_into_subordinate(
            sub_path, clone_from, log=log))

        log.info("Running write_packed_refs(%s, %s)" %
                 (sub_path, packable_refs))
        write_packed_refs(sub_path, packable_refs)

    new_alternates = []
    if alternate_repo_paths:
        new_alternates.extend(alternate_repo_paths)
    if clone_from:
        new_alternates.append('../turnip-subordinate')

    log.info("Running write_alternates(%s, %s)" % (repo_path, new_alternates))
    write_alternates(repo_path, new_alternates)

    if clone_from and clone_refs:
        # With the objects all accessible via the subordinate, we
        # can just copy all refs from the origin. Unlike
        # pygit2.clone_repository, this won't set up a remote.
        # TODO: Filter out internal (eg. MP) refs.

        log.info("Running copy_refs(%s, %s)" % (clone_from, repo_path))
        packable_refs = copy_refs(clone_from, repo_path)

        log.info("Running write_packed_refs(%s, %s)" %
                 (repo_path, packable_refs))
        write_packed_refs(repo_path, packable_refs)

    log.info("Running ensure_config(%s)" % repo_path)
    ensure_config(repo_path)  # set repository configuration defaults

    log.info("Running set_repository_creating(%s, False)" % repo_path)
    set_repository_creating(repo_path, False)


@app.task
def init_and_confirm_repo(untranslated_path, repo_path, clone_from=None,
                          clone_refs=False, alternate_repo_paths=None,
                          is_bare=True):
    logger = tasks_logger
    xmlrpc_endpoint = config.get("virtinfo_endpoint")
    xmlrpc_timeout = float(config.get("virtinfo_timeout"))
    xmlrpc_auth_params = {"user": "+launchpad-services"}
    xmlrpc_proxy = TimeoutServerProxy(
        xmlrpc_endpoint, timeout=xmlrpc_timeout, allow_none=True)
    try:
        logger.info(
            "Initializing and confirming repository creation: "
            "%s; %s; %s; %s; %s", repo_path, clone_from, clone_refs,
            alternate_repo_paths, is_bare)
        init_repo(
            repo_path, clone_from, clone_refs, alternate_repo_paths, is_bare)
        logger.debug(
            "Confirming repository creation: %s; %s",
            untranslated_path, xmlrpc_auth_params)
        xmlrpc_proxy.confirmRepoCreation(untranslated_path, xmlrpc_auth_params)
    except Exception as e:
        logger.error("Error creating repository at %s: %s", repo_path, e)
        try:
            delete_repo(repo_path)
        except IOError as e:
            logger.error("Error deleting repository at %s: %s", repo_path, e)
        logger.debug(
            "Aborting repository creation: %s; %s",
            untranslated_path, xmlrpc_auth_params)
        xmlrpc_proxy.abortRepoCreation(untranslated_path, xmlrpc_auth_params)


@contextmanager
def open_repo(repo_store, repo_name, force_ephemeral=False):
    """Open an existing git repository. Optionally create an ephemeral
    repository with alternates if repo_name contains ':' or force_ephemeral
    is True.

    :param repo_store: path to repository root.
    :param repo_name: repository name.
    :param force_ephemeral: create an ephemeral repository even if repo_name
        does not contain ':'.
    """
    if force_ephemeral or ':' in repo_name:
        # Create ephemeral repo with alternates set from both.  Neither git
        # nor libgit2 will respect a relative alternate path except in the
        # root repo, so we explicitly include the turnip-subordinate for
        # each repo.  If it doesn't exist it'll just be ignored.
        repos = list(itertools.chain(*(
            (os.path.join(repo_store, repo),
             os.path.join(repo_store, repo, 'turnip-subordinate'))
            for repo in repo_name.split(':'))))
        ephemeral_repo_path = os.path.join(
            repo_store, 'ephemeral-' + uuid.uuid4().hex)
        try:
            init_repo(ephemeral_repo_path, alternate_repo_paths=repos)
            repo = Repository(ephemeral_repo_path)
            yield repo
        except AlreadyExistsError:
            # Don't clean up the repository in this case, since it already
            # existed so we didn't create it.
            raise
        except Exception:
            delete_repo(ephemeral_repo_path)
            raise
        else:
            delete_repo(ephemeral_repo_path)
    else:
        repo_path = os.path.join(repo_store, repo_name)
        yield Repository(repo_path)


def get_default_branch(repo_path):
    repo = Repository(repo_path)
    return repo.references['HEAD'].target


def set_repository_creating(repo_path, is_creating):
    if not os.path.exists(repo_path):
        raise ValueError("Repository %s does not exist." % repo_path)
    file_path = os.path.join(repo_path, REPOSITORY_CREATING_FILE_NAME)
    if is_creating:
        open(file_path, 'a').close()
    else:
        try:
            os.unlink(file_path)
        except OSError:
            pass


def is_repository_available(repo_path):
    """Checks if the repository is available (that is, if it is not in the
    middle of a clone or init operation)."""
    if not os.path.exists(repo_path):
        return False

    status_file_path = os.path.join(repo_path, REPOSITORY_CREATING_FILE_NAME)
    return not os.path.exists(status_file_path)


def set_default_branch(repo_path, target):
    repo = Repository(repo_path)
    repo.set_head(target)


def delete_repo(repo_path):
    """Permanently delete a git repository from repo store."""
    # XXX cjwatson 2018-11-20: Python 2's shutil.rmtree crashes if given a
    # Unicode path to a directory that contains non-UTF-8 files.
    if not isinstance(repo_path, bytes):
        repo_path = repo_path.encode('UTF-8')
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


def get_refs(repo_store, repo_name, exclude_prefixes=None):
    """Return all refs for a git repository."""
    with open_repo(repo_store, repo_name) as repo:
        refs = {}
        for ref_obj in repo.listall_reference_objects():
            # Filter non-unicode refs, as refs are treated as unicode
            # given json is unable to represent arbitrary byte strings.
            try:
                ref = ref_obj.name
                if isinstance(ref, bytes):
                    ref_bytes = ref
                    ref = ref.decode('utf-8')
                else:
                    ref_bytes = ref.encode('utf-8')
                git_object = repo.references[ref_bytes].peel()
            except UnicodeDecodeError:
                pass
            else:
                if not any(ref.startswith(exclude_prefix)
                           for exclude_prefix in (exclude_prefixes or [])):
                    refs.update(format_ref(ref, git_object))
        return refs


def get_ref(repo_store, repo_name, ref):
    """Return a specific ref for a git repository."""
    with open_repo(repo_store, repo_name) as repo:
        git_object = repo.references[ref.encode('utf-8')].peel()
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
        if common_ancestor is None:
            # We have no merge base.  Fall back to a ".."-style diff, just
            # like "git diff" does.
            common_ancestor = sha1_target
        return get_diff(repo_store, repo_name, common_ancestor,
                        sha1_source, context_lines)


def _add_conflicted_files(repo, index):
    """Add flattened versions of conflicted files in an index.

    Any conflicted files will be merged using
    `pygit2.Repository.merge_file_from_index` (thereby including conflict
    markers); the resulting files will be added to the index and the
    conflicts deleted.

    :param repo: a `pygit2.Repository`.
    :param index: a `pygit2.Index` to modify.
    :return: a set of files that contain conflicts.
    """
    conflicts = set()
    if index.conflicts is not None:
        for conflict in list(index.conflicts):
            conflict_entry = [
                entry for entry in conflict if entry is not None][0]
            path = conflict_entry.path
            conflicts.add(path)
            ancestor, ours, theirs = conflict

            # Skip any further check if it's a delete/delete conflict:
            # the file got renamed or deleted from both branches. Nothing to
            # merge and no useful conflict diff to generate.
            if ours is None and theirs is None:
                del index.conflicts[path]
                continue

            if ours is None or theirs is None:
                # A modify/delete conflict.  Turn the "delete" side into
                # a fake empty file so that we can generate a useful
                # conflict diff.
                empty_oid = repo.create_blob(b'')
                if ours is None:
                    ours = IndexEntry(
                        path, empty_oid, conflict_entry.mode)
                if theirs is None:
                    theirs = IndexEntry(
                        path, empty_oid, conflict_entry.mode)
            merged_file = repo.merge_file_from_index(
                ancestor, ours, theirs)
            # merge_file_from_index gratuitously decodes as UTF-8, so
            # encode it back again.
            blob_oid = repo.create_blob(merged_file.encode('utf-8'))
            index.add(IndexEntry(path, blob_oid, conflict_entry.mode))
            del index.conflicts[path]
    return conflicts


def get_merge_diff(repo_store, repo_name, sha1_base,
                   sha1_head, context_lines=3, sha1_prerequisite=None):
    """Get diff of common ancestor and source diff.

    :param sha1_base: target sha1 for merge.
    :param sha1_head: source sha1 for merge.
    :param context_lines: num unchanged lines that define a hunk boundary.
    :param sha1_prerequisite: if not None, sha1 of prerequisite commit to
        merge into `sha1_target` before computing diff to `sha1_source`.
    """
    with open_repo(
            repo_store, repo_name,
            force_ephemeral=(sha1_prerequisite is not None)) as repo:
        if sha1_prerequisite is not None:
            prerequisite_index = repo.merge_commits(
                sha1_base, sha1_prerequisite)
            _add_conflicted_files(repo, prerequisite_index)
            from_tree = repo[prerequisite_index.write_tree(repo=repo)]
        else:
            from_tree = repo[sha1_base].tree
        merged_index = repo.merge_commits(sha1_base, sha1_head)
        conflicts = _add_conflicted_files(repo, merged_index)
        diff = merged_index.diff_to_tree(
            from_tree, context_lines=context_lines)
        diff.find_similar()
        patch = diff.patch
        if patch is None:
            patch = u''
        shas = [sha1_base, sha1_head]
        commits = [get_commit(repo_store, repo_name, sha, repo)
                   for sha in shas]
        return {'commits': commits, 'patch': patch,
                'conflicts': sorted(conflicts)}


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
        diff = repo.diff(
            commits[0]['sha1'], commits[1]['sha1'], False, 0,
            context_lines)
        diff.find_similar()
        patch = diff.patch
        if patch is None:
            patch = u''
        return {
            'commits': commits,
            'patch': patch,
        }


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


def get_commit(repo_store, repo_name, revision, repo=None):
    """Return a single commit object from a revision."""
    with ExitStack() as stack:
        if not repo:
            repo = stack.enter_context(open_repo(repo_store, repo_name))
        try:
            if isinstance(revision, Oid):
                git_object = repo.get(revision)
                if git_object is None:
                    raise KeyError
            else:
                git_object = repo.revparse_single(revision)
        except KeyError:
            raise GitError('Object {} does not exist in repository {}.'.format(
                revision, repo_name))
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


def detect_merges(repo_store, repo_name, target_oid, source_oids):
    """Check whether each of the requested commits has been merged."""
    with open_repo(repo_store, repo_name) as repo:
        target = repo.get(target_oid)
        if target is None:
            raise GitError('Object {} does not exist in repository {}.'.format(
                target_oid, repo_name))
        if not source_oids:
            return {}

        search_oids = set(source_oids)
        merge_info = {}
        last_mainline = target_oid
        next_mainline = target_oid
        for commit in repo.walk(target_oid, GIT_SORT_TOPOLOGICAL):
            if commit.id.hex == next_mainline:
                last_mainline = commit.id.hex
                if commit.parent_ids:
                    next_mainline = commit.parent_ids[0].hex
                else:
                    next_mainline = None
            if commit.id.hex in search_oids:
                merge_info[commit.id.hex] = last_mainline
                search_oids.remove(commit.id.hex)
            if not search_oids:
                break
        return merge_info


def get_blob(repo_store, repo_name, rev, filename):
    """Return a blob from a revision and file name."""
    with open_repo(repo_store, repo_name) as repo:
        git_object = repo.revparse_single(rev)
        # This raises ValueError if you give it a tree or a blob to start
        # with, or GitError if it reaches a tree or a blob while recursively
        # dereferencing.  We don't really care about the difference.
        try:
            commit = git_object.peel(GIT_OBJ_COMMIT)
        except ValueError as e:
            raise GitError(str(e))
        return format_blob(repo[commit.tree[filename].id])

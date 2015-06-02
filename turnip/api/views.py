# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import os
import re
from subprocess import CalledProcessError

from cornice.resource import resource
from cornice.util import extract_json_data
from pygit2 import GitError
import pyramid.httpexceptions as exc

from turnip.config import TurnipConfig
from turnip.api import store


def is_valid_path(repo_store, repo_path):
    """Ensure path in within repo root and has not been subverted."""
    return os.path.realpath(repo_path).startswith(os.path.realpath(repo_store))


def validate_path(func):
    """Decorator validates repo path from request name and repo_store."""
    def validate_path_decorator(self):
        name = self.request.matchdict['name']
        if not name:
            self.request.errors.add('body', 'name', 'repo name is missing')
            return
        repo_path = os.path.join(self.repo_store, name)
        if not is_valid_path(self.repo_store, repo_path):
            self.request.errors.add('body', 'name', 'invalid path.')
            raise exc.HTTPInternalServerError()
        return func(self, self.repo_store, name)
    return validate_path_decorator


class BaseAPI(object):
    def __init__(self):
        config = TurnipConfig()
        self.repo_store = config.get('repo_store')


@resource(collection_path='/repo', path='/repo/{name}')
class RepoAPI(BaseAPI):
    """Provides HTTP API for repository actions."""

    def __init__(self, request):
        super(RepoAPI, self).__init__()
        self.request = request

    def collection_options(self):
        """Trivial response for the sake of haproxy."""
        pass

    def collection_post(self):
        """Initialise a new git repository, or clone from an existing repo."""
        repo_path = extract_json_data(self.request).get('repo_path')
        clone_path = extract_json_data(self.request).get('clone_from')
        clone_refs = extract_json_data(self.request).get('clone_refs', False)

        if not repo_path:
            self.request.errors.add('body', 'repo_path',
                                    'repo_path is missing')
            return
        repo = os.path.join(self.repo_store, repo_path)
        if not is_valid_path(self.repo_store, repo):
            self.request.errors.add('body', 'name', 'invalid path.')
            raise exc.HTTPNotFound()

        if clone_path:
            repo_clone = os.path.join(self.repo_store, clone_path)
        else:
            repo_clone = None

        try:
            new_repo_path = store.init_repo(
                repo, clone_from=repo_clone, clone_refs=clone_refs)
            repo_name = os.path.basename(os.path.normpath(new_repo_path))
            return {'repo_url': '/'.join([self.request.url, repo_name])}
        except GitError:
            return exc.HTTPConflict()  # 409

    @validate_path
    def get(self, repo_store, repo_name):
        """Get properties of an existing git repository."""
        repo_path = os.path.join(repo_store, repo_name)
        if not os.path.exists(repo_path):
            self.request.errors.add(
                'body', 'name', 'repository does not exist')
            raise exc.HTTPNotFound()
        return {
            'default_branch': store.get_default_branch(repo_path),
            }

    def _patch_default_branch(self, repo_path, value):
        try:
            store.set_default_branch(repo_path, value)
        except (KeyError, ValueError, GitError):
            raise exc.HTTPBadRequest()

    @validate_path
    def patch(self, repo_store, repo_name):
        """Change properties of an existing git repository."""
        repo_path = os.path.join(repo_store, repo_name)
        if not os.path.exists(repo_path):
            self.request.errors.add(
                'body', 'name', 'repository does not exist')
            raise exc.HTTPNotFound()
        data = extract_json_data(self.request)
        for key in data:
            if not hasattr(self, "_patch_%s" % key):
                self.request.errors.add('body', key, 'unknown property')
                raise exc.HTTPBadRequest()
        for key, value in data.items():
            getattr(self, "_patch_%s" % key)(repo_path, value)
        return exc.HTTPNoContent()

    @validate_path
    def delete(self, repo_store, repo_name):
        """Delete an existing git repository."""
        try:
            repo_path = os.path.join(repo_store, repo_name)
            store.delete_repo(repo_path)
        except (IOError, OSError):
            return exc.HTTPNotFound()  # 404


@resource(path='/repo/{name}/repack')
class RepackAPI(BaseAPI):
    """Provides HTTP API for repository repacking."""

    def __init__(self, request):
        super(RepackAPI, self).__init__()
        self.request = request

    @validate_path
    def post(self, repo_store, repo_name):
        repo_path = os.path.join(repo_store, repo_name)

        data = extract_json_data(self.request)
        ignore_alternates = data.get('ignore_alternates')
        no_reuse_delta = data.get('no_reuse_delta')
        prune = data.get('prune')
        single = data.get('single')
        window = data.get('window')
        depth = data.get('depth')

        try:
            store.repack(repo_path, single=single, prune=prune,
                         no_reuse_delta=no_reuse_delta,
                         ignore_alternates=ignore_alternates,
                         window=window, depth=depth)
        except (CalledProcessError):
            return exc.HTTPInternalServerError()
        return


@resource(collection_path='/repo/{name}/refs',
          path='/repo/{name}/refs/{ref:.*}')
class RefAPI(BaseAPI):
    """Provides HTTP API for git references."""

    def __init__(self, request):
        super(RefAPI, self).__init__()
        self.request = request

    @validate_path
    def collection_get(self, repo_store, repo_name):
        try:
            return store.get_refs(repo_store, repo_name)
        except (KeyError, GitError):
            return exc.HTTPNotFound()  # 404

    @validate_path
    def get(self, repo_store, repo_name):
        ref = 'refs/' + self.request.matchdict['ref']
        try:
            return store.get_ref(repo_store, repo_name, ref)
        except (KeyError, GitError):
            return exc.HTTPNotFound()


@resource(path='/repo/{name}/compare/{commits}')
class DiffAPI(BaseAPI):
    """Provides HTTP API for rev-rev 'double' and 'triple dot' diff.

    {commits} can be in the form sha1..sha1 or sha1...sha1.
    Two dots provides a simple diff, equivalent to `git diff A B`.
    Three dots provides the symmetric or common ancestor diff, equivalent
    to `git diff $(git-merge-base A B) B`.
    {name} can be two : separated repositories, for a cross repository diff.
    """
    def __init__(self, request):
        super(DiffAPI, self).__init__()
        self.request = request

    @validate_path
    def get(self, repo_store, repo_name):
        """Returns diff of two commits."""
        commits = re.split('(\.{2,3})', self.request.matchdict['commits'])
        context_lines = int(self.request.params.get('context_lines', 3))
        if not len(commits) == 3:
            return exc.HTTPBadRequest()
        try:
            diff_type = commits[1]
            args = (repo_store, repo_name, commits[0],
                    commits[2], context_lines)
            if diff_type == '..':
                patch = store.get_diff(*args)
            elif diff_type == '...':
                patch = store.get_common_ancestor_diff(*args)
        except (ValueError, GitError):
            # invalid pygit2 sha1's return ValueError: 1: Ambiguous lookup
            return exc.HTTPNotFound()
        return patch


@resource(path='/repo/{name}/compare-merge/{base}:{head}')
class DiffMergeAPI(BaseAPI):
    """Provides an HTTP API for merge previews.

    {head} will be merged into {base} and the diff from {base} returned.
    {name} can be two : separated repositories, for a cross repository diff.
    """
    def __init__(self, request):
        super(DiffMergeAPI, self).__init__()
        self.request = request

    @validate_path
    def get(self, repo_store, repo_name):
        """Returns diff of two commits."""
        context_lines = int(self.request.params.get('context_lines', 3))
        try:
            patch = store.get_merge_diff(
                repo_store, repo_name, self.request.matchdict['base'],
                self.request.matchdict['head'], context_lines)
        except (ValueError, GitError):
            # invalid pygit2 sha1's return ValueError: 1: Ambiguous lookup
            return exc.HTTPNotFound()
        return patch


@resource(collection_path='/repo/{name}/commits',
          path='/repo/{name}/commits/{sha1}')
class CommitAPI(BaseAPI):
    """Provides HTTP API for git commits."""

    def __init__(self, request):
        super(CommitAPI, self).__init__()
        self.request = request

    @validate_path
    def get(self, repo_store, repo_name):
        commit_sha1 = self.request.matchdict['sha1']
        try:
            commit = store.get_commit(repo_store, repo_name, commit_sha1)
        except GitError:
            return exc.HTTPNotFound()
        return commit

    @validate_path
    def collection_post(self, repo_store, repo_name):
        """Get commits in bulk."""
        commits = extract_json_data(self.request).get('commits')
        try:
            commits = store.get_commits(repo_store, repo_name, commits)
        except GitError:
            return exc.HTTPNotFound()
        return commits


@resource(path='/repo/{name}/log/{sha1}')
class LogAPI(BaseAPI):
    """Provides HTTP API for git logs."""

    def __init__(self, request):
        super(LogAPI, self).__init__()
        self.request = request

    @validate_path
    def get(self, repo_store, repo_name):
        """Get log by sha1, filtered by limit and stop."""
        sha1 = self.request.matchdict['sha1']
        limit = int(self.request.params.get('limit', -1))
        stop = self.request.params.get('stop')

        try:
            log = store.get_log(repo_store, repo_name, sha1, limit, stop)
        except GitError:
            return exc.HTTPNotFound()
        return log

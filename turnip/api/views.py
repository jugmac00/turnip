# Copyright 2015 Canonical Ltd.  All rights reserved.

import os

from cornice.resource import resource
from cornice.util import extract_json_data
from pygit2 import GitError
import pyramid.httpexceptions as exc

from turnip.config import TurnipConfig
from turnip.api import store


def repo_path(func):
    """Decorator builds repo path from request name and repo_store."""
    def repo_path_decorator(self):
        name = self.request.matchdict['name']
        if not name:
            self.request.errors.add('body', 'name', 'repo name is missing')
            return
        repo_path = os.path.join(self.repo_store, name)
        if not os.path.realpath(repo_path).startswith(
                os.path.realpath(self.repo_store)):
            self.request.errors.add('body', 'name', 'invalid path.')
            raise exc.HTTPInternalServerError()
        return func(self, repo_path)
    return repo_path_decorator


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
        """Initialise a new git repository."""
        repo_path = extract_json_data(self.request).get('repo_path')
        if not repo_path:
            self.request.errors.add('body', 'repo_path',
                                    'repo_path is missing')
            return
        repo = os.path.join(self.repo_store, repo_path)
        try:
            store.init_repo(repo)
        except GitError:
            return exc.HTTPConflict()  # 409

    @repo_path
    def delete(self, repo_path):
        """Delete an existing git repository."""
        try:
            store.delete_repo(repo_path)
        except (IOError, OSError):
            return exc.HTTPNotFound()  # 404


@resource(collection_path='/repo/{name}/refs',
          path='/repo/{name}/refs/{ref:.*}')
class RefAPI(BaseAPI):
    """Provides HTTP API for git references."""

    def __init__(self, request):
        super(RefAPI, self).__init__()
        self.request = request

    @repo_path
    def collection_get(self, repo_path):
        try:
            return store.get_refs(repo_path)
        except (KeyError, GitError):
            return exc.HTTPNotFound()  # 404

    @repo_path
    def get(self, repo_path):
        ref = 'refs/' + self.request.matchdict['ref']
        try:
            return store.get_ref(repo_path, ref)
        except (KeyError, GitError):
            return exc.HTTPNotFound()


@resource(collection_path='/repo/{name}/commits',
          path='/repo/{name}/commits/{sha1}')
class CommitAPI(BaseAPI):
    """Provides HTTP API for git commits."""

    def __init__(self, request):
        super(CommitAPI, self).__init__()
        self.request = request

    @repo_path
    def get(self, repo_path):
        commit_sha1 = self.request.matchdict['sha1']
        try:
            commit = store.get_commit(repo_path, commit_sha1)
        except GitError:
            return exc.HTTPNotFound()
        return commit

    @repo_path
    def collection_post(self, repo_path):
        """Get commits in bulk."""
        commits = extract_json_data(self.request).get('commits')
        try:
            commits = store.get_commits(repo_path, commits)
        except GitError:
            return exc.HTTPNotFound()
        return commits


@resource(path='/repo/{name}/log/{sha1}')
class LogAPI(BaseAPI):
    """Provides HTTP API for git logs."""

    def __init__(self, request):
        super(LogAPI, self).__init__()
        self.request = request

    @repo_path
    def get(self, repo_path):
        """Get log by sha1, filtered by limit and stop."""
        sha1 = self.request.matchdict['sha1']
        limit = int(self.request.params.get('limit', -1))
        stop = self.request.params.get('stop')

        try:
            log = store.get_log(repo_path, sha1, limit, stop)
        except GitError:
            return exc.HTTPNotFound()
        return log

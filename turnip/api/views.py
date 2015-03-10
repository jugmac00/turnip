# Copyright 2015 Canonical Ltd.  All rights reserved.

import json
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
        return func(self, repo_path)
    return repo_path_decorator


@resource(collection_path='/repo', path='/repo/{name}')
class RepoAPI(object):
    """Provides HTTP API for repository actions."""

    def __init__(self, request):
        config = TurnipConfig()
        self.request = request
        self.repo_store = config.get('repo_store')

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
class RefAPI(object):
    """Provides HTTP API for git references."""

    def __init__(self, request):
        config = TurnipConfig()
        self.request = request
        self.repo_store = config.get('repo_store')

    @repo_path
    def collection_get(self, repo_path):
        try:
            refs = store.get_refs(repo_path)
        except GitError:
            return exc.HTTPNotFound()  # 404
        return json.dumps(refs, ensure_ascii=False)

    @repo_path
    def get(self, repo_path):
        ref = 'refs/' + self.request.matchdict['ref']
        try:
            ref = store.get_ref(repo_path, ref)
        except GitError:
            return exc.HTTPNotFound()
        return json.dumps(ref, ensure_ascii=False)

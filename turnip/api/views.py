# Copyright 2015 Canonical Ltd.  All rights reserved.

import json
import os

from cornice.resource import resource
from cornice.util import extract_json_data
import pyramid.httpexceptions as exc

from turnip.config import TurnipConfig
from turnip.api.store import Store


def repo_path(func):
    """Decorator builds repo path from request name and repo_store."""
    def func_wrapper(self):
        name = self.request.matchdict['name']
        if not name:
            self.request.errors.add('body', 'name', 'repo name is missing')
            return
        self.repo = os.path.join(self.repo_store, name)
        return func(self)
    return func_wrapper


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
        is_bare = extract_json_data(self.request).get('bare_repo')
        try:
            Store.init(repo, is_bare)
        except Exception:
            return exc.HTTPConflict()  # 409

    @repo_path
    def delete(self):
        """Delete an existing git repository."""
        try:
            Store.delete(self.repo)
        except Exception:
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
    def collection_get(self):
        try:
            refs = Store.get_refs(self.repo)
        except Exception:
            return exc.HTTPNotFound()  # 404
        return json.dumps(refs)

    @repo_path
    def get(self):
        ref = 'refs/' + self.request.matchdict['ref']
        try:
            ref = Store.get_ref(self.repo, ref)
        except Exception:
            return exc.HTTPNotFound()
        return json.dumps(ref)


@resource(path='/repo/{name}/compare/{c1}..{c2}')
class DiffAPI(object):
    """Provides HTTP API for git references."""

    def __init__(self, request):
        config = TurnipConfig()
        self.request = request
        self.repo_store = config.get('repo_store')

    @repo_path
    def get(self):
        """Returns diff of two commits."""
        c1 = self.request.matchdict['c1']
        c2 = self.request.matchdict['c2']
        for sha in self.request.matchdict.iteritems():
            if 'c' in sha[0] and not 7 <= len(sha[1]) <= 40:
                return exc.HTTPBadRequest(
                    comment='invalid sha1: {}'.format(sha))
        try:
            patch = Store.get_diff(self.repo, c1, c2)
        except:
            return exc.HTTPNotFound()
        return json.dumps(patch)

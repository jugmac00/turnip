import os
import shutil

from cornice.resource import resource
from cornice.util import extract_json_data
import pygit2

from turnip.config import TurnipConfig


@resource(collection_path='repo', path='/repo/{name}')
class RepoAPI(object):

    def __init__(self, request):
        config = TurnipConfig()
        self.request = request
        self.repo_store = config.get('repo_store')

    def _init_repo(self, repo, isBare=True):
        try:
            pygit2.init_repository(repo, isBare)
        except pygit2.GitError as e:
            self.request.errors.add('Unable to create repository: %s' % e)
            return

    def collection_post(self):
        """Initialise a new git repository."""
        repo_path = extract_json_data(self.request).get('repo_path')
        if not repo_path:
            self.request.errors.add('body', 'repo_path',
                                    'repo_path is missing')
            return
        repo = os.path.join(self.repo_store, repo_path)
        isBare = extract_json_data(self.request).get('bare_repo')
        self._init_repo(repo, isBare)

    def delete(self):
        """Permanently delete a git repository from repo store."""
        name = self.request.matchdict['name']
        if not name:
            self.request.errors.add('body', 'name', 'repo name is missing')
            return
        repo = os.path.join(self.repo_store, name)
        try:
            shutil.rmtree(repo)
        except (IOError, OSError) as e:
            self.request.errors.add('Unable to delete repository: %s' % e)

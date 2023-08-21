# Copyright 2022 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import base64

from pygit2 import (
    GIT_OBJ_BLOB,
    GIT_OBJ_COMMIT,
    GIT_OBJ_TAG,
    GIT_OBJ_TREE,
    GitError,
)

REF_TYPE_NAME = {
    GIT_OBJ_COMMIT: "commit",
    GIT_OBJ_TREE: "tree",
    GIT_OBJ_BLOB: "blob",
    GIT_OBJ_TAG: "tag",
}


def format_blob(blob):
    """Return a formatted blob dict."""
    if blob.type != GIT_OBJ_BLOB:
        raise GitError(f"Invalid type: object {blob.oid.hex} is not a blob.")
    return {
        "size": blob.size,
        "data": base64.b64encode(blob.data),
    }


def format_commit(git_object):
    """Return a formatted commit object dict."""
    # XXX jugmac00 2022-01-14: this is an additional type check, which
    # currently does not get executed by the test suite
    # better safe than sorry
    if git_object.type != GIT_OBJ_COMMIT:
        raise GitError(
            "Invalid type: object {} is not a commit.".format(
                git_object.oid.hex
            )
        )
    parents = [parent.hex for parent in git_object.parent_ids]
    return {
        "sha1": git_object.oid.hex,
        "message": git_object.message,
        "author": format_signature(git_object.author),
        "committer": format_signature(git_object.committer),
        "parents": parents,
        "tree": git_object.tree.hex,
    }


def format_ref(ref, git_object):
    """Return a formatted object dict from a ref."""
    return {
        ref: {
            "object": {
                "sha1": git_object.oid.hex,
                "type": REF_TYPE_NAME[git_object.type],
            }
        }
    }


def format_signature(signature):
    """Return a formatted signature dict."""
    return {
        "name": signature.name,
        "email": signature.email,
        "time": signature.time,
    }

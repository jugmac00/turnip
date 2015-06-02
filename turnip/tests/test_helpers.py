# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

from testtools import TestCase

from turnip import helpers


class TestComposePath(TestCase):
    """Tests for path composition."""

    def test_basic(self):
        # The path is resolved within the given root tree.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            helpers.compose_path(b'/foo/bar', b'baz/quux'))

    def test_absolute(self):
        # Even absolute paths are contained.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            helpers.compose_path(b'/foo/bar', b'/baz/quux'))

    def test_normalises(self):
        # Paths are normalised.
        self.assertEqual(
            b'/foo/bar/baz/quux',
            helpers.compose_path(b'///foo/.//bar', b'//baz/..//baz/./quux'))

    def test_no_escape(self):
        # You can't get out.
        self.assertRaises(
            ValueError, helpers.compose_path, b'/foo', b'../bar')
        self.assertRaises(
            ValueError, helpers.compose_path, b'/foo', b'/foo/../../bar')

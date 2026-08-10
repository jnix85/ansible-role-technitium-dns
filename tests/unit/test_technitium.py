# -*- coding: utf-8 -*-
"""Unit tests for the shared API client helpers.

These cover the parts that are easy to get subtly wrong and impossible to notice
in a converge run: value encoding, the comparison rules that decide "changed",
and the pipe-separated multi-row format.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'module_utils'))

from technitium import (  # noqa: E402
    diff_dict,
    normalize,
    pipe_rows,
    serialize_params,
    to_api_value,
    values_equal,
)


class TestToApiValue:
    @pytest.mark.parametrize('value,expected', [
        (True, 'true'),
        (False, 'false'),
        (None, None),
        (5380, '5380'),
        ('example.com', 'example.com'),
        (['a', 'b'], 'a,b'),
        ([], ''),
        ([True, False], 'true,false'),
    ])
    def test_encoding(self, value, expected):
        assert to_api_value(value) == expected


class TestSerializeParams:
    def test_drops_none_but_keeps_false(self):
        result = serialize_params({'a': None, 'b': False, 'c': 0})
        assert result == {'b': 'false', 'c': '0'}

    def test_renames_the_password_keyword(self):
        assert serialize_params({'pass_': 'secret'}) == {'pass': 'secret'}


class TestPipeRows:
    def test_tsig_keys(self):
        rows = [
            {'keyName': 'home', 'sharedSecret': 'abc', 'algorithmName': 'hmac-sha256'},
            {'keyName': 'work', 'sharedSecret': 'def', 'algorithmName': 'hmac-sha512'},
        ]
        encoded = pipe_rows(rows, ['keyName', 'sharedSecret', 'algorithmName'])
        assert encoded == 'home|abc|hmac-sha256|work|def|hmac-sha512'

    def test_missing_fields_become_empty_cells(self):
        rows = [{'hostName': None, 'hardwareAddress': '00-11-22', 'address': '10.0.0.5',
                 'comments': None}]
        encoded = pipe_rows(rows, ['hostName', 'hardwareAddress', 'address', 'comments'])
        assert encoded == '|00-11-22|10.0.0.5|'

    def test_none_input(self):
        assert pipe_rows(None, ['a']) is None


class TestNormalize:
    def test_string_booleans_become_booleans(self):
        assert normalize('true') is True
        assert normalize('False') is False

    def test_nested(self):
        assert normalize({'a': [' x ', 'true']}) == {'a': ['x', True]}


class TestValuesEqual:
    @pytest.mark.parametrize('current,desired', [
        # The API returns ints where a task may declare strings.
        (5380, '5380'),
        ('true', True),
        (True, 'true'),
        # Lists come back as arrays but are sent as comma separated strings.
        (['9.9.9.9', '1.1.1.1'], '9.9.9.9,1.1.1.1'),
        ('9.9.9.9,1.1.1.1', ['9.9.9.9', '1.1.1.1']),
        ([], ''),
        (None, ''),
        (None, None),
        ('  spaced  ', 'spaced'),
    ])
    def test_equal(self, current, desired):
        assert values_equal(current, desired)

    @pytest.mark.parametrize('current,desired', [
        (5380, '5381'),
        (True, False),
        # Order is meaningful for forwarders and ACLs, so it must count.
        (['1.1.1.1', '9.9.9.9'], ['9.9.9.9', '1.1.1.1']),
        ('', 'something'),
        (['a'], []),
    ])
    def test_not_equal(self, current, desired):
        assert not values_equal(current, desired)

    def test_dict_is_a_subset_comparison(self):
        # Only declared keys matter, so extra keys on the server are not a diff.
        assert values_equal({'a': 1, 'b': 2}, {'a': 1})
        assert not values_equal({'a': 1}, {'a': 2})


class TestDiffDict:
    def test_reports_only_declared_keys_that_differ(self):
        current = {'forwarders': ['1.1.1.1'], 'dnssecValidation': True, 'other': 'x'}
        desired = {'forwarders': ['9.9.9.9'], 'dnssecValidation': True}

        changes, before, after = diff_dict(current, desired)

        assert changes == {'forwarders': ['9.9.9.9']}
        assert before == {'forwarders': ['1.1.1.1']}
        assert after == {'forwarders': ['9.9.9.9']}

    def test_none_values_are_skipped(self):
        changes, _, _ = diff_dict({'a': 1}, {'a': None, 'b': None})
        assert changes == {}

    def test_write_only_keys_are_masked_and_always_changed(self):
        changes, before, after = diff_dict(
            {'proxyPassword': None}, {'proxyPassword': 'secret'},
            write_only=('proxyPassword',))

        assert changes == {'proxyPassword': 'secret'}
        assert 'secret' not in str(before)
        assert 'secret' not in str(after)

    def test_no_changes_when_everything_matches(self):
        changes, before, after = diff_dict(
            {'webServiceHttpPort': 5380}, {'webServiceHttpPort': '5380'})
        assert changes == {}
        assert before == {}
        assert after == {}

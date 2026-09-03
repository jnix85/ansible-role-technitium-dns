# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)
"""Shared HTTP API client for the Technitium DNS Server modules.

The Technitium web console and its HTTP API are the same surface: every action the
console performs is reachable at ``/api/...``.  Responses are always shaped as::

    {"status": "ok" | "error" | "invalid-token", "response": {...}}

so this client unwraps ``response`` and turns anything else into an exception.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json

from ansible.module_utils.basic import env_fallback
from ansible.module_utils.six import string_types
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils.urls import fetch_url


class TechnitiumError(Exception):
    """Raised for any API level failure. ``details`` is merged into the failure JSON."""

    def __init__(self, msg, **details):
        Exception.__init__(self, msg)
        self.msg = msg
        self.details = details


def api_argument_spec():
    """Connection arguments shared by every technitium_dns_* module."""
    return dict(
        api_url=dict(
            type='str',
            default='http://127.0.0.1:5380',
            fallback=(env_fallback, ['TECHNITIUM_API_URL']),
        ),
        api_token=dict(
            type='str',
            no_log=True,
            fallback=(env_fallback, ['TECHNITIUM_API_TOKEN']),
        ),
        api_username=dict(
            type='str',
            fallback=(env_fallback, ['TECHNITIUM_API_USERNAME']),
        ),
        api_password=dict(
            type='str',
            no_log=True,
            fallback=(env_fallback, ['TECHNITIUM_API_PASSWORD']),
        ),
        api_totp=dict(type='str', no_log=True),
        api_timeout=dict(type='int', default=30),
        api_retries=dict(type='int', default=3),
        validate_certs=dict(type='bool', default=True),
        node=dict(type='str'),
    )


def api_required_together():
    return [['api_username', 'api_password']]


def api_required_one_of():
    return [['api_token', 'api_username']]


def to_api_value(value):
    """Encode a Python value the way the Technitium API expects it in a query string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, tuple)):
        return ','.join([to_api_value(item) for item in value])
    if isinstance(value, string_types):
        return value
    return str(value)


def serialize_params(params):
    """Drop ``None`` values and encode the rest. ``None`` means "do not send".

    ``pass`` is a Python keyword, so callers spell the API's password parameter
    ``pass_`` and it is renamed here.
    """
    encoded = {}
    for key, value in (params or {}).items():
        encoded_value = to_api_value(value)
        if encoded_value is None:
            continue
        encoded['pass' if key == 'pass_' else key] = encoded_value
    return encoded


def pipe_rows(rows, fields):
    """Encode a list of dicts as the pipe separated multi-row format the API uses.

    Used by ``tsigKeys``, ``staticRoutes``, ``exclusions``, ``reservedLeases`` and the
    permission parameters, all of which are flat ``a|b|c|a|b|c`` strings.
    """
    if rows is None:
        return None
    cells = []
    for row in rows:
        for field in fields:
            cells.append(to_api_value(row.get(field)) or '')
    return '|'.join(cells)


def normalize(value):
    """Coerce a value into a form that compares sanely against the API's own output."""
    if isinstance(value, bool):
        return value
    if isinstance(value, string_types):
        stripped = value.strip()
        lowered = stripped.lower()
        if lowered in ('true', 'false'):
            return lowered == 'true'
        return stripped
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return dict((key, normalize(item)) for key, item in value.items())
    return value


def values_equal(current, desired):
    """Compare a current API value against a desired one, tolerating type drift.

    The API returns ``blockListUrls`` as a JSON array but accepts a comma separated
    string, returns ports as ints but accepts strings, and so on.
    """
    current_n = normalize(current)
    desired_n = normalize(desired)

    if isinstance(desired_n, list) or isinstance(current_n, list):
        current_list = current_n if isinstance(current_n, list) else _split_csv(current_n)
        desired_list = desired_n if isinstance(desired_n, list) else _split_csv(desired_n)
        current_strs = [str(item) for item in current_list]
        desired_strs = [str(item) for item in desired_list]
        if current_strs == desired_strs:
            return True
        # An IPv6 wildcard bind subsumes the IPv4 wildcard in dual-stack mode,
        # and Technitium's web service settings persist only the IPv6 entry
        # when both are declared - "0.0.0.0" (or "0.0.0.0:<port>") never
        # comes back from settings/get once "[::]" is also present, so it
        # would otherwise look like a permanent, unfixable drift. Only drop
        # entries this specific, well-defined way; everything else about list
        # comparison stays a strict, order-sensitive equality check.
        reduced_desired = [
            item for item in desired_strs
            if not _ipv4_any_subsumed_by_ipv6_any(item, current_strs)
        ]
        return current_strs == reduced_desired

    if isinstance(current_n, bool) or isinstance(desired_n, bool):
        return bool(current_n) == bool(desired_n)

    if isinstance(current_n, dict) and isinstance(desired_n, dict):
        return all(
            key in current_n and values_equal(current_n[key], value)
            for key, value in desired_n.items()
        )

    if current_n is None or desired_n is None:
        # The API omits or empties unset values interchangeably.
        return _is_empty(current_n) and _is_empty(desired_n)

    return str(current_n) == str(desired_n)


def _ipv4_any_subsumed_by_ipv6_any(item, current_strs):
    """True when ``item`` is an IPv4-any address the IPv6-any entry covers."""
    if item == '0.0.0.0':
        suffix = ''
    elif item.startswith('0.0.0.0:'):
        suffix = item[len('0.0.0.0'):]
    else:
        return False
    return ('[::]' + suffix) in current_strs or ('::' + suffix) in current_strs


def _is_empty(value):
    return value is None or value == '' or value == []


def _split_csv(value):
    if value is None or value == '':
        return []
    return [item.strip() for item in str(value).split(',') if item.strip()]


def diff_dict(current, desired, write_only=()):
    """Return ``(changes, before, after)`` for the keys named in ``desired``.

    Keys in ``write_only`` are never returned by the API (passwords), so they are
    reported as changed only when explicitly present and their values are masked.
    """
    changes = {}
    before = {}
    after = {}
    for key, value in desired.items():
        if value is None:
            continue
        if key in write_only:
            changes[key] = value
            before[key] = 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
            after[key] = 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
            continue
        if not values_equal(current.get(key), value):
            changes[key] = value
            before[key] = current.get(key)
            after[key] = value
    return changes, before, after


class TechnitiumClient(object):
    """Thin, retrying client around the Technitium DNS Server HTTP API."""

    def __init__(self, module):
        self.module = module
        params = module.params
        self.base_url = params['api_url'].rstrip('/')
        self.timeout = params['api_timeout']
        self.retries = max(1, params['api_retries'])
        self.node = params.get('node')
        self._token = params.get('api_token')
        self._owns_session = False

    # ------------------------------------------------------------------ auth

    @property
    def token(self):
        if not self._token:
            self._login()
        return self._token

    def login_as(self, username, password, totp=None):
        """Log in and return a session token without adopting it as this client's."""
        response = self._request(
            '/api/user/login',
            params=dict(user=username, pass_=password, totp=totp, includeInfo=False),
            authenticated=False,
        )
        token = response.get('token')
        if not token:
            raise TechnitiumError('Login succeeded but no session token was returned.')
        return token

    def use_token(self, token, owns_session=False):
        """Adopt a token obtained elsewhere, for example by :meth:`login_as`."""
        self._token = token
        self._owns_session = owns_session

    def create_api_token(self, username, password, token_name, totp=None):
        """Create a named, non-expiring API token."""
        response = self._request(
            '/api/user/createToken',
            params=dict(user=username, pass_=password, totp=totp, tokenName=token_name),
            authenticated=False,
        )
        token = response.get('token')
        if not token:
            raise TechnitiumError('Token creation succeeded but no token was returned.')
        return token

    def _login(self):
        params = self.module.params
        if not params.get('api_username'):
            raise TechnitiumError(
                'No api_token supplied and no api_username/api_password to log in with.'
            )
        self._token = self.login_as(
            params['api_username'], params['api_password'], params.get('api_totp'))
        self._owns_session = True

    def logout(self):
        """Invalidate a session this client created. Tokens supplied by the user survive."""
        if self._owns_session and self._token:
            try:
                self._request('/api/user/logout')
            except TechnitiumError:
                pass
            finally:
                self._token = None
                self._owns_session = False

    # --------------------------------------------------------------- requests

    def status(self):
        """``/api/status`` needs no authentication and reports ``hasDefaultCredentials``."""
        return self._request('/api/status', authenticated=False, method='GET')

    def get(self, path, params=None):
        return self.call(path, params=params, method='GET')

    def download_text(self, path, params=None):
        """Fetch an endpoint that returns a plain text file rather than JSON.

        The allowed/blocked zone ``export`` calls are the only way to enumerate
        those zones completely; ``list`` is a tree browser, not a flat listing.
        """
        call_params = dict(params or {})
        if self.node and 'node' not in call_params:
            call_params['node'] = self.node
        url = self.base_url + path
        query = serialize_params(call_params)
        if query:
            url += '?' + urlencode(query)
        headers = {'Authorization': 'Bearer %s' % self.token}
        response, info = fetch_url(self.module, url, headers=headers,
                                   method='GET', timeout=self.timeout)
        status_code = info.get('status', -1)
        if status_code < 0:
            raise TechnitiumError('Could not reach the Technitium API at %s: %s'
                                  % (url, info.get('msg')))
        if status_code >= 400:
            raise TechnitiumError('Technitium API returned HTTP %s for %s'
                                  % (status_code, url))
        raw = response.read() if response is not None else b''
        return raw.decode('utf-8', errors='replace')

    def call(self, path, params=None, body=None, method='POST'):
        """Make an authenticated API call, adding the cluster ``node`` when configured."""
        call_params = dict(params or {})
        if self.node and 'node' not in call_params:
            call_params['node'] = self.node
        return self._request(path, params=call_params, body=body, method=method)

    def _request(self, path, params=None, body=None, method='POST',
                 authenticated=True):
        query = serialize_params(params)
        headers = {'Accept': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer %s' % self.token

        url = self.base_url + path
        data = None
        if body is not None:
            # JSON body calls (settings/set, apps/config/set) still take query params.
            if query:
                url += '?' + urlencode(query)
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode('utf-8')
        elif method == 'GET':
            if query:
                url += '?' + urlencode(query)
        else:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            data = urlencode(query).encode('utf-8')

        last_error = None
        for attempt in range(self.retries):
            try:
                return self._fetch(url, data, headers, method)
            except TechnitiumError as exc:
                # Only transport/server faults are worth retrying; a rejected request
                # will be rejected again.
                if not exc.details.get('retryable') or attempt == self.retries - 1:
                    raise
                last_error = exc
        raise last_error  # pragma: no cover - loop always returns or raises

    def _fetch(self, url, data, headers, method):
        response, info = fetch_url(
            self.module, url, data=data, headers=headers,
            method=method, timeout=self.timeout,
        )
        status_code = info.get('status', -1)

        if status_code < 0:
            raise TechnitiumError(
                'Could not reach the Technitium API at %s: %s' % (url, info.get('msg')),
                retryable=True,
            )

        raw = b''
        if response is not None:
            raw = response.read()
        elif info.get('body'):
            raw = info['body']

        text = raw.decode('utf-8', errors='replace') if raw else ''

        if status_code >= 500:
            raise TechnitiumError(
                'Technitium API returned HTTP %s for %s' % (status_code, url),
                retryable=True, body=text[:2000],
            )
        if status_code >= 400:
            raise TechnitiumError(
                'Technitium API returned HTTP %s for %s' % (status_code, url),
                body=text[:2000],
            )

        try:
            payload = json.loads(text)
        except ValueError:
            raise TechnitiumError(
                'Technitium API returned a non-JSON response for %s' % url,
                body=text[:2000],
            )

        api_status = payload.get('status')
        if api_status == 'ok':
            # Most calls wrap their data as {"status": "ok", "response": {...}}.
            # A handful - /api/status, user/login, user/createToken,
            # user/createSingleUseToken - put their fields at the top level
            # instead, alongside "status", with no "response" key at all.
            # Detecting the shape here means every caller gets the right one
            # without needing to know which category its endpoint falls into.
            if 'response' in payload:
                return payload['response']
            return payload
        if api_status == 'invalid-token':
            raise TechnitiumError(
                'The API token was rejected. Supply a valid api_token, or '
                'api_username/api_password.'
            )
        raise TechnitiumError(
            payload.get('errorMessage') or 'Technitium API call to %s failed' % url,
            stack_trace=payload.get('stackTrace'),
        )


def run_module(module, handler):
    """Run ``handler(client)``, converting API errors into ``fail_json``.

    Every module body is the same shape, so the plumbing lives here once.
    """
    client = TechnitiumClient(module)
    try:
        result = handler(client)
    except TechnitiumError as exc:
        client.logout()
        module.fail_json(msg=exc.msg, **exc.details)
    except Exception as exc:  # noqa: BLE001 - surface anything else as a clean failure
        client.logout()
        module.fail_json(msg='Unexpected error: %s' % exc)
    else:
        client.logout()
        module.exit_json(**result)

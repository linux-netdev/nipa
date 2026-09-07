# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

import datetime
try:
    import simplejson as json
except ImportError:
    import json
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import time
import urllib

import core

# TODO: document


class PatchworkCheckState:
    PENDING = "pending"
    SUCCESS = "success",
    WARNING = "warning",
    FAIL = "fail"


class PatchworkPostException(Exception):
    pass


class PatchworkFetchException(Exception):
    pass


def series_patches_ordered(series):
    """Return the patches of a series in the order they should be applied

    Patchwork lists them in arrival order, use the n/total counter it parsed
    out of the subject to put them back into the order the author intended.
    """
    patches = series['patches']
    total = series['total']
    if total != len(patches):
        core.log("Patch order - count does not add up?!", "")
        return patches

    ordered = list(patches)
    for i in range(total):
        found = False
        name = patches[i]['name']
        for j in range(total):
            # scanning PW-parsed name - tags are separated by commas
            if name.find(f" {j + 1}/{total}") >= 0 or \
               name.find(f",{j + 1}/{total}") >= 0 or \
               name.find(f"[{j + 1}/{total}") >= 0 or \
               name.find(f"0{j + 1}/{total}") >= 0:
                if ordered[j] is not patches[i]:
                    core.log(f"Patch order - reordering {i} => {j + 1}")
                    ordered[j] = patches[i]
                found = True
                break
        if not found:
            core.log("Patch order - not all patches were found!", "")
            return patches
    return ordered


class Patchwork(object):
    # Patchwork mbox object types vs the names of the REST collections
    _mbox_apis = {'cover': 'covers', 'patch': 'patches'}

    def __init__(self, config):
        self._session = requests.Session()
        allowed_methods = Retry.DEFAULT_ALLOWED_METHODS | {'POST', 'PATCH'}
        retry = Retry(connect=10, status=10,
                      status_forcelist={429, 502, 503, 504},
                      allowed_methods=allowed_methods, backoff_factor=1)
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount('http://', adapter)
        self._session.mount('https://', adapter)

        self.server = config.get('patchwork', 'server')
        self.archive = config.get('patchwork', 'archive',
                                  fallback='https://lore.kernel.org/all').rstrip('/')
        ssl = config.getboolean('patchwork', 'use_ssl', fallback=True)
        self._proto = "https://" if ssl else "http://"
        self._token = config.get('patchwork', 'token', fallback='')
        self._user = config.get('patchwork', 'user', fallback='')

        ua = config.get('patchwork', 'user-agent', fallback='')
        if ua:
            self._session.headers.update({"user-agent":ua})

        config_project = config.get('patchwork', 'project')
        pw_project = self.get_project(config_project)
        if pw_project:
            self._project = pw_project['id']
        else:
            try:
                self._project = int(config_project)
            except ValueError:
                raise Exception("Patchwork project not found", config_project)

    def _request(self, url):
        core.log_open_sec(f"Patchwork {self.server} request: {url}")
        start = datetime.datetime.now()
        core.log("Start", str(start))

        try:
            ret = self._session.get(url)
            core.log("Response", ret)
            try:
                core.log("Response data", ret.json())
            except json.decoder.JSONDecodeError:
                core.log("Response data", ret.content.decode('utf-8', 'replace'))
        finally:
            end = datetime.datetime.now()
            core.log("Response time GET (sec)", (end - start).total_seconds())
            core.log_end_sec()

        return ret

    def request(self, url):
        return self._request(url).json()

    def request_all(self, url):
        items = []

        while url:
            response = self._request(url)
            items += response.json()

            if 'Link' not in response.headers:
                break
            url = ''
            links = response.headers['Link'].split(',')
            for link in links:
                info = link.split(';')
                if info[1].strip() == 'rel="next"':
                    url = info[0][1:-1]

        return items

    def get(self, object_type, identifier):
        return self._get(f'{object_type}/{identifier}/').json()

    def get_all(self, object_type, filters=None, api='1.1'):
        if filters is None:
            filters = {}
        params = ''
        for key, val in filters.items():
            if val is not None:
                params += f'{key}={val}&'

        items = []

        response = self._get(f'{object_type}/?{params}', api=api)
        # Handle paging, by chasing the "Link" elements
        while response:
            for o in response.json():
                items.append(o)

            if 'Link' not in response.headers:
                break

            # There are multiple links separated by commas
            links = response.headers['Link'].split(',')
            # And each link has the format of <url>; rel="type"
            response = None
            for link in links:
                info = link.split(';')
                if info[1].strip() == 'rel="next"':
                    response = self._request(info[0][1:-1])

        return items

    def get_by_msgid(self, object_type, msgid):
        msgid = urllib.parse.quote(msgid)
        return self._get(f'{object_type}/?msgid={msgid}&project={self._project}', api='').json()

    # Patchwork's own /mbox/ endpoints have been serving empty responses ever
    # since one of its upgrades, so the messages come from the list archive.
    # Patchwork is only asked for the message ids. Note that the archive
    # requires a well-known user-agent, see the 'user-agent' config option.
    def get_mbox_by_msgid(self, msgid):
        url = f'{self.archive}/{urllib.parse.quote(msgid.strip("<>"))}/raw'
        ret = self._request(url)
        if ret.status_code != 200:
            raise PatchworkFetchException(url, ret)
        # Archives serve the message as it was posted, which is not necessarily
        # valid UTF-8. Losing a character beats blowing up the entire series.
        return ret.content.decode('utf-8', 'replace')

    # Like patchwork's series mbox this contains the patches only, the cover
    # letter is not part of it.
    def series_to_mbox(self, series):
        return ''.join([self.get_mbox_by_msgid(p['msgid'])
                        for p in series_patches_ordered(series)])

    def get_mbox(self, object_type, identifier):
        if object_type == 'series':
            return self.series_to_mbox(self.get('series', identifier))
        obj = self.get(self._mbox_apis[object_type], identifier)
        return self.get_mbox_by_msgid(obj['msgid'])

    def _get(self, req, api='1.1'):
        if api:
            api += "/"
        return self._request(f'{self._proto}{self.server}/api/{api}{req}')

    def _post(self, req, headers, data, api='1.1'):
        url = f'{self._proto}{self.server}/api/{api}/{req}'
        core.log_open_sec(f"Patchwork {self.server} post: {url}")
        start = datetime.datetime.now()
        core.log("Start", str(start))

        try:
            ret = self._session.post(url, headers=headers, data=data)
            core.log("Headers", headers)
            core.log("Data", data)
            core.log("Response", ret)
            try:
                core.log("Response data", ret.json())
            except json.decoder.JSONDecodeError:
                core.log("Response data", ret.content.decode())
        finally:
            end = datetime.datetime.now()
            core.log("Response time POST (sec)", (end - start).total_seconds())
            core.log_end_sec()

        return ret

    # PATCH as in the HTTP method, not getting a patch
    def _patch(self, req, headers, data, api='1.1'):
        url = f'{self._proto}{self.server}/api/{api}/{req}'
        core.log_open_sec(f"Patchwork {self.server} patch: {url}")
        start = datetime.datetime.now()
        core.log("Start", str(start))

        try:
            ret = self._session.patch(url, headers=headers, data=data)
            core.log("Headers", headers)
            core.log("Data", data)
            core.log("Response", ret)
            try:
                core.log("Response data", ret.json())
            except json.decoder.JSONDecodeError:
                core.log("Response data", ret.content.decode())
        finally:
            end = datetime.datetime.now()
            core.log("Response time PATCH (sec)", (end - start).total_seconds())
            core.log_end_sec()

        return ret

    def get_project(self, name):
        all_projects = self.get_projects_all()
        for project in all_projects:
            if project['name'] == name:
                return project

    def get_projects_all(self):
        return self.get_all('projects')

    def get_patches_all(self, delegate=None, project=None, since=None, action_required=None):
        if project is None:
            project = self._project
        query = {'project': project}
        if delegate:
            query['delegate'] = delegate
        if since:
            query['since'] = since
        # Hack up "action required" as patchwork doesn't have actual filter for it
        # we assume states 1 and 2 are action required ('New' and 'Under Review')
        if action_required:
            query['state'] = '1&state=2'
            query['archived'] = 'false'
        return self.get_all('patches', query)

    def get_new_series(self, project=None, since=None):
        if project is None:
            project = self._project
        event_params = {
            'project': project,
            'since': since,
            'order': 'date',
            'category': 'series-completed',
        }
        events = self.get_all('events', event_params)
        if not events:
            return [], since
        since = events[-1]['date']
        series = [self.get('series', e['payload']['series']['id']) for e in events]
        return series, since

    def post_check(self, patch, name, state, url, desc):
        headers = {}
        if self._token:
            headers['Authorization'] = f'Token {self._token}'

        data = {
            'user': self._user,
            'state': state,
            'target_url': url,
            'context': name,
            'description': desc
        }

        r = self._post(f'patches/{patch}/checks/', headers=headers, data=data)
        if r.status_code != 201:
            raise PatchworkPostException(r)

    def update_state(self, patch, state):
        headers = {}
        if self._token:
            headers['Authorization'] = f'Token {self._token}'

        data = {
            'state': state
        }

        r = self._patch(f'patches/{patch}/', headers=headers, data=data)
        if r.status_code != 200:
            raise PatchworkPostException(r)

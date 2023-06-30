# SPDX-License-Identifier: GPL-2.0

import fnmatch
import requests


class Person:
    """
    TODO: turn this into proper identity mapping thing
          allowing us to alias people in non-stupid ways
    """
    def __init__(self, name_email):
        self.name_email = name_email
        self.name, self.email = self.name_email_split(name_email)

    @staticmethod
    def name_email_split(name_email):
        idx = name_email.rfind('<')
        if idx > 1:
            idx = name_email.rfind('<')
            name = name_email[:idx].strip()
            email = name_email[idx + 1:-1].strip()
        else:
            if idx > -1:
                name_email = name_email[idx + 1:-1]
            name = ''
            email = name_email
        return name, email

    def __repr__(self):
        return f"Person('{self.name}', '{self.email}')"

    def __eq__(self, other):
        if self.name_email == other:
            return True
        _, email = self.name_email_split(other)
        return self.email == email


class Maintainers:
    def __init__(self, *, file=None, url=None):
        self.entries = MaintainersList()

        if file:
            self._load_from_file(file)
        elif url:
            self._load_from_url(url)

    def _load_from_lines(self, lines):
        group = []
        started = False
        for line in lines:
            # Skip the "intro" section of MAINTAINERS
            started |= line.isupper()
            if not started:
                continue

            if line == '':
                if len(group) > 1:
                    self.entries.add(MaintainersEntry(group))
                    group = []
                else:
                    print('Empty group:', group)
            elif (len(line) > 3 and line[1:3] == ':\t') or len(group) == 0:
                group.append(line.strip())
            else:
                print("Bad group:", group, line.strip())
                group = [line.strip()]

    def _load_from_file(self, file):
        with open(file, 'r') as f:
            self._load_from_lines(f.read().split('\n'))

    def _load_from_url(self, url):
        r = requests.get(url)
        data = r.content.decode('utf-8')
        self._load_from_lines(data.split('\n'))

    def find_by_path(self, path):
        return self.entries.find_by_paths([path])

    def find_by_paths(self, paths):
        return self.entries.find_by_paths(paths)

    def find_by_owner(self, person):
        return self.entries.find_by_owner(person)


class MaintainersEntry:
    def __init__(self, lines):
        self._raw = lines

        self.title = lines[0]
        self.maintainers = []
        self.reviewers = []
        self.files = []

        for line in lines[1:]:
            if line[:3] == 'M:\t':
                self.maintainers.append(Person(line[3:]))
            elif line[:3] == 'R:\t':
                self.reviewers.append(Person(line[3:]))
            elif line[:3] == 'F:\t':
                self.files.append(line[3:])

        self._owners = self.maintainers + self.reviewers

        self._file_match = []
        self._file_pfx = []
        for F in self.files:
            # Strip trailing wildcard, it's implicit and slows down the match
            if F.endswith('*'):
                F = F[:-1]
            if '?' in F or '*' in F or '[' in F:
                self._file_match.append(F)
            else:
                self._file_pfx.append(F)

    def __repr__(self):
        return f"MaintainersEntry('{self.title}')"

    def match_owner(self, person):
        for M in self._owners:
            if person == M:
                return True
        return False

    def match_path(self, path):
        for F in self._file_pfx:
            if path.startswith(F):
                return True
        for F in self._file_match:
            if fnmatch.fnmatch(path, F):
                return True
        return False


class MaintainersList:
    def __init__(self):
        self._list = []

    def __len__(self):
        return len(self._list)

    def __repr__(self):
        return repr(self._list)

    def add(self, other):
        self._list.append(other)

    def find_by_paths(self, paths):
        ret = MaintainersList()
        for entry in self._list:
            for path in paths:
                if entry.match_path(path):
                    ret.add(entry)
                    break
        return ret

    def find_by_owner(self, person):
        ret = MaintainersList()
        for entry in self._list:
            if entry.match_owner(person):
                ret.add(entry)
        return ret

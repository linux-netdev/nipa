#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0

import requests
import sys
import os


#
# DocRef code
#


class DocTooManyMatches(Exception):
    pass


class DocNotFound(Exception):
    pass


class DocReference:
    def __init__(self, tag):
        self.tag = tag
        self.title = tag
        self.lines = []

    def set_title(self, title):
        if self.title != self.tag:
            raise Exception(f'Title for {self.tag} already set to "{self.title}" now "{title}"')
        self.title = title

    def add_line(self, line):
        self.lines.append(line)

    def __repr__(self):
        ret = self.title + '\n'
        ret += '\n'.join(self.lines)
        return ret


class FormLetter:
    def __init__(self, filename):
        with open(filename, 'r') as file:
            self.contents = file.read()
        # TODO: auto-populate details like version

    def __repr__(self):
        return self.contents


class DocRefs:
    def __init__(self):
        self.refs = dict()
        self.loc_map = dict()
        self.name_alias = dict()

    def dump(self):
        for n in self.refs:
            for t in self.refs[n]:
                print(n, t, sep='/')

    def _unalias_name(self, name):
        if name in self.name_alias:
            return self.name_alias[name]
        return name

    def search(self, name, tag):
        """
        Find the relevant doc based on inputs. The name is optional and if it is
        specified can much exactly or partially. Tag is required. Full matches take
        precedence. If multiple equivalent matches are found error will be raised.

        :param name: partial or exact match for doc, optional
        :param tag: partial or exact match on section
        :return: tuple of (doc, tag) which can be used to get text out of get_doc()
        """

        match = None
        if name and name not in self.refs:
            for n in self.refs:
                if name in n:
                    if match:
                        raise DocTooManyMatches(f'Section {name} matched both {match} and {n}')
                    match = n
            if not match:
                raise DocNotFound(f'Section {name} not found')
            name = match

        match = None
        match_n = None
        full_match = False

        for n in self.refs:
            # If name is empty search all, otherwise only the matching section
            if name and name != n:
                continue
            for t in self.refs[n]:
                if tag in t:
                    is_full = (t == tag) and (not name or n == name)
                    if match and (full_match == is_full):
                        raise DocTooManyMatches(f'{name}/{tag} matched both {match_n}/{match} and {n}/{t}')
                    if is_full >= full_match:
                        full_match = is_full
                        match = t
                        match_n = n
        if not match:
            raise DocNotFound(f'{name}/{tag} not found')

        return match_n, match

    def get_doc(self, name, tag):
        return repr(self.refs[name][tag])

    def alias_section(self, name, alias):
        self.name_alias[alias] = name

    @staticmethod
    def _sphinx_title_to_heading(name):
        heading = []
        for i in range(len(name)):
            # Leading numbers are definitely removed, not sure about mid-title numbers
            if name[i].isalpha():
                heading.append(name[i].lower())
            elif len(heading) == 0:
                pass
            elif heading[-1] != "-":
                heading.append("-")
        if len(heading) and heading[-1] == "-":
            heading.pop()

        return "".join(heading)

    def get_url(self, name, tag):
        location = self.loc_map[name]
        url = f'https://www.kernel.org/doc/html/next/{location}.html'
        r = requests.get(url)
        data = r.content.decode('utf-8')

        offs = 0
        while True:
            # Find all the sections in the HTML version of the doc
            offs = data.find('<section id=', offs)
            if offs == -1:
                break
            offs += 13  # skip '<section id="'
            start = offs
            end = start + 1
            while data[end] != '"' and len(data) > end:
                end += 1
            if data[start:end] == tag:
                return url + '#' + tag
            offs += 1

    def load_section(self, location, name):
        self.refs[name] = dict()
        refs = self.refs[name]

        self.loc_map[name] = location

        # Populate the plain text contents
        filename = sys.argv[1] + "/Documentation/" + location + ".rst"
        with open(filename, 'r') as file:
            lines = [line.rstrip() for line in file]

        headings = {'-', '~', '='}
        docref = DocReference('')  # Make a fake one so we don't have to None-check
        prev = ""
        for line in lines:
            # Non-headings get fed into the current section
            if len(line) == 0 or line[0] not in headings:
                docref.add_line(prev)
                prev = line
                continue
            if line != line[0] * len(line):
                docref.add_line(prev)
                prev = line
                continue

            # Headings are kept as 'docref'
            heading = self._sphinx_title_to_heading(prev)
            if heading:
                docref = DocReference(heading)
                refs[heading] = docref
                docref.set_title(prev)
            prev = line

    def load_form_letter(self, filename, name):
        if 'form-letters' not in self.refs:
            self.refs['form-letters'] = dict()
        self.refs['form-letters'][name] = FormLetter(filename)


def doc_act(dr, act):
    names = act.split('/')
    if len(names) > 2 or len(names) < 1:
        print(">>> ERROR: bad doc action token count:", act)
        return False
    if len(names) == 1:
        names = [''] + names

    try:
        name, sec = dr.search(names[0], names[1])
        doc = dr.get_doc(name, sec)

        if name == 'form-letters':
            print('## Form letter -', sec)
            print()
            print(doc)
        else:
            url = dr.get_url(name, sec)

            print("Quoting documentation:")
            print()
            doc_lines = doc.split('\n')
            line = ''
            for line in doc_lines:
                print(' ', line)
            if line:
                print()
            if url:
                print('See:', url)
            else:
                print(">>> ERROR: URL not found for", name, sec)
    except DocTooManyMatches as e:
        print(">>> ERROR: ambiguous doc search:", act)
        print(">>> ERROR:", str(e))
        return False
    except DocNotFound as e:
        print(">>> ERROR: doc not found:", act)
        print(">>> ERROR:", str(e))
        return False
    except Exception as e:
        print(">>> ERROR: failed doc search:", act)
        print()
        print(e)
        return False

    return True


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} LINUX-TREE [FORM-LETTERS]")
        sys.exit(1)

    dr = DocRefs()
    for file in os.listdir(os.path.join(sys.argv[1], 'Documentation', 'process')):
        if not os.path.isfile(os.path.join(sys.argv[1], 'Documentation', 'process', file)):
            return
        name = file[:-4]
        dr.load_section('process/' + name, name)
    if len(sys.argv) > 2:
        form_letters = sys.argv[2]
    else:
        form_letters = os.path.join(os.path.dirname(sys.argv[0]), 'form-letters')
    for file in os.listdir(form_letters):
        dr.load_form_letter(os.path.join(form_letters, file), file)

    for line in sys.stdin:
        skip = False
        if line.find('doc-bot:') == 0:
            act = line[8:].strip()
            skip = doc_act(dr, act)
        if not skip:
            print(line, end='')


if __name__ == "__main__":
    main()

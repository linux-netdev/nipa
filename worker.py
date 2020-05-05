#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

import configparser
import json
import os
import time
from typing import Generator, Optional, ValuesView
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from pw import Patchwork, PatchworkCheckState

# TODO: document

CONFIG = None
DEPENDENCY_TREE = None
SERIES = {}


class WorkerTask:
    def __init__(self, name: str, parent: 'WorkerTaskContainer') -> None:
        self._name = name
        self._is_done = False
        self._parent = parent

    def __eq__(self, other: 'WorkerTask'):
        return self._name.__eq__(other.get_name())

    def __hash__(self):
        return self._name.__hash__()

    def get_name(self) -> str:
        return self._name

    def is_done(self):
        return self._is_done

    def set_done(self, done: bool) -> None:
        self._is_done = done


class WorkerTaskContainer:
    def __init__(self, name: str, parent: 'WorkerTaskContainer' = None) -> None:
        self._name = name
        self._parent = parent
        self._children = {}
        self._tasks = {}

    def get_name(self) -> str:
        return self._name

    def get_parent(self) -> 'WorkerTaskContainer':
        return self._parent

    def has_task(self, name: str) -> bool:
        return name in self._tasks

    def has_done_task(self, name: str) -> bool:
        return name in self._tasks and self._tasks[name].is_done()

    def get_task(self, name: str) -> WorkerTask:
        return self._tasks[name]

    def add_task(self, task: WorkerTask):
        self._tasks[task.get_name()] = task

    def get_prev_sibling(self) -> Optional['WorkerTaskContainer']:
        if self._parent is None:
            return None

        return self._parent.get_prev_child(self)

    def get_child(self, name: str) -> 'WorkerTaskContainer':
        return self._children[name]

    def get_prev_child(self, child) -> Optional['WorkerTaskContainer']:
        sorted_children = sorted(list(self._children.keys()))
        child_id = sorted_children.index(child.get_name())
        if child_id < 1:
            return None
        return self._children[sorted_children[child_id - 1]]

    def get_children(self) -> ValuesView['WorkerTaskContainer']:
        return self._children.values()

    def add_child(self, child: 'WorkerTaskContainer'):
        self._children[child.get_name()] = child


class TaskDependency:
    def __init__(self) -> None:
        with open(os.path.join(NIPA_DIR, "tests", "dependency_graph.json"), 'rb') as fp:
            dep_tree_json = json.load(fp)

        self._task_deps = {}
        for cont_type in dep_tree_json:
            self._parse_dependencies_container(cont_type, dep_tree_json[cont_type])

    def _parse_dependencies_container(self, cont_type: str, dep) -> None:
        self._task_deps[cont_type] = {}
        for test_name in dep:
            self._task_deps[cont_type][test_name] = dep[test_name]

    def get_runnable_tasks(self, cont: WorkerTaskContainer, cont_type: str) -> Generator[str, None, None]:
        dep_tree = self._task_deps[cont_type]

        for task, dependencies in dep_tree.items():
            if cont.has_task(task):
                continue

            met = True
            for dep in dependencies:
                if "prev" in dep:
                    prev = cont.get_prev_sibling()
                    met = prev is None or prev.has_done_task(dep["prev"])
                elif "parent" in dep:
                    parent = cont.get_parent()
                    met = parent is not None and parent.has_done_task(dep["parent"])
                elif "self" in dep:
                    met = cont.get_task(dep["self"]).is_done()

                if not met:
                    break

            if met:
                yield task


def _initial_scan_tasks(task_dir: str, name: str, parent: Optional['WorkerTaskContainer'] = None) \
        -> WorkerTaskContainer:
    container = WorkerTaskContainer(name, parent)
    for root, dirs, _ in os.walk(task_dir):
        for task_name in dirs:
            task = WorkerTask(task_name, container)

            task_dir = os.path.join(root, task_name)
            task.set_done(os.path.exists(os.path.join(task_dir, 'done')))

            container.add_task(task)
        return container


def _initial_scan_series(results_dir: str, series_name: str) -> None:
    global SERIES

    log(f"Scanning {series_name}")
    series = _initial_scan_tasks(os.path.join(results_dir, series_name, "all"), series_name)
    SERIES[series_name] = series

    patches_dir = os.path.join(results_dir, series_name, "patches")
    for root, dirs, _ in os.walk(patches_dir):
        for patch_name in dirs:
            patch = _initial_scan_tasks(os.path.join(root, patch_name), patch_name, series)
            series.add_child(patch)
        break


def _initial_scan(results_dir: str, config: configparser.ConfigParser) -> None:
    for root, dirs, _ in os.walk(results_dir):
        for series in dirs:
            _initial_scan_series(root, series)
        break


def initial_scan(results_dir: str, config: configparser.ConfigParser) -> None:
    log_open_sec(f'Initial scan of {results_dir}')
    try:
        _initial_scan(results_dir, config)
    finally:
        log_end_sec()


def on_created(event):
    global SERIES, DEPENDENCY_TREE

    log('Async event for ' + event.src_path)
    task_dir = os.path.dirname(event.src_path)
    task_name = os.path.basename(task_dir)
    task_grp_dir = os.path.dirname(task_dir)
    task_grp_name = os.path.basename(task_grp_dir)

    if task_grp_name == "all":
        series_dir = os.path.dirname(task_grp_dir)
        series_name = os.path.basename(series_dir)
        if task_name == "load":
            results_dir = os.path.dirname(series_dir)
            _initial_scan_series(results_dir, series_name)
        series = SERIES[series_name]
        cont = series
        patches = series.get_children()
    else:
        patch_dir = os.path.dirname(task_grp_dir)
        patch_name = os.path.basename(patch_dir)
        series_dir = os.path.dirname(patch_dir)
        series_name = os.path.basename(series_dir)
        series = SERIES[series_name]
        cont = series.get_child(patch_name)
        patches = set(cont)

    task = cont.get_task(task_name)
    task.set_done(True)

    log(f"Series {series.get_name()}, task {task.get_name()} is now done")
    for new_task_name in DEPENDENCY_TREE.get_runnable_tasks(series, 'series'):
        log("Kicking off series " + new_task_name)
        # TODO: create dir
        # TODO: actually kick off

    for patch in patches:
        for new_task_name in DEPENDENCY_TREE.get_runnable_tasks(patch, 'patch'):
            log("Kicking off patch " + new_task_name)


class SomeEventHandler(PatternMatchingEventHandler):
    def on_created(self, event):
        print(self)


def watch_scan(results_dir):
    event_handler = SomeEventHandler(patterns=['*done'],
                                     ignore_patterns=[],
                                     ignore_directories=True,
                                     case_sensitive=True)

    observer = Observer()
    observer.schedule(event_handler, results_dir, recursive=True)

    observer.start()

    # TODO: kick of the initial scan ones

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()


def worker():
    global CONFIG, DEPENDENCY_TREE

    # Init state
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'worker.config'])

    log_init(config.get('log', 'type', fallback='org'),
             config.get('log', 'file', fallback=os.path.join(NIPA_DIR,
                                                             "worker.org")))

    results_dir = config.get('results', 'dir',
                             fallback=os.path.join(NIPA_DIR, "results"))

    CONFIG = config
    DEPENDENCY_TREE = TaskDependency()

    # Initial walk
    initial_scan(results_dir, config)
    # Watcher
    watch_scan(results_dir)


if __name__ == "__main__":
    worker()

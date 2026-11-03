# SPDX-License-Identifier: GPL-2.0

"""Collect host counters for isolated performance test executions."""

import json
import os
import shlex
import subprocess
import threading
import time


CPU_FIELDS = ("user", "nice", "sys", "idle", "iowait", "irq", "sirq",
              "steal")
CPU_TIME_METRICS = {
    "cpu.time.usr": ("user", "nice"),
    "cpu.time.sys": ("sys",),
    "cpu.time.sirq": ("sirq",),
}
NIC_METRICS = {
    "rx_dropped": "nic.rx.dropped",
    "rx_missed_errors": "nic.rx.missed_errors",
    "tx_dropped": "nic.tx.dropped",
}
CLOCK_TICKS = 100
SNAPSHOT_END = "__NIPA_SYSTEM_STAT_END__"
NIC_STATS = "__NIPA_NIC_STATS__"


class SystemMonitor:
    """Sample raw CPU and NIC counters on a DUT and an optional SSH peer."""

    def __init__(self, ifname, remote=None, remote_ifname=None,
                 sample_interval=1.0, clock=time.monotonic):
        self.ifname = ifname
        self.remote = remote
        self.remote_ifname = remote_ifname
        self.sample_interval = sample_interval
        self._clock = clock
        self._origin = None
        self._remote_sampler = None
        self._thread = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._lock = threading.Lock()
        self._samples = []
        self.error = None

    @staticmethod
    def _parse_cpu(text):
        stats = {}
        for line in text.splitlines():
            fields = line.split()
            if not fields or not fields[0].startswith("cpu") or \
                    not fields[0][3:].isdigit():
                continue
            stats[int(fields[0][3:])] = {
                name: float(value)
                for name, value in zip(CPU_FIELDS, fields[1:])
            }
        return stats

    @staticmethod
    def _parse_nic(text):
        for line in text.splitlines():
            if not line.startswith(NIC_STATS):
                continue
            fields = line.split()
            return {
                name: float(value)
                for name, value in zip(NIC_METRICS, fields[1:])
            }
        raise ValueError("remote NIC statistics are missing")

    @staticmethod
    def _read_nic(ifname):
        stats = {}
        for name in NIC_METRICS:
            path = f"/sys/class/net/{ifname}/statistics/{name}"
            with open(path, encoding="utf-8") as stat_file:
                stats[name] = float(stat_file.read())
        return stats

    def elapsed(self):
        return self._clock() - self._origin

    def _start_remote_sampler(self):
        if not self.remote or not self.remote_ifname:
            return

        nic_reads = ""
        for name in NIC_METRICS:
            path = shlex.quote(
                f"/sys/class/net/{self.remote_ifname}/statistics/{name}")
            nic_reads += (f"read value < {path} || exit; "
                          f"printf ' %s' \"$value\"; ")
        command = (
            "while :; do cat /proc/stat || exit; "
            f"printf '{NIC_STATS}'; {nic_reads}"
            f"printf '\\n{SNAPSHOT_END}\\n'; "
            f"sleep {shlex.quote(str(self.sample_interval))} || exit; done"
        )
        self._remote_sampler = subprocess.Popen(
            ["ssh", "-o", "ConnectTimeout=10",
             "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             self.remote, command],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def _read_remote(self):
        lines = []
        while True:
            line = self._remote_sampler.stdout.readline()
            if not line:
                status = self._remote_sampler.poll()
                raise RuntimeError(
                    f"remote system sampler stopped unexpectedly ({status})")
            line = line.decode("utf-8", "replace").rstrip("\n")
            if line == SNAPSHOT_END:
                return "\n".join(lines)
            lines.append(line)

    def _stop_remote_sampler(self):
        sampler = self._remote_sampler
        self._remote_sampler = None
        if sampler is None:
            return
        sampler.terminate()
        try:
            sampler.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sampler.kill()
            sampler.wait(timeout=5)

    def _snapshot(self, remote_text=None):
        with open("/proc/stat", encoding="utf-8") as stat_file:
            local_cpu = self._parse_cpu(stat_file.read())
        hosts = {
            "local": {
                "cpu": local_cpu,
                "nic": self._read_nic(self.ifname),
            },
        }
        if remote_text is not None:
            hosts["remote"] = {
                "cpu": self._parse_cpu(remote_text),
                "nic": self._parse_nic(remote_text),
            }
        return {"time": self.elapsed(), "hosts": hosts}

    def _append_sample(self, sample):
        with self._lock:
            self._samples.append(sample)
        self._ready_event.set()

    def _sample_loop(self):
        try:
            if self._remote_sampler is not None:
                while True:
                    remote_text = self._read_remote()
                    self._append_sample(self._snapshot(remote_text))
                    if self._stop_event.is_set():
                        break
            else:
                while not self._stop_event.is_set():
                    self._append_sample(self._snapshot())
                    self._stop_event.wait(self.sample_interval)
        except Exception as error:  # pylint: disable=broad-except
            if not self._stop_event.is_set():
                self.error = str(error)
                self._ready_event.set()

    def start(self):
        """Start sampling and wait for the initial snapshot."""
        self._origin = self._clock()
        self._stop_event.clear()
        self._ready_event.clear()
        self._samples = []
        self.error = None
        self._start_remote_sampler()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=15):
            self.stop()
            raise TimeoutError("system monitor did not produce an initial sample")
        if self.error:
            error = self.error
            self.stop()
            raise RuntimeError(error)
        return self

    def stop(self):
        """Stop sampling and return a stable copy of the raw snapshots."""
        self._stop_event.set()
        if self._thread is not None:
            if self._remote_sampler is not None:
                self._thread.join(timeout=self.sample_interval + 2)
                self._stop_remote_sampler()
                self._thread.join(timeout=5)
            else:
                self._thread.join(timeout=5)
                try:
                    self._append_sample(self._snapshot())
                except OSError as error:
                    self.error = self.error or str(error)
            self._thread = None
        else:
            self._stop_remote_sampler()
        with self._lock:
            return list(self._samples)

    def write_case_metrics(self, path, windows):
        """Stop sampling and write metrics attributed to completed cases."""
        samples = self.stop()
        cases = []
        for window in windows:
            # The samples of a failed sampler have gaps
            metrics = [] if self.error else metrics_for_window(
                samples, window["start"], window["end"])
            cases.append({**window, "metrics": metrics})
        data = {"version": 1, "cases": cases}
        if self.error:
            data["error"] = self.error
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as fp:
                json.dump(data, fp, separators=(",", ":"))
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError):
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return data


def _interpolate_number(before, after, ratio):
    return before + (after - before) * ratio


def _interpolate_host(before, after, ratio):
    cpus = {}
    for cpu in before["cpu"].keys() & after["cpu"].keys():
        cpus[cpu] = {
            field: _interpolate_number(before["cpu"][cpu][field],
                                       after["cpu"][cpu][field], ratio)
            for field in CPU_FIELDS
        }
    nic = {
        name: _interpolate_number(before["nic"][name], after["nic"][name],
                                  ratio)
        for name in NIC_METRICS
    }
    return {"cpu": cpus, "nic": nic}


def _snapshot_at(samples, timestamp):
    if timestamp <= samples[0]["time"]:
        return {"time": timestamp, "hosts": samples[0]["hosts"]}
    if timestamp >= samples[-1]["time"]:
        return {"time": timestamp, "hosts": samples[-1]["hosts"]}

    for before, after in zip(samples, samples[1:]):
        if before["time"] <= timestamp <= after["time"]:
            elapsed = after["time"] - before["time"]
            ratio = (timestamp - before["time"]) / elapsed
            hosts = {}
            for host in before["hosts"].keys() & after["hosts"].keys():
                hosts[host] = _interpolate_host(
                    before["hosts"][host], after["hosts"][host], ratio)
            return {"time": timestamp, "hosts": hosts}
    raise ValueError(f"no samples around timestamp {timestamp}")


def _window_samples(samples, start, end):
    # Only the samples tell what happened, a sampler which stopped early must
    # not pass for a quiet host
    if not samples or end <= start or samples[0]["time"] > start or \
            samples[-1]["time"] < end:
        return []
    return [_snapshot_at(samples, start)] + [
        sample for sample in samples if start < sample["time"] < end
    ] + [_snapshot_at(samples, end)]


def _cpu_deltas(before, after):
    result = {}
    for cpu in before.keys() & after.keys():
        delta = {
            field: after[cpu][field] - before[cpu][field]
            for field in CPU_FIELDS
        }
        if all(value >= 0 for value in delta.values()):
            result[cpu] = delta
    return result


def _per_cpu_array(values):
    if not values:
        return None
    result = [None] * (max(values) + 1)
    for cpu, value in values.items():
        result[cpu] = value
    return result


def _metric(name, metric_type, timestamp, value, host, **policy):
    return {
        "name": name,
        "type": metric_type,
        "time": round(timestamp, 6),
        "value": value,
        "host": host,
        "origin": "system-monitor",
        **policy,
    }


def metrics_for_window(samples, start, end):
    """Convert raw cumulative snapshots into one testcase's metric series."""
    selected = _window_samples(samples, start, end)
    if len(selected) < 2:
        return []

    metrics = []
    hosts = set.intersection(*(set(sample["hosts"]) for sample in selected))
    for host in sorted(hosts):
        baseline = selected[0]["hosts"][host]
        previous = baseline
        for index, sample in enumerate(selected):
            current = sample["hosts"][host]
            timestamp = sample["time"] - start
            elapsed = _cpu_deltas(baseline["cpu"], current["cpu"])
            for name, fields in CPU_TIME_METRICS.items():
                values = _per_cpu_array({
                    cpu: sum(delta[field] for field in fields) / CLOCK_TICKS
                    for cpu, delta in elapsed.items()
                })
                if values is not None:
                    metrics.append(_metric(
                        name, "per-cpu", timestamp, values, host,
                        summarize="p90", transform="rate",
                        aggregation="busy-core-equivalents",
                        display_range={"min": 0, "max": 100},
                        display_scale=100,
                        display_label="Percent of one CPU"))

            for counter, name in NIC_METRICS.items():
                metrics.append(_metric(
                    name, "scalar", timestamp,
                    current["nic"][counter] - baseline["nic"][counter], host,
                    summarize="max", transform="rate",
                    display_range={"min": 0}))

            if index:
                interval = _cpu_deltas(previous["cpu"], current["cpu"])
                utilization = {}
                for cpu, delta in interval.items():
                    total = sum(delta.values())
                    if total > 0:
                        busy = total - delta["idle"] - delta["iowait"]
                        utilization[cpu] = round(100 * busy / total, 4)
                values = _per_cpu_array(utilization)
                if values is not None:
                    metrics.append(_metric(
                        "cpu.utilization", "per-cpu", timestamp, values,
                        host, summarize="p90",
                        aggregation="busy-core-equivalents",
                        display_range={"min": 0, "max": 100},
                        display_label="Percent of one CPU"))
            previous = current
    return metrics

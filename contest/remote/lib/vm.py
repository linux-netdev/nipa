# SPDX-License-Identifier: GPL-2.0

import unicodedata
import requests
import subprocess
from time import sleep
import fcntl
import json
import os
import psutil
import re
import signal


"""
Config:

[remote]
branches=https://url-to-branches-manifest
[local]
base_path=/common/path
json_path=base-relative/path/to/json
results_path=base-relative/path/to/raw/outputs
tree_path=/root-path/to/kernel/git
[www]
url=https://url-to-reach-base-path
# Specific stuff
[env]
paths=/extra/exec/PATH:/another/bin
[vm]
paths=/extra/exec/PATH:/another/bin
ld_paths=/extra/lib/PATH:/another/lib
exports=VAR1=val1,VAR2=val2
configs=relative/path/config,another/config
init_prompt=expected_on-boot#
virtme_opt=--opt,--another one
default_timeout=15
boot_timeout=45
slowdown=2.5 # mark the machine as slow and multiply the ksft timeout by 2.5
"""


def decode_and_filter(buf):
    while True:
        ctrl_seq = buf.find(b'\x1b[?2004')
        if ctrl_seq == -1:
            break
        buf = buf[:ctrl_seq] + buf[ctrl_seq + 8:]

    buf = buf.decode("utf-8", "ignore")
    return "".join([x for x in buf if (x in ['\n'] or unicodedata.category(x)[0]!="C")])


def finger_print_skip_pfx_len(filters, needles):
    # Filter may contain a list of needles we want to skip
    # Assume it's well sorted, so we don't need LPM...
    if filters and 'crash-prefix-skip' in filters:
        for skip_pfx in filters['crash-prefix-skip']:
            if len(needles) < len(skip_pfx):
                continue
            if needles[:len(skip_pfx)] == skip_pfx:
                return len(skip_pfx)
    return 0


def crash_finger_print(filters, lines):
    needles = []
    need_re = re.compile(r'.*(  |0:)([a-z0-9_]+)\+0x[0-9a-f]+/0x[0-9a-f]+.*')
    skip = 0
    for line in lines:
        m = need_re.match(line)
        if not m:
            continue
        needles.append(m.groups()[1])
        skip = finger_print_skip_pfx_len(filters, needles)
        if len(needles) - skip == 5:
            break

    needles = needles[skip:]
    return ":".join(needles)


class VM:
    def __init__(self, config, vm_name=""):
        self.fail_state = ""
        self.p = None
        self.procs = []
        self.config = config
        self.vm_name = vm_name
        self.print_pfx = (": " + vm_name) if vm_name else ":"

        self.cfg_boot_to = int(config.get('vm', 'boot_timeout'))

        self.filter_data = None
        self.has_kmemleak = None
        self.log_out = ""
        self.log_err = ""

    def tree_popen(self, cmd):
        env = os.environ.copy()
        if self.config.get('env', 'paths'):
            env['PATH'] += ':' + self.config.get('env', 'paths')

        return subprocess.Popen(cmd, env=env, cwd=self.config.get('local', 'tree_path'),
                                stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def tree_cmd(self, cmd):
        if isinstance(cmd, str):
            cmd = cmd.split()
        self.log_out += "> TREE CMD: " + " ".join(cmd) + "\n"
        proc = self.tree_popen(cmd)
        stdout, stderr = proc.communicate()
        self.log_out += stdout.decode("utf-8", "ignore")
        self.log_err += stderr.decode("utf-8", "ignore")
        proc.stdout.close()
        proc.stderr.close()
        return proc.returncode

    def build(self, extra_configs, override_configs=None):
        if self.log_out or self.log_err:
            raise Exception("Logs were not flushed before calling build")

        configs = []
        if override_configs is not None:
            configs += override_configs
        elif self.config.get('vm', 'configs', fallback=None):
            configs += self.config.get('vm', 'configs').split(",")
        if extra_configs:
            configs += extra_configs

        print(f"INFO{self.print_pfx} building kernel")
        # Make sure we rebuild, config and module deps can be stale otherwise
        self.tree_cmd("make mrproper")

        rc = self.tree_cmd("vng -v -b" + " -f ".join([""] + configs))
        if rc != 0:
            print(f"INFO{self.print_pfx} kernel build failed")
            return False

        return True

    def _get_ksft_timeout(self):
        default_timeout = 45 # from tools/testing/selftests/kselftest/runner.sh

        targets = self.config.get('ksft', 'target', fallback=None)
        tree_path = self.config.get('local', 'tree_path', fallback=None)
        if not targets or not tree_path:
            return default_timeout
        target = targets.split()[0]

        settings_path = f'{tree_path}/tools/testing/selftests/{target}/settings'
        if not os.path.isfile(settings_path):
            return default_timeout

        with open(settings_path, 'r') as fp:
            lines = fp.readlines()
            for l in lines:
                if l.startswith('timeout='):
                    return int(l.split('=')[1])

        return default_timeout

    def _set_env(self):
        # Install extra PATHs
        if self.config.get('vm', 'paths', fallback=None):
            self.cmd("export PATH=" + self.config.get('vm', 'paths') + ':$PATH')
            self.drain_to_prompt()

        if self.config.get('vm', 'ld_paths', fallback=None):
            self.cmd("export LD_LIBRARY_PATH=" + self.config.get('vm', 'ld_paths') + ':$LD_LIBRARY_PATH')
            self.drain_to_prompt()

        exports = self.config.get('vm', 'exports', fallback=None)
        if exports:
            for export in exports.split(','):
                self.cmd("export " + export)
                self.drain_to_prompt()

        slowdown = self.config.getfloat('vm', 'slowdown', fallback=0)
        if slowdown:
            self.cmd("export KSFT_MACHINE_SLOW=yes")
            self.drain_to_prompt()

            # only when needed, to avoid 'overriding timeout' message
            if slowdown > 1:
                timeout = self._get_ksft_timeout() * slowdown
                self.cmd(f"export kselftest_override_timeout={round(timeout)}")
                self.drain_to_prompt()

        self.cmd("env")
        self.drain_to_prompt()

    def start(self, cwd=None):
        cmd = "vng -v -r arch/x86/boot/bzImage --user root"
        cmd = cmd.split(' ')
        if cwd:
            cmd += ["--cwd", cwd]

        opts = self.config.get('vm', 'virtme_opt', fallback="")
        cmd += opts.split(',') if opts else []

        cpus = self.config.get('vm', 'cpus', fallback="")
        if cpus:
            cmd += ["--cpus", cpus]

        print(f"INFO{self.print_pfx} VM starting:", " ".join(cmd))
        self.log_out += "# " + " ".join(cmd) + "\n"
        self.p = self.tree_popen(cmd)

        for pipe in [self.p.stdout, self.p.stderr]:
            flags = fcntl.fcntl(pipe, fcntl.F_GETFL)
            fcntl.fcntl(pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # get the output
        init_prompt = self.config.get('vm', 'init_prompt')
        if init_prompt[-1] != ' ':
            init_prompt += ' '
        print(f"INFO{self.print_pfx} expecting prompt: '{init_prompt}'")
        try:
            self.drain_to_prompt(prompt=init_prompt, dump_after=self.cfg_boot_to)
        finally:
            # Save the children, we'll need to kill them on crash
            proc = psutil.Process(self.p.pid)
            self.procs = proc.children(recursive=True) + [proc]

        print(f"INFO{self.print_pfx} reached initial prompt")
        self.cmd("PS1='xx__-> '")
        self.drain_to_prompt()

        off = len(self.log_out)
        self.cmd("ls /sys/kernel/debug/")
        self.drain_to_prompt()
        self.has_kmemleak = "kmemleak" in self.log_out[off:]

        self._set_env()

    def stop(self):
        self.cmd("exit")
        try:
            stdout, stderr = self.p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            print(f"WARN{self.print_pfx} process did not exit, sending a KILL to", self.p.pid, self.procs)
            for p in self.procs:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            stdout, stderr = self.p.communicate(timeout=2)

        self.p.stdout.close()
        self.p.stderr.close()
        stdout = stdout.decode("utf-8", "ignore")
        stderr = stderr.decode("utf-8", "ignore")

        print(f"INFO{self.print_pfx} VM stopped")
        self.log_out += stdout
        self.log_err += stderr


    def cmd(self, command):
        buf = command.encode('utf-8')
        if buf[-1] != '\n':
            buf += b'\n'
        self.p.stdin.write(buf)
        self.p.stdin.flush()

    def ctrl_c(self):
        self.log_out += '\nCtrl-C stdout\n'
        self.log_err += '\nCtrl-C stderr\n'
        self.p.stdin.write(b'\x03')
        self.p.stdin.flush()

    def kill_current_cmd(self):
        try:
            self.ctrl_c()
            self.ctrl_c()
            self.drain_to_prompt(dump_after=12)
        except TimeoutError:
            print(f"WARN{self.print_pfx} failed to interrupt process")

    def _read_pipe_nonblock(self, pipe):
        read_some = False
        output = ""
        try:
            buf = os.read(pipe.fileno(), 1024)
            if not buf:
                return read_some, output
            read_some = True
            output = decode_and_filter(buf)
            if output.find("] RIP: ") != -1 or \
                    output.find("] Call Trace:") != -1 or \
                    output.find('] ref_tracker: ') != -1 or \
                    output.find('unreferenced object 0x') != -1:
                self.fail_state = "oops"
        except BlockingIOError:
            pass
        return read_some, output

    def drain_to_prompt(self, prompt="xx__-> ", dump_after=None, deadline=None):
        _dump_after = dump_after
        if dump_after is None:
            dump_after = self.config.getint('vm', 'default_timeout')
        hard_stop = self.config.getint('vm', 'hard_timeout', fallback=(1 << 63))
        if deadline is not None:
            hard_stop = max(0, min(deadline, hard_stop))

        waited = 0
        total_wait = 0
        stdout = ""
        stderr = ""
        while True:
            read_some, out = self._read_pipe_nonblock(self.p.stdout)
            self.log_out += out
            stdout += out
            read_som2, err = self._read_pipe_nonblock(self.p.stderr)
            read_some |= read_som2
            self.log_err += err
            stderr += err

            if read_some:
                if stdout.endswith(prompt):
                    break
                if self.fail_state == "oops" and _dump_after is None and dump_after > 300:
                    dump_after = 300
                    self.log_out += '\nDETECTED CRASH, lowering timeout\n'

                # A bit of a hack, sometimes kernel spew will clobber
                # the prompt. Until we have a good way of sending kernel
                # logs elsewhere try to get a new prompt by sending a new line.
                if prompt in out:
                    self.cmd('\n')
                    sleep(0.25)
                waited = 0
            else:
                total_wait += 0.03
                waited += 0.03
                sleep(0.03)

            if total_wait > hard_stop:
                self.log_err += f'\nHARD STOP ({hard_stop})\n'
                waited = 1 << 63
            if waited > dump_after:
                print(f"WARN{self.print_pfx} TIMEOUT retcode:", self.p.returncode,
                      "waited:", waited, "total:", total_wait)
                self.log_out += '\nWAIT TIMEOUT stdout\n'
                self.log_err += '\nWAIT TIMEOUT stderr\n'
                if not self.fail_state:
                    self.fail_state = "timeout"
                raise TimeoutError(stderr, stdout)

        if self.fail_state == "timeout":
            self.fail_state = ""

        return stdout, stderr

    def dump_log(self, dir_path, result=None, info=None):
        os.makedirs(dir_path)

        if self.log_out:
            with open(os.path.join(dir_path, 'stdout'), 'w') as fp:
                fp.write(self.log_out)
        if self.log_err:
            with open(os.path.join(dir_path, 'stderr'), 'w') as fp:
                fp.write(self.log_err)
        if result is not None:
            with open(os.path.join(dir_path, 'result'), 'w') as fp:
                fp.write(repr(result))
        if info is not None:
            strinfo = ""
            for k, v in info.items():
                strinfo += f'{k}:\t{v}\n'
            with open(os.path.join(dir_path, 'info'), 'w') as fp:
                fp.write(strinfo)

        self.log_out = ""
        self.log_err = ""

    def _load_filters(self):
        if self.filter_data is not None:
            return
        url = self.config.get("remote", "filters", fallback=None)
        if not url:
            return
        r = requests.get(url)
        self.filter_data = json.loads(r.content.decode('utf-8'))

    def extract_crash(self, out_path):
        in_crash = False
        start = 0
        crash_lines = []
        finger_prints = set()
        last5 = [""] * 5
        combined = self.log_out.split('\n') + self.log_err.split('\n')
        for line in combined:
            if in_crash:
                in_crash &= '] ---[ end trace ' not in line
                in_crash &= ']  </TASK>' not in line
                in_crash &= line[-2:] != '] '
                if not in_crash:
                    self._load_filters()
                    finger_prints.add(crash_finger_print(self.filter_data,
                                                         crash_lines[start:]))
            else:
                in_crash |= '] Hardware name: ' in line
                in_crash |= '] ref_tracker: ' in line
                if in_crash:
                    start = len(crash_lines)
                    crash_lines += last5

            # Keep last 5 to get some of the stuff before stack trace
            last5 = last5[1:] + ["| " + line]

            if in_crash:
                crash_lines.append(line)
        if not crash_lines:
            print(f"WARN{self.print_pfx} extract_crash found no crashes")
            return ["crash-extract-fail"]

        proc = self.tree_popen("./scripts/decode_stacktrace.sh vmlinux auto ./".split())
        stdout, stderr = proc.communicate("\n".join(crash_lines).encode("utf-8"))
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()
        decoded = stdout.decode("utf-8", "ignore")

        with open(out_path, 'a') as fp:
            fp.write("======================================\n")
            fp.write(decoded)
            fp.write("\n\nFinger prints:\n" + "\n".join(finger_prints))

        self._load_filters()
        if self.filter_data is not None and 'ignore-crashes' in self.filter_data:
            ignore = set(self.filter_data["ignore-crashes"])
            if not finger_prints - ignore:
                print(f"INFO{self.print_pfx} all crashes were ignored")
                self.fail_state = ""
        return list(finger_prints)

    def check_health(self):
        if self.fail_state:
            return
        if self.has_kmemleak:
            self.cmd("echo scan > /sys/kernel/debug/kmemleak && cat /sys/kernel/debug/kmemleak")
            self.drain_to_prompt()

    def bash_prev_retcode(self):
        self.cmd("echo $?")
        stdout, stderr = self.drain_to_prompt()
        return int(stdout.split('\n')[1])


def new_vm(results_path, vm_id, thr=None, vm=None, config=None, cwd=None):
    thr_pfx = f"thr{thr}-" if thr is not None else ""
    if vm is None:
        vm = VM(config, vm_name=f"{thr_pfx}{vm_id + 1}")
    # For whatever reason starting sometimes hangs / crashes
    i = 0
    while True:
        try:
            vm.start(cwd=cwd)
            vm_id += 1
            vm.dump_log(results_path + '/vm-start-' + thr_pfx + str(vm_id))
            return vm_id, vm
        except TimeoutError:
            i += 1
            if i > 4:
                raise
            print(f"WARN{vm.print_pfx} VM did not start, retrying {i}/4")
            vm.dump_log(results_path + f'/vm-crashed-{thr_pfx}{vm_id}-{i}')
            vm.stop()


def guess_indicators(output):
    return {
        "fail": output.find("[FAIL]") != -1 or output.find("[fail]") != -1 or \
                output.find("\nnot ok 1 selftests: ") != -1 or \
                output.find("\n# not ok 1") != -1,
        "skip": output.find("[SKIP]") != -1 or output.find("[skip]") != -1 or \
                output.find(" # SKIP") != -1 or output.find("SKIP:") != -1,
        "pass": output.find("[OKAY]") != -1 or output.find("[PASS]") != -1 or \
                output.find("[ OK ]") != -1 or output.find("[OK]") != -1 or \
                output.find("[ ok ]") != -1 or output.find("[pass]") != -1 or \
                output.find("PASSED all ") != -1 or output.find("\nok 1 selftests: ") != -1,
    }

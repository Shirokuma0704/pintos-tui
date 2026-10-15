#!/usr/bin/env python3
"""PintOS 테스트와 GDB를 한 터미널에서 열어요. 기본은 Project 2(User Programs)예요.
Python 표준 라이브러리만 써요."""

import argparse
import curses
import locale
import os
from pathlib import Path
import re
import runpy
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
PINTOS = ROOT / "pintos"
LOGS = ROOT / ".tui" / "logs"
# 어느 프로젝트 폴더에서 빌드할지는 main()에서 --project 로 정해요.
PROJECT = PINTOS / "userprog"
BUILD = PROJECT / "build"
TITLES = {"threads": "Threads", "userprog": "User Programs", "vm": "Virtual Memory", "filesys": "File Systems"}
DEFAULT_TIMEOUT = 60        # tests/Make.tests 의 TIMEOUT 기본값과 같아요
# 휠은 ncurses에서 4·5번 버튼 '눌림'으로 와요. 오래된 파이썬에는 5번 상수가 없어서 ncurses 6 값으로 채워요.
WHEEL_UP = curses.BUTTON4_PRESSED
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0x200000)


class Test:
    __slots__ = ("path", "name", "section", "timeout")

    def __init__(self, path, section, timeout):
        self.path = path
        self.name = Path(path).name
        self.section = section
        self.timeout = timeout


class Section:
    """TEST_SUBDIRS 한 칸이 한 묶음이에요. extra는 Make.vars에 주석으로 꺼 둔 추가 과제예요."""

    def __init__(self, directory, label, short, extra):
        self.directory = directory
        self.label = label
        self.short = short
        self.extra = extra
        self.tests = []


def make_text(path):
    """Make 파일을 읽고, 줄 끝 \\ 로 이어 쓴 줄은 한 줄로 합쳐요."""
    return re.sub(r"\\\n", " ", path.read_text())


def read_subdirs(project):
    """<프로젝트>/Make.vars 의 TEST_SUBDIRS 를 읽어요. make check 가 돌리는 목록이 바로 이거예요.
    주석으로 꺼 둔 `# TEST_SUBDIRS += ...` 줄은 추가 과제로 따로 모아요."""
    official, extra = [], []
    for line in make_text(project / "Make.vars").splitlines():
        match = re.match(r"\s*(#\s*)?TEST_SUBDIRS\s*\+?=\s*(.*)", line)
        if match:
            words = [word for word in match[2].split() if not word.startswith("$")]
            (extra if match[1] else official).extend(words)
    return official, [directory for directory in dict.fromkeys(extra) if directory not in official]


def read_tests(directory):
    """<묶음>/Make.tests 에서 테스트 이름과 시간 제한을 읽어요. 두 모양을 다 받아요.
    `dir_TESTS = $(addprefix 앞부분,이름 이름 ...)` 와 `dir_TESTS = 경로 경로 ...`"""
    text = make_text(PINTOS / directory / "Make.tests")
    names = []
    for match in re.finditer(rf"^{re.escape(directory)}_TESTS\s*\+?=\s*(.*)$", text, re.M):
        value = match[1].strip()
        prefixed = re.fullmatch(r"\$\(addprefix\s+([^,]+),(.*)\)", value)
        names += [prefixed[1].strip() + name for name in prefixed[2].split()] if prefixed else value.split()
    if not names:
        raise RuntimeError(f"테스트 목록을 읽지 못했어요: {directory}/Make.tests")
    # `$(MLFQS_OUTPUTS): TIMEOUT = 480` 처럼 변수로 건 제한은 그 파일의 테스트 전부에 걸어요.
    default = DEFAULT_TIMEOUT
    for match in re.finditer(r"^\$\(\w+\):\s*TIMEOUT\s*=\s*(\d+)", text, re.M):
        default = int(match[1])
    own = {match[1]: int(match[2])
           for match in re.finditer(r"^(\S+)\.output:\s*TIMEOUT\s*=\s*(\d+)", text, re.M)}
    return [(name, own.get(name, default)) for name in names]


def family(test_name):
    return test_name.split("-")[0]


def discover(project):
    """묶음 순서: make check 순서를 따르되 하위 폴더는 부모 바로 뒤에, 추가 과제는 맨 뒤에 둬요.
    묶음 안에서는 이름 앞부분(create-, open-, ...)이 같은 것끼리 Make.tests에 처음 나온 순서로 모아요."""
    official, extra = read_subdirs(project)
    every = official + extra

    def top(directory):
        parents = [other for other in every if directory.startswith(other + "/")]
        return every.index(min(parents, key=len)) if parents else every.index(directory)

    sections, used = [], set()
    for directory in sorted(every, key=lambda d: (d in extra, top(d), every.index(d))):
        label = directory.removeprefix("tests/")
        short = label.split("/")[0]
        if short in used:
            short = label.split("/")[-1]
        used.add(short)
        section = Section(directory, label, short, directory in extra)
        tests = read_tests(directory)
        order = {}
        for name, _ in tests:
            order.setdefault(family(Path(name).name), len(order))
        for name, timeout in sorted(tests, key=lambda item: order[family(Path(item[0]).name)]):
            section.tests.append(Test(name, section, timeout))
        sections.append(section)
    # 추가 과제 규칙은 Make.vars가 꺼 둬서, 그 테스트를 돌릴 때만 make에 목록을 직접 넘겨줘요.
    make_vars = ["TEST_SUBDIRS=" + " ".join(every)] if extra else []
    return sections, make_vars


def environment(temp_dir=None):
    env = os.environ.copy()
    env["PATH"] = str(PINTOS / "utils") + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    if temp_dir:
        env["TMPDIR"] = temp_dir
    return env


def read_lines(path):
    """Windows 폴더를 바인드 마운트하면, 쓰는 중인 파일을 읽다가 ENODATA 같은 OSError가 가끔 나요.
    잠깐 쉬고 몇 번 다시 읽어 보고, 그래도 안 되면 빈 내용으로 넘겨서 화면이 죽지 않게 해요."""
    for attempt in range(3):
        try:
            return path.read_text(errors="replace").splitlines()
        except FileNotFoundError:
            return []
        except OSError:
            time.sleep(0.05 * (attempt + 1))
    return []


def result_of(test):
    lines = read_lines(BUILD / (test + ".result"))
    return "PASS" if lines == ["PASS"] else ("FAIL" if lines else "--")


def stop_process(process):
    """우리가 시작한 프로세스 그룹만 종료해요. make 아래의 QEMU도 함께 정리해요."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def test_command(test, make_vars):
    """make가 이 테스트를 돌릴 때 쓰는 pintos 명령을 그대로 받아 와요.
    -n 은 실행하지 않고 명령만 보여 주고, -W os.dsk 는 결과가 최신이어도 다시 돌릴 것처럼 굴게 해요.
    돌려주는 값: (pintos 인자, 먼저 빌드해 둘 파일들 = -p 로 디스크에 넣는 유저 프로그램 등)"""
    done = subprocess.run(["make", "-n", "-W", "os.dsk", *make_vars, test + ".output"], cwd=BUILD,
                          env=environment(), stdin=subprocess.DEVNULL, capture_output=True, text=True)
    line = next((line for line in done.stdout.splitlines() if line.startswith("pintos ")), None)
    if line is None:
        raise RuntimeError("make에서 이 테스트의 pintos 명령을 못 찾았어요: " + (done.stderr.strip() or "출력 없음"))
    words = shlex.split(line)[1:]
    for index, word in enumerate(words):
        if word in ("<", ">", "2>"):
            words = words[:index]
            break
    if "-T" in words:                       # 디버깅 중에는 시간 제한으로 QEMU가 끊기면 안 돼요
        index = words.index("-T")
        del words[index:index + 2]
    hosts = [word.split(":")[0] for before, word in zip(words, words[1:]) if before == "-p"]
    return words, hosts


class Runner:
    def __init__(self, tests, make_vars):
        self.process = None
        self.output = None
        self.queue = []
        self.current = None
        self.phase = "idle"
        self.message = "테스트를 고르고 Enter로 실행해 보세요."
        self.log = LOGS / "run.log"
        self.deadline = 0
        self.temp_dir = None
        self.errors = set()
        self.timeouts = {test.path: test.timeout for test in tests}
        self.make_vars = make_vars
        # 판정 파일은 테스트가 끝날 때만 바뀌어요. 바인드 마운트에서 파일 하나 여는 게 느려서
        # 화면을 그릴 때마다 전부 다시 읽지 않고, 한 번 읽은 판정을 기억해 둬요.
        self.verdicts = {}
        # 여러 개를 돌릴 땐 make -jN 하나로 한꺼번에 돌려요. 테스트마다 디스크 사본을 따로 써서 안 겹쳐요.
        self.parallel = True
        self.jobs = max(1, (os.cpu_count() or 2) // 2)
        self.pending = set()
        self.next_poll = 0
        self.started = 0

    def is_running(self, test):
        return test == self.current or test in self.pending

    def status(self, test):
        if test in self.errors:
            return "ERR"
        if test not in self.verdicts:
            self.verdicts[test] = result_of(test)
        return self.verdicts[test]

    def forget(self, test):
        """테스트 산출물을 지우고 기억해 둔 판정도 같이 비워요."""
        for suffix in (".output", ".result", ".errors"):
            (BUILD / (test + suffix)).unlink(missing_ok=True)
        self.verdicts[test] = "--"

    @property
    def busy(self):
        return self.phase != "idle"

    def start(self, tests):
        if self.busy:
            return
        LOGS.mkdir(parents=True, exist_ok=True)
        self.queue = list(tests)
        self.current = None
        self.started = time.monotonic()
        self.phase = "build"
        self.log = LOGS / "run.log"
        self.output = self.log.open("w")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="pintos-tui-")
        # 처음 빌드는 바인드 마운트에서 몇 분 걸릴 수 있어요.
        self.spawn(["make", *self.make_vars], PROJECT, 600)
        self.message = f"빌드 중 · 실행 대기 {len(self.queue)}개"

    def spawn(self, command, directory, timeout):
        self.output.write("$ " + shlex.join(command) + "\n")
        self.output.flush()
        self.process = subprocess.Popen(command, cwd=directory, env=environment(self.temp_dir.name),
                                        stdin=subprocess.DEVNULL, stdout=self.output,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        self.deadline = time.monotonic() + timeout

    def finish(self, message):
        self.phase = "idle"
        self.process = None
        self.queue = []
        self.current = None
        self.pending = set()
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None
        if self.output:
            self.output.close()
            self.output = None
        self.message = message

    def poll_results(self, force=False):
        """병렬 실행 중에 새로 생긴 판정 파일을 1초에 한 번만 확인해요.
        파일마다 따로 묻지 않고 폴더 목록을 한 번씩만 읽어요. 바인드 마운트에서는 그게 훨씬 싸요."""
        if not force and time.monotonic() < self.next_poll:
            return
        self.next_poll = time.monotonic() + 1
        found = set()
        for directory in {str(Path(test).parent) for test in self.pending}:
            try:
                found |= {directory + "/" + entry.name[:-len(".result")]
                          for entry in os.scandir(BUILD / directory) if entry.name.endswith(".result")}
            except OSError:
                pass
        for test in self.pending & found:
            verdict = result_of(test)
            if verdict != "--":
                self.verdicts[test] = verdict
                self.pending.discard(test)
        elapsed = int(time.monotonic() - self.started)
        self.message = f"병렬 실행 중 (-j{self.jobs}) · 남은 {len(self.pending)}개 · {elapsed // 60}분 {elapsed % 60}초"

    def start_batch(self):
        tests, self.queue = self.queue, []
        for test in tests:
            self.errors.discard(test)
            self.forget(test)
        self.pending = set(tests)
        self.phase = "batch"
        jobs = min(self.jobs, len(tests))
        limits = [self.timeouts[test] + 30 for test in tests]
        # -k: 유저 프로그램 하나가 컴파일에 실패해도 나머지 테스트는 계속 돌려요.
        self.spawn(["make", "-k", f"-j{jobs}", *self.make_vars] + [test + ".result" for test in tests], BUILD,
                   sum(limits) // jobs + max(limits) + 300)
        self.poll_results(force=True)

    def finish_batch(self, code):
        self.poll_results(force=True)
        for test in self.pending:
            self.errors.add(test)
            self.output.write(f"\nERR {test} (판정 파일이 안 생겼어요)\n")
        elapsed = int(time.monotonic() - self.started)
        message = f"실행을 마쳤어요. 병렬로 {elapsed // 60}분 {elapsed % 60}초 걸렸어요."
        if self.errors:
            message += f" 실행 오류 {len(self.errors)}개는 로그를 확인하세요."
        self.output.write(f"\n(make exit {code})\n")
        self.finish(message)

    def tick(self):
        if not self.busy:
            return
        code = self.process.poll()
        if self.phase == "batch":
            self.poll_results()
        if code is None and time.monotonic() < self.deadline:
            return
        if code is None:
            stop_process(self.process)
            for test in ([self.current] if self.current else []) + list(self.pending):
                self.errors.add(test)
                self.forget(test)
            self.finish("시간 제한을 넘었어요. 실행 로그를 확인하세요.")
            return
        if self.phase == "build" and code != 0:
            self.finish(f"빌드 실패 (종료 코드 {code}) · 실행 로그를 확인하세요.")
            return
        if self.phase == "batch":
            self.finish_batch(code)
            return
        if self.phase == "build" and self.parallel and len(self.queue) > 1:
            self.start_batch()
            return
        if self.current:
            verdict = self.verdicts[self.current] = result_of(self.current)
            if code != 0 or verdict == "--":
                self.errors.add(self.current)
                verdict = "ERR"
            self.output.write(f"\n{verdict} {self.current} (make exit {code})\n")
            self.output.flush()
        if not self.queue:
            message = "실행을 마쳤어요. 판정은 PintOS의 .result 파일에서 읽어요."
            if self.errors:
                message += f" 실행 오류 {len(self.errors)}개는 로그를 확인하세요."
            self.finish(message)
            return
        self.current = self.queue.pop(0)
        self.errors.discard(self.current)
        self.phase = "test"
        # 선택한 테스트의 산출물만 지워서 make가 실제로 다시 실행하게 해요.
        self.forget(self.current)
        self.spawn(["make", *self.make_vars, self.current + ".result"], BUILD,
                   self.timeouts[self.current] + 300)
        self.message = f"실행 중 · {Path(self.current).name} · 뒤에 {len(self.queue)}개"

    def cancel(self):
        if self.busy:
            stop_process(self.process)
            for test in ([self.current] if self.current else []) + list(self.pending):
                self.forget(test)
            self.finish("실행을 중단했어요. 중단된 테스트의 불완전한 결과는 지웠어요.")


class DebugSession:
    def __init__(self, test, make_vars):
        self.test = test
        self.make_vars = make_vars
        self.process = None
        self.output = None
        self.log = LOGS / "debug.log"
        self.temp_dir = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]

    def start(self):
        """make가 쓰는 pintos 명령을 받아 와서, 디스크에 넣을 유저 프로그램을 빌드하고 QEMU를 멈춘 채로 띄워요.
        준비가 안 되면 이유를 문자열로 돌려줘요."""
        LOGS.mkdir(parents=True, exist_ok=True)
        self.output = self.log.open("w")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="pintos-tui-debug-")
        try:
            arguments, hosts = test_command(self.test, self.make_vars)
        except RuntimeError as error:
            self.output.write(f"{error}\n")
            return str(error)
        command = ["make", *self.make_vars, *hosts]
        self.output.write("$ " + shlex.join(command) + "\n")
        self.output.flush()
        if hosts and subprocess.run(command, cwd=BUILD, env=environment(), stdin=subprocess.DEVNULL,
                                    stdout=self.output, stderr=subprocess.STDOUT).returncode != 0:
            return "유저 프로그램 빌드에 실패했어요. 디버그 QEMU 로그를 확인하세요."
        self.output.write("$ pintos " + shlex.join(arguments) + "  (+ --gdb)\n")
        self.output.flush()
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--port", str(self.port), "--qemu", *arguments],
            cwd=BUILD, env=environment(self.temp_dir.name), stdin=subprocess.DEVNULL, stdout=self.output,
            stderr=subprocess.STDOUT, start_new_session=True)
        return None

    def gdb_command(self, tui=True):
        command = ["gdb", "-q", "-nx", str(BUILD / "kernel.o")]
        if tui:
            command.append("-tui")
        commands = ["set pagination off", "set confirm off", "set tcp auto-retry on",
                    "set tcp connect-timeout 5", f"directory {PINTOS}"]
        program = BUILD / self.test
        if program.is_file():
            # 유저 프로그램 심볼도 같이 올려서 `b main` 이나 유저 코드 줄 번호로도 멈출 수 있게 해요.
            commands.append(f"add-symbol-file {program}")
        # run_task 는 threads/init.c 에서 테스트 하나를 시작하는 함수예요. 유저 프로그램도 커널 테스트도 여기를 지나요.
        commands += [f"target remote 127.0.0.1:{self.port}", "hbreak run_task", "continue", "next"]
        if tui:
            commands += ["layout src", "focus cmd"]
        for command_text in commands:
            command.extend(["-ex", command_text])
        return command

    def close(self):
        stop_process(self.process)
        if self.output:
            self.output.close()
        if self.temp_dir:
            self.temp_dir.cleanup()


def run_qemu(arguments, port):
    """원래 pintos 스크립트를 그대로 돌려요. 디스크 준비·정리는 그쪽에 맡기고,
    QEMU를 띄우는 순간에만 기본 디버그 포트(-s = 1234)를 우리가 고른 빈 포트로 바꿔요."""
    real_run = subprocess.run

    def run(command, *args, **kwargs):
        if command and str(command[0]).startswith("qemu-system") and "-s" in command:
            index = command.index("-s")
            command = command[:index] + ["-gdb", f"tcp:127.0.0.1:{port}"] + command[index + 1:]
        return real_run(command, *args, **kwargs)

    subprocess.run = run
    split = arguments.index("--") if "--" in arguments else len(arguments)
    script = str(PINTOS / "utils" / "pintos")
    sys.argv = [script, *arguments[:split], "--gdb", *arguments[split:]]
    runpy.run_path(script, run_name="__main__")


def fit(text, columns):
    result = ""
    width = 0
    for char in str(text).expandtabs(4):
        if unicodedata.category(char).startswith("C"):
            char = " "
        size = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if width + size > columns:
            break
        result += char
        width += size
    return result


def text_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


HELP = [
    "Enter/r  선택한 테스트 실행 (항상 빌드 후 재실행)",
    "g        현재 묶음 전체 실행    a  make check 목록 전체 실행 (추가 과제 빼고)",
    "p        여러 개 실행할 때 병렬(make -jN) / 하나씩 바꾸기. 처음엔 병렬",
    "f        현재 묶음의 FAIL/ERR 재실행    s  실행 중단",
    "1~9      1 전체, 2부터 묶음 하나씩 (아래 줄에 번호가 나와요)",
    "o        정렬 바꾸기: 분류순(묶음별) / 결과순(FAIL·ERR 먼저)",
    "         정렬을 바꾸거나 결과가 들어와 줄이 움직여도 고른 테스트는 그대로예요",
    "/        테스트 이름 검색 (Enter 완료, Esc 취소)",
    "Tab      판정 / QEMU 출력 / 오류 / 실행 로그 / 디버그 로그",
    "PgUp/Dn  오른쪽 로그 스크롤    Home/End  처음/끝",
    "→ / ←    ↑↓(와 휠)이 움직일 쪽을 로그 / 테스트 목록으로 바꾸기",
    "         CLion 터미널은 휠을 ↑↓로 보내서, → 로 로그 쪽에 두면 휠로 로그가 내려가요",
    "j/k      어느 쪽이든 테스트 선택    [ ]  어느 쪽이든 로그 3줄 스크롤",
    "휠       마우스를 넘겨주는 터미널에서는 언제나 로그 스크롤",
    "m        마우스 켜기/끄기. 화면 글자를 드래그로 복사할 땐 끄거나 Shift+드래그",
    "d        선택한 테스트를 GDB TUI로 열기 (run_task에서 정지)",
    "         유저 프로그램 심볼도 올려 둬서 b main 으로 유저 코드에서도 멈출 수 있어요",
    "GDB: b 함수명 | n 다음 줄 | s 함수 안으로 | c 계속",
    "GDB: p 변수명 | info locals 지역변수 | bt 호출 스택",
    "GDB: tui reg general 레지스터 | layout split 소스+어셈블리",
    "GDB: Ctrl-X O 포커스 이동 | Ctrl-L 화면 복구",
    "GDB: quit 입력 → QEMU 종료, 테스트 화면으로 복귀",
    "? 도움말 닫기 · q TUI 종료 (진행 중이면 먼저 s)",
    "PASS/FAIL은 마지막 테스트 결과예요. 코드 수정 후 다시 실행하세요.",
    "시간 제한은 Make.tests 값 그대로예요 (기본 60초, syn-read 300초, multi-oom 600초).",
]
ORDERS = {"section": "분류순", "result": "결과순"}
# 결과순에서 위에서부터 보여 줄 칸이에요. FAIL과 ERR은 둘 다 고쳐야 하는 것이라 한 칸에 모아요.
BUCKETS = (("RUN", "실행 중"), ("BAD", "FAIL · ERR"), ("--", "미실행"), ("PASS", "PASS"))


class App:
    def __init__(self, screen, project):
        self.screen = screen
        self.project = project
        self.sections, make_vars = discover(PROJECT)
        self.tests = [test for section in self.sections for test in section.tests]
        self.official = [test for test in self.tests if not test.section.extra]
        self.runner = Runner(self.tests, make_vars)
        self.group = None           # None이면 전체, 아니면 Section 하나
        self.order = "section"
        self.query = ""
        self.cursor = self.tests[0] if self.tests else None   # 고른 테스트. 줄 번호가 아니라 테스트 자체를 기억해요
        self.index = 0
        self.top = 0                # 왼쪽 목록이 몇 번째 줄부터 보이는지
        self.view = 0
        self.offset = None
        self.help = False
        self.focus = "tests"        # ↑↓가 움직이는 쪽. "tests" 또는 "log"
        self.debug_pending = None
        self.screen.timeout(100)
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        for pair, color in ((1, curses.COLOR_GREEN), (2, curses.COLOR_RED), (3, curses.COLOR_CYAN),
                            (4, curses.COLOR_YELLOW)):
            curses.init_pair(pair, color, -1)
        self.mouse = True
        self.set_mouse(self.mouse)

    def set_mouse(self, on):
        """휠만 받아요. 켜 두면 터미널이 클릭을 가져가서, 글자 복사는 Shift+드래그나 m으로 끄고 해요."""
        try:
            curses.mouseinterval(0)
            curses.mousemask(WHEEL_UP | WHEEL_DOWN if on else 0)
        except curses.error:
            pass

    def scroll(self, delta):
        """오른쪽 로그를 delta줄 움직여요. 맨 끝까지 내려가면 다시 끝을 따라가요."""
        rows = max(1, self.screen.getmaxyx()[0] - 7)
        maximum = max(0, len(self.log_lines()[1]) - rows)
        position = maximum if self.offset is None else min(self.offset, maximum)
        position = max(0, position + delta)
        self.offset = None if position >= maximum and delta > 0 else position

    def state(self, test):
        return "RUN" if self.runner.is_running(test.path) else self.runner.status(test.path)

    def rows(self):
        """왼쪽 목록을 줄 단위로 만들어요. ("head", 글자, 오른쪽 숫자, 색) 또는 ("test", Test)."""
        query = self.query.lower()
        sections = [section for section in self.sections if self.group in (None, section)]
        shown = [test for section in sections for test in section.tests if query in test.name.lower()]
        rows = []
        if self.order == "section":
            for section in sections:
                tests = [test for test in shown if test.section is section]
                if not tests:
                    continue
                passed = sum(self.runner.status(test.path) == "PASS" for test in tests)
                color = curses.color_pair(1 if passed == len(tests) else 4 if section.extra else 3)
                label = section.label + (" · 추가 과제" if section.extra else "")
                rows.append(("head", label, f"{passed}/{len(tests)}", color))
                rows += [("test", test) for test in tests]
        else:
            for key, label in BUCKETS:
                tests = [test for test in shown
                         if ("BAD" if self.state(test) in ("FAIL", "ERR") else self.state(test)) == key]
                if tests:
                    color = curses.color_pair({"BAD": 2, "PASS": 1}.get(key, 3))
                    rows.append(("head", label, str(len(tests)), color))
                    rows += [("test", test) for test in tests]
        return rows

    def visible(self):
        return [row[1] for row in self.rows() if row[0] == "test"]

    def selected(self, tests=None):
        """고른 테스트가 지금 목록에 있으면 그대로, 검색·묶음으로 사라졌으면 같은 자리의 테스트로 옮겨요."""
        tests = self.visible() if tests is None else tests
        if not tests:
            return None
        if self.cursor in tests:
            self.index = tests.index(self.cursor)
        else:
            self.index = min(max(self.index, 0), len(tests) - 1)
            self.cursor = tests[self.index]
        return self.cursor

    def move(self, delta):
        tests = self.visible()
        if self.selected(tests):
            self.index = min(max(self.index + delta, 0), len(tests) - 1)
            self.cursor = tests[self.index]
        self.offset = None

    def put(self, y, x, text, attr=0, width=None):
        height, columns = self.screen.getmaxyx()
        if 0 <= y < height and 0 <= x < columns - 1:
            try:
                self.screen.addstr(y, x, fit(text, min(width or columns, columns - x - 1)), attr)
            except curses.error:
                pass

    def log_lines(self):
        test = self.selected()
        if self.help:
            return "도움말", HELP
        if self.view == 3:
            return "실행 로그", read_lines(self.runner.log)
        if self.view == 4:
            return "디버그 QEMU 로그", read_lines(LOGS / "debug.log")
        label, suffix = (("판정", ".result"), ("QEMU 출력", ".output"), ("오류", ".errors"))[self.view]
        lines = read_lines(BUILD / (test.path + suffix)) if test else []
        return label, lines

    def draw_list(self, left, rows_available):
        rows = self.rows()
        selected = self.selected([row[1] for row in rows if row[0] == "test"])
        position = next((index for index, row in enumerate(rows) if row[0] == "test" and row[1] is selected), 0)
        # 고른 줄이 화면 안에 있게 하고, 묶음의 첫 테스트를 고르면 그 위 제목 줄도 같이 보여 줘요.
        if position - 1 < self.top:
            self.top = max(0, position - 1)
        if position >= self.top + rows_available:
            self.top = position - rows_available + 1
        self.top = max(0, min(self.top, len(rows) - rows_available))
        inner = left - 1
        for y, row in enumerate(rows[self.top:self.top + rows_available], 3):
            if row[0] == "head":
                _, label, count, color = row
                self.put(y, 1, label, color | curses.A_BOLD | curses.A_UNDERLINE, inner - len(count) - 1)
                self.put(y, 1 + inner - len(count), count, color | curses.A_BOLD)
                continue
            test = row[1]
            status = self.state(test)
            attr = curses.color_pair(1 if status == "PASS" else 2 if status in ("FAIL", "ERR") else 3)
            if test is selected:
                attr |= curses.A_REVERSE
            text = f" {status:4} {test.name}"
            if self.order == "result" and self.group is None:
                # 결과순에서는 묶음 제목이 없어서, 어느 묶음 테스트인지 오른쪽 끝에 작게 붙여요.
                gap = inner - text_width(text) - len(test.section.short)
                if gap >= 1:
                    self.put(y, 1, text + " " * gap, attr, inner)
                    self.put(y, 1 + inner - len(test.section.short), test.section.short,
                             (attr & curses.A_REVERSE) | curses.A_DIM)
                    continue
            self.put(y, 1, text.ljust(inner) if test is selected else text, attr, inner)

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 12 or width < 80:
            self.put(0, 0, "터미널을 80열 × 12줄 이상으로 넓혀 주세요. q로 종료할 수 있어요.")
            self.screen.refresh()
            return
        left = min(44, max(36, width // 3))
        rows = height - 7
        statuses = [self.runner.status(test.path) for test in self.official]
        extras = [test for test in self.tests if test.section.extra]
        summary = (f"PintOS · {TITLES.get(self.project, self.project)}   make check {len(statuses)}개: "
                   f"PASS {statuses.count('PASS')} / FAIL {statuses.count('FAIL')} / 오류 {statuses.count('ERR')}"
                   f" / 미실행 {statuses.count('--')}")
        if extras:
            summary += f"   추가 과제 PASS {sum(self.runner.status(test.path) == 'PASS' for test in extras)}/{len(extras)}"
        self.put(0, 1, summary + (f"   병렬 ×{self.runner.jobs}" if self.runner.parallel else "   하나씩"), curses.A_BOLD)
        group = self.group.label if self.group else "전체"
        self.put(1, 1, f"묶음: {group}   정렬: {ORDERS[self.order]} (o)   검색: {self.query or '없음'}   ? 도움말",
                 curses.color_pair(3))
        focused = curses.A_BOLD | curses.A_REVERSE
        self.put(2, 1, ("▶ " if self.focus == "tests" else "  ") + "테스트 / 마지막 판정",
                 focused if self.focus == "tests" else curses.A_BOLD, left - 1)
        label, lines = self.log_lines()
        self.put(2, left + 2, ("▶ " if self.focus == "log" else "") + label
                 + (" · 끝을 따라가는 중" if self.offset is None else " · 스크롤 중"),
                 focused if self.focus == "log" else curses.A_BOLD)
        self.draw_list(left, rows)
        maximum = max(0, len(lines) - rows)
        offset = maximum if self.offset is None else min(self.offset, maximum)
        for row, line in enumerate((lines or ["아직 출력이 없어요."])[offset:offset + rows], 3):
            self.put(row, left + 2, line)
        self.put(height - 3, 1, self.runner.message, curses.color_pair(3))
        arrows = "↑↓ 테스트 선택  → 로그로" if self.focus == "tests" else "↑↓ 로그 스크롤  ← 테스트로"
        self.put(height - 2, 1, arrows + "  Enter 실행  g 묶음  a 전체  f 실패  d 디버그  Tab 로그  s 중단  q 종료")
        groups = "  ".join(f"{number} {section.short}" for number, section in enumerate(self.sections[:8], 2))
        self.put(height - 1, 1, f"1 전체  {groups}   o 정렬  / 검색  p 병렬  "
                 + ("m 마우스 끄기" if self.mouse else "m 마우스 켜기") + "  ? 도움말")
        self.screen.refresh()

    def search(self):
        original = self.query
        curses.curs_set(1)
        try:
            while True:
                self.draw()
                self.put(1, 1, "검색: " + self.query + "  (Enter 완료 / Esc 취소)")
                self.screen.refresh()
                self.runner.tick()
                key = self.screen.getch()
                if key in (10, 13, 27):
                    if key == 27:
                        self.query = original
                    break
                if key in (curses.KEY_BACKSPACE, 127, 8):
                    self.query = self.query[:-1]
                elif 32 <= key < 127:
                    self.query += chr(key)
                self.index, self.top = 0, 0
        finally:
            curses.curs_set(0)

    def debug(self, test):
        session = DebugSession(test.path, self.runner.make_vars)
        curses.def_prog_mode()
        curses.endwin()
        # 부모는 Ctrl-C를 넘겨받아도 유지되고, exec한 GDB는 기본 SIGINT 동작을 써요.
        old_sigint = signal.signal(signal.SIGINT, lambda signum, frame: None)
        problem = None
        try:
            print(f"\n{test.name} 디버깅 준비 중 · 유저 프로그램을 빌드하고 QEMU를 멈춘 채로 띄워요.", flush=True)
            problem = session.start()
            if problem is None:
                print("run_task에서 멈춰요. n 다음 줄 | s 함수 진입 | c 계속 | p 변수 | bt 스택 | quit 복귀",
                      flush=True)
                subprocess.run(session.gdb_command(), cwd=BUILD, env=environment())
        finally:
            session.close()
            signal.signal(signal.SIGINT, old_sigint)
            curses.reset_prog_mode()
            self.screen.clear()
            self.screen.timeout(100)
            self.set_mouse(self.mouse)
        self.runner.message = problem or "디버깅을 마쳤어요. Tab으로 디버그 QEMU 로그를 볼 수 있어요."
        self.view = 4
        self.offset = None

    def run(self):
        try:
            while True:
                self.runner.tick()
                if self.debug_pending and not self.runner.busy:
                    test, self.debug_pending = self.debug_pending, None
                    if (BUILD / "kernel.o").exists() and self.runner.message.startswith("실행을 마쳤"):
                        self.debug(test)
                self.draw()
                key = self.screen.getch()
                if key == ord("q"):
                    if not self.runner.busy:
                        return
                    self.runner.message = "실행 중이에요. s로 중단한 뒤 q로 나갈 수 있어요."
                elif key in (curses.KEY_RIGHT, curses.KEY_LEFT):
                    self.focus = "log" if key == curses.KEY_RIGHT else "tests"
                elif key in (curses.KEY_UP, curses.KEY_DOWN) and self.focus == "log":
                    self.scroll(-3 if key == curses.KEY_UP else 3)
                elif key in (ord("["), ord("]")):
                    self.scroll(-3 if key == ord("[") else 3)
                elif key in (curses.KEY_UP, ord("k")):
                    self.move(-1)
                elif key in (curses.KEY_DOWN, ord("j")):
                    self.move(1)
                elif ord("1") <= key <= ord("9"):
                    number = key - ord("1")
                    if number == 0 or number <= len(self.sections):
                        self.group = self.sections[number - 1] if number else None
                        self.index, self.top, self.offset = 0, 0, None
                        self.cursor = None
                elif key == ord("o"):
                    self.order = "result" if self.order == "section" else "section"
                    self.top = 0
                    self.runner.message = ("결과순: 실행 중 → FAIL·ERR → 미실행 → PASS 순서예요. 같은 칸 안은 분류순이에요."
                                           if self.order == "result" else "분류순: make check 묶음별로, 이름 앞부분이 같은 것끼리 모아요.")
                elif key == ord("/"):
                    self.search()
                elif key == 9:
                    self.view, self.offset, self.help = (self.view + 1) % 5, None, False
                elif key == ord("?"):
                    self.help, self.offset = not self.help, 0
                elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
                    rows = max(1, self.screen.getmaxyx()[0] - 7)
                    self.scroll(-rows if key == curses.KEY_PPAGE else rows)
                elif key == curses.KEY_MOUSE:
                    try:
                        _, _, _, _, state = curses.getmouse()
                    except curses.error:
                        state = 0
                    if state & WHEEL_UP:
                        self.scroll(-3)
                    elif state & WHEEL_DOWN:
                        self.scroll(3)
                elif key == ord("p") and not self.runner.busy:
                    self.runner.parallel = not self.runner.parallel
                    self.runner.message = (f"여러 개를 돌릴 때 make -j{self.runner.jobs} 로 한꺼번에 돌려요."
                                           if self.runner.parallel else "여러 개를 돌릴 때 하나씩 차례로 돌려요.")
                elif key == ord("m"):
                    self.mouse = not self.mouse
                    self.set_mouse(self.mouse)
                    self.runner.message = ("마우스 켰어요. 휠로 로그를 내려요. 복사는 Shift+드래그예요." if self.mouse
                                           else "마우스 껐어요. 드래그로 글자를 복사할 수 있어요. 휠은 m으로 다시 켜요.")
                elif key in (curses.KEY_HOME, curses.KEY_END):
                    self.offset = 0 if key == curses.KEY_HOME else None
                elif key == ord("s"):
                    self.debug_pending = None
                    self.runner.cancel()
                elif not self.runner.busy:
                    selected = self.selected()
                    if key in (10, 13, ord("r")) and selected:
                        self.runner.start([selected.path])
                        self.view, self.offset, self.help = 3, None, False
                    elif key in (ord("g"), ord("a"), ord("f")):
                        tests = self.official if key == ord("a") else self.visible()
                        if key == ord("f"):
                            tests = [test for test in tests if self.runner.status(test.path) in ("FAIL", "ERR")]
                        if tests:
                            self.runner.start([test.path for test in tests])
                            self.view, self.offset, self.help = 3, None, False
                        else:
                            self.runner.message = "실행할 테스트가 없어요."
                    elif key == ord("d") and selected:
                        self.debug_pending = selected
                        self.runner.start([])
                        self.view, self.offset, self.help = 3, None, False
        finally:
            self.runner.cancel()


def configure(project):
    global PROJECT, BUILD
    PROJECT = PINTOS / project
    BUILD = PROJECT / "build"
    if not (PROJECT / "Make.vars").exists():
        raise SystemExit(f"{PROJECT}/Make.vars 가 없어요. --project 이름을 확인해 주세요.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="userprog",
                        help="빌드할 폴더: userprog(기본, Project 2) / threads(Project 1) / vm / filesys")
    parser.add_argument("--list", action="store_true", help="테스트 목록만 출력해요")
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    # --qemu 뒤는 pintos 인자 그대로예요. 중간에 `--`가 있어서 argparse에 맡기면 잘려요. 그래서 직접 잘라요.
    argv = sys.argv[1:]
    qemu = argv[argv.index("--qemu") + 1:] if "--qemu" in argv else None
    args = parser.parse_args(argv[:argv.index("--qemu")] if qemu is not None else argv)
    if qemu is not None:
        if not args.port:
            parser.error("--qemu에는 --port가 필요해요")
        run_qemu(qemu, args.port)
        return
    configure(args.project)
    if args.list:
        sections, make_vars = discover(PROJECT)
        for section in sections:
            tag = "  (추가 과제, make check에는 없어요)" if section.extra else ""
            print(f"[{section.label}] {len(section.tests)}개{tag}")
            for test in section.tests:
                print(f"  {test.path}  ({test.timeout}초)")
        if make_vars:
            print("추가 과제를 돌릴 때 make에 붙이는 값: " + " ".join(make_vars))
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "TUI는 터미널이 필요해요. docker exec -it로 실행해 주세요.\n")
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(lambda screen: App(screen, args.project).run())
    except KeyboardInterrupt:
        print("TUI를 종료했어요.")


if __name__ == "__main__":
    main()

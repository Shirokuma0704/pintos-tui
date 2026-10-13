#!/usr/bin/env python3
"""PintOS Threads 테스트와 GDB를 한 터미널에서 열어요. Python 표준 라이브러리만 써요."""

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
PROJECT = PINTOS / "threads"
BUILD = PROJECT / "build"
LOGS = ROOT / ".tui" / "logs"
GROUPS = ("all", "alarm", "priority", "mlfqs")
# 휠은 ncurses에서 4·5번 버튼 '눌림'으로 와요. 오래된 파이썬에는 5번 상수가 없어서 ncurses 6 값으로 채워요.
WHEEL_UP = curses.BUTTON4_PRESSED
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0x200000)


def discover_tests():
    tests = []
    for directory in ("tests/threads", "tests/threads/mlfqs"):
        text = (PINTOS / directory / "Make.tests").read_text()
        match = re.search(r"_TESTS\s*=\s*\$\(addprefix[^,]+,([^)]*)\)", text, re.S)
        if not match:
            raise RuntimeError(f"테스트 목록을 읽지 못했어요: {directory}/Make.tests")
        tests.extend(f"{directory}/{name}" for name in match[1].replace("\\", " ").split())
    return tests


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


class Runner:
    def __init__(self):
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
        # 판정 파일은 테스트가 끝날 때만 바뀌어요. 바인드 마운트에서 파일 하나 여는 게 느려서
        # 화면을 그릴 때마다 27개를 다시 읽지 않고, 한 번 읽은 판정을 기억해 둬요.
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
        self.spawn(["make"], PROJECT, 120)
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
        """병렬 실행 중에 새로 생긴 판정 파일을 1초에 한 번만 확인해요."""
        if not force and time.monotonic() < self.next_poll:
            return
        self.next_poll = time.monotonic() + 1
        for test in list(self.pending):
            if (BUILD / (test + ".result")).exists():
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
        limits = [510 if "/mlfqs/" in test else 90 for test in tests]
        self.spawn(["make", f"-j{jobs}"] + [test + ".result" for test in tests], BUILD,
                   sum(limits) // jobs + max(limits) + 60)
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
        timeout = 510 if "/mlfqs/" in self.current else 90
        self.spawn(["make", self.current + ".result"], BUILD, timeout)
        self.message = f"실행 중 · {Path(self.current).name} · 뒤에 {len(self.queue)}개"

    def cancel(self):
        if self.busy:
            stop_process(self.process)
            for test in ([self.current] if self.current else []) + list(self.pending):
                self.forget(test)
            self.finish("실행을 중단했어요. 중단된 테스트의 불완전한 결과는 지웠어요.")


class DebugSession:
    def __init__(self, test):
        self.test = test
        self.process = None
        self.output = None
        self.log = LOGS / "debug.log"
        self.temp_dir = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]

    def start(self):
        LOGS.mkdir(parents=True, exist_ok=True)
        self.output = self.log.open("w")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="pintos-tui-debug-")
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--qemu", self.test, "--port", str(self.port)],
            cwd=BUILD, env=environment(self.temp_dir.name), stdin=subprocess.DEVNULL, stdout=self.output,
            stderr=subprocess.STDOUT, start_new_session=True)

    def gdb_command(self, tui=True):
        command = ["gdb", "-q", "-nx", str(BUILD / "kernel.o")]
        if tui:
            command.append("-tui")
        commands = ["set pagination off", "set confirm off", "set tcp auto-retry on",
                    "set tcp connect-timeout 5", f"directory {PINTOS}",
                    f"target remote 127.0.0.1:{self.port}", "hbreak run_test", "continue", "next"]
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


def run_qemu(test, port):
    """기존 실행기의 디스크 준비/정리를 재사용하고 디버그 주소만 지정해요."""
    original = runpy.run_path(str(PINTOS / "utils" / "pintos"))["Pintos"]

    class LocalPintos(original):
        def _Pintos__prepare_cmd(self):
            command = super()._Pintos__prepare_cmd()
            index = command.index("-s")
            command[index:index + 1] = ["-gdb", f"tcp:127.0.0.1:{port}"]
            return command

    flags = ["-q"]
    if "/mlfqs/" in test:
        flags.append("-mlfqs")
    LocalPintos(gdb=True, mem=20, args=flags + ["run", Path(test).name]).run()


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


HELP = [
    "Enter/r  선택한 테스트 실행 (항상 빌드 후 재실행)",
    "g        현재 묶음 전체 실행    a  27개 전체 실행",
    "p        여러 개 실행할 때 병렬(make -jN) / 하나씩 바꾸기. 처음엔 병렬",
    "         병렬에서 mlfqs가 FAIL이면 그 테스트만 Enter로 하나씩 다시 확인해 보세요",
    "f        현재 묶음의 FAIL/ERR 재실행    s  실행 중단",
    "1/2/3/4  전체 / alarm / priority / mlfqs",
    "/        테스트 이름 검색 (Enter 완료, Esc 취소)",
    "Tab      판정 / QEMU 출력 / 오류 / 실행 로그 / 디버그 로그",
    "PgUp/Dn  오른쪽 로그 스크롤    Home/End  처음/끝",
    "→ / ←    ↑↓(와 휠)이 움직일 쪽을 로그 / 테스트 목록으로 바꾸기",
    "         CLion 터미널은 휠을 ↑↓로 보내서, → 로 로그 쪽에 두면 휠로 로그가 내려가요",
    "j/k      어느 쪽이든 테스트 선택    [ ]  어느 쪽이든 로그 3줄 스크롤",
    "휠       마우스를 넘겨주는 터미널에서는 언제나 로그 스크롤",
    "m        마우스 켜기/끄기. 화면 글자를 드래그로 복사할 땐 끄거나 Shift+드래그",
    "d        선택한 테스트를 GDB TUI로 열기 (run_test에서 정지)",
    "GDB: b 함수명 | n 다음 줄 | s 함수 안으로 | c 계속",
    "GDB: p 변수명 | info locals 지역변수 | bt 호출 스택",
    "GDB: tui reg general 레지스터 | layout split 소스+어셈블리",
    "GDB: Ctrl-X O 포커스 이동 | Ctrl-L 화면 복구",
    "GDB: quit 입력 → QEMU 종료, 테스트 화면으로 복귀",
    "? 도움말 닫기 · q TUI 종료 (진행 중이면 먼저 s)",
    "PASS/FAIL은 마지막 테스트 결과예요. 코드 수정 후 다시 실행하세요.",
    "MLFQS 판정은 테스트당 최대 480초 걸릴 수 있어요.",
]


class App:
    def __init__(self, screen):
        self.screen = screen
        self.tests = discover_tests()
        self.runner = Runner()
        self.group = "all"
        self.query = ""
        self.index = 0
        self.view = 0
        self.offset = None
        self.help = False
        self.focus = "tests"        # ↑↓가 움직이는 쪽. "tests" 또는 "log"
        self.debug_pending = None
        self.screen.timeout(100)
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        for pair, color in ((1, curses.COLOR_GREEN), (2, curses.COLOR_RED), (3, curses.COLOR_CYAN)):
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

    def visible(self):
        return [test for test in self.tests
                if (self.group == "all" or Path(test).name.startswith(self.group + "-"))
                and self.query.lower() in Path(test).name.lower()]

    def selected(self):
        tests = self.visible()
        self.index = min(max(self.index, 0), max(0, len(tests) - 1))
        return tests[self.index] if tests else None

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
        lines = read_lines(BUILD / (test + suffix)) if test else []
        return label, lines

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 12 or width < 80:
            self.put(0, 0, "터미널을 80열 × 12줄 이상으로 넓혀 주세요. q로 종료할 수 있어요.")
            self.screen.refresh()
            return
        self.selected()
        left = min(42, max(34, width // 3))
        rows = height - 7
        statuses = [self.runner.status(test) for test in self.tests]
        self.put(0, 1, f"PintOS · Threads   마지막 결과: PASS {statuses.count('PASS')} / FAIL {statuses.count('FAIL')} / 오류 {statuses.count('ERR')} / 미실행 {statuses.count('--')}"
                 + (f"   병렬 ×{self.runner.jobs}" if self.runner.parallel else "   하나씩"), curses.A_BOLD)
        self.put(1, 1, f"묶음: {self.group}   검색: {self.query or '전체'}   ? 도움말", curses.color_pair(3))
        focused = curses.A_BOLD | curses.A_REVERSE
        self.put(2, 1, ("▶ " if self.focus == "tests" else "  ") + "테스트 / 마지막 판정",
                 focused if self.focus == "tests" else curses.A_BOLD, left - 1)
        label, lines = self.log_lines()
        self.put(2, left + 2, ("▶ " if self.focus == "log" else "") + label
                 + (" · 끝을 따라가는 중" if self.offset is None else " · 스크롤 중"),
                 focused if self.focus == "log" else curses.A_BOLD)
        tests = self.visible()
        start = max(0, self.index - rows + 1)
        for row, test in enumerate(tests[start:start + rows], 3):
            status = "RUN" if self.runner.is_running(test) else self.runner.status(test)
            attr = curses.color_pair(1 if status == "PASS" else 2 if status in ("FAIL", "ERR") else 3)
            if test == self.selected():
                attr |= curses.A_REVERSE
            self.put(row, 1, f"{status:4} {Path(test).name}", attr, left - 1)
        maximum = max(0, len(lines) - rows)
        offset = maximum if self.offset is None else min(self.offset, maximum)
        for row, line in enumerate((lines or ["아직 출력이 없어요."])[offset:offset + rows], 3):
            self.put(row, left + 2, line)
        self.put(height - 3, 1, self.runner.message, curses.color_pair(3))
        arrows = "↑↓ 테스트 선택  → 로그로" if self.focus == "tests" else "↑↓ 로그 스크롤  ← 테스트로"
        self.put(height - 2, 1, arrows + "  Enter 실행  g 묶음  f 실패  d 디버그  Tab 로그  s 중단  q 종료")
        self.put(height - 1, 1, "1 전체  2 alarm  3 priority  4 mlfqs  / 검색  j/k 선택  [ ] 스크롤  p 병렬  "
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
                self.index = 0
        finally:
            curses.curs_set(0)

    def debug(self, test):
        session = DebugSession(test)
        curses.def_prog_mode()
        curses.endwin()
        # 부모는 Ctrl-C를 넘겨받아도 유지되고, exec한 GDB는 기본 SIGINT 동작을 써요.
        old_sigint = signal.signal(signal.SIGINT, lambda signum, frame: None)
        try:
            print(f"\n{Path(test).name} 디버깅 · 테스트 진입점 run_test에서 멈춰요.", flush=True)
            print("n 다음 줄 | s 함수 진입 | c 계속 | p 변수 | bt 스택 | quit 복귀", flush=True)
            session.start()
            subprocess.run(session.gdb_command(), cwd=BUILD, env=environment())
        finally:
            session.close()
            signal.signal(signal.SIGINT, old_sigint)
            curses.reset_prog_mode()
            self.screen.clear()
            self.screen.timeout(100)
            self.set_mouse(self.mouse)
        self.runner.message = "디버깅을 마쳤어요. Tab으로 디버그 QEMU 로그를 볼 수 있어요."
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
                    self.index -= 1
                    self.offset = None
                elif key in (curses.KEY_DOWN, ord("j")):
                    self.index += 1
                    self.offset = None
                elif key in map(ord, "1234"):
                    self.group, self.index, self.offset = GROUPS[key - ord("1")], 0, None
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
                        self.runner.start([selected])
                        self.view, self.offset, self.help = 3, None, False
                    elif key in (ord("g"), ord("a"), ord("f")):
                        tests = self.tests if key == ord("a") else self.visible()
                        if key == ord("f"):
                            tests = [test for test in tests if self.runner.status(test) in ("FAIL", "ERR")]
                        if tests:
                            self.runner.start(tests)
                            self.view, self.offset, self.help = 3, None, False
                        else:
                            self.runner.message = "실행할 테스트가 없어요."
                    elif key == ord("d") and selected:
                        self.debug_pending = selected
                        self.runner.start([])
                        self.view, self.offset, self.help = 3, None, False
        finally:
            self.runner.cancel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="테스트 목록만 출력해요")
    parser.add_argument("--qemu", choices=discover_tests(), help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.list:
        print("\n".join(discover_tests()))
        return
    if args.qemu:
        if not args.port:
            parser.error("--qemu에는 --port가 필요해요")
        run_qemu(args.qemu, args.port)
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "TUI는 터미널이 필요해요. docker exec -it로 실행해 주세요.\n")
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(lambda screen: App(screen).run())
    except KeyboardInterrupt:
        print("TUI를 종료했어요.")


if __name__ == "__main__":
    main()

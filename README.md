# pintos-tui

PintOS 테스트를 터미널 화면 하나에서 골라 돌리고, 바로 GDB까지 붙여 보는 도구예요 (๑•̀ㅂ•́)و
기본은 **Project 2(User Programs)** 예요. `pintos/userprog/` 에서 빌드하고 돌려요.

- 테스트 목록은 기억해 둔 게 아니라 저장소 파일에서 그때그때 읽어요.
  `pintos/userprog/Make.vars` 의 `TEST_SUBDIRS` 가 `make check` 가 돌리는 묶음이고, 묶음마다 `Make.tests` 에서 이름을 읽어요.
  지금 저장소 기준으로 userprog 63 · no-vm 1 · filesys/base 13 · threads 18 = **95개**예요.
- `Make.vars` 에 주석으로 꺼 둔 `# TEST_SUBDIRS += tests/userprog/dup2` 는 **추가 과제** 묶음으로 맨 아래에 따로 보여 줘요 (dup2 2개).
  `make check` 개수에는 안 들어가요.
- PASS/FAIL은 공식 판정기가 만든 `.result` 파일에서 읽어요. `make check`과 기준이 같아요.
- `d` 한 번이면 QEMU를 멈춘 채로 띄우고 GDB를 붙여 줘요. 유저 프로그램 심볼도 같이 올려 줘요.
- Project 1이 다시 필요하면 `--project threads` 로 켜면 돼요. 그때는 27개(alarm · priority · mlfqs)가 나와요.

파일은 `pintos_tui.py` 하나뿐이고, 파이썬 표준 라이브러리만 써요. 따로 설치할 건 없어요.

## 준비물

정글 Docker 이미지(`pintos_22.04_lab_docker`) 안이면 다 들어 있어요.

| 필요한 것 | 이미지에 있는 버전 |
|---|---|
| Python | 3.10.12 (표준 `curses`) |
| GDB | 12.1 |
| make, qemu | PintOS 빌드에 쓰는 그대로 |

## 설치

**저장소 루트 바로 아래 폴더**에 넣어 주세요. 스크립트가 자기 위치에서 한 단계 위를 저장소로 봐서, `pintos/` 폴더랑 같은 높이에 있는 폴더 안이어야 해요.

```
SW-AI-W08/
├── pintos/
└── scripts/
    └── pintos_tui.py   ← 여기
```

컨테이너에는 `curl`이 없어서, 호스트(Windows/macOS) 터미널에서 받는 게 편해요. 저장소 루트에서요.

```sh
mkdir scripts
curl.exe -fsSL https://raw.githubusercontent.com/Shirokuma0704/pintos-tui/main/pintos_tui.py -o scripts/pintos_tui.py
```

macOS나 리눅스에서는 `curl.exe` 대신 `curl`이에요. Windows PowerShell 5.1에서 그냥 `curl`이라고 치면 다른 명령(`Invoke-WebRequest`)이 불려서 꼭 `.exe`까지 붙여 주세요 (・_・;)

로그가 저장소 루트의 `.tui/`에 쌓여요. git에 안 올라가게 하나 막아 두세요.

```sh
echo ".tui/" >> .git/info/exclude
```

`scripts/`도 팀 저장소에 안 올릴 거면 같은 방법으로 한 줄 더 넣으면 돼요.

## 실행

**컨테이너 안 터미널**에서요. VS Code나 CLion의 컨테이너 터미널이면 그냥 이렇게 치면 돼요.

```sh
python3 scripts/pintos_tui.py
```

호스트에서 바로 띄우려면 `-it`가 꼭 있어야 해요. 컨테이너 ID와 경로는 각자 것으로 바꿔 주세요.

```sh
docker exec -it -e TERM=xterm-256color -e LANG=C.UTF-8 <컨테이너ID> python3 /<저장소경로>/scripts/pintos_tui.py
```

Project 1(Threads)로 돌리고 싶으면 뒤에 `--project threads` 를 붙여요. 목록만 보고 싶으면 `--list` 예요.

```sh
python3 scripts/pintos_tui.py --list
```

터미널은 80열 × 12줄 이상이어야 해요. 한 저장소에서는 TUI 하나만 켜 주세요. 두 개가 같은 테스트를 돌리면 결과 파일이 겹쳐요.

## 조작

| 키 | 동작 |
|---|---|
| ↑↓ | 테스트 고르기. → 로 로그 쪽에 두면 로그 스크롤 |
| → / ← | ↑↓(와 CLion 터미널의 휠)이 움직일 쪽을 로그 / 테스트 목록으로 바꾸기 |
| j / k | 어느 쪽에 있든 테스트 고르기 |
| [ / ] | 어느 쪽에 있든 로그 3줄 스크롤 |
| Enter 또는 r | 빌드하고 고른 테스트 다시 실행 |
| 1 ~ 6 | 전체 / userprog / no-vm / filesys / threads / dup2 묶음 (화면 맨 아래 줄에 번호가 나와요) |
| o | 정렬 바꾸기: 분류순 ↔ 결과순 |
| g | 지금 묶음 전부 실행 |
| a | `make check` 목록 95개 전부 실행 (추가 과제 dup2는 빼고) |
| f | 지금 묶음에서 FAIL·ERR만 다시 실행 |
| / | 테스트 이름 검색 |
| Tab | 판정 / QEMU 출력 / 오류 / 실행 로그 / 디버그 QEMU 로그 |
| PgUp / PgDn, Home / End | 로그 스크롤 |
| 마우스 휠 | 마우스를 넘겨주는 터미널에서는 로그 스크롤. CLion 터미널은 휠을 ↑↓로 보내서 → 로 로그 쪽에 둬야 해요 |
| m | 마우스 켜기/끄기. 켜 둔 동안 글자 복사는 Shift+드래그 |
| d | 빌드하고 고른 테스트를 GDB로 디버깅 |
| s | 실행 중단 |
| ? | 도움말 |
| q | 종료 (실행 중이면 먼저 s) |

`ERR`은 판정 파일이 안 생겼거나 make가 실패한 상태예요. Tab으로 실행 로그를 보면 이유가 나와요.
시간 제한은 `Make.tests` 값 그대로예요. 대부분 60초이고, `syn-read`는 300초, `multi-oom`은 600초까지 걸릴 수 있어요. 멈춘 게 아니니까 조금 기다려 주세요 (｡•ᴗ•｡)

## 정렬

왼쪽 목록은 묶음 제목 줄과 테스트 줄로 나뉘어요. 제목 줄 오른쪽 숫자는 그 묶음의 `PASS 개수/전체`예요.

| 정렬 | 순서 |
|---|---|
| 분류순 (처음) | userprog → no-vm → filesys/base → threads → dup2(추가 과제). `make check` 순서를 따르되 하위 폴더는 부모 바로 뒤에 둬요. 묶음 안에서는 `create-`, `open-` 처럼 이름 앞부분이 같은 것끼리 `Make.tests`에 처음 나온 순서로 모아요 |
| 결과순 (`o`) | 실행 중 → FAIL · ERR → 미실행 → PASS. 같은 칸 안은 분류순이고, 줄 오른쪽 끝에 어느 묶음인지 작게 붙여요 |

정렬을 바꾸거나 결과가 들어와서 줄이 움직여도 **고른 테스트는 그대로** 따라가요.
`전체` 화면의 결과순 개수에는 추가 과제도 들어가요. 맨 위 줄의 `make check 95개` 숫자에는 안 들어가요.

## GDB 화면

`d`를 누르면 새 QEMU를 멈춘 상태로 띄우고, GDB가 같은 컨테이너의 빈 포트로 붙어요.
QEMU 명령은 make가 그 테스트에 쓰는 `pintos` 명령을 그대로 받아 와요 (`make -n`). 그래서 인자(`args-single onearg` 같은 것)와 디스크에 넣는 파일이 `make check` 때와 같아요. 시간 제한(`-T`)만 빼요.
테스트를 시작하는 함수 `run_task`(threads/init.c)에 중단점을 걸어 두고 소스 화면을 열어 줘요. QEMU 출력은 `debug.log`로 따로 빠져서 GDB 화면이랑 안 섞여요.
유저 프로그램 심볼도 `add-symbol-file` 로 같이 올려 둬요. 그래서 `main` 이 커널(init.c)과 유저 프로그램(args.c 등) 양쪽에서 보여요.

| GDB 명령 | 뜻 |
|---|---|
| `b 함수명` / `b 파일.c:줄` | 중단점 추가 |
| `n` / `s` / `c` | 다음 줄 / 함수 안으로 / 계속 |
| `p 변수` / `info locals` | 변수 하나 / 지역변수 전부 |
| `bt` | 호출 스택 |
| `layout split` | 소스 + 어셈블리 |
| `focus src` / `focus cmd` | 방향키를 소스 스크롤에 / 명령 입력에 |
| Ctrl-X 다음 O | 화면 포커스 옮기기 |
| Ctrl-L | 화면 다시 그리기 |
| `quit` | GDB 끝내고 TUI로 돌아오기 |

`info threads`는 PintOS 스레드가 아니라 QEMU의 CPU 스레드를 보여 줘요. 헷갈리기 쉬우니 조심하세요 (ˊ˘ˋ*)
GDB는 `-nx`로 띄워서 본인 `.gdbinit`은 안 읽어요. 필요하면 GDB 안에서 직접 `source` 해 주세요.

## 알아 두면 좋은 것

- **판정은 기억해 뒀다가 보여 줘요.** Windows 폴더를 바인드 마운트한 환경에서는 파일 하나 여는 데 시간이 꽤 걸려서, 화면을 그릴 때마다 전부 다시 읽으면 키 반응이 밀렸어요. 그래서 TUI 안에서 테스트를 돌릴 때만 판정을 새로 읽어요.
- 그래서 **TUI를 켜 둔 채 다른 터미널에서 `make check`를 돌리면**, 그 결과는 화면에 바로 안 바뀌어요. TUI를 껐다 켜면 다시 읽어요.
- 화면의 개수는 **마지막으로 돌린 결과**예요. 코드를 고쳤으면 다시 돌려야 맞는 숫자가 나와요.
- **추가 과제(dup2)는 `Make.vars`를 안 고치고 돌려요.** 대신 make에 `TEST_SUBDIRS=<원래 목록> tests/userprog/dup2` 를 넘겨줘요. 이 값을 붙여도 95개 테스트의 `pintos` 명령이 하나도 안 바뀌는 걸 확인했어요.
- 여러 개를 돌릴 땐 `make -k -jN` 이에요. 유저 프로그램 하나가 컴파일에 실패해도 나머지는 계속 돌아요.

## 어디까지 확인했나

상철님 Windows + Docker Desktop 환경에서 확인한 것만 적어요.

**Project 2 (2026-10-15, SW-AI-W08 develop, 아직 Week7 코드라 시스템 콜 없음)**
가상 터미널(pty)에서 실제 키를 눌러 확인했어요.

| 항목 | 상태 |
|---|---|
| 목록 | TUI 97개 = make가 보는 `TESTS` 97개 (dup2 넣었을 때). 넣지 않으면 95개 |
| `a` 95개 전체 | 병렬(-j7) 42초. PASS 18(threads) / FAIL 77. 화면 맨 위 숫자 = `.result` 파일 |
| 공식 `make check` 비교 | 하나씩 211초. 95개 판정이 TUI 병렬 결과와 전부 같음 (한 번 측정) |
| dup2 추가 과제 `6` → `g` | 두 개 다 `.result` 생김 |
| `o` 정렬, `5` 묶음 고르기 | 확인함 |
| `d` GDB | `run_task`에서 멈춤, `p argv[1]` = `args-single onearg`, `main` 이 init.c·args.c 둘 다 보임 |
| 실행 중 `q` 막기, `s` 중단 | 확인함. 중단 뒤 QEMU 안 남음 |

**Project 1 (2026-10-13)**

| 항목 | 상태 |
|---|---|
| 목록·실행·판정 표시 | 확인함 (alarm-single PASS) |
| 판정 캐시 전후 속도 | 화면 한 번에 106–146 ms → 0.01 ms |
| 27개 전체 실행 시간 | 하나씩 약 14분 → 병렬(-j7, CPU 14개) 3분 21초. 판정 26 PASS / 1 FAIL 로 하나씩 돌린 결과와 같음 (한 번 측정) |
| `d` GDB 디버깅 | 확인함 (`run_test`에서 멈춤, `p` 로 전역 변수 출력) |
| macOS / 리눅스 호스트 | 안 돌려 봄 |

안 되는 게 있으면 Tab → 실행 로그 화면을 같이 보여 주세요. 거기에 답이 거의 다 있어요 ٩(ˊᗜˋ*)و

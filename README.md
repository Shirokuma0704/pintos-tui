# pintos-tui

PintOS Project 1(Threads) 테스트를 터미널 화면 하나에서 골라 돌리고, 바로 GDB까지 붙여 보는 도구예요 (๑•̀ㅂ•́)و

- 27개 테스트(alarm 6 · priority 12 · mlfqs 9)를 목록으로 보여 주고, 하나만 골라서 다시 돌릴 수 있어요.
- PASS/FAIL은 공식 판정기가 만든 `.result` 파일에서 읽어요. `make check`과 기준이 같아요.
- `d` 한 번이면 QEMU를 멈춘 채로 띄우고 GDB를 붙여 줘요.

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
SW-AI-W07/
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

터미널은 80열 × 12줄 이상이어야 해요. 한 저장소에서는 TUI 하나만 켜 주세요. 두 개가 같은 테스트를 돌리면 결과 파일이 겹쳐요.

## 조작

| 키 | 동작 |
|---|---|
| ↑↓ 또는 j/k | 테스트 고르기 |
| Enter 또는 r | 빌드하고 고른 테스트 다시 실행 |
| 1 / 2 / 3 / 4 | 전체 / alarm / priority / mlfqs 묶음 |
| g | 지금 묶음 전부 실행 |
| a | 27개 전부 실행 |
| f | 지금 묶음에서 FAIL·ERR만 다시 실행 |
| / | 테스트 이름 검색 |
| Tab | 판정 / QEMU 출력 / 오류 / 실행 로그 / 디버그 QEMU 로그 |
| PgUp / PgDn, Home / End | 로그 스크롤 |
| d | 빌드하고 고른 테스트를 GDB로 디버깅 |
| s | 실행 중단 |
| ? | 도움말 |
| q | 종료 (실행 중이면 먼저 s) |

`ERR`은 판정 파일이 안 생겼거나 make가 실패한 상태예요. Tab으로 실행 로그를 보면 이유가 나와요.
MLFQS 테스트는 하나에 최대 480초까지 걸릴 수 있어요. 멈춘 게 아니니까 조금 기다려 주세요 (｡•ᴗ•｡)

## GDB 화면

`d`를 누르면 새 QEMU를 멈춘 상태로 띄우고, GDB가 같은 컨테이너의 빈 포트로 붙어요.
테스트 시작 함수 `run_test`에 중단점을 걸어 두고 소스 화면을 열어 줘요. QEMU 출력은 `debug.log`로 따로 빠져서 GDB 화면이랑 안 섞여요.

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

- **판정은 기억해 뒀다가 보여 줘요.** Windows 폴더를 바인드 마운트한 환경에서는 파일 하나 여는 데 시간이 꽤 걸려서, 화면을 그릴 때마다 27개를 다시 읽으면 키 반응이 밀렸어요. 그래서 TUI 안에서 테스트를 돌릴 때만 판정을 새로 읽어요.
- 그래서 **TUI를 켜 둔 채 다른 터미널에서 `make check`를 돌리면**, 그 결과는 화면에 바로 안 바뀌어요. TUI를 껐다 켜면 다시 읽어요.
- 화면의 개수는 **마지막으로 돌린 결과**예요. 코드를 고쳤으면 다시 돌려야 맞는 숫자가 나와요.

## 어디까지 확인했나

2026-10-13, 상철님 Windows + Docker Desktop 환경에서 확인한 것만 적어요.

| 항목 | 상태 |
|---|---|
| 목록·실행·판정 표시 | 확인함 (alarm-single PASS) |
| 판정 캐시 전후 속도 | 화면 한 번에 106–146 ms → 0.01 ms |
| `d` GDB 디버깅 | 확인함 (`run_test`에서 멈춤, `p` 로 전역 변수 출력) |
| macOS / 리눅스 호스트 | 안 돌려 봄 |

안 되는 게 있으면 Tab → 실행 로그 화면을 같이 보여 주세요. 거기에 답이 거의 다 있어요 ٩(ˊᗜˋ*)و

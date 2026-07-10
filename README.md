# 백링크 자동 글쓰기

그누보드 게시판 자동 글쓰기 · 콘텐츠 세트 · 게시판 수집 · 게시 이력

## 일반 사용자 (Python 설치 불필요)

1. [Releases](https://github.com/lee3215-ko/backlink-auto-writer/releases)에서 `BacklinkWriter.zip` 다운로드
2. 압축 해제 후 **`BacklinkWriter.exe`** 실행 (폴더 전체 유지)
3. 세트·URL·이력은 **`%APPDATA%\BacklinkWriter`** 에 저장 (업데이트해도 유지)
4. 프로그램을 다시 켜면 새 버전이 있을 때 **자동 업데이트 후 실행** (저장 데이터 유지)

## 실행 (개발)

```powershell
pip install -r requirements.txt
playwright install chromium
python main.py
```

또는 `시작.bat` / `run.bat`

## 배포 (개발자)

```powershell
.\deploy.bat
```

## 원격 로그 (관리자)

다른 PC 실행 로그를 GitHub private 저장소에 모아 관리자 PC에서 조회합니다.

1. GitHub에 **private** 저장소 `backlink-writer-logs` 생성 (비어 있어도 됨)
2. [Fine-grained PAT](https://github.com/settings/personal-access-tokens) 발급  
   - Repository access: `backlink-writer-logs`만  
   - Permissions → Contents: **Read and write**
3. 앱 **원격 로그** 탭에서 Owner / Repo / Token 입력 후 **로그 자동 업로드 켜기**
4. 이용자 PC에도 동일 토큰을 넣으면 배치 종료·주기(기본 30분)마다 `clients/{PC-ID}/latest.log` 가 덮어쓰기 업로드됨
5. 관리자 PC에서 **원격 PC 목록 새로고침** → PC 선택 → 로그 확인

용량: PC당 최신 로그 1개(최대 약 512KB)만 유지. Free 계정으로 충분하며 Actions 분은 쓰지 않습니다.

## 릴리스

https://github.com/lee3215-ko/backlink-auto-writer/releases

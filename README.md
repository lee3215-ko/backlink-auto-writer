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

## 릴리스

https://github.com/lee3215-ko/backlink-auto-writer/releases

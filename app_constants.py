"""앱 버전 및 업데이트 URL."""

APP_VERSION = "1.0.10"
APP_NAME = "BacklinkWriter"
APP_DISPLAY_NAME = "백링크 자동 글쓰기"
EXE_NAME = "BacklinkWriter.exe"
ZIP_INNER_FOLDER = "BacklinkWriter"

# 연속 실패 시 해당 게시판은 배치에서 자동 제외
FAIL_SKIP_THRESHOLD = 10

UPDATE_VERSION_URL = (
    "https://raw.githubusercontent.com/lee3215-ko/backlink-auto-writer/main/version.json"
)

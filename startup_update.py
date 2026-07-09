"""프로그램 시작 시 자동 업데이트 (데이터는 AppData에 유지)."""



from __future__ import annotations



import sys

import tempfile

import urllib.error

from pathlib import Path



from app_constants import APP_NAME, APP_VERSION, EXE_NAME, UPDATE_VERSION_URL, ZIP_INNER_FOLDER

from updater import (

    check_for_update,

    download_file_with_fallbacks,

    can_auto_update,

    format_network_error,

    schedule_apply_update,

)





def try_startup_update(*, on_status=None) -> bool:

    """

    새 버전이 있으면 다운로드·설치 후 재시작.

    True = 곧 종료됨 (호출측에서 return).

    """

    if not can_auto_update():

        return False



    def status(msg: str) -> None:

        if on_status:

            on_status(msg)



    status("업데이트 확인 중...")

    info = check_for_update(UPDATE_VERSION_URL, APP_VERSION, app_name=APP_NAME)

    if info is None:

        return False



    urls = info.download_urls or ((info.url,) if info.url else ())

    if not urls:

        return False



    status(f"v{info.version} 다운로드 중...")

    zip_path = Path(tempfile.gettempdir()) / f"{APP_NAME}-update-{info.version}.zip"

    try:

        download_file_with_fallbacks(

            urls,

            zip_path,

            user_agent=f"{APP_NAME}/{APP_VERSION}",

        )

    except (urllib.error.URLError, TimeoutError, OSError, ValueError):

        return False



    status("업데이트 적용 중... 재시작합니다.")

    try:

        schedule_apply_update(

            zip_path,

            exe_name=EXE_NAME,

            zip_inner_folder=ZIP_INNER_FOLDER,

            app_slug=APP_NAME,

        )

    except RuntimeError:

        return False



    sys.exit(0)

    return True



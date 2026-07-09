"""자동 검색어 생성 — 랜덤 키워드 + 그누보드 비회원 게시판 탐색."""

from __future__ import annotations

import json
import random
from pathlib import Path

from app_paths import data_file, migrate_legacy_data

# 한국 사이트 주제 키워드 (랜덤 조합용)
KOREAN_KEYWORDS: tuple[str, ...] = (
    "교회", "성당", "절", "사찰", "학원", "유치원", "어린이집", "병원", "한의원", "치과",
    "동아리", "모임", "동호회", "카페", "맛집", "음식점", "식당", "펜션", "민박", "호텔",
    "부동산", "공인중개", "인테리어", "건설", "설비", "전기", "배관", "이사", "청소",
    "학교", "대학", "캠퍼스", "과외", "입시", "학습", "도서관", "문화센터", "평생교육",
    "체육관", "헬스", "요가", "필라테스", "수영", "골프", "테니스", "축구", "농구",
    "반려동물", "애견", "펫샵", "미용실", "네일", "마사지", "스파", "피부관리",
    "자동차", "정비", "카센터", "타이어", "중고차", "렌트카", "주차",
    "여행", "관광", "펜션", "캠핑", "글램핑", "등산", "낚시", "골프장",
    "결혼", "웨딩", "스튜디오", "장례", "상조", "법률", "세무", "회계", "노무",
    "보험", "대출", "금융", "투자", "창업", "쇼핑몰", "온라인몰", "도매", "소매",
    "농장", "농산물", "수산", "꽃집", "화원", "가구", "침대", "매트리스", "인테리어",
    "공예", "도예", "미술", "음악", "피아노", "기타", "드럼", "댄스", "무용",
    "사진", "영상", "웨딩촬영", "인쇄", "출판", "광고", "마케팅", "홍보", "디자인",
    "IT", "컴퓨터", "수리", "휴대폰", "전자", "가전", "조명", "문구", "사무",
    "복지", "요양", "요양원", "실버", "노인", "장애인", "복지관", "지역아동",
    "아파트", "빌라", "상가", "오피스", "공장", "창고", "물류", "택배", "운송",
    "협회", "조합", "단체", "재단", "센터", "연구소", "연구원", "기술원",
    "관공서", "주민", "마을", "리", "읍", "면", "구청", "시청", "군청",
    "자원봉사", "기부", "나눔", "후원", "모금", "축제", "행사", "박람회",
    "미용", "화장품", "패션", "의류", "가방", "신발", "액세서리", "시계",
    "식품", "제과", "베이커리", "떡집", "정육", "수산시장", "과일", "채소",
    "반찬", "도시락", "배달", "프랜차이즈", "창업", "가맹", "점포",
)

SITE_FILTERS: tuple[str, ...] = ("site:kr", "site:co.kr", "site:or.kr")

# 자주 쓰이는 bo_table 이름
COMMON_BO_TABLES: tuple[str, ...] = (
    "free", "qna", "gallery", "notice", "data", "bbs", "board", "community",
    "guest", "inquiry", "consult", "review", "photo", "market", "sell",
)

QUERY_TEMPLATES: tuple[str, ...] = (
    'inurl:board.php bo_table {kw} 비회원 {site}',
    'inurl:write.php bo_table {kw} 비회원 {site}',
    'inurl:"/bbs/board.php" bo_table {kw} {site}',
    'inurl:board.php bo_table {kw} wr_password {site}',
    'inurl:board.php bo_table {kw} 자동등록방지 {site}',
    'inurl:write.php bo_table {kw} 글쓰기 {site}',
)

BO_TABLE_TEMPLATES: tuple[str, ...] = (
    'inurl:board.php bo_table {bt} 비회원 {site}',
    'inurl:write.php bo_table {bt} {site}',
    'inurl:"/bbs/board.php" bo_table {bt} {site}',
)

# 검색 품질이 좋은 고정 쿼리 (가끔 섞음)
CORE_QUERIES: tuple[str, ...] = (
    'inurl:"/bbs/board.php" "bo_table" site:kr',
    'inurl:board.php bo_table 비회원 site:kr',
    'inurl:write.php bo_table 비회원 site:co.kr',
)


class AutoQueryGenerator:
    """세션·재실행 간 중복을 줄이며 랜덤 검색어 생성."""

    _MAX_USED = 3000

    def __init__(self, state_path: Path | None = None) -> None:
        migrate_legacy_data()
        self._state_path = state_path or data_file("auto_search_state.json")
        self._used: set[str] = set()
        self._core_idx = 0
        self._load_state()

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._used = set(raw.get("used", []))
            self._core_idx = int(raw.get("core_idx", 0))
        except Exception:
            pass

    def _save_state(self) -> None:
        used = sorted(self._used)[-self._MAX_USED:]
        data = {"used": used, "core_idx": self._core_idx}
        self._state_path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")

    def reset(self) -> None:
        """수집 세션 시작 시 — 이전 검색어 기록은 유지 (재시작 후 중복 검색 방지)."""
        pass

    def clear_history(self) -> None:
        """검색어 이력 초기화 (필요 시 수동 호출)."""
        self._used.clear()
        self._core_idx = 0
        self._save_state()

    def next_query(self) -> str:
        # 15% 확률로 검증된 핵심 쿼리
        if random.random() < 0.15:
            q = CORE_QUERIES[self._core_idx % len(CORE_QUERIES)]
            self._core_idx += 1
            self._save_state()
            return q

        site = random.choice(SITE_FILTERS)

        # 25% — bo_table 이름 기반 (키워드 없이)
        if random.random() < 0.25:
            bt = random.choice(COMMON_BO_TABLES)
            tpl = random.choice(BO_TABLE_TEMPLATES)
            q = tpl.format(bt=bt, site=site)
        else:
            kw = random.choice(KOREAN_KEYWORDS)
            tpl = random.choice(QUERY_TEMPLATES)
            q = tpl.format(kw=kw, site=site)

        # 같은 문장 반복 방지 (풀 고갈 시 used 일부만 비움)
        if q in self._used:
            if len(self._used) > self._MAX_USED:
                self._used = set(sorted(self._used)[self._MAX_USED // 2:])
            else:
                return self.next_query()
        self._used.add(q)
        self._save_state()
        return q


def generate_auto_query() -> str:
    """단발성 랜덤 검색어."""
    return AutoQueryGenerator().next_query()

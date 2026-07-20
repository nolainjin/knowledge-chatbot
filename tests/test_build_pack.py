"""Phase 9: 빌드 단계 팩 변환 -- 원본 보호 가드 + 코칭 모드 불변식 (D09).

원본 폴더(fixture)는 절대 쓰기 대상이 아니다 -- 이 스위트는 해시 매니페스트
불변, src==dst/dst-in-src 거부, 코칭 모드(=_intake_schema.md 없음) 유지,
NON_KNOWLEDGE 파일의 조용하지 않은 제외(D12)를 실측으로 단언한다.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from app.knowledge_pack import validate_pack
from scripts.build_knowledge_pack import build_pack, main

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "tests" / "fixtures" / "knowledge-source"
SCAN_SRC = REPO_ROOT / "tests" / "fixtures" / "knowledge-source-scan"


def _hash_manifest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_fixture_covers_md_txt_pdf_docx_html_plus_non_knowledge_and_subfolder():
    names = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*") if p.is_file()}
    assert names == {
        "readme.md",
        "info.txt",
        "paper.pdf",
        "report.docx",
        "article.html",
        "index.csv",
        "sub/child.md",
    }


def test_build_creates_pack_without_intake_schema(tmp_path):
    # GM4: 빌드 산출 팩에 _intake_schema.md 가 없다 (= 코칭 모드).
    dst = tmp_path / "pack"
    build_pack(SRC, dst)

    assert not (dst / "_intake_schema.md").exists()


def test_built_pack_passes_validate_pack(tmp_path):
    dst = tmp_path / "pack"
    build_pack(SRC, dst)

    result = validate_pack(dst)

    assert result.valid, [e.as_dict() for e in result.errors]


def test_build_does_not_mutate_source_tree(tmp_path):
    # GM14: 실행 전후 원본 트리 해시 매니페스트가 동일해야 한다.
    before = _hash_manifest(SRC)
    build_pack(SRC, tmp_path / "pack")
    after = _hash_manifest(SRC)

    assert before == after


def test_cli_rejects_src_equals_dst():
    # GM14: src == dst 거부.
    code = main(["--src", str(SRC), "--dst", str(SRC)])

    assert code != 0


def test_cli_rejects_dst_inside_src():
    # GM14: dst가 src 안에 있으면 거부.
    code = main(["--src", str(SRC), "--dst", str(SRC / "out")])

    assert code != 0


def test_cli_rejects_existing_dst_without_force(tmp_path):
    dst = tmp_path / "pack"
    dst.mkdir()

    code = main(["--src", str(SRC), "--dst", str(dst)])

    assert code != 0


def test_cli_allows_existing_dst_with_force(tmp_path):
    dst = tmp_path / "pack"
    dst.mkdir()

    code = main(["--src", str(SRC), "--dst", str(dst), "--force"])

    assert code == 0
    assert validate_pack(dst).valid


def test_zero_char_scan_pdf_stops_build_explicitly(tmp_path, capsys):
    # D07: 0자 스캔 PDF는 조용히 통과하지 않고 빌드를 명시적으로 세운다.
    code = main(["--src", str(SCAN_SRC), "--dst", str(tmp_path / "pack")])
    captured = capsys.readouterr()

    assert code != 0
    assert "0자" in (captured.out + captured.err)


def test_non_knowledge_files_excluded_reported_and_not_converted(tmp_path, capsys):
    # D12: NON_KNOWLEDGE 확장자는 빌드를 세우지 않고 --report 제외 목록에 남는다.
    dst = tmp_path / "pack"
    code = main(["--src", str(SRC), "--dst", str(dst), "--report"])
    captured = capsys.readouterr()

    assert code == 0
    assert "index.csv" in captured.out
    assert not list(dst.rglob("index*"))


def test_report_prints_doc_count_depth_ext_distribution_and_char_counts(tmp_path, capsys):
    dst = tmp_path / "pack"
    main(["--src", str(SRC), "--dst", str(dst), "--report"])
    out = capsys.readouterr().out

    assert "문서 수:" in out
    assert "최대 깊이:" in out
    assert "확장자 분포:" in out
    assert "글자수:" in out
    assert "0자 실패:" in out
    assert "지식 아님 제외:" in out
    assert ".pdf" in out and ".docx" in out and ".html" in out


def test_report_lists_zero_char_failure_but_still_converts_the_rest(tmp_path, capsys):
    # 체크리스트: --report 는 0자 실패 목록도 보여준다. 실패한 파일 하나 때문에
    # 나머지(정상 md) 스캔·변환까지 죽지는 않는다 -- 전체 빌드 판정만 실패로 끝난다.
    dst = tmp_path / "pack"
    code = main(["--src", str(SCAN_SRC), "--dst", str(dst), "--report"])
    out = capsys.readouterr().out

    assert code != 0
    assert "scan.pdf" in out
    assert "문서 수: 1" in out
    assert (dst / "notes.md").exists()


def test_conflicting_dst_stems_are_not_silently_overwritten(tmp_path, capsys):
    # 실물 폴더 실측(오케스트레이터, 522건 변환): 서로 다른 확장자라도 stem
    # 이 같으면(x.md, x.txt 모두 x.md 로 변환) 출력 경로가 충돌해 나중 파일이
    # 먼저 쓴 파일을 조용히 덮어썼다(글자수 dict 522 vs 팩 실제 문서 521).
    # 이제는 충돌을 조용히 덮지 않고 실패로 처리하며 양쪽 소스 경로를 남긴다.
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.md").write_text("MD ORIGIN CONTENT\n", encoding="utf-8")
    (src / "x.txt").write_text("TXT ORIGIN CONTENT\n", encoding="utf-8")
    dst = tmp_path / "pack"

    code = main(["--src", str(src), "--dst", str(dst), "--report"])
    captured = capsys.readouterr()
    combined = captured.out + captured.err

    assert code != 0
    assert "x.md" in combined and "x.txt" in combined

    written = dst / "x.md"
    if written.exists():
        content = written.read_text(encoding="utf-8")
        # 조용한 덮어쓰기 금지: 먼저 쓴 쪽 내용이 나중 파일 내용으로 바뀌지 않았다.
        assert "TXT ORIGIN CONTENT" not in content


def test_exclude_skips_file_reports_it_and_resolves_conflict(tmp_path, capsys):
    # --exclude: 원본(읽기전용)을 삭제하지 않고 빌드 단에서 처분한다.
    # 충돌 쌍(x.md, x.txt)에서 .txt 를 제외하면 빌드가 성공하고 제외가 보고된다.
    # 파일명은 한글 NFD(macOS 저장형) / CLI 인자는 NFC — 정규화 불일치까지 실측 재현.
    import unicodedata

    stem_nfd = unicodedata.normalize("NFD", "목록")
    stem_nfc = unicodedata.normalize("NFC", "목록")
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{stem_nfd}.md").write_text("MD ORIGIN CONTENT\n", encoding="utf-8")
    (src / f"{stem_nfd}.txt").write_text("TXT ORIGIN CONTENT\n", encoding="utf-8")
    dst = tmp_path / "pack"

    code = main(["--src", str(src), "--dst", str(dst), "--report", "--exclude", f"{stem_nfc}.txt"])
    out = capsys.readouterr().out

    assert code == 0
    # 보고는 사용자가 CLI 에 친 NFC 형으로 남아야 grep 재검증이 된다(리뷰 발견).
    assert f"{stem_nfc}.txt" in out
    converted = list(dst.glob("*.md"))
    assert any("MD ORIGIN CONTENT" in p.read_text(encoding="utf-8") for p in converted)


def test_unmatched_exclude_fails_build_explicitly(tmp_path, capsys):
    # 리뷰 발견(2026-07-18): 아무 파일에도 안 맞는 --exclude(오타)를 조용히 버리면
    # 명시적으로 뺀 문서가 팩에 실려 나간다 -- 실패로 세워야 한다.
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.md").write_text("내용\n", encoding="utf-8")

    code = main(["--src", str(src), "--dst", str(tmp_path / "pack"), "--exclude", "오타난-경로.txt"])
    err = capsys.readouterr().err

    assert code != 0
    assert "미매칭" in err and "오타난-경로.txt" in err


def test_nfc_nfd_same_stem_sources_detected_as_conflict(tmp_path, capsys):
    # 리뷰 발견(2026-07-18): 충돌 키가 바이트 비교면 NFC/NFD 동명 소스가 감지를
    # 비껴가고, APFS 에선 같은 출력 파일로 합쳐져 조용히 덮어쓴다.
    import unicodedata

    stem_nfd = unicodedata.normalize("NFD", "목록")
    stem_nfc = unicodedata.normalize("NFC", "목록")
    src = tmp_path / "src"
    src.mkdir()
    (src / f"{stem_nfd}.md").write_text("NFD 쪽 내용\n", encoding="utf-8")
    (src / f"{stem_nfc}.txt").write_text("NFC 쪽 내용\n", encoding="utf-8")

    code = main(["--src", str(src), "--dst", str(tmp_path / "pack"), "--report"])
    combined = capsys.readouterr()

    assert code != 0
    assert "충돌" in (combined.out + combined.err)


def test_cli_subprocess_end_to_end_matches_in_process_behavior(tmp_path):
    # 위 테스트들은 in-process main()을 쓴다 -- 실제 사용자가 두드리는 CLI
    # 서브프로세스 경로도 최소 1건은 그대로 실행해 둘이 어긋나지 않게 한다.
    dst = tmp_path / "pack"
    result = subprocess.run(
        [sys.executable, "scripts/build_knowledge_pack.py", "--src", str(SRC), "--dst", str(dst)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert validate_pack(dst).valid


def test_ui_option_bakes_ui_json_into_pack(tmp_path, capsys):
    # --ui: 팩 재생성마다 _ui.json을 손으로 다시 넣지 않도록 빌드가 굽는다.
    src = tmp_path / "src"
    src.mkdir()
    (src / "doc.md").write_text("내용\n", encoding="utf-8")
    ui_src = tmp_path / "starters.json"
    ui_src.write_text('{"greeting": "환영"}', encoding="utf-8")
    dst = tmp_path / "pack"

    code = main(["--src", str(src), "--dst", str(dst), "--ui", str(ui_src)])

    assert code == 0
    assert (dst / "_ui.json").read_text(encoding="utf-8") == '{"greeting": "환영"}'
    result = validate_pack(dst)
    assert result.valid and not result.warnings


def test_ui_option_rejects_broken_json(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "doc.md").write_text("내용\n", encoding="utf-8")
    ui_src = tmp_path / "starters.json"
    ui_src.write_text("{broken", encoding="utf-8")

    code = main(["--src", str(src), "--dst", str(tmp_path / "pack"), "--ui", str(ui_src)])

    assert code != 0
    assert "--ui" in capsys.readouterr().err

import os
import json
from LLM_fn import analyze_news  # LLM_fn.py 파일에서 analyze_news 함수를 가져옵니다.

INPUT_FILE_NAME = "test_data/test.json"


def run_test():
    print("=== [테스트] 외부 파일에서 함수 호출 테스트 시작 ===\n")

    # 0. 입력 파일 존재 여부 확인
    if not os.path.exists(INPUT_FILE_NAME):
        print(f"❌ 에러: 테스트를 하려면 {INPUT_FILE_NAME} 파일이 먼저 존재해야 합니다!")
        print("네이버 검색 API 코드를 먼저 실행해서 JSON 파일을 만들어주세요.")
        return

    # 1. 원본 네이버 뉴스 결과 파일 읽기
    with open(INPUT_FILE_NAME, "r", encoding="utf-8") as f:
        raw_json_data = f.read()

    # 2. LLM 분석 실행
    #    language: 출력 언어 지정 (예: "한국어", "영어")
    #    length: 요약 분량 프리셋 - "짧게"(2~3문장) / "중간"(4~5문장) / "길게"(6~7문장)
    print("💡 LLM 뉴스 분석을 진행 중입니다...\n")
    result = analyze_news(raw_json_data, language="한국어", length="중간")

    # 3. 분석 성공 여부 확인
    if not result.get("success"):
        print(f"❌ 분석 실패: {result.get('error')}")
        if result.get("raw_response"):
            print("\n[LLM 원본 응답 (파싱 실패)]")
            print(result["raw_response"])
        return

    # 4. 분석 결과 출력
    print("[LLM이 작성한 이슈별 요약]")
    print("-" * 60)

    issues = result.get("issues", [])
    print(f"총 {len(issues)}개의 이슈가 분석되었습니다.\n")

    for i, issue in enumerate(issues, 1):
        print(f"### {i}. {issue.get('headline', '(제목 없음)')}")
        for topic in issue.get("topics", []):
            print(f"  - {topic.get('subtitle')}: {topic.get('summary')}")
        print("  [관련기사]")
        for link in issue.get("articles", []):
            print(f"    · {link}")
        print()

    print("-" * 60)

    # 5. 편집/QA Agent가 남긴 검수 리포트 확인
    qa_report = result.get("qa_report", [])
    print("\n=== [QA Agent] 검수 리포트 ===")
    if not qa_report:
        print("✅ QA Agent가 별도로 수정한 사항이 없습니다.")
    else:
        for note in qa_report:
            print(f"  - {note}")

    # 6. 링크 무결성 검증 결과 확인
    validation = result.get("link_validation", {})
    print("\n=== [검증] 링크 무결성 확인 ===")
    if validation.get("is_valid"):
        print("✅ 모든 링크가 원본 데이터와 정확히 일치합니다.")
    else:
        print(f"⚠️ 원본에 없는 링크가 감지되었습니다: {validation.get('invalid_links')}")

    print(f"\n분석 시각: {result.get('analyzed_at')}")

    # 7. 실제로 저장된 JSON 파일 확인 (고정 파일명, 매번 덮어써짐)
    saved_file_name = "test_summary.json"
    print("\n=== [검증] 저장된 JSON 파일 확인 ===")

    if not os.path.exists(saved_file_name):
        print(f"❌ 에러: 저장된 '{saved_file_name}' 파일을 찾을 수 없습니다.")
        return

    with open(saved_file_name, "r", encoding="utf-8") as f:
        saved_data = json.load(f)

    print(f"- 저장된 파일명: {saved_file_name}")
    print(f"- 저장된 시간: {saved_data.get('analyzed_at')}")
    print(f"- 저장된 이슈 개수: {len(saved_data.get('issues', []))}")
    print(f"- 링크 검증 상태: {saved_data.get('link_validation', {}).get('is_valid')}")

    print("\n✅ 테스트가 성공적으로 완료되었습니다!")


if __name__ == "__main__":
    run_test()
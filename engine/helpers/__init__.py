"""단계와 상관없이 여러 곳이 같이 쓰는 보조 함수.

stage 파일(engine/pipeline/stage0~5, answer)에는 그 단계가 하는 일(LLM 호출·LLM 입력 만들기·단계 고유의 판단·검증 규칙)만 둔다.
여기에는 단계의 뜻과 상관없는 처리만 둔다.
- text: 대조용 정규화(norm_space·norm_quote), 띄어쓰기 정리(tidy·tidy_lines)
- dates: 날짜 읽기, 오늘·학기, 지난 시기 찾기
- checks: LLM 글 검사에 같이 쓰는 패턴 (R번호·학기-방학·한자·숫자·전화번호, 집계 표 기업)
- school: 건양대 문서 판정, 교과목 코드·개설 학년·교양/전공 읽기
- student: 학생 입력·목표 직무·보유 항목을 LLM에 줄 글로
- sources: 출처 구분(건양대 자료·공식·웹·커뮤니티·검색 요약)
- history: 채팅 이전 대화·이전 답변(추천·근거)을 LLM에 줄 글로
- display: 확인용 화면 글(render_chat·render_markdown)
"""

# 커피 교육 starter pack 접수 스키마

이 폴더는 실제 고객 데이터나 운영 상담을 위한 것이 아니라, `MODEL=fake`로
커피 교육 챗봇 커스터마이징 과정을 검증하는 합성 starter pack이다.

```yaml
intake_schema:
  version: "1"
  opening_question: "오늘은 어떤 커피 학습 목표를 먼저 다뤄볼까요?"
  slots:
    - id: brew_goal
      label: 학습 목표
      required: true
      priority: 0
      values: [드립, 프렌치프레스, 원두선택, 보관]
      ask: "드립, 프렌치프레스, 원두 선택, 보관 중 무엇을 먼저 배우고 싶으신가요?"
      signals:
        드립: [드립, 핸드드립, 추출]
        프렌치프레스: [프렌치프레스, 침출]
        원두선택: [원두 선택, 로스팅, 그라인더]
        보관: [보관, 신선도, 산패]
    - id: learner_level
      label: 학습 수준
      required: true
      priority: 1
      values: [처음, 경험있음]
      ask: "커피 추출을 처음 배우시는지, 이미 몇 번 해본 경험이 있는지 알려주세요."
      signals:
        처음: [처음, 입문, 초보]
        경험있음: [해봤, 경험, 몇 번]
    - id: available_tools
      label: 준비 도구
      required: true
      priority: 2
      capture: full_message
      ask: "지금 가지고 있는 도구가 있다면 드리퍼, 저울, 그라인더처럼 알려주세요."
      signals: [드리퍼, 저울, 그라인더, 프렌치프레스, 서버, 주전자, 없음, 준비]
    - id: flavor_preference
      label: 선호 맛
      required: true
      priority: 3
      values: [산미, 균형, 진한맛]
      ask: "산미, 균형, 진한 맛 중 어떤 쪽을 선호하시나요?"
      signals:
        산미: [산미, 상큼, 밝은]
        균형: [균형, 무난, 밸런스]
        진한맛: [진한, 바디감, 쓴맛]
    - id: demo_boundary
      label: 교육용 경계 확인
      required: true
      priority: 4
      capture: full_message
      ask: "이 대화가 실제 주문·민감정보 수집이 아닌 교육용 연습이라는 점을 확인해 주세요."
      signals: [교육용, 연습, 데모, 합성, 개인정보 없음, 집에서]
```

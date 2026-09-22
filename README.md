# FlexRIC A2A 최소 구현

FlexRIC의 `nearRT-RIC`, 내장 gNB 에뮬레이터, Python xApp을 기존 rAgent/xAgent 흐름에 연결한 최소 실행본이다.

![A2A 기반 O-RAN 시스템 모델](results/system_model/a2a_oran_system_model.png)

```text
gNB emulator --E2/MAC indication--> nearRT-RIC --> FlexRIC Python xApp
                                                       |
                         rAgent <--> xAgent <-----------+
                                          |
                              E2 MAC control (action=42)
```

## 실행

Ubuntu 22.04 기준으로 `cmake`, `gcc/g++`, `swig`, `libsctp-dev`, `libpcre2-dev`, `python3-dev`가 필요하다. GitHub의 공개 FlexRIC 미러를 사용하므로 Git 로그인이나 SSH 키는 필요 없다.

```bash
./scripts/setup_flexric.sh
./scripts/run_flexric_demo.sh
```

자동 검증 후 종료하려면 다음처럼 실행한다.

```bash
./scripts/run_flexric_demo.sh --smoke
```

보고서용 30회 반복 실험은 다음 명령으로 실행한다.

```bash
./scripts/run_30_experiments.sh
```

직접 REST/A2A 지연시간, 성공률, E2 indication 지연, 제어 전후 합성 PRB, Agent Card 1/10/50개 탐색시간이 `artifacts/experiment-results/<실행시각>/` 아래 CSV·JSON·Markdown으로 저장된다.

성공 기준은 xApp의 `e2_nodes >= 1`, `indications > 0`, `controls_sent >= 1`, `last_control_success = true`이다. 로그는 `artifacts/flexric-demo/`에 저장된다.

## 구현 범위

- FlexRIC E2AP v2.03 및 MAC service model 사용
- 내장 gNB 에뮬레이터에서 실제 E2 indication 수신
- `prb_usage > 0.70` 정책 충족 시 xAgent가 FlexRIC xApp 호출
- xApp에서 MAC control(action `42`)을 E2 노드로 전송하고 결과 피드백
- GPU/LLM 없이 deterministic fallback으로 즉시 실행 가능

`prb_usage`는 FlexRIC 내장 에뮬레이터가 생성하는 `dl_aggr_prb`(0~1023)를 0~1로 정규화한 데모 지표다. 실제 기지국 연동 시에는 해당 장비의 PRB 산식으로 교체해야 한다.

현재 control `42`는 FlexRIC의 custom MAC service model에서 에뮬레이터가 ACK하는 최소 제어 메시지다. E2 제어 왕복 후 에뮬레이터는 5초 동안 합성 PRB·처리량·BSR 기반 큐 지연·재전송 대리지표를 바꿔 폐루프 효과를 측정한다. 이는 실제 무선 스케줄러 제어가 아니므로, 실제 기지국 단계에서는 대상 RAN이 지원하는 E2SM-RC 또는 scheduler control로 교체해야 한다.

## 최종보고서용 최소 실험

실제 E2 indication 자동 폐루프, 등록 action 30건과 미등록 표현 hold-out 30건의 Rule/LLM 비교, Agent Card 확장성, 별도 프로세스인 두 xApp의 충돌 완화 전후 비교, QoS 대리지표 episode를 한 번에 실행하려면 다음 명령을 사용한다.

```bash
./scripts/run_final_experiments.sh
```

OpenAI 호환 LLM 서버가 `127.0.0.1:8000`에 없으면 로컬 `vllm`으로 `Qwen/Qwen2.5-1.5B-Instruct`를 실행한다. 최초 실행에는 약 3.1 GB 모델 다운로드가 필요하다. 결과는 `artifacts/final-experiment-results/<실행시각>/`에 저장되며 다음 파일을 포함한다.

- `FINAL_EXPERIMENT_RESULTS.md`: 최종 요약표와 해석 범위
- `core_results_dashboard.png`, `core_results_dashboard.pdf`: 핵심 결과 요약 그림
- `llm_performance_tradeoff.*`, `multi_xapp_conflict_performance.*`: LLM 및 충돌 완화 상세 그림
- `qos_episode_effects.*`, `qos_tradeoff_relationship.*`: QoS 효과와 지표 관계 그림
- `final_evaluation.png`, `final_evaluation.pdf`: 전체 실험 6-panel 그림
- `summary.json`: 전체 집계값
- `*.csv`: 각 반복의 원시 측정값
- 컴포넌트별 실행 로그

QoS 결과는 custom action에 반응하는 내장 gNB 에뮬레이터의 합성 대리지표이고, 실제 Mbps·종단 지연 측정값은 아니다. 1~100개 Agent Card 확장성 결과는 mock registry 기반이다.

## 저장소 구성

- `ragent/`, `xagent/`: 정책 생성·전달, Agent Card 탐색, 라우팅 및 충돌 조정
- `flexric_xapp/`, `xapp_monitor/`: FlexRIC E2 연동 및 상태 보고
- `common/`: A2A 메시지와 공통 데이터 스키마
- `experiments/`: 반복실험과 결과 도식화 코드
- `scripts/`: FlexRIC 설치, 데모 및 최종 실험 자동화
- `results/`: 보고서용 시스템 모델과 핵심 결과 그림
- `artifacts/final-experiment-results/20260921T090629Z/`: 공개용 최종 원시 데이터, 요약 및 상세 그림

가상환경, FlexRIC 다운로드·빌드 결과, 실행 로그와 중간 실험은 저장소에 포함하지 않는다. `scripts/setup_flexric.sh`를 실행하면 고정된 FlexRIC commit을 내려받아 필요한 patch를 적용하고 로컬 실행 환경을 다시 구성한다.

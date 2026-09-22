# 보완 실험 plot 및 해석

## LLM 정확도–지연 trade-off

사용 그림: `llm_performance_tradeoff.png` 또는 `.pdf`

| 시험군 | Rule-only | LLM always | Adaptive hybrid |
|---|---:|---:|---:|
| 등록 action 정확도 | 100.0% | 100.0% | 100.0% |
| 등록 action 평균 지연 | 0.018 ms | 474.342 ms | 0.008 ms |
| 미등록 표현 정확도 | 0.0% | 90.0% | 86.7% |
| 미등록 표현 평균 지연 | 0.018 ms | 485.774 ms | 466.463 ms |
| 전체 정확도 | 50.0% | 95.0% | 93.3% |
| 전체 평균 지연 | 0.018 ms | 480.058 ms | 233.236 ms |

Rule-only는 고정 action→capability table과 제한된 keyword 규칙이다. Hold-out의 action과 intent 표현은 이 table에서 제외했다. LLM은 action·objective·metric 전체를 사용했다.

Adaptive hybrid는 rule lookup에 성공하면 LLM을 호출하지 않고, 실패한 30건에만 LLM을 호출한다. 항상 LLM을 호출하는 방식보다 전체 정확도는 1.7%p 낮지만 LLM 호출 수는 60회에서 30회로, 평균 의미 판단 지연은 51.4% 감소했다.

권장 결론: **등록된 정형 action에는 Rule-only가 적합하며 LLM은 정확도 향상 없이 지연만 증가시킨다. 반면 미등록 표현에서는 LLM이 90%의 라우팅 정확도를 제공한다. Rule lookup 실패 시에만 LLM을 호출하는 Adaptive hybrid는 정확도 93.3%를 유지하면서 항상 LLM을 호출하는 방식보다 평균 판단 지연을 51.4% 줄였다.**

## 다중 xApp 충돌 완화 전후

사용 그림: `multi_xapp_conflict_performance.png` 또는 `.pdf`

- LLM 비활성화
- 두 정책 모두 `radio_load > 0.7` threshold에서 발동
- 처리량 xApp: `boost_throughput`
- 에너지 xApp: `reduce_energy`
- 동일 mutex group: `radio_resource`
- 우선순위: 10 대 1, 처리량/에너지 승자를 각각 10회씩 교대
- 완화 전: arbiter 비활성화, 두 xApp 모두 실행
- 완화 후: deterministic priority+mutex arbiter, 높은 priority xApp만 실행

| 지표 | 완화 전 | 완화 후 | 변화 |
|---|---:|---:|---:|
| 충돌 발생률 | 100% | 0% | -100%p |
| 하위 우선순위 xApp 오실행률 | 100% | 0% | -100%p |
| 올바른 단일 승자율 | 0% | 100% | +100%p |
| 상태당 실행 action 수 | 2.0 | 1.0 | -50% |
| 평균 상태→실행 완료 지연 | 102.8 ms | 51.5 ms | -51.3 ms |

지연 감소는 arbiter 계산이 빨라서가 아니라 하위 우선순위 xApp 실행과 feedback 경로 1회를 제거했기 때문이다.

권장 결론: **Rule-based priority+mutex arbiter 적용 전에는 상충하는 두 xApp이 모두 실행되어 충돌률이 100%였으나, 적용 후 높은 priority xApp만 실행되어 충돌률과 하위 우선순위 오실행률이 0%로 감소했다. 상태당 action 수는 2개에서 1개로 감소했고, 불필요한 실행 경로가 제거되어 평균 완료 지연도 51.3 ms 감소했다.**

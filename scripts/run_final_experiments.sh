#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_root/.venv-flexric/bin/python"
flexric_bin="$project_root/.local/flexric/bin"
flexric_conf="$project_root/.local/flexric/etc/flexric/flexric.conf"
flexric_sm="$project_root/.local/flexric/lib/flexric"
sdk_dir="$project_root/third_party/flexric/build_min/examples/xApp/python3"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
result_dir="$project_root/artifacts/final-experiment-results/$run_id"
llm_model="${LLM_MODEL:-Qwen/Qwen2.5-1.5B-Instruct}"

mkdir -p "$result_dir"
pids=()

cleanup() {
  curl --silent --max-time 2 -X POST http://127.0.0.1:8003/demo/auto_report \
    -H 'content-type: application/json' -d '{"enabled":false}' >/dev/null 2>&1 || true
  for pid in "${pids[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  for _ in $(seq 1 20); do
    any_alive=false
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then any_alive=true; fi
    done
    if [[ "$any_alive" == false ]]; then break; fi
    sleep 0.2
  done
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then kill -9 "$pid" >/dev/null 2>&1 || true; fi
    wait "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

wait_http() {
  local url="$1"
  local attempts="${2:-120}"
  for _ in $(seq 1 "$attempts"); do
    if curl --silent --fail --max-time 2 "$url" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

if [[ ! -x "$python_bin" ]] || [[ ! -x "$flexric_bin/nearRT-RIC" ]]; then
  echo "Run scripts/setup_flexric.sh first." >&2
  exit 1
fi

for port in 8001 8002 8003 8101 8102; do
  if ss -ltn 2>/dev/null | grep -qE ":${port}[[:space:]]"; then
    echo "TCP port $port is already in use; stop the stale experiment service first." >&2
    exit 1
  fi
done

if ! curl --silent --fail --max-time 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
  if ! command -v vllm >/dev/null 2>&1; then
    echo "vllm is required when no OpenAI-compatible server is listening on port 8000." >&2
    exit 1
  fi
  vllm serve "$llm_model" --served-model-name "$llm_model" \
    --max-model-len 2048 --gpu-memory-utilization 0.55 \
    --host 127.0.0.1 --port 8000 >"$result_dir/llm.log" 2>&1 &
  pids+=("$!")
  wait_http http://127.0.0.1:8000/v1/models 900
fi

cd "$project_root"
(cd "$result_dir" && exec "$flexric_bin/nearRT-RIC" -c "$flexric_conf" -p "$flexric_sm/") \
  >"$result_dir/nearRT-RIC.log" 2>&1 &
pids+=("$!")
sleep 1
"$flexric_bin/emu_agent_gnb" -c "$flexric_conf" -p "$flexric_sm/" \
  >"$result_dir/emu_agent_gnb.log" 2>&1 &
pids+=("$!")

env LLM_ENABLED=true LLM_BASE_URL=http://127.0.0.1:8000/v1 LLM_MODEL="$llm_model" \
  XAGENT_URL=http://127.0.0.1:8002 \
  "$python_bin" -m uvicorn ragent.app:app --host 127.0.0.1 --port 8001 \
  >"$result_dir/ragent.log" 2>&1 &
pids+=("$!")
env LLM_ENABLED=true LLM_BASE_URL=http://127.0.0.1:8000/v1 LLM_MODEL="$llm_model" \
  RAGENT_URL=http://127.0.0.1:8001 \
  XAPP_URLS=http://127.0.0.1:8003,http://127.0.0.1:8101,http://127.0.0.1:8102 \
  "$python_bin" -m uvicorn xagent.app:app --host 127.0.0.1 --port 8002 \
  >"$result_dir/xagent.log" 2>&1 &
pids+=("$!")
env PYTHONPATH="$sdk_dir" XAGENT_URL=http://127.0.0.1:8002 \
  FLEXRIC_CONF="$flexric_conf" FLEXRIC_SM_DIR="$flexric_sm" \
  REPORT_INTERVAL_SEC=0.05 AUTO_REPORT_ENABLED=false \
  "$python_bin" -m uvicorn flexric_xapp.app:app --host 127.0.0.1 --port 8003 \
  >"$result_dir/flexric_xapp.log" 2>&1 &
pids+=("$!")
env MOCK_XAPP_NAME="Throughput xApp" MOCK_XAPP_CAPABILITY=throughput_control \
  MOCK_XAPP_ACTION=boost_throughput \
  "$python_bin" -m uvicorn experiments.mock_xapp:app --host 127.0.0.1 --port 8101 \
  >"$result_dir/throughput_xapp.log" 2>&1 &
pids+=("$!")
env MOCK_XAPP_NAME="Energy xApp" MOCK_XAPP_CAPABILITY=energy_saving \
  MOCK_XAPP_ACTION=reduce_energy \
  "$python_bin" -m uvicorn experiments.mock_xapp:app --host 127.0.0.1 --port 8102 \
  >"$result_dir/energy_xapp.log" 2>&1 &
pids+=("$!")

wait_http http://127.0.0.1:8001/card
wait_http http://127.0.0.1:8002/card
wait_http http://127.0.0.1:8003/card
wait_http http://127.0.0.1:8101/card
wait_http http://127.0.0.1:8102/card

ric_ready=false
for _ in $(seq 1 120); do
  health="$(curl --silent http://127.0.0.1:8003/healthz)"
  if "$python_bin" -c 'import json,sys; raise SystemExit(not json.load(sys.stdin).get("ok"))' <<<"$health"; then
    ric_ready=true
    break
  fi
  sleep 0.5
done
if [[ "$ric_ready" != true ]]; then
  echo "FlexRIC xApp did not discover an E2 node. Logs: $result_dir" >&2
  exit 1
fi

env LLM_BASE_URL=http://127.0.0.1:8000/v1 \
  "$python_bin" -m experiments.run_final_experiments \
  --output-dir "$result_dir" \
  >"$result_dir/console-summary.json"
"$python_bin" -m experiments.plot_final_results --result-dir "$result_dir" \
  >"$result_dir/plot-files.txt"
"$python_bin" -m experiments.plot_core_results --result-dir "$result_dir" \
  >>"$result_dir/plot-files.txt"

ln -sfn "$run_id" "$project_root/artifacts/final-experiment-results/latest"

echo "Final experiments complete: $result_dir"
sed -n '1,220p' "$result_dir/FINAL_EXPERIMENT_RESULTS.md"

#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_root/.venv-flexric/bin/python"
flexric_bin="$project_root/.local/flexric/bin"
flexric_conf="$project_root/.local/flexric/etc/flexric/flexric.conf"
flexric_sm="$project_root/.local/flexric/lib/flexric"
sdk_dir="$project_root/third_party/flexric/build_min/examples/xApp/python3"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
result_dir="$project_root/artifacts/experiment-results/$run_id"

if [[ ! -x "$python_bin" ]] || [[ ! -x "$flexric_bin/nearRT-RIC" ]]; then
  echo "Run scripts/setup_flexric.sh first." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required to run the experiments." >&2
  exit 1
fi

mkdir -p "$result_dir"
pids=()
cleanup() {
  for pid in "${pids[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  for _ in $(seq 1 10); do
    any_alive=false
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        any_alive=true
      fi
    done
    if [[ "$any_alive" == false ]]; then
      break
    fi
    sleep 0.2
  done
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done
  for pid in "${pids[@]}"; do
    wait "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

wait_http() {
  local url="$1"
  for _ in $(seq 1 60); do
    if curl --silent --fail "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

cd "$project_root"
(cd "$result_dir" && exec "$flexric_bin/nearRT-RIC" -c "$flexric_conf" -p "$flexric_sm/") \
  >"$result_dir/nearRT-RIC.log" 2>&1 &
pids+=("$!")
sleep 1
"$flexric_bin/emu_agent_gnb" -c "$flexric_conf" -p "$flexric_sm/" \
  >"$result_dir/emu_agent_gnb.log" 2>&1 &
pids+=("$!")

env LLM_ENABLED=false XAGENT_URL=http://127.0.0.1:8002 \
  "$python_bin" -m uvicorn ragent.app:app --host 127.0.0.1 --port 8001 \
  >"$result_dir/ragent.log" 2>&1 &
pids+=("$!")
env LLM_ENABLED=false RAGENT_URL=http://127.0.0.1:8001 \
  XAPP_MONITOR_URL=http://127.0.0.1:8003 \
  "$python_bin" -m uvicorn xagent.app:app --host 127.0.0.1 --port 8002 \
  >"$result_dir/xagent.log" 2>&1 &
pids+=("$!")
env PYTHONPATH="$sdk_dir" XAGENT_URL=http://127.0.0.1:8002 \
  FLEXRIC_CONF="$flexric_conf" FLEXRIC_SM_DIR="$flexric_sm" \
  REPORT_INTERVAL_SEC=0.05 AUTO_REPORT_ENABLED=false \
  "$python_bin" -m uvicorn flexric_xapp.app:app --host 127.0.0.1 --port 8003 \
  >"$result_dir/flexric_xapp.log" 2>&1 &
pids+=("$!")

wait_http http://127.0.0.1:8001/card
wait_http http://127.0.0.1:8002/card
wait_http http://127.0.0.1:8003/card

ric_ready=false
for _ in $(seq 1 60); do
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

"$python_bin" -m experiments.run_benchmark \
  --output-dir "$result_dir" --trials 30 --kpi-samples 30 --routing-iterations 5000 \
  >"$result_dir/console-summary.json"
"$python_bin" -m experiments.plot_results --result-dir "$result_dir" \
  >"$result_dir/plot-files.txt"
ln -sfn "$run_id" "$project_root/artifacts/experiment-results/latest"

echo "Experiment complete: $result_dir"
sed -n '1,160p' "$result_dir/REPORT_RESULTS.md"

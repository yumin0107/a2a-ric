import os
import queue
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI

from common.schemas import AgentCard


ROOT = Path(__file__).resolve().parents[1]
SDK_DIR = Path(
    os.getenv(
        "FLEXRIC_PYTHON_SDK",
        ROOT / "third_party/flexric/build_min/examples/xApp/python3",
    )
).resolve()
FLEXRIC_CONF = Path(
    os.getenv("FLEXRIC_CONF", ROOT / ".local/flexric/etc/flexric/flexric.conf")
).resolve()
FLEXRIC_SM_DIR = Path(
    os.getenv("FLEXRIC_SM_DIR", ROOT / ".local/flexric/lib/flexric")
).resolve()
XAGENT_URL = os.getenv("XAGENT_URL", "http://127.0.0.1:8002").rstrip("/")
REPORT_INTERVAL_SEC = float(os.getenv("REPORT_INTERVAL_SEC", "1.0"))
AUTO_REPORT_ENABLED = os.getenv("AUTO_REPORT_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}


class FlexRICService:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.report_queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=1)
        self.sdk: Any = None
        self.nodes: list[Any] = []
        self.callbacks: list[Any] = []
        self.subscription_handles: list[int] = []
        self.initialized = False
        self.last_callback_monotonic = 0.0
        self.last_metrics: Dict[str, float] = {}
        self.metric_history: deque[Dict[str, Any]] = deque(maxlen=10000)
        self.last_error: Optional[str] = None
        self.indication_count = 0
        self.report_count = 0
        self.control_count = 0
        self.last_control_success: Optional[bool] = None
        self.auto_report_enabled = AUTO_REPORT_ENABLED

    def start(self) -> None:
        threading.Thread(target=self._sdk_loop, name="flexric-sdk", daemon=True).start()
        threading.Thread(target=self._report_loop, name="a2a-reporter", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.sdk is not None:
            for handle in list(self.subscription_handles):
                try:
                    self.sdk.rm_report_mac_sm(handle)
                except Exception:
                    pass

    def _sdk_loop(self) -> None:
        try:
            if not FLEXRIC_CONF.is_file():
                raise FileNotFoundError(f"FlexRIC config not found: {FLEXRIC_CONF}")
            if not FLEXRIC_SM_DIR.is_dir():
                raise FileNotFoundError(f"FlexRIC SM directory not found: {FLEXRIC_SM_DIR}")
            sys.path.insert(0, str(SDK_DIR))
            import xapp_sdk as ric

            self.sdk = ric
            ric.init_with_paths(str(FLEXRIC_CONF), f"{FLEXRIC_SM_DIR}/")
            self.initialized = True

            deadline = time.monotonic() + 30
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                self.nodes = list(ric.conn_e2_nodes())
                if self.nodes:
                    break
                time.sleep(0.25)
            if not self.nodes:
                raise RuntimeError("no E2 node connected within 30 seconds")

            service = self

            class MACCallback(ric.mac_cb):
                def __init__(self) -> None:
                    ric.mac_cb.__init__(self)

                def handle(self, indication: Any) -> None:
                    service._handle_mac_indication(indication)

            for node in self.nodes:
                callback = MACCallback()
                handle = ric.report_mac_sm(node.id, ric.Interval_ms_10, callback)
                self.callbacks.append(callback)
                self.subscription_handles.append(handle)
        except Exception as exc:
            self.last_error = f"SDK startup failed: {exc}"

    def _handle_mac_indication(self, indication: Any) -> None:
        self.indication_count += 1
        now = time.monotonic()
        if now - self.last_callback_monotonic < REPORT_INTERVAL_SEC:
            return
        self.last_callback_monotonic = now

        ue_stats = list(indication.ue_stats)
        # The built-in FlexRIC emulator emits dl_aggr_prb in [0, 1023].
        # Normalizing that native field gives the demo's 0..1 policy metric.
        prb_usage = 0.0
        throughput_proxy = 0.0
        queue_delay_proxy_ms = 0.0
        retransmission_proxy = 0.0
        if ue_stats:
            prb_usage = max(
                min(float(ue.dl_aggr_prb) / 1023.0, 1.0) for ue in ue_stats
            )
            throughput_proxy = sum(
                min(float(ue.dl_aggr_tbs) / 1023.0, 1.0) for ue in ue_stats
            ) / len(ue_stats)
            queue_delay_proxy_ms = (
                sum(min(float(ue.bsr) / 1023.0, 1.0) for ue in ue_stats)
                / len(ue_stats)
                * 100.0
            )
            retransmission_proxy = sum(
                min(float(ue.dl_aggr_retx_prb) / 1023.0, 1.0) for ue in ue_stats
            ) / len(ue_stats)
        latency_us = max(0.0, time.time_ns() / 1000.0 - float(indication.tstamp))
        metrics = {
            "prb_usage": round(prb_usage, 4),
            "throughput_proxy": round(throughput_proxy, 4),
            "queue_delay_proxy_ms": round(queue_delay_proxy_ms, 3),
            "retransmission_proxy": round(retransmission_proxy, 4),
            "e2_latency_us": round(latency_us, 1),
            "ue_count": float(len(ue_stats)),
        }
        with self.lock:
            self.last_metrics = metrics
            self.metric_history.append(
                {"timestamp_ms": int(time.time() * 1000), **metrics}
            )

        if not self.auto_report_enabled:
            return

        envelope = {
            "msg_type": "StateReport",
            "sender": "FlexRIC-xApp",
            "receiver": "xAgent",
            "trace_id": str(uuid.uuid4()),
                "payload": {
                    "source": "FlexRIC/MAC_SM",
                    "metrics": metrics,
                    "timestamp_ms": int(time.time() * 1000),
                    "report_started_ns": time.perf_counter_ns(),
                },
        }
        try:
            self.report_queue.put_nowait(envelope)
        except queue.Full:
            try:
                self.report_queue.get_nowait()
            except queue.Empty:
                pass
            self.report_queue.put_nowait(envelope)

    def _report_loop(self) -> None:
        with httpx.Client(timeout=3) as client:
            while not self.stop_event.is_set():
                try:
                    envelope = self.report_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    response = client.post(f"{XAGENT_URL}/a2a/state/report", json=envelope)
                    response.raise_for_status()
                    self.report_count += 1
                    self.last_error = None
                except Exception as exc:
                    self.last_error = f"state report failed: {exc}"

    def execute_mac_control(self) -> bool:
        if self.sdk is None or not self.nodes:
            raise RuntimeError("FlexRIC SDK or E2 node is not ready")
        success = bool(self.sdk.control_mac_sm(self.nodes[0].id))
        self.control_count += 1
        self.last_control_success = success
        return success

    def status(self) -> Dict[str, Any]:
        with self.lock:
            metrics = dict(self.last_metrics)
            metric_samples = len(self.metric_history)
        return {
            "sdk_loaded": self.sdk is not None,
            "initialized": self.initialized,
            "e2_nodes": len(self.nodes),
            "subscriptions": len(self.subscription_handles),
            "indications": self.indication_count,
            "reports_sent": self.report_count,
            "controls_sent": self.control_count,
            "last_control_success": self.last_control_success,
            "auto_report_enabled": self.auto_report_enabled,
            "last_metrics": metrics,
            "metric_samples": metric_samples,
            "last_error": self.last_error,
        }

    def metrics(self) -> list[Dict[str, Any]]:
        with self.lock:
            return list(self.metric_history)

    def reset_metrics(self) -> None:
        with self.lock:
            self.metric_history.clear()
            self.last_metrics = {}

    def set_auto_report(self, enabled: bool) -> None:
        self.auto_report_enabled = enabled


service = FlexRICService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    service.start()
    yield
    service.stop()


app = FastAPI(title="FlexRIC MAC xApp Adapter", lifespan=lifespan)


@app.get("/card", response_model=AgentCard)
def card() -> AgentCard:
    return AgentCard(
        name="FlexRIC MAC xApp",
        role="xApp",
        version="0.1",
        capabilities=["kpi_reporting", "e2_mac_monitoring", "e2_mac_control"],
        endpoints={
            "execute_action": "/a2a/action/execute",
            "health": "/healthz",
        },
        constraints={"service_model": "MAC_STATS_V0", "control_action": 42},
    )


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    status = service.status()
    status["ok"] = bool(status["initialized"] and status["e2_nodes"])
    return status


@app.get("/demo/metrics")
def metrics() -> Dict[str, Any]:
    return {"samples": service.metrics()}


@app.post("/demo/reset_metrics")
def reset_metrics() -> Dict[str, bool]:
    service.reset_metrics()
    return {"ok": True}


@app.post("/demo/auto_report")
def set_auto_report(request: Dict[str, Any]) -> Dict[str, bool]:
    enabled = bool(request.get("enabled", False))
    service.set_auto_report(enabled)
    return {"ok": True, "enabled": enabled}


@app.post("/a2a/action/execute")
def execute_action(event: Dict[str, Any]) -> Dict[str, Any]:
    action = event.get("action") or event.get("payload", {}).get("action")
    if action != "suggest_prb_boost":
        return {"ok": False, "reason": f"unsupported action: {action}"}
    started = time.perf_counter()
    try:
        control_success = service.execute_mac_control()
        return {
            "ok": control_success,
            "control_success": control_success,
            "e2_node_count": len(service.nodes),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "completed_at_ms": int(time.time() * 1000),
        }
    except Exception as exc:
        service.last_error = f"control failed: {exc}"
        return {"ok": False, "control_success": False, "reason": str(exc)}

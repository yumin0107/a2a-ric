import os
import time, uuid, random, asyncio
import httpx
from fastapi import FastAPI
from common.schemas import AgentCard

app = FastAPI(title="xApp Monitor")

XAGENT_URL = os.getenv("XAGENT_URL", "http://localhost:8002")


@app.get("/card", response_model=AgentCard)
def card():
    return AgentCard(
        name="xAppMonitor",
        role="xApp",
        capabilities=["kpi_reporting"],
        endpoints={"report_state": "/a2a/state/report"},
    )


async def loop_report():
    async with httpx.AsyncClient(timeout=5) as client:
        await asyncio.sleep(2.0)  # small grace period for xAgent startup
        while True:
            # toy KPI
            prb_usage = random.uniform(0.3, 0.95)
            thr_mbps = random.uniform(5, 60)

            envelope = {
                "msg_type": "ReportState",
                "sender": "xAppMonitor",
                "receiver": "xAgent",
                "trace_id": str(uuid.uuid4())[:8],
                "payload": {
                    "source": "xAppMonitor",
                    "metrics": {"prb_usage": prb_usage, "throughput_mbps": thr_mbps},
                    "timestamp_ms": int(time.time() * 1000),
                },
            }
            try:
                await client.post(f"{XAGENT_URL}/a2a/state/report", json=envelope)
            except Exception as e:
                # keep the loop alive; warn and back off briefly
                print(f"[xAppMonitor] post failed, retry next tick. err={e}")
                await asyncio.sleep(1.5)
                continue
            await asyncio.sleep(1.0)


@app.on_event("startup")
async def startup():
    asyncio.create_task(loop_report())


@app.get("/healthz")
def healthz():
    return {"ok": True}

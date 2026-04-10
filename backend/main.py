import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from typing import Optional, AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from scraper import scrape, ScraperError
from parser import parse
from simulation import run_simulation
from analytics import compute_analytics
from report import generate_ux_report
from vision import run_visual_analysis

load_dotenv()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CustomPersona(BaseModel):
    id: str
    type: str
    budget: float = Field(ge=1.0, le=10_000.0)
    impulsiveness: float = Field(ge=0.0, le=1.0)
    goal: str = ""


class RunRequest(BaseModel):
    url: str
    price_modifier: Optional[float] = Field(default=1.0, ge=0.1, le=3.0)
    custom_personas: Optional[list[CustomPersona]] = None
    enable_vision: Optional[bool] = False


class ActionStep(BaseModel):
    step: int
    agent_id: str
    action: str
    target: str
    reason: str
    tool_result_preview: Optional[str] = None
    product_url: Optional[str] = None
    product_detail_fetched: Optional[bool] = None


class AgentLog(BaseModel):
    agent_id: str
    persona_type: str
    budget: float
    steps: list[ActionStep]
    result: str


class AgentBreakdown(BaseModel):
    conversion_rate: float
    avg_steps: float
    count: int
    top_drop_theme: Optional[str] = None


class AgentJourneyStep(BaseModel):
    step: int
    action: str
    target: str
    reason: str


class AgentInsight(BaseModel):
    agent_id: str
    persona_type: str
    budget: float
    step_count: int
    exit_reason: str
    journey: list[AgentJourneyStep]
    theme: Optional[str] = None
    purchased_product: Optional[str] = None


class AnalyticsResult(BaseModel):
    conversion_rate: float
    dropoff_rate: float
    avg_steps: float
    ux_score: int
    top_complaints: list[str]
    agent_breakdown: dict[str, AgentBreakdown]
    total_agents: int
    purchased_count: int
    left_count: int
    dropoff_reasons: list[AgentInsight]
    purchase_reasons: list[AgentInsight]
    drop_themes: dict[str, int]


class UXReport(BaseModel):
    executive_summary: str
    overall_score: int
    score_rationale: str
    critical_issues: list[str]
    quick_wins: list[str]
    persona_insights: dict[str, str]
    tools_used_insights: str
    redesign_priorities: list[str]


class VisualAnalysis(BaseModel):
    visual_first_impression: str = ""
    layout_clarity: str = ""
    cta_visibility: str = ""
    trust_signals: str = ""
    friction_points: list[str] = []
    mobile_readiness_guess: str = ""
    visual_score: int = 0
    one_line_verdict: str = ""


class SiteInfo(BaseModel):
    headline: str
    cta_text: str
    products: list[dict]
    ux_breakdown: dict[str, bool]


class RunResponse(BaseModel):
    logs: list[AgentLog]
    analytics: AnalyticsResult
    site_info: SiteInfo
    ux_report: UXReport
    visual_analysis: Optional[VisualAnalysis] = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="AI Customer Simulator", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Helper: build response payload
# ---------------------------------------------------------------------------

def _personas_from_request(custom: list[CustomPersona] | None) -> list[dict] | None:
    if not custom:
        return None
    return [p.model_dump() for p in custom]


def _build_run_response(
    logs: list[dict],
    parsed: dict,
    report: dict,
    visual: dict | None = None,
) -> dict:
    analytics = compute_analytics(logs, parsed["ux_score"])
    return {
        "logs": logs,
        "analytics": analytics,
        "site_info": {
            "headline": parsed["headline"],
            "cta_text": parsed["cta_text"],
            "products": parsed["products"],
            "ux_breakdown": parsed["ux_breakdown"],
        },
        "ux_report": report,
        "visual_analysis": visual or None,
    }


# ---------------------------------------------------------------------------
# POST /run  (blocking — returns full response)
# ---------------------------------------------------------------------------

@app.post("/run", response_model=RunResponse)
async def run_endpoint(body: RunRequest):
    try:
        raw = scrape(body.url)
    except ScraperError as e:
        raise HTTPException(status_code=422, detail=f"Scrape failed: {e}")

    parsed = parse(raw, base_url=body.url)
    custom_personas = _personas_from_request(body.custom_personas)

    try:
        logs = await asyncio.to_thread(
            run_simulation,
            parsed,
            body.price_modifier or 1.0,
            body.url,
            custom_personas,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")

    analytics = compute_analytics(logs, parsed["ux_score"])

    try:
        report = await asyncio.to_thread(generate_ux_report, parsed, logs, analytics)
    except Exception:
        report = _empty_report()

    visual: dict | None = None
    if body.enable_vision:
        try:
            visual = await asyncio.to_thread(run_visual_analysis, body.url, parsed)
        except Exception:
            visual = None

    return RunResponse(
        logs=logs,
        analytics=AnalyticsResult(**analytics),
        site_info=SiteInfo(
            headline=parsed["headline"],
            cta_text=parsed["cta_text"],
            products=parsed["products"],
            ux_breakdown=parsed["ux_breakdown"],
        ),
        ux_report=UXReport(**report),
        visual_analysis=VisualAnalysis(**visual) if visual else None,
    )


# ---------------------------------------------------------------------------
# POST /run-stream  (SSE — sends events as they happen)
# ---------------------------------------------------------------------------

def _sse(event_type: str, data: dict) -> str:
    payload = json.dumps({"type": event_type, **data})
    return f"data: {payload}\n\n"


async def _stream_simulation(body: RunRequest) -> AsyncGenerator[str, None]:
    # 1. Scrape
    yield _sse("status", {"message": "Scraping page..."})
    try:
        raw = await asyncio.to_thread(scrape, body.url)
    except ScraperError as e:
        yield _sse("error", {"message": f"Scrape failed: {e}"})
        return

    # 2. Parse
    yield _sse("status", {"message": "Parsing site structure..."})
    parsed = await asyncio.to_thread(parse, raw, body.url)
    yield _sse("site_info", {
        "headline": parsed["headline"],
        "cta_text": parsed["cta_text"],
        "products": parsed["products"],
        "ux_score": parsed["ux_score"],
        "ux_breakdown": parsed["ux_breakdown"],
    })

    # 3. Vision (optional, runs in background — emit when ready)
    vision_task: asyncio.Task | None = None
    if body.enable_vision:
        yield _sse("status", {"message": "Taking page screenshot for visual analysis..."})
        vision_task = asyncio.create_task(
            asyncio.to_thread(run_visual_analysis, body.url, parsed)
        )

    # 4. Simulate with streaming events
    queue: asyncio.Queue = asyncio.Queue()
    custom_personas = _personas_from_request(body.custom_personas)

    def _on_event(event: dict) -> None:
        queue.put_nowait(event)

    sim_task = asyncio.create_task(
        asyncio.to_thread(
            run_simulation,
            parsed,
            body.price_modifier or 1.0,
            body.url,
            custom_personas,
            _on_event,
        )
    )

    # Drain queue while simulation runs
    while not sim_task.done():
        try:
            event = queue.get_nowait()
            yield _sse(event["type"], {k: v for k, v in event.items() if k != "type"})
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.05)

    # Drain any remaining events
    while not queue.empty():
        event = queue.get_nowait()
        yield _sse(event["type"], {k: v for k, v in event.items() if k != "type"})

    if sim_task.exception():
        yield _sse("error", {"message": f"Simulation failed: {sim_task.exception()}"})
        return

    logs: list[dict] = sim_task.result()

    # 5. Analytics
    yield _sse("status", {"message": "Computing analytics..."})
    analytics = compute_analytics(logs, parsed["ux_score"])
    yield _sse("analytics", analytics)

    # 6. UX Report
    yield _sse("status", {"message": "Generating GPT-4o UX consultant report..."})
    try:
        report = await asyncio.to_thread(generate_ux_report, parsed, logs, analytics)
    except Exception:
        report = _empty_report()
    yield _sse("report", report)

    # 7. Visual analysis result (if requested)
    if vision_task is not None:
        visual = await vision_task
        if visual:
            yield _sse("visual_analysis", visual)

    # 8. Done
    yield _sse("done", {"logs": logs})


@app.post("/run-stream")
async def run_stream_endpoint(body: RunRequest):
    return StreamingResponse(
        _stream_simulation(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_report() -> dict:
    return {
        "executive_summary": "Report generation unavailable.",
        "overall_score": 0,
        "score_rationale": "",
        "critical_issues": [],
        "quick_wins": [],
        "persona_insights": {"budget": "", "luxury": "", "impulsive": ""},
        "tools_used_insights": "",
        "redesign_priorities": [],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

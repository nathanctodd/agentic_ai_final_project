# AI Customer Simulator

An agentic AI system that simulates real customer behavior on any e-commerce website. Point it at a URL and watch AI personas browse, compare, research, and decide — then get a full UX report with actionable conversion insights.

Link to demo video: [YouTube Link](https://www.youtube.com/watch?v=2bC05EXA2RM)

## How It Works

1. **Scrape** — The backend fetches and parses the target e-commerce page (headline, products, prices, UX signals).
2. **Simulate** — Nine AI personas (budget, luxury, impulsive) independently browse the site using OpenAI's function-calling API. Each agent decides its own tool sequence: viewing products, comparing options, searching reviews, checking return policies, and navigating to other pages.
3. **Analyze** — Conversion rates, drop-off rates, average steps, and behavioral themes are computed across all agents.
4. **Report** — A GPT-4o powered UX consultant report synthesizes agent journeys into prioritized, actionable recommendations.
5. **Vision (optional)** — Playwright captures a full-page screenshot and GPT-4o vision analyzes it for layout clarity, CTA visibility, trust signals, and friction points.

## Features

- **9 default personas** across 3 archetypes (budget, luxury, impulsive) with configurable budgets and impulsiveness
- **Custom personas** — define your own persona types, budgets, and goals via the UI
- **A/B price testing** — slide a price modifier (0.5x–1.5x) to see how pricing changes affect conversion
- **Real-time streaming** — SSE endpoint streams agent steps live as they happen
- **Visual analysis** — optional GPT-4o vision pass for screenshot-based UX feedback
- **Tool-calling agents** — agents use `view_product`, `compare_products`, `search_reviews`, `check_return_policy`, `navigate_to_page`, `purchase`, and `leave`

## Project Structure

```
agentic_ai_final_project/
├── backend/
│   ├── main.py          # FastAPI app — /run and /run-stream endpoints
│   ├── agents.py        # OpenAI tool-calling agent loop + persona definitions
│   ├── simulation.py    # Runs all personas, emits streaming events
│   ├── scraper.py       # Page + product detail scraping
│   ├── parser.py        # Extracts headline, products, UX signals from HTML
│   ├── analytics.py     # Computes conversion/drop-off metrics and themes
│   ├── report.py        # GPT-4o UX consultant report generation
│   └── vision.py        # Playwright screenshot + GPT-4o vision analysis
├── frontend/
│   ├── index.html       # Single-page UI
│   ├── app.js           # SSE client, rendering, A/B test logic
│   └── styles.css       # Styles
└── requirements.txt
```

## Setup

### Prerequisites

- Python 3.11+
- An OpenAI API key with access to `gpt-4o` and `gpt-4o-mini`

### Install

```bash
pip install -r requirements.txt
playwright install chromium  # only needed for Visual Analysis
```

### Configure

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...
```

### Run

```bash
cd backend
python main.py
```

The API starts at `http://localhost:8000`. Open `frontend/index.html` directly in a browser (no build step needed).

## API

### `POST /run`

Blocking endpoint. Returns the full simulation result when all agents finish.

**Request body:**
```json
{
  "url": "https://example-store.com",
  "price_modifier": 1.0,
  "enable_vision": false,
  "custom_personas": [
    {
      "id": "my_persona_1",
      "type": "budget",
      "budget": 75.0,
      "impulsiveness": 0.3,
      "goal": "find the cheapest option available"
    }
  ]
}
```

**Response:** `RunResponse` — logs, analytics, site info, UX report, optional visual analysis.

### `POST /run-stream`

Same request body as `/run`. Returns a Server-Sent Events stream with incremental events:

| Event type | Payload |
|---|---|
| `status` | Status message string |
| `site_info` | Parsed page data |
| `agent_start` | Persona id, type, budget, goal |
| `step` | Individual agent tool call |
| `agent_done` | Agent result and step count |
| `analytics` | Computed metrics |
| `report` | UX consultant report |
| `visual_analysis` | Vision results (if enabled) |
| `done` | Full logs |

### `GET /health`

Returns `{"status": "ok"}`.

## Agent Tools

Each persona has access to these tools and calls them in any order it chooses:

| Tool | Description |
|---|---|
| `view_product` | Scrapes a product's detail page for full specs |
| `compare_products` | Side-by-side price and description comparison |
| `search_reviews` | DuckDuckGo web search for real reviews |
| `check_return_policy` | Fetches the site's return/refund policy |
| `navigate_to_page` | Navigates to cart, checkout, shipping, sale sections, etc. |
| `purchase` | Buys a product — ends the session |
| `leave` | Leaves without buying — ends the session |

Agents run for a maximum of 8 tool calls before being forced to leave.

## Default Personas

| ID | Type | Budget | Impulsiveness | Goal |
|---|---|---|---|---|
| budget_1 | budget | $90 | 0.20 | Find the best deal |
| budget_2 | budget | $110 | 0.25 | Get solid value for money |
| budget_3 | budget | $80 | 0.15 | Spend as little as possible |
| luxury_1 | luxury | $500 | 0.60 | Highest quality premium product |
| luxury_2 | luxury | $400 | 0.50 | Premium product that signals quality |
| luxury_3 | luxury | $600 | 0.65 | Best version, price is no barrier |
| impulsive_1 | impulsive | $180 | 0.90 | Buy if it catches my eye |
| impulsive_2 | impulsive | $200 | 0.85 | First appealing product, purchased |
| impulsive_3 | impulsive | $150 | 0.95 | Quick gut-feeling decision |

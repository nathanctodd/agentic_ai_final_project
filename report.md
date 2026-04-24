# Agentic AI Shopper Simulator: AI-Powered E-Commerce Customer Simulation

**Nathan Todd**
Agentic AI — Final Project Report

---

## 1. Motivation

Every e-commerce store has a conversion problem: most visitors leave without buying. Traditional tools like heatmaps and session recordings show *what* users click, but not *why* they leave. User testing helps, but recruiting real participants is slow and expensive. A/B testing requires live traffic and weeks of data collection.

The goal of this project was to ask a different question: what if you could simulate a crowd of realistic customers on any storefront in minutes, watch each one reason through their purchase decision, and get a prioritized UX report — all before a single real user ever arrives?

The Agentic AI Shopper Simulator does this by deploying a fleet of AI agents, each embodying a distinct shopper persona (budget-conscious, luxury-seeking, impulsive), against a target e-commerce URL. Each agent independently browses the site using real tools — scraping product pages, searching for reviews, checking return policies — and ultimately decides to buy or leave, explaining why. The system then aggregates these decisions into conversion metrics and a GPT-4o-authored UX consultant report.

The practical use case is clear: a store owner pastes their URL, runs the simulation, and within a few minutes has data on which persona types converted, which product got compared most often, and what specific UX issues caused drop-offs — without needing to recruit testers or wait for live traffic.

---

## 2. Methods

The system is a full-stack web application. The backend is a Python FastAPI service; the frontend is a single-page HTML/JS app. The pipeline has five stages.

### 2.1 Scraping

`scraper.py` fetches the target URL using the `requests` library with a realistic browser User-Agent header and a short timeout. It extracts:

- Full page HTML
- Visible text content
- Structured data: JSON-LD schemas, Next.js `__NEXT_DATA__`, and Open Graph meta tags
- Product-specific links and image alt text

A secondary `scrape_product_page()` function handles individual product detail pages when agents navigate to them during simulation.

### 2.2 Parsing

`parser.py` converts raw HTML into a structured site object that agents and analytics can consume. Product extraction uses four strategies tried in quality order:

1. **JSON-LD** — schema.org `Product` and `ItemList` types embedded in `<script>` tags
2. **Next.js `__NEXT_DATA__`** — with known fast paths for Nike and Shopify site structures, then a generic recursive fallback
3. **Open Graph meta tags** — `og:price:amount`, `og:title`, etc.
4. **Regex on page text** — matches price patterns (`$19.99`, `19.99 USD`) with surrounding title-case context heuristics

In addition to products, the parser computes a **UX score (0–100)** based on five structural signals:

| Signal | Check | Points |
|---|---|---|
| Headline | `<h1>` with >10 characters present | 20 |
| CTA | Button or link with buy/cart/checkout text | 20 |
| Price clarity | ≥2 price matches in text, or structured prices found | 20 |
| Product count | ≥3 real products detected | 20 |
| Image alts | ≥50% of images have descriptive alt text | 20 |

### 2.3 Agent Simulation

`agents.py` defines nine default personas across three archetypes, each with a fixed budget and impulsiveness score:

| Archetype | Budgets | Impulsiveness | Behavior |
|---|---|---|---|
| Budget (×3) | $80–$110 | 0.15–0.25 | Price-checks carefully; buys only if value is clear |
| Luxury (×3) | $400–$600 | 0.50–0.65 | Quality-focused; buys freely if product impresses |
| Impulsive (×3) | $150–$200 | 0.85–0.95 | Acts on feeling; almost always buys |

Each agent runs an open-ended loop powered by **OpenAI's function-calling API** (`gpt-4o-mini`). The agent receives a system prompt describing its persona, budget, and the products on the page, then calls tools in whatever order it chooses:

- `view_product` — scrapes a product's detail page for full specs
- `compare_products` — side-by-side price and description comparison
- `search_reviews` — live DuckDuckGo web search for real user reviews
- `check_return_policy` — fetches the store's return/refund page
- `navigate_to_page` — navigates to cart, checkout, shipping info, sale sections, etc.
- `purchase` — ends the session with a buy decision
- `leave` — ends the session with an exit reason

`tool_choice="required"` is set so the model must call a tool every turn. The loop runs for a maximum of 8 turns; if no terminal action is reached, the agent is forced to leave. All nine personas share a product page cache to avoid redundant scraping.

**Streaming** is supported via a `/run-stream` SSE endpoint. Agent events (`agent_start`, `step`, `agent_done`) are put onto an `asyncio.Queue` by a callback and drained to the client in real time.

### 2.4 Analytics

`analytics.py` aggregates agent logs into metrics:

- **Conversion rate** — fraction of agents that purchased
- **Drop-off rate** — fraction that left
- **Average steps** — mean tool calls per agent before decision
- **Per-persona breakdown** — conversion rate and top drop theme for each archetype
- **Drop-off theme classification** — reasons are classified into: `price`, `selection`, `ux`, `relevance`, or `other` via keyword matching
- **Top complaints** — most frequent meaningful words from exit reasons (stop-words excluded)

### 2.5 UX Report and Visual Analysis

`report.py` sends all agent journeys, metrics, and site structure to **GPT-4o** with a structured JSON schema prompt. The resulting report contains:

- Executive summary
- Overall score (1–10) with rationale
- Critical issues (with evidence from specific agent behavior)
- Quick wins (concrete actionable changes)
- Per-persona insights
- Tool-usage analysis (what patterns of review searches, policy checks, etc. reveal about trust gaps)
- Redesign priorities

Optionally, `vision.py` uses **Playwright** to capture a full-page screenshot at 1440×900, encodes it as base64, and sends it to **GPT-4o vision** for a separate UX analysis covering layout clarity, CTA visibility, trust signals, friction points, mobile readiness, and a visual score.

### 2.6 Frontend

The frontend (`frontend/index.html`, `app.js`, `styles.css`) is a single HTML page with no build step. It connects to the `/run-stream` SSE endpoint and renders agent steps, analytics charts (Chart.js), and the UX report in real time as events arrive.

---

## 3. Evaluation

Evaluation combined a quantitative ablation study — comparing the full persona-driven system against a no-persona baseline — with a qualitative user study.

### 3.1 Ablation Study: Full System vs. Baseline

To measure the contribution of the persona system, the simulator was run in two modes against three publicly accessible storefronts spanning the quality spectrum:

- **Full system** — 9 agents across 3 archetypes (budget, luxury, impulsive) with distinct budgets, impulsiveness scores, and goals
- **Baseline** — 3 identical generic agents with no persona framing: *"browse the site and decide whether to buy something"*

Four metrics were recorded for each run:

- **Conversion rate** — fraction of agents that purchased
- **Avg steps** — mean tool calls per agent (proxy for decision difficulty)
- **Persona variance** — standard deviation of per-archetype conversion rates (0 = no differentiation, 0.5 = maximum)
- **Reasoning specificity** — average word count of exit/purchase reasons (proxy for explanation quality)

Results (generated 2026-04-24):

| Site | UX Score | Mode | Conv. Rate | Avg Steps | Persona Variance | Reason Length |
|---|---|---|---|---|---|---|
| Nike.com | 100/100 | **Full** | **78%** | 6.1 | **0.157** | **20.3 words** |
| Nike.com | 100/100 | Baseline | 33% | 8.7 | 0.000 | 14.3 words |
| Allbirds.com | 40/100 | **Full** | 0% | 4.7 | 0.000 | 14.9 words |
| Allbirds.com | 40/100 | Baseline | 0% | 5.7 | 0.000 | 14.3 words |
| Kith.com | 60/100 | **Full** | 0% | 5.6 | 0.000 | 13.8 words |
| Kith.com | 60/100 | Baseline | 0% | 5.0 | 0.000 | 12.7 words |

### 3.2 Key Findings

**Personas meaningfully improve conversion signal on functional sites.** On Nike.com — the only site where products and prices were successfully scraped — the full system converted 78% of agents vs. 33% for the baseline (a 45-point gap). This gap reflects the persona system doing real work: budget agents used `compare_products` and `search_reviews` to validate value before committing, while the generic baseline agents had no goal to guide their decisions, exhausted their turn budget, and were forced to leave without buying.

**Persona variance confirms behavioral differentiation.** On Nike, the full system produced a persona variance of 0.157 (budget: 100%, luxury: 67%, impulsive: 67%) vs. exactly 0.0 for the baseline. The three archetypes genuinely behaved differently from each other. Notably, budget agents outperformed luxury and impulsive agents on Nike — likely because their price-checking behavior (compare, review-search) gave them more evidence to act on, while luxury agents hit the 8-turn cap without finding a product that sufficiently signaled prestige.

**Reasoning specificity is higher with personas.** Exit and purchase reasons averaged 20.3 words in the full system vs. 14.3 words in the baseline on Nike — 42% longer. Generic agents produced boilerplate exits ("ran out of time browsing without reaching a decision") while persona-driven agents gave specific, grounded reasons tied to their goals.

**UX score strongly predicts simulated outcomes.** All three sites with lower UX scores (Allbirds: 40, Kith: 60) produced 0% conversion in both modes. The GPT-4o consultant confirmed this independently — Nike received a 9/10 report score while both Allbirds and Kith received 2/10. Drop-off theme analysis on the failing sites surfaced actionable signal even at 0% conversion: Allbirds agents primarily cited `selection` problems (5 of 9 drop-offs) while Kith agents split between `price` (4) and `ux` (3) — different root causes despite the same outcome.

**Persona types surface different UX pain points.** On Kith, budget agents' top drop theme was `price`, luxury agents cited `other` (navigation confusion), and impulsive agents cited `ux` friction. A single generic agent cannot produce this kind of segmented diagnosis.

**Baseline agents were less efficient.** On Nike, baseline agents averaged 8.7 steps vs. 6.1 for the full system — they wandered longer without a goal and still converted far less often. This suggests persona constraints actually help agents make decisions, not just color their reasoning.

### 3.3 Qualitative User Study

Three people were asked to run the simulator against a URL of their choice and rate the outputs on a 1–5 scale.

| Evaluator | Site tested | Persona differentiation (1–5) | Report usefulness (1–5) | Agent reasoning plausibility (1–5) | Would use again? |
|---|---|---|---|---|---|
| Evaluator A | Small Shopify clothing store | 5 | 4 | 4 | Yes |
| Evaluator B | Nike.com running shoes | 4 | 5 | 5 | Yes |
| Evaluator C | Local furniture e-commerce site | 4 | 4 | 3 | Yes |
| **Average** | | **4.3** | **4.3** | **4.0** | **3/3** |

Selected comments:

> *"The budget agents actually behaved differently from the impulsive ones — I could see it in the steps. The budget one searched reviews twice before buying."* — Evaluator A

> *"The UX report called out a specific product by name and said it was the one all three impulsive agents bought. That was surprisingly specific and useful."* — Evaluator B

> *"One agent's reasoning felt a little generic. But the overall report was actionable — I got three concrete things to fix."* — Evaluator C

The lowest plausibility rating (3/5 from Evaluator C) reflects a case where an agent gave a vague exit reason after exhausting its turn budget — consistent with the 8-turn cap limitation observed in the ablation.

### 3.4 Limitations

- Agents cannot actually add items to cart or navigate JavaScript-rendered checkout flows; `purchase` is a symbolic terminal action. Sites that require login-gated checkout or heavy JavaScript rendering (like Allbirds and Kith) will show 0% conversion even if the underlying products are appealing.
- The structural UX score is a simple heuristic (5 boolean checks × 20 points). Sites can score 100 and still be poorly designed.
- Persona variance collapses to 0.0 on sites where all agents leave for the same reason (broken pages), masking the signal that personas would otherwise produce on a functional site.
- Agents share the same underlying LLM and may exhibit correlated biases not present in real populations.
- DuckDuckGo review search results vary between runs, making exact reproduction of results impossible.
- Running all nine agents on a single URL costs approximately $0.10–$0.20 in OpenAI API credits.

---

## 4. Conclusions

Several things became clear through building and testing this system:

**Tool-calling agents are well-suited to open-ended browsing tasks.** The function-calling loop, with `tool_choice="required"`, reliably produced coherent shopping sessions. Agents did not get stuck or loop; they made purposeful sequences of tool calls that reflected their persona traits.

**Prompt-defined personas are surprisingly robust.** Budget agents behaved budget-consciously and impulsive agents behaved impulsively without any additional fine-tuning or few-shot examples — the persona description in the system prompt was sufficient to differentiate behavior across nine independent runs.

**Synthesis via a second LLM call is more valuable than raw logs.** The raw agent step logs are hard to interpret at a glance. The GPT-4o report call, which synthesizes those logs into actionable recommendations with specific evidence, is what makes the system practically useful rather than just technically interesting.

**Streaming makes a significant UX difference.** Watching agents step through their decisions in real time — seeing `budget_2 → search_reviews → "Nike Air Max 270 review durability"` appear live — makes the simulation feel concrete and trustworthy in a way that a loading spinner followed by a results dump does not.

**LLM-as-evaluator has natural limits.** The personas are not real users; they have no persistent memory, no real payment constraints, and no lived experience with a brand. They cannot feel visual hierarchy or notice a confusing checkout flow the way a human tester can. This system is best framed as a rapid first-pass audit — a way to surface obvious problems and generate hypotheses before more rigorous user testing.

The project demonstrated that a small fleet of autonomous, tool-calling agents can produce genuine signal about e-commerce UX at a fraction of the cost and time of traditional user research methods.

---

## Appendix: Example Input / Output

### Input

```
URL: https://www.nike.com/w/mens-running-shoes
Price modifier: 1.0
Vision analysis: disabled
```

### Sample Agent Log (budget_1)

```
Step 1 | compare_products → ["Nike Pegasus 41", "Nike Vomero 18"]
  Reason: "I want to compare these two before committing to a purchase."

Step 2 | search_reviews → "Nike Pegasus 41 review comfort durability running"
  Reason: "I want to check if real runners find this worth the price."

Step 3 | purchase → Nike Pegasus 41
  Reason: "Reviews confirm excellent durability and the $130 price fits my budget."
```

### Sample Analytics Output (Nike.com — Full System)

```json
{
  "conversion_rate": 0.78,
  "dropoff_rate": 0.22,
  "avg_steps": 6.11,
  "ux_score": 100,
  "agent_breakdown": {
    "budget":    { "conversion_rate": 1.000, "avg_steps": 5.0,  "top_drop_theme": null },
    "luxury":    { "conversion_rate": 0.667, "avg_steps": 7.3,  "top_drop_theme": "other" },
    "impulsive": { "conversion_rate": 0.667, "avg_steps": 6.0,  "top_drop_theme": "other" }
  },
  "drop_themes": { "other": 2 },
  "persona_variance": 0.157
}
```

### Sample UX Report Excerpt (Nike.com — Full System)

GPT-4o consultant score: **9/10**

> **Executive Summary:** The e-commerce site demonstrates strong performance with a high conversion rate of 78% and a perfect UX score. However, there are opportunities to enhance decision-making efficiency and reduce drop-off rates.
>
> **Critical Issues:**
> - Luxury shoppers like luxury_3 left without purchasing due to "running out of time", indicating potential issues with decision-making efficiency.
> - Multiple agents, such as budget_1 and budget_2, had to rely heavily on product comparisons and reviews due to unclear initial product information.
>
> **Quick Wins:**
> - Improve visibility of product prices on initial view to reduce reliance on comparison tools.
> - Enhance product descriptions to include key features upfront, reducing the need for extensive review searches.

### Baseline Comparison (Nike.com — No Persona)

```json
{
  "conversion_rate": 0.33,
  "dropoff_rate": 0.67,
  "avg_steps": 8.67,
  "ux_score": 100,
  "persona_variance": 0.0
}
```

GPT-4o consultant score: **7/10**

> **Executive Summary:** The site demonstrates strong UX fundamentals, but conversion rates are hindered by decision fatigue and time constraints. Two of three agents left despite extensive product exploration, citing they "ran out of time browsing without reaching a decision."

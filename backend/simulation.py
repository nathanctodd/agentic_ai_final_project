from typing import Callable

from agents import PERSONAS, run_agent_loop


def run_simulation(
    parsed_site: dict,
    price_modifier: float = 1.0,
    base_url: str = "",
    custom_personas: list[dict] | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Run all personas through tool-calling agent loops.

    Args:
        parsed_site: Output from parser.parse()
        price_modifier: Price multiplier shown to agents in prompts.
        base_url: Original URL — used by navigate_to_page + check_return_policy.
        custom_personas: If provided, overrides the default PERSONAS list.
        on_event: Optional callback for streaming. Called with event dicts:
                  {"type": "agent_start", ...}
                  {"type": "step", ...}
                  {"type": "agent_done", ...}

    Returns:
        List of agent log dicts, one per persona.
    """
    personas = custom_personas if custom_personas else PERSONAS
    all_logs = []
    product_page_cache: dict = {}

    for persona in personas:
        if on_event:
            on_event({
                "type": "agent_start",
                "agent_id": persona["id"],
                "persona_type": persona["type"],
                "budget": persona["budget"],
                "goal": persona.get("goal", ""),
            })

        def _on_step(step: dict, _pid=persona["id"]) -> None:
            if on_event:
                on_event({"type": "step", "agent_id": _pid, "data": step})

        steps, result = run_agent_loop(
            persona=persona,
            page_data=parsed_site,
            price_modifier=price_modifier,
            product_page_cache=product_page_cache,
            base_url=base_url,
            on_step=_on_step,
        )

        log = {
            "agent_id": persona["id"],
            "persona_type": persona["type"],
            "budget": persona["budget"],
            "steps": steps,
            "result": result,
        }
        all_logs.append(log)

        if on_event:
            on_event({"type": "agent_done", "agent_id": persona["id"],
                      "result": result, "step_count": len(steps)})

    return all_logs

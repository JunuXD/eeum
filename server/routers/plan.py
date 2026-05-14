from fastapi import APIRouter

from models.schemas import (
    AwaitClickAction,
    AwaitClickTextAction,
    ClickAction,
    ClickTextAction,
    DomElement,
    HighlightAction,
    NavigateAction,
    PlanRequest,
    PlanResponse,
    ScrollAction,
    SelectAction,
    TypeAction,
    WaitAction,
    WaitForUserAction,
)
from services import llm, session


router = APIRouter(tags=["plan"])


def _action_from_llm(a: dict, elements: list[DomElement]):
    action_type = a.get("type")

    if action_type == "navigate":
        url = a.get("url")
        if not url:
            return None
        return NavigateAction(url=url)
    if action_type == "click_text":
        text = a.get("text")
        if not text:
            return None
        return ClickTextAction(text=text)
    if action_type == "scroll":
        return ScrollAction(
            direction=a.get("direction", "down"),
            amount=int(a.get("amount", 300)),
        )
    if action_type == "wait":
        return WaitAction(ms=int(a.get("ms", 500)))
    if action_type == "wait_for_user":
        return WaitForUserAction(instruction=a.get("instruction", ""))

    # index 기반 액션 — index → xpath 변환
    idx = a.get("index")
    if not isinstance(idx, int) or not (0 <= idx < len(elements)):
        return None
    xpath = elements[idx].xpath

    if action_type == "click":
        return ClickAction(xpath=xpath)
    if action_type == "type":
        return TypeAction(xpath=xpath, value=a.get("value", ""))
    if action_type == "select":
        return SelectAction(xpath=xpath, value=a.get("value", ""))
    if action_type == "highlight":
        return HighlightAction(xpath=xpath)

    return None


async def _run_plan(req: PlanRequest, plan_fn) -> PlanResponse:
    session_id, expires_at = await session.touch_or_create(req.session_id)

    elements = req.current_elements or []
    raw_plan = await plan_fn(
        query=req.query,
        url=req.current_url or "",
        elements=elements,
    )

    actions = []
    for a in raw_plan.get("actions") or []:
        converted = _action_from_llm(a, elements)
        if converted is not None:
            actions.append(converted)

    return PlanResponse(
        session_id=session_id,
        expires_at=expires_at,
        explanation=raw_plan.get("explanation") or "",
        actions=actions,
        needs_more_elements=bool(raw_plan.get("needs_more_elements")),
    )


def _defer_click(action):
    # 사용자 의도가 "어디로 가줘"가 아닌 한 자동 클릭을 막고
    # 하이라이트 + 클릭 대기 형태로 바꾼다. navigate/type/select/scroll은 그대로.
    if isinstance(action, ClickAction):
        return AwaitClickAction(xpath=action.xpath)
    if isinstance(action, ClickTextAction):
        return AwaitClickTextAction(text=action.text)
    return action


@router.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest) -> PlanResponse:
    return await _run_plan(req, llm.plan_actions)


@router.post("/plan/strict", response_model=PlanResponse)
async def plan_strict(req: PlanRequest) -> PlanResponse:
    resp = await _run_plan(req, llm.plan_actions_strict)
    resp.actions = [_defer_click(a) for a in resp.actions]
    return resp

"""Keep fixed and agent pipelines explicitly selectable for fair comparisons."""

from app.answering.service import PIPELINE_VERSION as FIXED_VERSION
from app.answering.service import answer as fixed_answer


def pipeline_version(name):
    if name == "fixed":
        return FIXED_VERSION
    if name == "agent":
        from app.agents.service import PIPELINE_VERSION

        return PIPELINE_VERSION
    raise ValueError("Choose the fixed or agent pipeline")


async def answer(*args, pipeline="fixed", planner=None, **kwargs):
    if pipeline == "fixed":
        return await fixed_answer(*args, **kwargs)
    if pipeline == "agent":
        from app.agents.service import answer as agent_answer

        return await agent_answer(*args, planner=planner, **kwargs)
    raise ValueError("Choose the fixed or agent pipeline")

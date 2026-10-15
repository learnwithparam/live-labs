import asyncio
from types import SimpleNamespace

from fakes import FakeClient
from pipecat.frames.frames import EndFrame, LLMContextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.workers.runner import WorkerRunner

from lucca.agent import MeteredLLM, OrderTools
from lucca.till import Till


async def converse(replies: list[dict], heard: str) -> tuple[Till, FakeClient, MeteredLLM, list[dict]]:
    """One caller turn through Pipecat's own LLM service and context aggregator: the model calls a till
    tool, Pipecat runs it, puts the result in the context, and asks the model again."""
    till, events = Till(), []
    tools = OrderTools(till, 412, events.append)
    llm = MeteredLLM(model="openai/gpt-6-luna", api_key="test", base_url="http://127.0.0.1:9")
    client = FakeClient(replies)
    llm._client = client
    context = LLMContext(messages=[{"role": "user", "content": heard}], tools=tools.schema())
    pair = LLMContextAggregatorPair(context)
    worker = PipelineWorker(Pipeline([llm, pair.assistant()]), idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    running = asyncio.create_task(runner.run())
    await worker.queue_frame(LLMContextFrame(context))
    for _ in range(200):
        if not client.completions.replies and len(llm.usage) == len(client.completions.requests):
            break
        await asyncio.sleep(0.02)
    await worker.queue_frame(EndFrame())
    await running
    return till, client, llm, events


def test_the_model_keys_the_order_through_till_tools_and_reads_back_what_the_till_holds():
    replies = [{"tools": [("add_item", {"item": "pepperoni", "size": "large", "options": ["thin crust"]}),
                          ("add_item", {"item": "buffalo wings", "size": "dozen"})]},
               {"text": "Got it, a large thin crust pepperoni and a dozen buffalo wings."}]
    till, client, llm, events = asyncio.run(converse(replies, "a large pepperoni on thin crust and a dozen buffalo wings"))
    assert [line.item for line in till.lines] == ["pepperoni-pizza", "wings"]
    # The second request carries both tool results, so the read-back comes from the till.
    tool_results = [message for message in client.completions.requests[1]["messages"] if message["role"] == "tool"]
    assert len(tool_results) == 2 and "12 piece Wings with buffalo" in tool_results[1]["content"]
    # Both results land before the model is asked again, and it is asked once: two replies in flight
    # would disagree with each other.
    assert len(client.completions.requests) == 2
    assert [event["tool"] for event in events] == ["add_item", "add_item"]
    assert llm.cost() == 0.0001  # two model calls, each billed by the endpoint


def test_a_refused_item_reaches_the_model_as_an_error_with_the_next_step():
    replies = [{"tools": [("add_item", {"item": "family special"})]},
               {"tools": [("send_to_staff", {"reason": "the family special is not on the menu"})]},
               {"text": "A team member will call you right back."}]
    till, client, _, events = asyncio.run(converse(replies, "the family special please"))
    assert "send_to_staff" in events[0]["result"]["error"]
    assert till.status == "staff" and till.staff_reason == "the family special is not on the menu"
    assert not till.lines


def test_usage_falls_back_to_the_list_price_when_the_endpoint_reports_no_cost():
    llm = MeteredLLM(model="openai/gpt-6-luna", api_key="test", base_url="http://127.0.0.1:9")

    class Usage:
        prompt_tokens, completion_tokens, prompt_tokens_details, model_extra = 1_000_000, 0, None, {}

    llm.keep_usage(Usage())
    assert llm.cost() == 0.10


def test_only_the_last_tool_call_of_a_reply_asks_the_model_again():
    llm = MeteredLLM(model="openai/gpt-6-luna", api_key="test", base_url="http://127.0.0.1:9")
    tools, asked = OrderTools(Till(), 412), []

    async def result_callback(result, properties=None):
        asked.append(properties.run_llm)

    # On the live line Pipecat can deliver the first result before it registers the second call, so
    # the count, not Pipecat's grouping, decides when the model answers.
    llm.tool_calls_left = 2  # what run_function_calls sets for a reply with two tool calls
    for arguments in ({"item": "cheese", "size": "small"}, {"item": "cannoli"}):
        call = SimpleNamespace(function_name="add_item", arguments=arguments, llm=llm, result_callback=result_callback)
        asyncio.run(tools.handle(call))
    assert asked == [False, True]

"""The ordering agent: what the model is told, the till tools it may call, and what each call costs.

On a phone line every word the model writes is spoken, and the caller waits for it. So the instructions
ask for short replies, and the tools do the bookkeeping the model would otherwise say out loud."""

from collections.abc import Callable

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService

from lucca.config import PRICES
from lucca.till import OrderProblem, Till

GREETING = "Thanks for calling Lucca Brick Oven. What can I get started for you?"

INSTRUCTIONS = """This is the phone ordering line for Lucca Brick Oven, a pizzeria at 2140 Murray Avenue,
Pittsburgh. Orders are for pickup only. The caller hears every word you write, so reply in one or two
short sentences, with no lists, symbols or prices unless the caller asks.

How to take the order:
- Call add_item as soon as the caller names an item, using the caller's own words for the item, size and
  options. One call per item; set quantity for "two small cheese".
- When the caller changes an item, call remove_item for the old line, then add_item for the new one.
- When the caller says that is everything, get their name if you do not have it. Then read the order
  back in one sentence with the total from the till, and ask if it is right.
- Call place_order only after the caller says the read-back is right. Then give the order number and
  say it will be ready in about 25 minutes.

The till knows the menu, its crusts, toppings, sauces and drinks, so pass every item to add_item
before you decide anything. When the till says the menu cannot hold it, or the caller asks for a special,
a deal or a half and half pizza, do not guess or offer something close. Call send_to_staff with the
reason, tell the caller a team member will call them right back, and take nothing else.
If a tool answers with an error, do what its message says."""


class OrderTools:
    """The four till actions the model may take on one call. Each returns the ticket as it now stands, so
    the model reads back what the till holds, never its own memory of the call."""

    def __init__(self, till: Till, order_number: int, on_event: Callable[[dict], None] = lambda event: None):
        self.till, self.order_number, self.on_event = till, order_number, on_event

    def run(self, tool: str, arguments: dict) -> dict:
        """Run one tool call on the till and report it, whether the till took it or refused it."""
        try:
            if tool == "add_item":
                line = self.till.add_item(arguments.get("item", ""), arguments.get("size"),
                                          arguments.get("options") or [], arguments.get("quantity") or 1)
                result = {"added": line.spoken()}
            elif tool == "remove_item":
                result = {"removed": self.till.remove_item(arguments.get("line_number", 0)).spoken()}
            elif tool == "place_order":
                number = self.till.place_order(arguments.get("name", ""), self.order_number)
                result = {"placed": True, "order_number": number, "ready_in_minutes": 25}
            elif tool == "send_to_staff":
                self.till.send_to_staff(arguments.get("reason", ""))
                result = {"sent_to_staff": True, "tell_the_caller": "A team member will call you right back."}
            else:
                result = {"error": f"There is no tool called {tool}."}
        except OrderProblem as problem:
            result = {"error": str(problem)}
        if "error" not in result:
            result.update(ticket=self.till.read_back(), total=self.till.total()["total"])
        self.on_event({"kind": "tool", "tool": tool, "arguments": arguments, "result": result,
                       "ticket": self.till.ticket()})
        return result

    async def handle(self, params: FunctionCallParams) -> None:
        result = self.run(params.function_name, dict(params.arguments))
        # "A large sausage and peppers and a large cheese" is two add_item calls in one reply. The model
        # must answer once, after both results: two answers in flight disagree, and one of them may
        # send the call to staff while the other is still keying the order.
        last = params.llm.finish_tool_call() if isinstance(params.llm, MeteredLLM) else True
        await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=last))

    def schema(self) -> ToolsSchema:
        """The tools as Pipecat advertises them. Each carries its handler, so the LLM service runs it when
        the model calls it, and the result goes back into the context for the next reply."""
        words = {"type": "string", "description": "the caller's words"}
        tools = [
            FunctionSchema("add_item", "Put one menu item on the ticket, in the caller's words.", {
                "item": {**words, "description": "the item, e.g. 'pepperoni pizza', 'wings', 'garlic knots'"},
                "size": {**words, "description": "the size if the item has one, e.g. 'large', 'dozen', '6 piece'"},
                "options": {"type": "array", "items": {"type": "string"},
                            "description": "crust, toppings, sauce or flavor, e.g. ['thin crust', 'mushrooms']"},
                "quantity": {"type": "integer", "description": "how many of this item, default 1"},
            }, ["item"], self.handle),
            FunctionSchema("remove_item", "Take a line off the ticket when the caller changes or drops it.", {
                "line_number": {"type": "integer", "description": "the line number from the ticket, from 1"},
            }, ["line_number"], self.handle),
            FunctionSchema("place_order", "Send the confirmed ticket to the kitchen. Only after the caller "
                           "says the read-back is right.", {
                "name": {"type": "string", "description": "the caller's name for pickup"},
            }, ["name"], self.handle),
            FunctionSchema("send_to_staff", "Hand the call to a person when the menu cannot hold what the "
                           "caller asked for. Nothing goes to the kitchen.", {
                "reason": {"type": "string", "description": "what the caller asked for that the menu cannot hold"},
            }, ["reason"], self.handle),
        ]
        return ToolsSchema(standard_tools=tools)


class MeteredStream:
    """Passes the model's streamed reply through untouched, keeping the usage block from the last chunk."""

    def __init__(self, stream, on_usage: Callable[[object], None]):
        self.stream, self.on_usage = stream, on_usage

    async def chunks(self):
        async for chunk in self.stream:
            if getattr(chunk, "usage", None):
                self.on_usage(chunk.usage)
            yield chunk

    def __aiter__(self):
        return self.chunks()

    async def close(self) -> None:
        await self.stream.close()


class MeteredLLM(OpenAILLMService):
    """Pipecat's OpenAI service with the bill kept per call. OpenRouter reports the billed cost when the
    request asks for usage.include; other providers fall back to the list price in config."""

    def __init__(self, *, model: str, **kwargs):
        settings = OpenAILLMService.Settings(model=model, system_instruction=INSTRUCTIONS, temperature=0,
                                             extra={"extra_body": {"usage": {"include": True}}})
        super().__init__(settings=settings, **kwargs)
        self.model_name, self.usage = model, []
        self.tool_calls_left = 0

    async def run_function_calls(self, function_calls):
        # Pipecat runs a reply's tool calls side by side. Counting them here lets the last one to finish
        # ask for the next reply, so the model reads every result at once.
        self.tool_calls_left = len(function_calls)
        await super().run_function_calls(function_calls)

    def finish_tool_call(self) -> bool:
        self.tool_calls_left = max(0, self.tool_calls_left - 1)
        return self.tool_calls_left == 0

    async def get_chat_completions(self, context):
        return MeteredStream(await super().get_chat_completions(context), self.keep_usage)

    def keep_usage(self, usage) -> None:
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        billed = (getattr(usage, "model_extra", None) or {}).get("cost")
        price_in, price_cached, price_out = PRICES.get(self.model_name, (0.0, 0.0, 0.0))
        listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached
                  + usage.completion_tokens * price_out) / 1e6
        self.usage.append({"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                           "cost": float(billed) if billed is not None else listed})

    def cost(self) -> float:
        return sum(call["cost"] for call in self.usage)


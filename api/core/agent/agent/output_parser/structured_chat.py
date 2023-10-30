import json
import re
from typing import Union

from langchain.agents.structured_chat.output_parser import StructuredChatOutputParser as LCStructuredChatOutputParser, \
    logger
from langchain.schema import AgentAction, AgentFinish, OutputParserException


class StructuredChatOutputParser(LCStructuredChatOutputParser):
    def parse(self, text: str) -> Union[AgentAction, AgentFinish]:
        try:
            action_match = re.search(r"```(\w*)\n?({.*?)```", text, re.DOTALL)
            if action_match is None:
                return AgentFinish({"output": text}, text)
            response = json.loads(action_match.group(2).strip(), strict=False)
            if isinstance(response, list):
                # gpt turbo frequently ignores the directive to emit a single action
                logger.warning("Got multiple action responses: %s", response)
                response = response[0]
            return (
                AgentFinish({"output": response["action_input"]}, text)
                if response["action"] == "Final Answer"
                else AgentAction(
                    response["action"], response.get("action_input", {}), text
                )
            )
        except Exception as e:
            raise OutputParserException(f"Could not parse LLM output: {text}")

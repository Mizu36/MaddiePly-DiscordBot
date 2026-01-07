import datetime
import random
import requests
from collections import deque
from tools import set_reference, get_reference, debug_print
from dotenv import load_dotenv
from openai import OpenAI
from local_database import (
    get_prompt,
    get_setting,
    search_policies,
    get_database_loop,
)
import asyncio
import os
import threading
import base64
import re
import json

class ChatGPT:
    def __init__(self):
        set_reference("GPTManager", self)
        load_dotenv()
        self.api_key = os.getenv("CHATGPT_API_KEY")
        self.working_memory = ""
        self.default_model = None
        self.fine_tuned_model = None
        self.bot_detector_model = None
        self.opener_tracker = OpenerTracker()
        self.assistant = get_reference("AssistantManager")
        try:
            self.client = OpenAI(api_key=self.api_key)
        except Exception as e:
            print(f"Error initializing OpenAI client: {e}")

    async def set_models(self):
        """Fetches and sets the OpenAI models from settings."""
        #Placeholder for database call to get settings
        debug_print("OpenAIManager", "Fetching OpenAI models from settings.")
        self.default_model = await get_setting("Default OpenAI Model")
        self.fine_tune_model = await get_setting("Fine-tune GPT Model")
        self.bot_detector_model = await get_setting("Fine-tune Bot Detection Model")

    async def prepare_history(self):
        """Prepares all chat histories and system prompts."""
        self.memory_summarization_prompt = {"role": "system", "content": await get_prompt("Memory Summarization Prompt")}
        self.tool_prompt = {"role": "system", "content": await get_prompt("Tool Selection Prompt")}
        self.personality_prompt = await get_prompt("Personality Prompt")
        self.global_output_rules = await get_prompt("Global Output Rules")
        self.prompts = [{"role": "system", "content": self.personality_prompt}, {"role": "system", "content": self.global_output_rules}]
        self.discord_emotes_prompt = {"role": "system", "content": await get_prompt("Discord Emotes")}

    def get_all_models(self):
        debug_print("OpenAIManager", "Fetching available OpenAI models.")
        return ["gpt-3.5-turbo", "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro", "gpt-5.1", "o3", "o3-mini", "o4-mini"]
    
    def summarize_memory(self, recent_interaction: str) -> None:
        debug_print("OpenAIManager", "Summarizing recent interactions into long-term memory.")
        prompt = {"role": "system", "content": f"CURRENT MEMORY:\n{self.working_memory if self.working_memory else 'NONE'}\n\nRECENT EVENTS (RAW):\n{recent_interaction}"}
        messages = [{"role": "system", "content": self.personality_prompt}, self.memory_summarization_prompt, prompt]
        try:
            completion = self.client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=messages,
                            temperature=0.3,
                            top_p=0.8,
                            presence_penalty=0.0,
                            frequency_penalty=0.0
                            )
            openai_answer = completion.choices[0].message.content
            debug_print("OpenAIManager", f"Memory summarization response: {openai_answer}")
            if openai_answer.strip() != "NO UPDATE":
                current_time = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                self.working_memory = f"\n[Last Updated: {current_time} UTC]\n{openai_answer.strip()}"
        except Exception as e:
            print(f"[ERROR]Failed to get response from OpenAI for memory summarization: {e}")
            return

    def get_working_memory(self) -> str:
        """Return the cached working memory string for diagnostics/UI."""
        return self.working_memory or ""

    def handle_chat(self, task_prompt: dict | None, context_prompt: dict, use_discord_emotes = True, use_personality = True, use_tools = True):
        tool_result_prompt = None
        if use_tools:
            tool_decision = self.perform_tool_selection(context_prompt)

            if tool_decision["tool"] != "NONE":
                tool_output = self.execute_tool(
                    tool_decision["tool"],
                    tool_decision["argument"]
                )
                tool_result_prompt = {
                    "role": "system",
                    "content": f"TOOL RESULT:\n{tool_output}"
                }

        prompts = []
        prompts.extend(self.prompts if use_personality else [])
        if use_discord_emotes:
            prompts.append(self.discord_emotes_prompt)
        if task_prompt:
            prompts.append(task_prompt)
        prompts.append(context_prompt)

        if tool_result_prompt:
            prompts.insert(-1, tool_result_prompt)

        opener_blacklist = self.opener_tracker.blacklist()
        
        if opener_blacklist:
            dynamic_opener_prompt = {
                "role": "system",
                "content": (
                    "DYNAMIC OPENER RESTRICTIONS:\n"
                    "- Do NOT begin your response with any of the following:\n"
                    + "\n".join(f"• {o}" for o in opener_blacklist) +
                    "\n- Rewrite the opening if necessary."
                )
            }
            prompts.insert(-1, dynamic_opener_prompt)

        return self.chat(prompts)
    
    def execute_tool(self, tool_name: str, argument: str) -> str:
        debug_print("OpenAIManager", f"Executing tool: {tool_name} with argument: {argument}")
        if not self.assistant:
            self.assistant = get_reference("AssistantManager")
        tool_output = ""
        if tool_name == "SEARCH_WEB":
            tool_output = self.assistant.search_web(argument)
        #elif tool_name == "QUERY_MEMORY":
        #    pass #Unused
        #else:
        #    tool_output = "[UNKNOWN TOOL]"
        return tool_output

    def perform_tool_selection(self, context_prompt: dict) -> dict:
        """Asks the tool selection prompt to OpenAI's chat model to determine if a tool is needed."""
        debug_print("OpenAIManager", f"Asking tool selection question.")
        messages = [
            {
                "role": "system",
                "content": "You are selecting tools. You are not responding to chat."
            },
            {
                "role": "system",
                "content": f"CURRENT MEMORY:\n{self.working_memory if self.working_memory else 'NONE'}"
            },
            context_prompt,
            self.tool_prompt
        ]
        print("Asking ChatGPT for tool selection...")
        # Process the answer
        try:
            completion = self.client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=messages,
                            temperature=0.0,
                            top_p=1.0,
                            presence_penalty=0.0,
                            frequency_penalty=0.0
                            )
        except Exception:
            try:
                completion = self.client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    temperature=0.0,
                    top_p=1.0,
                    presence_penalty=0.0,
                    frequency_penalty=0.0
                    )
            except Exception as e:
                print(f"[ERROR]Failed to get response from OpenAI: {e}")
                return {"tool": "NONE", "argument": None}
        openai_answer = completion.choices[0].message.content
        debug_print("OpenAIManager", f"Tool selection response: {openai_answer}")
        parsed = self._parse_tool_response(openai_answer)
        return parsed if parsed else {"tool": "NONE", "argument": None}

    def _schedule_memory_summary(self, recent_interaction: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(
                target=self.summarize_memory,
                args=(recent_interaction,),
                daemon=True,
            ).start()
            return
        loop.create_task(asyncio.to_thread(self.summarize_memory, recent_interaction))

    def chat(self, prompts: list[dict]) -> str:
        working_memory_prompt = {"role": "system", "content": f"MEMORY (REFERENCE ONLY):\n{self.working_memory if self.working_memory else 'NONE'}"}
        prompts.insert(1, working_memory_prompt)
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=prompts,
                temperature=0.7,
                frequency_penalty=0.7,
                presence_penalty=0.4
            )
            content = response.choices[0].message.content
            self.opener_tracker.record(content)
            recent_interaction = f"{prompts[-1]['content']}\nMaddiePly's Response: {content}"
            self._schedule_memory_summary(recent_interaction)
            return content
        except Exception as e:
            print(f"Error during chat completion: {e}")
            return ""
        
    def analyze_image(self, image_url: str):
        if not image_url:
            print("Didn't receive an image URL!")
            return

        try:
            response = requests.get(image_url)
            if response.status_code != 200:
                print(f"Failed to download image, status code: {response.status_code}")
                return
            content_type = response.headers.get('content-type')

            print("Asking ChatGPT to analyze a local image...")

            base64_image = base64.b64encode(response.content).decode("utf-8")

            completion = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Describe this image in detail. "
                            "If the image contains a tabby cat, the cat's "
                            "name is Junebug, include that in the description. If you recognize any characters "
                            "from popular media, include that in the description. "
                            "Focus on describing actions or events happening in the image. "
                        )
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{content_type};base64,{base64_image}",
                                    "detail": "auto"
                                }
                            }
                        ]
                    }
                ]
            )

        except Exception as e:
            print(f"Error analyzing image: {e}")
            return

        # Process the answer
        openai_answer = completion.choices[0].message.content
        print(f"{openai_answer}")
        return openai_answer
    
    def get_all_models(self):
        debug_print("OpenAIManager", "Fetching available OpenAI models.")
        return ["gpt-3.5-turbo", "gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro", "gpt-5.1", "o3", "o3-mini", "o4-mini"]
    
    def _parse_tool_response(self, response: str | None) -> dict | None:
        if not response:
            return None
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        candidate = cleaned if start == -1 or end == -1 or end < start else cleaned[start:end+1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Fallback: allow single-quoted dicts
        alt = candidate.replace("'", '"')
        try:
            return json.loads(alt)
        except json.JSONDecodeError:
            return None
        
    def handle_policy(self, task_prompts: list[dict], context_prompt: dict) -> tuple[dict, str]:
        prompt = {
            "role": "system",
            "content": (
                """
                TASK: Determine a single word to use as a search term for policy information
                based on the user's message.

                RULES:
                - Output MUST be exactly one word.
                - Do NOT correct spelling.
                - Do NOT normalize slang.
                - Do NOT guess or infer meaning.
                - Use a word only if it appears explicitly in the user's message.
                - The response must NOT be the word 'Policy'.
                - If no suitable word is found, respond with 'NONE'.
                """
            ),
        }
        completion = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[prompt, context_prompt],
        )
        content = completion.choices[0].message.content or ""
        terms = content.strip().split()
        search_term = terms[0] if terms else "NONE"
        debug_print("OpenAIManager", f"Policy search term: {search_term}")

        # Normalize task prompts so we never mutate the cached templates
        prepared_prompts: list[dict] = []
        for task_prompt in task_prompts:
            if isinstance(task_prompt, dict):
                prepared_prompts.append(task_prompt.copy())
            else:
                prepared_prompts.append({"role": "system", "content": str(task_prompt)})

        add_prompt, found_prompt, none_prompt = prepared_prompts

        if search_term.upper() == "NONE":
            if random.randint(1, 100) <= 40:
                return add_prompt, "add"
            return none_prompt, "none"

        policies: list[tuple[str, str]] = []
        try:
            db_loop = get_database_loop()
            if not db_loop or not getattr(db_loop, "is_running", lambda: False)():
                raise RuntimeError("Database loop is not running; cannot search policies.")
            future = asyncio.run_coroutine_threadsafe(search_policies(search_term), db_loop)
            policies = future.result()
        except Exception as exc:
            debug_print("OpenAIManager", f"Failed to fetch policies for term '{search_term}': {exc}")

        if not policies:
            debug_print("OpenAIManager", f"No existing policies matched '{search_term}'. Falling back to creation.")
            return add_prompt, "add"

        found_policies = "\n".join(f"{name}: {text}" for name, text in policies)
        found_prompt["content"] = found_prompt["content"].replace("{FOUND_POLICIES}", found_policies)
        return found_prompt, "get"
    
    def violates_opener(self, response, blacklist):
        opener = self.opener_tracker.extract_opener(response)
        return opener in blacklist

    
class OpenerTracker:
    def __init__(self, max_history=20):
        self.recent_openers = deque(maxlen=max_history)

    def extract_opener(self, text: str) -> str:
        text = text.lower().strip()
        # Remove leading punctuation
        text = re.sub(r"^[^\w]+", "", text)

        # Capture first 1–3 words
        words = text.split()
        return " ".join(words[:2]) if len(words) >= 2 else words[0]

    def record(self, response: str):
        opener = self.extract_opener(response)
        if opener:
            self.recent_openers.append(opener)

    def blacklist(self, max_items=8):
        # Most frequent / recent first
        return list(dict.fromkeys(self.recent_openers))[-max_items:]
    

                    
if __name__ == "__main__":
    gpt_manager = ChatGPT()
    prompts = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"}
    ]
    response = gpt_manager.chat(prompts)
    print(response)
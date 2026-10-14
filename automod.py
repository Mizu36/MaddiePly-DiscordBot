from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from collections.abc import Awaitable, Callable
from typing import Any
import discord
import asyncio
from tools import get_reference

class Probation():
    prohibited_links = ["bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "t.co", "is.gd", "buff.ly", "adf.ly", "bit.do", "cutt.ly"]

    def __init__(
        self,
        user_id: int,
        datetime_joined: datetime,
        member: discord.Member | discord.User | None,
        persist_callback: Callable[[Probation], Awaitable[None]] | None = None,
    ):
        self.user_id = user_id
        self.datetime_joined = datetime_joined
        self.probation_end = datetime_joined + timedelta(days=7)
        self.is_first_message = True
        self.member = member
        self.persist_callback = persist_callback
        self.total_points = 0
        self.rolling_points = 0
        self.message_ids = []
        self.message_contents = []
        self.history = {}
        self.decay_task: asyncio.Task | None = None
        self.resting_points = 0
        if member is not None:
            self.check_age()
        self._start_decay_timer()

    @classmethod
    def from_state(
        cls,
        user_id: int,
        state: dict[str, Any],
        persist_callback: Callable[[Probation], Awaitable[None]] | None = None,
    ) -> Probation:
        joined_at = datetime.fromisoformat(state["datetime_joined"])
        probation = cls(user_id, joined_at, None, persist_callback)
        probation.probation_end = datetime.fromisoformat(state["probation_end"])
        probation.is_first_message = bool(state["is_first_message"])
        probation.total_points = int(state["total_points"])
        probation.resting_points = int(state["resting_points"])
        probation.message_ids = list(state["message_ids"])
        probation.message_contents = list(state["message_contents"])
        probation.history = {
            key: {
                "content": entry["content"],
                "timestamp": datetime.fromisoformat(entry["timestamp"]),
                "points": int(entry["points"]),
            }
            for key, entry in state["history"].items()
        }
        return probation

    def to_state(self) -> dict[str, Any]:
        return {
            "datetime_joined": self.datetime_joined.isoformat(),
            "probation_end": self.probation_end.isoformat(),
            "is_first_message": self.is_first_message,
            "total_points": self.total_points,
            "resting_points": self.resting_points,
            "message_ids": self.message_ids,
            "message_contents": self.message_contents,
            "history": {
                str(key): {
                    "content": entry["content"],
                    "timestamp": entry["timestamp"].isoformat(),
                    "points": entry["points"],
                }
                for key, entry in self.history.items()
            },
        }

    def is_expired(self, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        return current_time >= self.probation_end

    async def _persist(self) -> None:
        if self.persist_callback:
            await self.persist_callback(self)

    def _start_decay_timer(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.decay_task = None
            return
        self.decay_task = loop.create_task(self.decay_timer_task())

    def stop_decay(self):
        if self.decay_task and not self.decay_task.done():
            self.decay_task.cancel()

    def check_age(self):
        account_age = (datetime.now(timezone.utc) - self.member.created_at).days
        if account_age < 7:
            self.total_points += 3 #points for account less than 7 days old
        if account_age < 3:
            self.total_points += 5 #points for account less than 3 days old

    async def add_message(self, message_id, message_content, message_time, *, attachment_count: int = 0, skip_first_message_risk: bool = False):
        points_received = 1  # standard point for sending a message
        if self.is_first_message:
            if attachment_count > 1:
                self.is_first_message = False
                points_received += 5  # auto high risk for multiple attachments
                self.resting_points += 5 # set resting points to high risk level for multiple attachments
            elif skip_first_message_risk:
                pass
            else:
                self.is_first_message = False
                chat_gpt = get_reference("GPTManager")
                risk = await asyncio.to_thread(
                    chat_gpt.check_first_message, message_content
                )
                if risk == "MEDIUM":
                    points_received += 3
                    self.resting_points += 3 # set resting points to medium risk level for medium risk first message
                elif risk == "HIGH":
                    points_received += 5
                    self.resting_points += 5 # set resting points to high risk level for high risk first message
                elif risk == "EXTREME":
                    points_received += 10
                    self.resting_points += 10 # set resting points to extreme risk level for extreme risk first message
        else:
            similarity, count = self.check_similarity(message_content)
            if similarity:
                if count < 3:
                    points_received += 3 #points for repeated content
                elif count >= 3:
                    points_received += 6 #points for extreme repeated content
        if message_time - self.datetime_joined < timedelta(seconds=3):
            points_received += 4 #points for sending message less than 3 seconds after joining
        self.message_ids.append(message_id)
        self.message_contents.append(message_content)
        if "@everyone" in message_content or "@here" in message_content:
            points_received += 5 #points for mass mentions
        #check if message contains a link using regex
        if re.search(r'http[s]?://', message_content):
            points_received += 3 #points for containing a link
        if any(link in message_content for link in self.prohibited_links):
            points_received += 3 #points for prohibited links
        self.history[message_id] = {
            "content": message_content,
            "timestamp": message_time,
            "points": points_received,
        }
        self.total_points += points_received
        await self._persist()
        self._evaluate_thresholds()
            
    def check_similarity(self, new_message_content: str) -> tuple[bool, int]:
        count = 0
        similar = False
        for content in self.message_contents:
            if self.similarity(content, new_message_content) >= 0.9:
                similar = True
                count += 1
        return similar, count
    
    def similarity(self, a: str, b: str) -> float:
        a_words = set(a.split())
        b_words = set(b.split())
        if not a_words or not b_words:
            return 0.0
        return len(a_words.intersection(b_words)) / len(a_words.union(b_words))
    
    async def report_user(self):
        report_time = datetime.now(timezone.utc)
        self.total_points += 5 #points for being reported by a user
        self.resting_points += 5 #increase resting points for being reported by a user
        self.history[f"report-{report_time.timestamp()}"] = {
            "content": "[REPORT]",
            "timestamp": report_time,
            "points": 5,
        }
        await self._persist()
        self._evaluate_thresholds()

    async def decay_timer_task(self):
        while True:
            await asyncio.sleep(600) #decay points every ten minutes
            self.total_points = max(self.resting_points, self.total_points - 1)
            await self._persist()

    def get_rolling_points(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.rolling_points = sum(
            entry["points"] for entry in self.history.values() if entry["timestamp"] >= cutoff
        )
        return self.rolling_points

    def check_rolling_points(self):
        return self.get_rolling_points() >= 15

    async def reset_points(self):
        self.history.clear()
        self.message_ids.clear()
        self.message_contents.clear()
        self.total_points = self.resting_points
        self.rolling_points = 0
        await self._persist()

    def _evaluate_thresholds(self):
        if self.resting_points >= 20:
            self.resting_points = 20 # cap resting points at 20 to ensure eventual decay
        if self.total_points >= 25:
            self._schedule_kick("Exceeded probation points")
        elif self.check_rolling_points():
            self._schedule_kick("Exceeded rolling probation points")

    def _schedule_kick(self, reason: str):
        discord_bot = get_reference("DiscordBot")
        discord_bot.create_background_task(
            discord_bot.kick_user(self.user_id, reason),
            f"probation-kick-{self.user_id}",
        )



#   +1 per message
#   +3/5/10 first message medium/high/extreme risk
#   +3 per message w/ links
#   +5 per @everyone/@here
#   +3 per repeated content or 90% similarity
#   +6 same message in 3 channels
#   +5 reported by user (using a new / command)
#   +4 if message less than 3 seconds after join
#   +4 points if link contains bit.ly, tinyurl, t.co
#   +3 if account less than 7 days old
#   +5 if account less than 3 days old
from email.mime import message
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from tools import path_from_storage_root, set_reference, set_debug, get_reference, debug_print
from local_database import (
    setup_database,
    get_setting,
    get_prompt,
    get_banned_words,
    get_all_settings,
    update_setting,
    get_database_loop,
    add_policy
)
from ai_logic import setup_gpt_manager
from google_api import GoogleSheets
import os
import asyncio
import asqlite
import discord
import datetime
from random import randint, choice
from discord.ext import commands
from discord import app_commands

class DiscordBot:
    def __init__(self, token: str, prefix: str):
        set_reference("DiscordBot", self)
        self.chatGPT = get_reference("GPTManager")
        self.link_codes: list[dict] = []
        self.google_sheets: GoogleSheets = get_reference("GoogleSheets")
        self.owner_id = None
        self.owner_name = None
        self.token = token
        self.prefix = prefix
        self.prefix_commands = [("askmaddie", "Directly talk to MaddiePly, the AI assistant. Include a message after the command or she will roast you."), ("policy", "Request information on policies regarding the subject. Include a message after the command or MaddiePly will roast you.")]
        debug_print("DiscordBot", f"Initialized with prefix: {self.prefix}")
        self.timezones = {
            "PST": "America/Los_Angeles",
            "PDT": "America/Los_Angeles",
            "EST": "America/New_York",
            "EDT": "America/New_York",
            "CST": "America/Chicago",
            "CDT": "America/Chicago",
            "MST": "America/Denver",
            "MDT": "America/Denver",
            "UTC": "UTC",
            "GMT": "Etc/GMT",
        }
        self._timezone_offsets = {
            "PST": -8,
            "PDT": -7,
            "EST": -5,
            "EDT": -4,
            "CST": -6,
            "CDT": -5,
            "MST": -7,
            "MDT": -6,
            "UTC": 0,
            "GMT": 0,
        }
        self.intents = discord.Intents.default()
        self.intents.guilds = True
        self.intents.messages = True
        self.intents.message_content = True
        self.intents.members = True
        self.intents.emojis_and_stickers = True
        self.intents.reactions = True
        self.bot=commands.Bot(command_prefix = self.prefix, intents = self.intents)
        self.bot.event(self.on_ready)
        self.bot.add_listener(self._on_message_listener, "on_message")
        self.bot.event(self.on_disconnect)
        self.bot.event(self.on_error)
        self.bot.event(self.on_socket_event_type)
        self.bot.event(self.on_member_join)
        self.bot.event(self.on_member_remove)
        self.bot.event(self.on_reaction_add)
        self.bot.event(self.on_reaction_remove)
        self.bot.event(self.on_message_edit)
        self.bot.event(self.on_message_delete)
        self._register_commands()
        debug_print("DiscordBot", "Registered slash commands.")
        self._register_prefix_commands()
        debug_print("DiscordBot", "Registered prefix commands.")
        self.general_channel_id = None
        self.online_database = get_reference("OnlineDatabase")
        self.response_timer = get_reference("ResponseTimer")

    def run_forever(self) -> None:
        async def _runner():
            try:
                await self.bot.start(self.token)
            finally:
                await self.bot.close()

        asyncio.run(_runner())

    # ------------------------------------------------------------------
    # Event Listeners
    # ------------------------------------------------------------------

    async def on_ready(self):
        debug_print("DiscordBot", f"Logged in as {self.bot.user} (ID: {self.bot.user.id})")
        self.owner_id = await get_setting("Owner Discord ID", 0)
        self.owner_name = await get_setting("Owner Name", "ModdiPly")
        self.general_channel_id = await get_setting("Discord General Channel ID", 0)
        debug_print("DiscordBot", f"Owner set to {self.owner_name} (ID: {str(self.owner_id)})")
        try:
            await self._clear_guild_scoped_commands()
            synced = await self.bot.tree.sync()
            debug_print("DiscordBot", f"Synced {len(synced)} global command(s) to Discord.")
        except Exception as e:
            debug_print("DiscordBot", f"Failed to sync command tree: {e}")

    async def _clear_guild_scoped_commands(self) -> int:
        """Remove lingering guild-specific slash commands so only globals remain."""
        cleared_total = 0
        for guild in list(self.bot.guilds):
            try:
                existing = await self.bot.tree.fetch_commands(guild=guild)
            except Exception as exc:
                debug_print("DiscordBot", f"Failed to fetch commands for guild {guild.id}: {exc}")
                continue
            if not existing:
                continue
            try:
                self.bot.tree.clear_commands(guild=discord.Object(id=guild.id))
                await self.bot.tree.sync(guild=guild)
                cleared_total += len(existing)
                debug_print(
                    "DiscordBot",
                    f"Cleared {len(existing)} guild-specific command(s) for {guild.name} ({guild.id}).",
                )
            except Exception as exc:
                debug_print("DiscordBot", f"Failed to clear commands for guild {guild.id}: {exc}")
        return cleared_total

    async def _on_message_listener(self, message: discord.Message):
        if message.author == self.bot.user:
            return
        asyncio.create_task(self.handle_message(message))

    async def on_disconnect(self):
        debug_print("DiscordBot", "Bot disconnected.")

    async def on_error(self, event, *args, **kwargs):
        debug_print("DiscordBot", f"An error occurred in {event}.")
        print(f"Error in {event}: {args} {kwargs}")

    async def on_socket_event_type(self, event_type):
        debug_print("DiscordBot", f"Socket event: {event_type}")

    async def on_member_join(self, member: discord.Member | discord.User):
        if hasattr(member, 'nick'):
            name = member.nick
        else:
            name = member.name
        debug_print("DiscordBot", f"Member joined: {name} (ID: {member.id})")
        asyncio.create_task(self.handle_new_member(member))
        # Further member join handling logic goes here

    async def on_member_remove(self, member: discord.Member | discord.User):
        if hasattr(member, 'nick'):
            name = member.nick
        else:
            name = member.name
        debug_print("DiscordBot", f"Member removed: {name} (ID: {member.id})")

    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.Member | discord.User):
        if hasattr(user, 'nick'):
            name = user.nick
        else:
            name = user.name
        debug_print("DiscordBot", f"Reaction added by {name} on message ID {reaction.message.id}")
        # Further reaction handling logic goes here

    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.Member | discord.User):
        if hasattr(user, 'nick'):
            name = user.nick
        else:
            name = user.name
        debug_print("DiscordBot", f"Reaction removed by {name} on message ID {reaction.message.id}")
        # Further reaction removal handling logic goes here

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        debug_print("DiscordBot", f"Message edited by {before.author} from '{before.content}' to '{after.content}'")
        asyncio.create_task(self.handle_message_edit(before.id, before.author.nick or before.author.name, after.content))
    
    async def on_message_delete(self, message: discord.Message):
        debug_print("DiscordBot", f"Message deleted by {message.author}: {message.content}")
        asyncio.create_task(self.handle_message_delete(message.id))
    

    # ------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------

    async def handle_message(self, message: discord.Message):
        if await self.check_for_banned_words(message):
            debug_print("DiscordBot", f"Message from {message.author} contained banned words and was handled accordingly.")
            return
        if len(message.content) > await get_setting("Discord Max Message Length", 500):
            debug_print("DiscordBot", f"Message from {message.author} exceeded max length and was ignored.")
            return
        if message.reference:
            reference_message_id = message.reference.message_id
            referenced_message = await message.channel.fetch_message(reference_message_id)
            if referenced_message.author == self.bot.user:
                await self.respond_to_reply(message, referenced_message)
                return
        timer = self._get_response_timer()
        if timer is None:
            debug_print("DiscordBot", "ResponseTimer unavailable; skipping message queue append.")
            return
        if isinstance(message.channel, discord.DMChannel):
            debug_print("DiscordBot",f"Received DM from {message.author}: {message.content}")
            #Insert DM handling logic here
            return
        if not self.online_database:
            self.online_database = get_reference("OnlineDatabase")
        if not await self.online_database.user_exists(str(message.author.id)):
            await self.online_database.create_user(str(message.author.id), {"discord_username": message.author.name, "discord_display_name": message.author.nick or message.author.name, "discord_currency": 500, "active_gacha_set": "humble beginnings"})
        update_task = asyncio.create_task(self.online_database.handle_message_update(str(message.author.id), message.author.nick))
        content: str = message.content or ""
        if "hey maddie" in content.lower():
            await self.handle_askmaddie(message)
            await update_task
            return
        if randint(1, 100) <= await get_setting("Chance For Reaction", 0) or "maddie" in content.lower() or "maddieply" in content.lower(): #Will react to a message if mentioning MaddiePly or randomly based on Chance For Reaction setting
            await self.apply_reaction(message)
        channel_id = message.channel.id
        if channel_id != self.general_channel_id:
            return
        if content.startswith(self.prefix):
            return
        author_display = message.author.nick or None
        author_username = message.author.name or None
        user_id = message.author.id
        if user_id == self.owner_id:
            author_display = self.owner_name
        debug_print("DiscordBot", f"Handling message from {author_display or author_username}: {message.content}")
        created_at: datetime.datetime = message.created_at
        attachments: list[discord.Attachment] = message.attachments
        list_attachments_urls = []
        if attachments:
            for attachment in attachments:
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                    if attachment.url:
                        list_attachments_urls.append(attachment.url)
        message_data = {"content": content, "author_display": author_display, "author_username": author_username, "created_at": created_at, "attachment_urls": list_attachments_urls, "message_id": message.id}
        timer.messages_to_process.append(message_data)
        await update_task

    async def handle_new_member(self, member: discord.Member | discord.User):
        created_at: datetime.datetime = member.created_at
        age_threshold_days = await get_setting("Discord New Account Age Threshold Days", 7)
        now = datetime.datetime.now(datetime.timezone.utc)
        account_age = (now - created_at).days
        debug_print("DiscordBot", f"New member {member.name} account age: {account_age} days")
        if account_age < age_threshold_days:
            debug_print("DiscordBot", f"Member {member.name} flagged as new account (age: {account_age} days)")
            try:
                guild = member.guild
                await guild.kick(member, reason="New account age below threshold.")
                debug_print("DiscordBot", f"Kicked member {member.name} for being a new account.")
            except Exception as e:
                print(f"[ERROR] Failed to kick member {member.name}: {e}")
        pass

    async def handle_message_edit(self, message_id: int, author_display: str, new_content: str):
        timer = self._get_response_timer()
        if timer is None:
            debug_print("DiscordBot", "ResponseTimer unavailable; skipping message edit handling.")
            return
        timer.edit_processed_message(message_id, author_display, new_content)

    async def handle_message_delete(self, message_id: int):
        timer = self._get_response_timer()
        if timer is None:
            debug_print("DiscordBot", "ResponseTimer unavailable; skipping message delete handling.")
            return
        timer.remove_processed_message(message_id)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _register_commands(self):
        @self.bot.tree.command(name="ping", description="Check the bot's responsiveness.")
        async def ping(interaction: discord.Interaction):
            await interaction.response.send_message("Pong!", ephemeral=True)
            debug_print("DiscordBot", f"Ping command used by {interaction.user}.")

        @self.bot.tree.command(name="currency", description="Get your currency balance.")
        async def currency(interaction: discord.Interaction):
            if not self.online_database:
                self.online_database = get_reference("OnlineDatabase")
            user_id = str(interaction.user.id)
            current_currency = 0
            try:
                value = await self.online_database.get_specific_user_data(user_id, "discord_currency")
                if value is not None:
                    current_currency = value
            except Exception as exc:
                debug_print("DiscordBot", f"Currency lookup failed for {user_id}: {exc}")
            await interaction.response.send_message(f"You currently have {current_currency} ModdBucks.", ephemeral=True)
            debug_print("DiscordBot", f"Currency command used by {interaction.user}.")

        @self.bot.tree.command(name="stats", description="Get your user statistics for this server.")
        async def stats(interaction: discord.Interaction):
            # Placeholder for stats command implementation
            if not self.online_database:
                self.online_database = get_reference("OnlineDatabase")
            formatted_reply = ""
            user_data = await self.online_database.get_user_data(str(interaction.user.id))
            discord_stats = {"ID": user_data.get("discord_id", "N/A"), "Username": user_data.get("discord_username", "N/A"), "Display Name": user_data.get("discord_display_name", "N/A"), "Messages Sent": user_data.get("discord_number_of_messages", 0), "Currency": user_data.get("discord_currency", 0)}
            twitch_stats = {}
            if user_data.get("twitch_id", None):
                twitch_stats = {"ID": user_data.get("twitch_id", "N/A"), "Username": user_data.get("twitch_username", "N/A"), "Display Name": user_data.get("twitch_display_name", "N/A"), "Messages Sent": user_data.get("twitch_number_of_messages", 0), "Bits Donated": user_data.get("bits_donated", 0), "Months Subscribed": user_data.get("months_subscribed", 0), "Subs Gifted": user_data.get("subs_gifted", 0), "Channel Points Redeemed": user_data.get("channel_points_redeemed", 0)}
            discord_str = "**Discord Stats:**\n" + "\n".join([f"{key}: {value}" for key, value in discord_stats.items()])
            if twitch_stats:
                twitch_str = "**Twitch Stats:**\n" + "\n".join([f"{key}: {value}" for key, value in twitch_stats.items()])
                formatted_reply = f"{discord_str}\n\n{twitch_str}"
            else:
                formatted_reply = discord_str
            await interaction.response.send_message(formatted_reply if formatted_reply else "No stats available.", ephemeral=True)
            debug_print("DiscordBot", f"Stats command used by {interaction.user}.")

        @self.bot.tree.command(name="help", description="Get a list of available commands.")
        async def help_command(interaction: discord.Interaction):
            commands_list = [f"/{cmd.name}: {cmd.description}" for cmd in self.bot.tree.get_commands()]
            #Insert prefix commands if any
            help_message = "Available Slash Commands:\n" + "\n".join(commands_list)
            if self.prefix_commands:
                help_message += "\n\nPrefix Commands:\n" + "\n".join([f"{self.prefix}{cmd}: {desc}" for cmd, desc in self.prefix_commands])
            await interaction.response.send_message(help_message, ephemeral=True)
            debug_print("DiscordBot", f"Help command used by {interaction.user}.")
        
        @self.bot.tree.command(name="convert_timezone", description="Show a user-provided time in every viewer's local timezone.")
        @app_commands.describe(time="Time to convert. Accepts 24h (military) or 12h with optional AM/PM and timezone e.g. '5:30 pm PST'.")
        async def find_times_and_convert(interaction: discord.Interaction, time: str):
            try:
                timestamp = self._parse_time_to_timestamp(time)
            except ValueError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

            response = (
                "Discord renders this clock in each viewer's timezone:\n"
                f"<t:{timestamp}:t> — short time\n"
                f"<t:{timestamp}:f> — full date/time"
            )
            await interaction.response.send_message(response, ephemeral=True)

        @self.bot.tree.command(name="link_twitch", description="Link your Twitch account to your Discord account. Useful for gacha pulls and rewards.")
        async def link_twitch(interaction: discord.Interaction):
            await interaction.response.send_message(f"🔗 To link your account:\n1. Go to https://twitch-discord-oauth-linker-production.up.railway.app/\n2. Log in with both your Discord and Twitch accounts.\nThat's it, if you have data for both accounts, they will be merged.", ephemeral=True)
            debug_print("DiscordBot", f"Link Twitch command used by {interaction.user}.")

        @self.bot.tree.command(name="quotes", description="Get link to the quotes database.")
        async def quotes(interaction: discord.Interaction):
            await interaction.response.send_message("You can find the quotes database here: http://bit.ly/4luynMp", ephemeral=True)
            debug_print("DiscordBot", f"Quotes command used by {interaction.user}.")

        @self.bot.tree.command(name="settings", description="Change bot settings. Moderators and Owner only. To see list of settings, use !settings list.")
        @app_commands.describe(setting_name="The name of the setting to change.", setting_value="The new value for the setting.")
        async def settings(interaction: discord.Interaction, setting_name: str = None, setting_value: str = None):
            if not self._authorized_user(interaction):
                await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
                return
            await self.handle_settings_command(interaction, setting_name, setting_value)
            debug_print("DiscordBot", f"Settings command used by {interaction.user}.")

    def _register_prefix_commands(self):
        @self.bot.command(name="askmaddie")
        async def askmaddie_command(ctx: commands.Context):
            debug_print("DiscordBot", f"askmaddie command used by {ctx.author}.")
            await self.askmaddie(ctx)

        @self.bot.command(name="clapback")
        async def clapback_command(ctx: commands.Context):
            debug_print("DiscordBot", f"clapback command used by {ctx.author}.")
            await self.clapback(ctx)

        @self.bot.command(name="policy")
        async def policy_command(ctx: commands.Context):
            debug_print("DiscordBot", f"policy command used by {ctx.author}.")
            await self.policy(ctx)

        @self.bot.command(name="quote")
        async def quote_command(ctx: commands.Context):
            debug_print("DiscordBot", f"quote command used by {ctx.author}.")
            await self.quote(ctx)

        @self.bot.command(name="game")
        async def game_command(ctx: commands.Context):
            debug_print("DiscordBot", f"game command used by {ctx.author}.")
            await self.game(ctx)

        @self.bot.command(name="reload")
        async def reload_command(ctx: commands.Context):
            debug_print("DiscordBot", f"reload command used by {ctx.author}.")
            await self.reload(ctx)

    async def refresh_slash_commands(self) -> dict[str, int]:
        """Forcefully remove and re-register all slash commands."""
        await self.bot.wait_until_ready()
        debug_print("DiscordBot", "Manual slash command refresh started.")
        cleared_guild_cmds = await self._clear_guild_scoped_commands()

        # Explicitly pass guild=None because discord.py now requires the keyword.
        self.bot.tree.clear_commands(guild=None)

        self._register_commands()
        registered = await self.bot.tree.sync()

        summary = {
            "guilds_processed": len(self.bot.guilds),
            "guild_commands_cleared": cleared_guild_cmds,
            "global_registered": len(registered),
        }
        debug_print("DiscordBot", f"Slash command refresh finished: {summary}")
        return summary

    async def askmaddie(self, ctx: commands.Context):
        """Responds to user's message and takes in context including attachments and parent message if is reply."""
        asyncio.create_task(self.handle_askmaddie(message=ctx.message))

    async def clapback(self, ctx: commands.Context):
        """MaddiePly will roast the user."""
        asyncio.create_task(self.handle_clapback(message=ctx.message))
    
    async def policy(self, ctx: commands.Context):
        """MaddiePly will provide information on policies regarding the subject."""
        asyncio.create_task(self.handle_policy(message=ctx.message))

    async def quote(self, ctx: commands.Context):
        """Retrieve a specific quote by ID."""
        asyncio.create_task(self.handle_quote(ctx))

    async def game(self, ctx: commands.Context):
        """Returns with active game."""
        current_game: str = await get_setting("Current Active Game", "null")
        if current_game and current_game.lower() != "null":
            await ctx.message.reply(f"Current active game is: {current_game}", mention_author=False)
        else:
            await ctx.message.reply("Games...\nUnlimited games...\nbut no games.", mention_author=False)

    async def reload(self, ctx: commands.Context):
        """Restarts the bot."""
        await self.handle_reload(ctx)

    async def handle_askmaddie(self, message: discord.Message):
        message_content = message.content[len(f"{self.prefix}askmaddie"):].strip()
        author_name = message.author.nick or message.author.name
        user_id = message.author.id
        if user_id == self.owner_id:
            author_name = self.owner_name
        elif user_id == 180794859374903296:
            author_name = "Mizu"
        image_threads = []
        parent_content = None
        parent_name = None
        parent_image_threads = []
        attachment_urls = []
        parent_attachment_urls = []
        if message.attachments:
            for attachment in message.attachments:
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                    attachment_urls.append(attachment.url)  
            if not self.chatGPT:
                self.chatGPT = get_reference("GPTManager")
            image_threads = []
            for url in attachment_urls:
                if len(image_threads) >= 3:  # Limit to first 3 images for performance
                    break
                image_threads.append(asyncio.to_thread(self.chatGPT.analyze_image, image_url=url))
        if message.reference:
            try:
                parent_message = await message.channel.fetch_message(message.reference.message_id)
                timer = self._get_response_timer()
                if timer:
                    timer.remove_processed_message(parent_message.id)
                parent_content = parent_message.content or ""
                parent_name = parent_message.author.nick or parent_message.author.name
                if parent_message.attachments:
                    for attachment in parent_message.attachments:
                        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                            parent_attachment_urls.append(attachment.url)
                    parent_image_threads = []
                    for url in parent_attachment_urls:
                        if len(parent_image_threads) >= 3:  # Limit to first 3 images for performance
                            break
                        parent_image_threads.append(asyncio.to_thread(self.chatGPT.analyze_image, image_url=url))
            except Exception as e:
                debug_print("DiscordBot", f"Failed to fetch parent message: {e}")
        image_descriptions = []
        parent_image_descriptions = []
        for thread in image_threads:
            description = await thread
            if description:
                image_descriptions.append(description)
        if parent_image_threads:
            for thread in parent_image_threads:
                description = await thread
                if description:
                    parent_image_descriptions.append(description)
        context_parts = []
        if parent_content:
            context_parts.append(f"Previous message from {parent_name}: {parent_content}")
        if parent_image_descriptions:
            context_parts.append(f"Images described: {'; '.join(parent_image_descriptions)}")
        if message_content:
            context_parts.append(f"Current message from {author_name}: {message_content}")
        elif message.reference:
            context_parts.append(f"The user did not provide any message to ask MaddiePly. However, they replied to a previous message, so use that for context. The user likely wants to know your opinion on it.")
        else:
            context_parts.append(f"The user did not provide any message to ask MaddiePly. If they provided images, use those for context. Otherwise call them out for wasting your time.")
        if image_descriptions:
            context_parts.append(f"Images described: {'; '.join(image_descriptions)}")
        context_prompt = {"role": "user", "content": "\n".join(context_parts)}
        if not self.chatGPT:
            self.chatGPT = get_reference("GPTManager")
        ask_prompt = {"role": "system", "content": await get_prompt("Respond to User Prompt")}
        response = await asyncio.to_thread(self.chatGPT.handle_chat, ask_prompt, context_prompt)
        await message.reply(response, mention_author=False)

    async def respond_to_reply(self, message: discord.Message, referenced_message: discord.Message):
        #Insert reply handling logic here, similar to handle_askmaddie but with a different system prompt and possibly different context construction
        new_message_content = message.content.strip()
        referenced_message_content = referenced_message.content.strip() if referenced_message.content else ""
        reply_prompt = {"role": "system", "content": await get_prompt("Respond to Reply Prompt")}
        context_prompt = {"role": "user", "content": f"Original message:\nYou: {referenced_message_content}\nReply:\n{message.author.nick or message.author.name}: {new_message_content}"}
        response = await asyncio.to_thread(self.chatGPT.handle_chat, reply_prompt, context_prompt)
        await message.reply(response, mention_author=False)

    async def handle_clapback(self, message: discord.Message):
        #Insert clapback handling logic here
        message_parts = []
        attachment_urls = []
        parent_attachment_urls = []
        if message.reference:
            try:
                parent_message = await message.channel.fetch_message(message.reference.message_id)
                timer = self._get_response_timer()
                if timer:
                    timer.remove_processed_message(parent_message.id)
                parent_content = parent_message.content or ""
                parent_name = parent_message.author.nick or parent_message.author.name
                if parent_message.attachments:
                    for attachment in parent_message.attachments:
                        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                            parent_attachment_urls.append(attachment.url)
                    if not self.chatGPT:
                        self.chatGPT = get_reference("GPTManager")
                    parent_image_threads = []
                    for url in parent_attachment_urls:
                        if len(parent_image_threads) >= 3:  # Limit to first 3 images for performance
                            break
                        parent_image_threads.append(asyncio.to_thread(self.chatGPT.analyze_image, image_url=url))
                    parent_image_descriptions = []
                    for thread in parent_image_threads:
                        description = await thread
                        if description:
                            parent_image_descriptions.append(description)
                formatted_parent_message = f"Previous message from {parent_name}: {parent_content}\n{'Images described from previous message: ' + '; '.join(parent_image_descriptions) if parent_image_descriptions else ''}"
            except Exception as e:
                debug_print("DiscordBot", f"Failed to fetch parent message: {e}")
                formatted_parent_message = ""
            finally:
                message_parts.append(formatted_parent_message)
        if message.attachments:
            for attachment in message.attachments:
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp']):
                    attachment_urls.append(attachment.url)
            if not self.chatGPT:
                self.chatGPT = get_reference("GPTManager")
            image_threads = []
            for url in attachment_urls:
                if len(image_threads) >= 3:  # Limit to first 3 images for performance
                    break
                image_threads.append(asyncio.to_thread(self.chatGPT.analyze_image, image_url=url))
            image_descriptions = []
            for thread in image_threads:
                description = await thread
                if description:
                    image_descriptions.append(description)
            formatted_images = f"Images described: {'; '.join(image_descriptions)}"
            message_parts.append(formatted_images)
        content = message.content[len(f"{self.prefix}clapback"):].strip()
        if content:
            message_parts.append(f"Current message from {message.author.nick or message.author.name}: {content}")
        if not content:
            message_parts.append(f"The user did not provide any message to clapback at. If they provided images, use those for context. If not, absolutely roast them. Destroy them. Make them feel inadequate.")
        complete_string = "\n".join(message_parts)
        context_prompt = {"role": "user", "content": complete_string}
        if not self.chatGPT:
            self.chatGPT = get_reference("GPTManager")
        clapback_prompt = {"role": "system", "content": await get_prompt("Clapback Prompt")}
        response = await asyncio.to_thread(self.chatGPT.handle_chat, clapback_prompt, context_prompt)
        await message.reply(response, mention_author=False)

    async def handle_policy(self, message: discord.Message):
        content = message.content[len(f"{self.prefix}policy"):].strip()
        prompt_templates = await asyncio.gather(
            get_prompt("Requested Policy (Add) Prompt"),
            get_prompt("Requested Policy (Get) Prompt"),
            get_prompt("Requested Policy (None) Prompt"),
        )
        policy_prompts = [
            {"role": "system", "content": template} for template in prompt_templates
        ]
        if message.reference:
            try:
                parent_message = await message.channel.fetch_message(message.reference.message_id)
                timer = self._get_response_timer()
                if timer:
                    timer.remove_processed_message(parent_message.id)
                parent_content = parent_message.content or ""
                parent_name = parent_message.author.nick or parent_message.author.name
                if content:
                    content = f"Previous message from {parent_name}: {parent_content}\nUser's policy request: {content}"
                else:
                    content = f"The user did not provide any message to request policy information on. However, they replied to a previous message, so use that for context. The user likely wants to know if there is a policy related to that message.\nPrevious message from {parent_name}: {parent_content}"
            except Exception as e:
                debug_print("DiscordBot", f"Failed to fetch parent message: {e}")
        if not content:
            content = "The user did not provide any message to request policy information on, call them out for wasting your time."
        response_prompt, policy_variant = await asyncio.to_thread(
            self.chatGPT.handle_policy,
            policy_prompts,
            {"role": "user", "content": content},
        )
        response = await asyncio.to_thread(
            self.chatGPT.handle_chat,
            response_prompt,
            {"role": "user", "content": content},
            use_tools=False,
        )
        if policy_variant == "add" and response:
            await self.create_policy(response)
        await message.reply(response, mention_author=False)

    async def create_policy(self, response: str = None):
        if not response:
            debug_print("DiscordBot", "Empty policy response; skipping creation.")
            return
        policy_prompt = {"role": "system", "content": await get_prompt("Creating Policy Tool")}
        policy_response = await asyncio.to_thread(self.chatGPT.chat, [policy_prompt, {"role": "user", "content": response}])
        if not policy_response:
            debug_print("DiscordBot", "Policy parser returned empty response; skipping creation.")
            return
        policy_name_match = re.search(r"POLICY NAME/NUMBER:\s*(.+)", policy_response)
        policy_text_match = re.search(r"POLICY TEXT:\s*(.+)", policy_response, re.DOTALL)
        if policy_name_match and policy_text_match:
            policy_name = policy_name_match.group(1).strip()
            policy_text = policy_text_match.group(1).strip()
            if policy_name.upper() == "NO POLICY FOUND":
                debug_print("DiscordBot", "No policy found in the response; skipping creation.")
                return
            await add_policy(policy_name, policy_text)
            debug_print("DiscordBot", f"Created new policy: {policy_name}")
        else:
            debug_print("DiscordBot", "NO POLICY FOUND in the response; skipping creation.")

    async def handle_quote(self, ctx: commands.Context):
        """Quote format examples: !quote 42, !quote random, !quote r, !quote <any number of words to match>"""
        if not self.google_sheets:
            self.google_sheets = get_reference("GoogleSheets")
        content = ctx.message.content[len(f"{self.prefix}quote"):].strip()
        parts = content.split()
        if not parts:
            random_quote = await self.google_sheets.get_random_quote()
            if random_quote:
                await ctx.message.reply(f"{random_quote["Quote"]}\nAdded on {random_quote["Date Added"]} during a {random_quote["Category"]} stream by {random_quote["Added by User"]}.", mention_author=False)
            else:
                debug_print("DiscordBot", "No quotes available in the database.")
        elif parts[0].lower() in ["random", "r"]:
            random_quote = await self.google_sheets.get_random_quote()
            if random_quote:
                await ctx.message.reply(f"{random_quote["Quote"]}\nAdded on {random_quote["Date Added"]} during a {random_quote["Category"]} stream by {random_quote["Added by User"]}.", mention_author=False)
            else:
                debug_print("DiscordBot", "No quotes available in the database.")
        elif parts[0].isdigit():
            quote_id = int(parts[0])
            specific_quote = await self.google_sheets.get_quote(quote_id)
            if specific_quote:
                await ctx.message.reply(f"{specific_quote["Quote"]}\nAdded on {specific_quote["Date Added"]} during a {specific_quote["Category"]} stream by {specific_quote["Added by User"]}.", mention_author=False)
            else:
                await ctx.message.reply(f"No quote found with ID {quote_id}.", mention_author=False)
        else:
            search_terms = " ".join(parts)
            matching_quote = await self.google_sheets.get_random_quote_containing_words(search_terms)
            if matching_quote:
                await ctx.message.reply(f"{matching_quote["Quote"]}\nAdded on {matching_quote["Date Added"]} during a {matching_quote["Category"]} stream by {matching_quote["Added by User"]}.", mention_author=False)
            else:
                await ctx.message.reply(f"No quotes found containing the words: {search_terms}.", mention_author=False)

    async def handle_reload(self, ctx: commands.Context):
        if self._authorized_user(ctx):
            await ctx.message.reply("Not implemented yet...", mention_author=False)

    async def handle_settings_command(self, interaction: discord.Interaction, setting_name: str = None, setting_value: str = None):
        """Parses and handles the settings command. Works with either the keyword 'list' or '<setting_name> <setting_value>'. Returns error message if value wrong data type."""
        settings: dict = await get_all_settings()
        content = interaction.message.content[len("/settings"):].strip()
        if content.lower() == "list":
            settings_list = "\n".join([f"{key}: {value['value']} ({value['type']})" for key, value in settings.items()])
            await interaction.response.send_message(f"Current Settings:\n{settings_list}", ephemeral=True)
        else:
            parts = content.split(" ", 1)
            if len(parts) != 2:
                await interaction.response.send_message("Invalid format. Use `/settings list` or `/settings <setting_name> <setting_value>`.", ephemeral=True)
                return
            setting_name = parts[0]
            setting_value = parts[1]
            if setting_name.lower() not in [key.lower() for key in settings.keys()]:
                await interaction.response.send_message(f"Setting '{setting_name}' not found.", ephemeral=True)
                return
            expected_type = settings[setting_name]['type']
            try:
                if expected_type == "int":
                    setting_value = int(setting_value)
                elif expected_type == "float":
                    setting_value = float(setting_value)
                elif expected_type == "bool":
                    setting_value = setting_value.lower() in ["true", "1", "yes", "on"]
            except ValueError:
                await interaction.response.send_message(f"Invalid value for setting '{setting_name}'. Expected type: {expected_type}.", ephemeral=True)
                return
            
            await update_setting(setting_name, setting_value)
            await interaction.response.send_message(f"Setting '{setting_name}' updated to '{setting_value}'.", ephemeral=True)

    # ------------------------------------------------------------------
    # Moderation
    # ------------------------------------------------------------------

    async def check_for_banned_words(self, message: discord.Message):
        banned_words = await self._fetch_banned_words()
        banned = [word[0].lower() for word in banned_words if word[1] == "Ban"]
        exact_match_only = [word[0].lower() for word in banned_words if word[1] == "Exact"]
        message_content = message.content.lower()

        word_boundary_pattern = r"\b(?:{})\b".format("|".join(re.escape(word) for word in exact_match_only))
        match = re.search(word_boundary_pattern, message_content)
        matched_word = None

        if match: #Exact match found
            matched_word = match.group(0)

        if not matched_word:
            matched_word = next((word for word in banned if word in message_content), None)

        if matched_word:
            await self.moderation_delete_message(message, matched_word, message_content)
            return True

    async def _fetch_banned_words(self):
        """Retrieve banned words on the DB loop when necessary."""
        db_loop = None
        try:
            db_loop = get_database_loop()
        except Exception:
            db_loop = None

        try:
            current_loop = asyncio.get_running_loop()
        except Exception:
            current_loop = None

        if db_loop and current_loop and db_loop is not current_loop:
            future = asyncio.run_coroutine_threadsafe(get_banned_words(), db_loop)
            return await asyncio.wrap_future(future, loop=current_loop)

        return await get_banned_words()

    async def moderation_delete_message(self, message: discord.Message, matched_word, message_content):
        pst = self._resolve_timezone("PST")
        now = datetime.datetime.now(pst)
        date_str = now.strftime("%m/%d/%Y")
        time_str = now.strftime("%I:%M %p")
        await message.delete()
        await self.send_moderation_log(f"**━━━━━━━━━━━━━━━━━━**\n**User**: {message.author.display_name} | *{message.author.name}*\n**Offence**: Used banned word **{matched_word}** in **{message.channel.name}**\n**Original message**: {message_content}\n**Action Taken**: Deleted Message\n**Date/Time**: **{date_str}** at **{time_str}**", message)

    async def moderation_censored_word(self, message: discord.Message, matched_word, message_content): #Unused for now
        pst = self._resolve_timezone("PST")
        now = datetime.datetime.now(pst)
        date_str = now.strftime("%m/%d/%Y")
        time_str = now.strftime("%I:%M %p")
        await self.send_moderation_log(f"**━━━━━━━━━━━━━━━━━━**\n**User**: {message.author.display_name} | *{message.author.name}*\n**Offence**: Used secret special word/phrase **{matched_word}** in **{message.channel.name}**\n**Original message**: {message_content}\n**Action Taken**: None\n**Date/Time**: **{date_str}** at **{time_str}**", message)

    async def send_moderation_log(self, message_to_send, message: discord.Message):
        mod_channel_id = await get_setting("Discord Moderation Logs Channel ID", 0)
        if mod_channel_id != 0:
            try:
                log_channel = await self.bot.fetch_channel(mod_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                print("Moderation Logs Channel is invalid")
                return
            await log_channel.send(message_to_send)
        else:
            print(f"Moderation Logs Channel not set.\n{message_to_send}")
        return
    
    async def send_chat(self, content: str):
        """Send a message to the general channel from any thread/loop."""

        async def _send() -> None:
            channel_id = await get_setting("Discord General Channel ID", 0)
            if channel_id == 0:
                debug_print("DiscordBot", "General channel ID not set; cannot send message.")
                return
            try:
                channel = await self.bot.fetch_channel(channel_id)
                await channel.send(content)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                debug_print("DiscordBot", f"Failed to send message to general channel: {exc}")
            except Exception as exc:
                debug_print("DiscordBot", f"Unexpected error sending message to general channel: {exc}")

        target_loop = self.bot.loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is target_loop:
            await _send()
            return

        future = asyncio.run_coroutine_threadsafe(_send(), target_loop)
        if current_loop is None:
            future.result()
        else:
            await asyncio.wrap_future(future, loop=current_loop)

    async def apply_reaction(self, message: discord.Message):
        """Add a reaction to the message by attempting to ask the bot, if that fails, a random reaction will be added instead."""
        try:
            guild = message.guild
            emojis = guild.emojis
            content = message.content
            task_prompt = {"role": "system", "content": "TASK: Choose an appropriate reaction emoji to add to the following message. Only respond with the name of the emoji, and nothing else. You may only select from emoji provided to you, not any global ones. If you cannot determine an appropriate reaction, choose an absurd one, but you must select one no matter what.\n\nMESSAGE CONTENT:\n" + (content)}
            discord_emotes_prompt = {"role": "system", "content": await get_prompt("Discord Emotes")}
            if not self.chatGPT:
                self.chatGPT = get_reference("GPTManager")
            reaction_response = await asyncio.to_thread(self.chatGPT.handle_chat, task_prompt, discord_emotes_prompt)
            reaction_response = reaction_response.strip()
            if reaction_response.lower() != "random":
                #Grab only text between two colons if the response is in format <:emojoname:emojiid>
                emote_name_match = re.match(r"<a?:(\w+):\d+>", reaction_response)
                print(f"Reaction response: {reaction_response}, Emote name match: {emote_name_match.group(1) if emote_name_match else 'No match'}")
                if emote_name_match:
                    reaction_response = emote_name_match.group(1)
                selected_emoji = next((emoji for emoji in emojis if emoji.name == reaction_response), None)
                if selected_emoji:
                    await message.add_reaction(selected_emoji)
                    return
            print(f"Failed to get valid emoji from GPT response, defaulting to random. GPT response was: {reaction_response}")
            random_emoji = choice(emojis) if emojis else None
            if random_emoji:
                await message.add_reaction(random_emoji)
        except Exception as exc:
            debug_print("DiscordBot", f"Failed to add a reaction: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_time_to_timestamp(self, time_text: str) -> int:
        """Parse a user supplied time string supporting 24h or 12h formats."""
        pattern = r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*([A-Za-z]+)?\s*$"
        match = re.match(pattern, time_text or "", re.IGNORECASE)
        if not match:
            raise ValueError("Enter a time like '17:30 PST' or '5:30 PM EST'.")

        raw_hour = int(match.group(1))
        minute = int(match.group(2)) if match.group(2) else 0
        period = match.group(3).lower() if match.group(3) else None
        tz_code = (match.group(4) or "PST").upper()

        if minute > 59:
            raise ValueError("Minutes must be between 0 and 59.")

        if period:
            if raw_hour < 1 or raw_hour > 12:
                raise ValueError("Use 1-12 when specifying AM/PM.")
            hour = raw_hour % 12
            if period == "pm":
                hour += 12
        else:
            if raw_hour > 23:
                raise ValueError("24-hour times must be between 0 and 23.")
            if raw_hour <= 12:
                # Assume AM when 12h format is provided without a suffix.
                hour = 0 if raw_hour == 12 else raw_hour
            else:
                hour = raw_hour

        tzinfo = self._resolve_timezone(tz_code)
        now = datetime.datetime.now(tzinfo)
        localized_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return int(localized_dt.timestamp())
    
    def _resolve_timezone(self, tz_code: str) -> datetime.tzinfo:
        tz_name = self.timezones.get(tz_code)
        offset_hours = self._timezone_offsets.get(tz_code)
        if not tz_name and offset_hours is None:
            raise ValueError(
                f"Unknown timezone '{tz_code}'. Try one of: {', '.join(sorted(self.timezones.keys()))}."
            )

        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                debug_print(
                    "DiscordBot",
                    f"ZoneInfo data for '{tz_name}' unavailable; using fixed offset for {tz_code}.",
                )

        if offset_hours is None:
            raise ValueError(
                f"Unknown timezone '{tz_code}'. Try one of: {', '.join(sorted(self.timezones.keys()))}."
            )

        return datetime.timezone(datetime.timedelta(hours=offset_hours), name=tz_code)

    def _authorized_user(self, ctx: commands.Context) -> bool:
        """Check if the command invoker is the bot owner or a moderator."""
        return ctx.author.guild_permissions.manage_channels or (self.owner_id and ctx.author.id == self.owner_id)

    def _get_response_timer(self):
        """Return a ResponseTimer instance with a message queue if available."""
        if self.response_timer and hasattr(self.response_timer, "messages_to_process"):
            return self.response_timer
        try:
            timer = get_reference("ResponseTimer")
        except Exception:
            timer = None
        if timer and hasattr(timer, "messages_to_process"):
            self.response_timer = timer
            return timer
        return None

def main() -> None:
    async def runner() -> None:
        data_dir = path_from_storage_root()
        os.makedirs(data_dir, exist_ok=True)
        db_path = str(data_dir / "maddieply.db")
        bot_token = os.getenv("DISCORD_TOKEN", "").strip()
        if not bot_token:
            raise ValueError("DISCORD_TOKEN environment variable is not set.")

        async with asqlite.create_pool(db_path) as tdb:
            await setup_database(tdb)
            await set_debug(await get_setting("Debug Mode", "False"))
            try:
                await setup_gpt_manager()
            except Exception as e:
                print(f"[ERROR] Failed to run setup_gpt_manager(): {e}")
            prefix = await get_setting("Command Prefix", "!")
            #Insert discord bot setup here

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        print("Shutting down due to KeyboardInterrupt")
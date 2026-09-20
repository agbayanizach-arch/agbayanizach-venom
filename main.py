import os
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask
from threading import Thread
import asyncio
import random
import re
import datetime
import json
import aiohttp

# --- 1. SETUP KEEPALIVE WEB SERVER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is active!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# --- DISCORD BOT CORE ---
# Prefix commands use `-` and message-content intent is enabled so Discord
# can deliver prefix messages to the bot. Member/guild intents are also
# enabled because this bot uses joins, invites, roles, moderation, and DMs.
intents = discord.Intents.all()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="-",
    intents=intents,
    case_insensitive=True,
    help_command=None
)

# Stores the configured ticket category for each server.
TICKET_FILE = "ticket_categories.json"
ticket_categories = {}

# Persistent settings are stored beside this Python file.
# Updating/restarting main.py will not overwrite these JSON files.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REACTION_ROLE_FILE = os.path.join(BASE_DIR, "reaction_roles.json")
AUTOROLE_FILE = os.path.join(BASE_DIR, "autoroles.json")
HONEYPOT_FILE = os.path.join(BASE_DIR, "honeypot_settings.json")
BOOST_FILE = os.path.join(BASE_DIR, "boost_settings.json")
WELCOME_FILE = os.path.join(BASE_DIR, "welcome_settings.json")
AUTOMODE_FILE = os.path.join(BASE_DIR, "automode_settings.json")
INVITE_FILE = os.path.join(BASE_DIR, "invite_tracking.json")
STAFF_APPLY_FILE = os.path.join(BASE_DIR, "staff_apply_settings.json")
GIVEAWAY_FILE = os.path.join(BASE_DIR, "giveaways.json")
SUGGESTIONS_FILE = os.path.join(BASE_DIR, "suggestions_settings.json")

def load_json_settings(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

def save_json_settings(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"Could not save {filename}: {e}")

reaction_role_settings = load_json_settings(REACTION_ROLE_FILE)
autorole_settings = load_json_settings(AUTOROLE_FILE)
honeypot_settings = load_json_settings(HONEYPOT_FILE)
boost_settings = load_json_settings(BOOST_FILE)
automode_settings = load_json_settings(AUTOMODE_FILE)
staff_apply_settings = load_json_settings(STAFF_APPLY_FILE)
giveaway_records = load_json_settings(GIVEAWAY_FILE)
suggestions_settings = load_json_settings(SUGGESTIONS_FILE)
invite_persistent = load_json_settings(INVITE_FILE)
ticket_categories = load_json_settings(TICKET_FILE)


# --- 2. INTERACTIVE TICKET ACTIONS (BUTTON CODES) ---
class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Set timeout to None so buttons work forever

    @discord.ui.button(label="Create Ticket 🎫", style=discord.ButtonStyle.green, custom_id="open_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = interaction.user

        # Check if user already has an active ticket to prevent spam
        existing_channel = discord.utils.get(guild.text_channels, name=f"ticket-{member.name.lower()}")
        if existing_channel:
            await interaction.response.send_message(f"❌ You already have an open ticket here: {existing_channel.mention}", ephemeral=True)
            return

        # Setup private channel overrides (Only staff and the ticket creator can see it)
        overrides = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Dynamically create the new private text channel
        category_id = ticket_categories.get(str(guild.id))
        category = guild.get_channel(category_id) if category_id else None

        if category is None or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "❌ Ticket category is not configured. An administrator must run `/ticket-setup <category>` first.",
                ephemeral=True
            )
            return

        try:
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{member.name}",
                category=category,
                overwrites=overrides
            )
            close_view = TicketCloseControl()
            embed = discord.Embed(
                title="Ticket Created!",
                description=f"Welcome {member.mention},\n\nPlease describe your issue or inquiry here. Support staff will assist you shortly.",
                color=discord.Color.blue()
            )
            await ticket_channel.send(embed=embed, view=close_view)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I cannot create the ticket. Check my **Manage Channels**, **View Channel**, and **Send Messages** permissions.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            print(f"Ticket creation failed in {guild.name}: {exc}")
            await interaction.response.send_message("❌ Discord rejected the ticket creation. Please try again.", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Ticket created successfully! Go to {ticket_channel.mention}", ephemeral=True)

class TicketCloseControl(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket 🔒", style=discord.ButtonStyle.red, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 This ticket will be deleted in 5 seconds...")
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

# --- 3. INTERACTIVE REACTION ROLE ACTIONS ---
class ReactionRoleView(discord.ui.View):
    def __init__(self, role_id):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="Get Role",
            emoji="🎟️",
            style=discord.ButtonStyle.primary,
            custom_id=f"reactionrole:{role_id}"
        )
        button.callback = self._button_callback
        self.add_item(button)
        self.role_id = int(role_id)

    async def _button_callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ This button can only be used in a server.", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message("❌ The configured role no longer exists.", ephemeral=True)
            return

        member = interaction.user
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ I need **Manage Roles** permission to give this role.", ephemeral=True)
            return

        if role >= me.top_role:
            await interaction.response.send_message(
                "❌ I cannot give this role because it is higher than or equal to my highest role.",
                ephemeral=True
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role button")
                await interaction.response.send_message(f"🗑️ Removed {role.mention} from you.", ephemeral=True)
            else:
                await member.add_roles(role, reason="Reaction role button")
                await interaction.response.send_message(f"✅ You received {role.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Discord denied the role change. Check my **Manage Roles** permission and role hierarchy.", ephemeral=True)
        except discord.HTTPException:
            await interaction.response.send_message("❌ Discord could not update your role. Please try again.", ephemeral=True)


# --- 4. GIVEAWAY REACTION SYSTEM ---
# Giveaways use a real Discord 🎉 reaction (no button/view).

# --- INVITE & GREET CONFIGURATION ---
# Welcome/greet settings are stored persistently in welcome_settings.json.
# Each server gets its own message and channel configuration.
welcome_settings = load_json_settings(WELCOME_FILE)

DEFAULT_WELCOME_MESSAGE = "Welcome {member} to the server! Make sure to read the guidelines."

def get_welcome_settings(guild_id):
    key = str(guild_id)
    changed = False
    if key not in welcome_settings or not isinstance(welcome_settings[key], dict):
        welcome_settings[key] = {
            "message": DEFAULT_WELCOME_MESSAGE,
            "channel_id": None
        }
        changed = True
    else:
        # Add missing fields without ever replacing an existing custom message.
        if "message" not in welcome_settings[key]:
            welcome_settings[key]["message"] = DEFAULT_WELCOME_MESSAGE
            changed = True
        if "channel_id" not in welcome_settings[key]:
            welcome_settings[key]["channel_id"] = None
            changed = True
    if changed:
        save_json_settings(WELCOME_FILE, welcome_settings)
    return welcome_settings[key]

# --- INVITE TRACKING CONFIGURATION ---
# Invite/welcome settings are also stored separately for each server.
# Do not use one global channel ID because that would send messages to another server.

invite_db = invite_persistent.get("stats", {}) if isinstance(invite_persistent.get("stats", {}), dict) else {}
invite_cache = {}
member_inviter_map = invite_persistent.get("member_inviter_map", {}) if isinstance(invite_persistent.get("member_inviter_map", {}), dict) else {}
history_db = set(invite_persistent.get("history", [])) if isinstance(invite_persistent.get("history", []), list) else set()

def _invite_key(guild_id, user_id):
    return f"{guild_id}:{user_id}"

def _member_key(guild_id, member_id):
    return f"{guild_id}:{member_id}"

def save_invite_data():
    invite_persistent["stats"] = invite_db
    invite_persistent["member_inviter_map"] = member_inviter_map
    invite_persistent["history"] = list(history_db)
    save_json_settings(INVITE_FILE, invite_persistent)

def get_user_stats(guild_id, user_id):
    key = _invite_key(guild_id, user_id)
    if key not in invite_db or not isinstance(invite_db[key], dict):
        invite_db[key] = {"regular": 0, "leaves": 0, "fake": 0, "bonus": 0}
    for field in ("regular", "leaves", "fake", "bonus"):
        invite_db[key].setdefault(field, 0)
    return invite_db[key]

# --------------------------------------

# --- AUTOMODE / SECURITY SETUP PANEL ---
AUTOMODE_OPTIONS = {
    "antinuke": ("🛡️ Anti-Nuke", "Bans members who perform destructive server actions."),
    "antiraid": ("🚨 Anti-Raid", "Helps protect against sudden join raids."),
    "antispam": ("💬 Anti-Spam", "5 messages within 1 second = 2 hour timeout."),
    "antilink": ("🔗 Anti-Link", "Deletes invite links and warns the member twice."),
    "antibot": ("🤖 Anti-Bot", "Permanently bans newly added bots."),
    "antiwebhook": ("🪝 Anti-Webhook", "Permanently bans members who create/update webhooks."),
}

spam_tracker = {}
antilink_warnings = {}
automode_audit_cache = {}


def get_automode_config(guild_id):
    key = str(guild_id)
    if key not in automode_settings or not isinstance(automode_settings[key], dict):
        automode_settings[key] = {name: False for name in AUTOMODE_OPTIONS}
    else:
        for name in AUTOMODE_OPTIONS:
            automode_settings[key].setdefault(name, False)
    return automode_settings[key]


def build_automode_embed(guild):
    config = get_automode_config(guild.id)
    lines = []
    for key, (label, description) in AUTOMODE_OPTIONS.items():
        status = "🟢 ON" if config.get(key) else "🔴 OFF"
        lines.append(f"{label} — **{status}**\n{description}")
    embed = discord.Embed(
        title="🛡️ Automode Security Center",
        description=(
            "Configure your server's automatic security protections from the select menu below.\n\n"
            + "\n\n".join(lines)
            + "\n\n**Only the server owner can change these settings.**"
        ),
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Automode • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


class AutomodeSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        config = get_automode_config(guild_id)
        options = []
        for key, (label, description) in AUTOMODE_OPTIONS.items():
            options.append(discord.SelectOption(
                label=label,
                value=key,
                description=f"{'ON' if config.get(key) else 'OFF'} • {description[:70]}"
            ))
        super().__init__(
            placeholder="Select an Automode protection to enable/disable...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"automode_select:{guild_id}"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("❌ This panel belongs to another server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("❌ Only the **Server Owner** can change Automode settings.", ephemeral=True)
            return
        key = self.values[0]
        config = get_automode_config(self.guild_id)
        config[key] = not bool(config.get(key, False))
        save_json_settings(AUTOMODE_FILE, automode_settings)
        await interaction.response.edit_message(
            embed=build_automode_embed(interaction.guild),
            view=AutomodeView(self.guild_id)
        )


class AutomodeView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(AutomodeSelect(guild_id))


# --- AUTOMODE ENFORCEMENT HELPERS ---
def automode_enabled(guild, key):
    return bool(get_automode_config(guild.id).get(key, False))


def automode_exempt(member):
    return member.id == member.guild.owner_id or member.guild_permissions.administrator


async def automode_ban_member(guild, member, reason):
    if not member or member.id == guild.owner_id:
        return False
    me = guild.me
    if not me or not me.guild_permissions.ban_members or member.top_role >= me.top_role:
        return False
    try:
        await guild.ban(member, reason=reason, delete_message_seconds=86400)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def is_invite_link(content):
    return bool(re.search(r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)/[A-Za-z0-9_-]+", content, re.IGNORECASE))


async def handle_automode_antilink(message):
    if not automode_enabled(message.guild, "antilink") or automode_exempt(message.author):
        return False
    if not is_invite_link(message.content):
        return False
    try:
        await message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass
    key = (message.guild.id, message.author.id)
    count = antilink_warnings.get(key, 0) + 1
    antilink_warnings[key] = count
    warning = f"⚠️ Your invite link was removed in **{message.guild.name}**. Warning **{min(count, 2)}/2**."
    if count >= 2:
        warning += " You have reached 2 warnings."
    try:
        await message.author.send(warning)
    except (discord.Forbidden, discord.HTTPException):
        pass
    return True


async def handle_automode_antispam(message):
    # Anti-Spam is for regular members. The server owner is always exempt.
    # Administrators are NOT exempt, so an admin who actually spams can still
    # be timed out (provided the bot can moderate their role).
    if not automode_enabled(message.guild, "antispam") or message.author.id == message.guild.owner_id:
        return False

    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    key = (message.guild.id, message.author.id)

    # Rolling 1-second window: 5 messages whose timestamps are no more than
    # 1.0 second apart trigger the punishment. This catches bursts such as
    # 0.1s, 0.2s, 0.3s, 0.4s, 0.5s ... up to 1.0s.
    timestamps = [t for t in spam_tracker.get(key, []) if now - t <= 1.0]
    timestamps.append(now)
    spam_tracker[key] = timestamps

    if len(timestamps) < 5:
        return False

    # Reset the counter immediately so a new burst can be detected after the
    # timeout is applied.
    spam_tracker[key] = []

    # Discord requires Moderate Members permission for a timeout.
    me = message.guild.me
    if not me or not me.guild_permissions.moderate_members:
        return False
    if message.author.top_role >= me.top_role:
        return False

    try:
        await message.author.timeout(
            datetime.timedelta(hours=2),
            reason="Automode Anti-Spam: 5 messages within 1 second"
        )
    except (discord.Forbidden, discord.HTTPException):
        return False

    return True


async def handle_automode_antibot(member):
    if not automode_enabled(member.guild, "antibot") or not member.bot:
        return False
    if member.id == member.guild.me.id if member.guild.me else False:
        return False
    return await automode_ban_member(member.guild, member, "Automode Anti-Bot: unauthorized bot added to server")


async def handle_automode_antinuke(guild, action, target_id=None):
    if not automode_enabled(guild, "antinuke"):
        return
    me = guild.me
    if not me or not me.guild_permissions.view_audit_log:
        return
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            # Only consider very recent actions so an old audit entry cannot trigger a ban.
            if (datetime.datetime.now(datetime.timezone.utc) - entry.created_at).total_seconds() > 8:
                continue
            executor = entry.user
            if not isinstance(executor, discord.Member):
                executor = guild.get_member(executor.id)
            if executor and not automode_exempt(executor):
                await automode_ban_member(guild, executor, f"Automode Anti-Nuke: unauthorized {action.name} action")
            break
    except (discord.Forbidden, discord.HTTPException):
        pass


async def handle_automode_antiwebhook(guild):
    if not automode_enabled(guild, "antiwebhook"):
        return
    me = guild.me
    if not me or not me.guild_permissions.view_audit_log:
        return
    try:
        async for entry in guild.audit_logs(limit=8, action=discord.AuditLogAction.webhook_create):
            if (datetime.datetime.now(datetime.timezone.utc) - entry.created_at).total_seconds() <= 8:
                executor = entry.user
                if not isinstance(executor, discord.Member):
                    executor = guild.get_member(executor.id)
                if executor and not automode_exempt(executor):
                    await automode_ban_member(guild, executor, "Automode Anti-Webhook: unauthorized webhook created")
                break
    except (discord.Forbidden, discord.HTTPException):
        pass

# --- SUGGESTIONS SYSTEM ---
# Each server can configure one suggestions channel. Messages posted there are
# converted into embeds automatically. Configuration and vote data persist in JSON.

class SuggestionView(discord.ui.View):
    def __init__(self, suggestion_id: int):
        super().__init__(timeout=None)
        self.suggestion_id = int(suggestion_id)

        # Every suggestion gets unique component IDs so multiple suggestions
        # can coexist and keep working after a restart.
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "suggestion_yes":
                    child.custom_id = f"suggestion_yes:{self.suggestion_id}"
                elif child.custom_id == "suggestion_no":
                    child.custom_id = f"suggestion_no:{self.suggestion_id}"
                elif child.custom_id == "suggestion_approved":
                    child.custom_id = f"suggestion_approved:{self.suggestion_id}"

        record = self._get_record()
        if record:
            yes_count = int(record.get("yes", 0) or 0)
            no_count = int(record.get("no", 0) or 0)
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.custom_id == f"suggestion_yes:{self.suggestion_id}":
                        child.label = f"Yes 👍 {yes_count}"
                    elif child.custom_id == f"suggestion_no:{self.suggestion_id}":
                        child.label = f"No 👎 {no_count}"

        if record and record.get("approved"):
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.custom_id == f"suggestion_approved:{self.suggestion_id}":
                    child.disabled = True

    def _get_record(self):
        record = suggestions_settings.get(f"suggestion:{self.suggestion_id}")
        return record if isinstance(record, dict) else None

    async def _vote(self, interaction: discord.Interaction, vote: str):
        record = self._get_record()
        if record is None:
            await interaction.response.send_message("❌ This suggestion is no longer available.", ephemeral=True)
            return

        voters = record.setdefault("voters", {})
        user_key = str(interaction.user.id)
        old_vote = voters.get(user_key)
        if old_vote == vote:
            await interaction.response.send_message(f"You already voted **{vote}** on this suggestion.", ephemeral=True)
            return

        voters[user_key] = vote
        record["yes"] = sum(1 for value in voters.values() if value == "yes")
        record["no"] = sum(1 for value in voters.values() if value == "no")
        save_json_settings(SUGGESTIONS_FILE, suggestions_settings)

        # Refresh the buttons immediately so everyone can see the current
        # Yes/No vote totals on the suggestion message.
        updated_view = SuggestionView(self.suggestion_id)
        await interaction.response.edit_message(view=updated_view)

    @discord.ui.button(label="Yes 👍", style=discord.ButtonStyle.success, custom_id="suggestion_yes")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "yes")

    @discord.ui.button(label="No 👎", style=discord.ButtonStyle.danger, custom_id="suggestion_no")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "no")

    @discord.ui.button(label="✅Approved", style=discord.ButtonStyle.success, custom_id="suggestion_approved")
    async def approved_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = self._get_record()
        if record is None:
            await interaction.response.send_message("❌ This suggestion is no longer available.", ephemeral=True)
            return

        # Only the actual Discord server owner can approve suggestions.
        if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                "❌ Only the **Server Owner** can approve suggestions.",
                ephemeral=True
            )
            return

        if record.get("approved"):
            approved_by = record.get("approved_by")
            mention = f"<@{approved_by}>" if approved_by else "the Server Owner"
            await interaction.response.send_message(
                f"✅ This suggestion was already approved by {mention}.",
                ephemeral=True
            )
            return

        record["approved"] = True
        record["approved_by"] = interaction.user.id
        record["approved_at"] = datetime.datetime.now(datetime.timezone.utc).timestamp()
        save_json_settings(SUGGESTIONS_FILE, suggestions_settings)

        # Keep the existing suggestion layout and add the approval information
        # directly into the suggestion embed.
        embed = interaction.message.embeds[0].copy() if interaction.message.embeds else discord.Embed(
            description="*(Suggestion)*",
            color=discord.Color.blurple()
        )

        # Avoid duplicate approval fields if an old record/message is approved again.
        for index, field in reversed(list(enumerate(embed.fields))):
            if field.name.lower().replace(" ", "") == "approvedby":
                embed.remove_field(index)

        embed.add_field(
            name="Approved by",
            value=interaction.user.mention,
            inline=False
        )

        # Disable only the approval button after approval; voting remains available.
        updated_view = SuggestionView(self.suggestion_id)
        for child in updated_view.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == f"suggestion_approved:{self.suggestion_id}":
                child.disabled = True

        await interaction.response.edit_message(embed=embed, view=updated_view)


def build_suggestion_embed(message: discord.Message, image_url: str | None = None) -> discord.Embed:
    # Render the heading as a Discord markdown heading so only
    # "💡 Suggestions" appears large, matching the requested layout.
    description = f"# 💡 Suggestions\n\n{message.content or '*(No text — attachment only)*'}"
    embed = discord.Embed(
        description=description,
        color=discord.Color.blurple()
    )
    embed.add_field(name="Suggested by", value=message.author.mention, inline=False)

    # Use the large embed image area (not the small top-right thumbnail).
    # This keeps the configured image prominently across the top of the embed.
    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(
        text=f"Suggestions • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}"
    )
    return embed


@bot.tree.command(name="suggestions", description="Set the channel where member suggestions are submitted.")
@app_commands.describe(
    channel="The channel where members will send suggestions.",
    image="Optional large image shown on every suggestion embed."
)
@app_commands.checks.has_permissions(administrator=True)
async def suggestions(interaction: discord.Interaction, channel: discord.TextChannel, image: discord.Attachment | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    me = interaction.guild.me
    if me is None:
        await interaction.response.send_message("❌ I could not find my bot member information.", ephemeral=True)
        return

    perms = channel.permissions_for(me)
    missing = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.embed_links:
        missing.append("Embed Links")
    if not perms.manage_messages:
        missing.append("Manage Messages")

    if missing:
        await interaction.response.send_message(
            "❌ I need " + ", ".join(f"**{name}**" for name in missing) + f" permission(s) in {channel.mention}.",
            ephemeral=True
        )
        return

    image_url = image.url if image is not None else None
    if image is not None and (not image.content_type or not image.content_type.startswith("image/")):
        await interaction.response.send_message("❌ The `<image>` attachment must be an image file.", ephemeral=True)
        return

    suggestions_settings[str(interaction.guild.id)] = {
        "channel_id": channel.id,
        "image_url": image_url
    }
    save_json_settings(SUGGESTIONS_FILE, suggestions_settings)

    await interaction.response.send_message(
        f"✅ Suggestions are now enabled in {channel.mention}.\n"
        f"🖼️ Image: {'Configured' if image_url else 'None'}",
        ephemeral=True
    )


bot.remove_command("help")

@bot.event
async def on_ready():
    # Set the bot status to Do Not Disturb and add an activity message
    await bot.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(type=discord.ActivityType.watching, name="Support Tickets")
    )
    print(f"Logged in as {bot.user}")
    try:
        # Sync slash commands globally across all your servers
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application slash commands successfully!")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

    # Prime invite-use snapshots after startup so the first new member is
    # matched against the correct invite instead of an arbitrary invite.
    for guild in bot.guilds:
        try:
            current_invites = await guild.invites()
            invite_cache[guild.id] = {
                inv.code: int(inv.uses or 0) for inv in current_invites
            }
        except (discord.Forbidden, discord.HTTPException):
            invite_cache[guild.id] = {}

    # Restore persistent suggestion buttons after a restart/reconnect.
    # This keeps the Yes/No/Approved buttons working on existing suggestions.
    for key, record in list(suggestions_settings.items()):
        if not isinstance(record, dict) or not key.startswith("suggestion:"):
            continue
        try:
            suggestion_message_id = int(record.get("message_id", 0))
        except (TypeError, ValueError):
            continue
        if not suggestion_message_id:
            continue
        try:
            bot.add_view(SuggestionView(suggestion_message_id), message_id=suggestion_message_id)
        except Exception as exc:
            print(f"Could not restore suggestion buttons for {suggestion_message_id}: {exc}")

    # Restore giveaway timers after a restart/reconnect.
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    for giveaway_id, record in list(giveaway_records.items()):
        if not isinstance(record, dict) or record.get("ended"):
            continue
        try:
            end_timestamp = int(record.get("end_timestamp", 0))
        except (TypeError, ValueError):
            continue
        if not end_timestamp:
            continue
        if str(giveaway_id) not in giveaway_tasks:
            giveaway_tasks[str(giveaway_id)] = asyncio.create_task(
                schedule_giveaway(str(giveaway_id), end_timestamp)
            )

# Helper function to convert time duration strings (e.g., 5m, 1h) to seconds
def convert_time(duration_str):
    pos = ["s", "m", "h", "d"]
    time_dict = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    match = re.match(r"^(\d+)([smhd])$", duration_str.lower())
    if not match:
        return -1
    num = int(match.group(1))
    unit = match.group(2)
    return num * time_dict[unit]

# 📖 HELP COMMAND
# The bot owner can use -help in any server.
# Server administrators can use -help only in the server where they are an administrator.
# Set OWNER_ID in your environment (for example on Render) to your Discord user ID.
BOT_OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

@bot.command(name="help", aliases=["h"])
async def help_command(ctx):
    is_bot_owner = ctx.author.id == BOT_OWNER_ID
    is_server_admin = (
        ctx.guild is not None
        and isinstance(ctx.author, discord.Member)
        and ctx.author.guild_permissions.administrator
    )

    if not is_bot_owner and not is_server_admin:
        await ctx.send(
            "❌ You need to be the bot owner or have the **Administrator** permission "
            "in this server to use `-help`.",
            delete_after=5
        )
        return

    embed = discord.Embed(
        title="🤖 Bot Command List",
        description="Here are all available commands and what they do.",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="🛠️ Prefix Commands",
        value=(
            "`-help` — Shows this command list.\n"
            "`-delete` — Deletes the current ticket channel.\n"
            "`-purge <amount>` — Deletes the specified number of messages.\n"
            "`-gstart <duration> <winners> <prize>` — Starts a giveaway.\n"
            "`-gend <giveawayid>` — Force-ends an active giveaway.\n"
            "`-greroll <giveawayid>` — Rerolls a completed giveaway.\n"
            "`-polls <duration> <answer1> <answer2> [answer3]` — Creates a poll.\n"
            "`-invites [@member]` — Shows invite statistics.\n"
            "`-addbonus @member <amount>` — Adds bonus invites.\n"
            "`-resetinvites [@member]` — Resets invite statistics.\n"
            "`-vouch` — Sends the vouch panel in the current channel.\n"
            "`-ban <@user> <reason>` — Permanently bans a member.\n"
            "`-lock <#channel>` — Locks a target channel.\n"
            "`-unlock <#channel>` — Unlocks a target channel.\n"
            "`-automode` — Opens the all-in-one Automode security setup panel."
        ),
        inline=False
    )
    embed.add_field(
        name="🛠️ Prefix Commands (More)",
        value=(
            "`/staffapply <panel> <role> <review> <results> <question1>...<question13>` — Configures the DM staff application system.\n"
            "`-closed` — Closes staff applications and disables the Apply button.\n"
            "`-open` — Reopens staff applications and enables the Apply button.\n"
            "`-greetvariables` — Shows all greet/welcome variables."
        ),
        inline=False
    )
    embed.add_field(
        name="⚙️ Slash Commands",
        value=(
            "`/customwelcome <message>` — Sets the custom welcome message.\n"
            "`/welcome-delafter <message> <deleteafter>` — Configures automatic welcomes with timed deletion.\n"
            "`/testgreet` — Sends a test welcome greeting.\n"
            "`/channel_set <channel>` — Sets the welcome greeting channel.\n"
            "`/announce <message>` — Sends an announcement embed and DMs members.\n"
            "`/ticket-setup` — Creates the support ticket panel.\n"
            "`/reactionrole <role>` — Creates a button to get/remove a role.\n"
            "`/autorole <role>` — Automatically gives a role to new members.\n"
            "`/honeypot` — Makes this channel a spam trap.\n"
            "`/suggestions <channel> [image]` — Converts member messages in the configured channel into suggestion embeds.\n"
            "`/lock <channel>` — Locks a target channel.\n"
             "`/unlock <channel>` — Unlocks a target channel."
        ),
        inline=False
    )
    embed.add_field(
        name="🧩 Boost & Greet Variables",
        value=(
            "`-greetvariables` — Shows every supported boost variable.\n"
            "`-greetvariables` — Shows every supported greet/welcome variable.\n"
            "Both commands show the complete variable list and examples."
        ),
        inline=False
    )
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)



# --- POLL SYSTEM ---
def parse_poll_duration(duration_str: str):
    """Return duration in seconds for s/m/h/d syntax, or -1 if invalid."""
    match = re.fullmatch(r"(\d+)([smhd])", duration_str.strip().lower())
    if not match:
        return -1
    number = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return number * multipliers[unit]


@bot.command(name="polls")
async def polls_command(ctx, *, arguments: str = ""):
    """Create a native Discord poll that matches Discord's built-in poll UI."""
    import shlex

    try:
        parts = shlex.split(arguments)
    except ValueError:
        await ctx.send(
            '❌ Invalid quotes. Use quotes around answers with spaces, e.g. `"Yes please"`.',
            delete_after=8
        )
        return

    if len(parts) < 3:
        await ctx.send(
            "❌ Usage: `-polls <answer1> <answer2> [answer3] <duration>`\n"
            "Example: `-polls Yes No Maybe 24h`\n"
            "You can also put the duration first: `-polls 24h Yes No Maybe`.",
            delete_after=10
        )
        return

    # Find the duration anywhere in the command so both formats work.
    duration_positions = [
        index for index, part in enumerate(parts)
        if parse_poll_duration(part) > 0
    ]

    if not duration_positions:
        await ctx.send(
            "❌ Please provide a valid duration such as `10s`, `5m`, `1h`, or `1d`.",
            delete_after=8
        )
        return

    if len(duration_positions) > 1:
        await ctx.send(
            "❌ Please provide only **one** poll duration.",
            delete_after=7
        )
        return

    duration_index = duration_positions[0]
    duration_seconds = parse_poll_duration(parts[duration_index])
    answer_parts = parts[:duration_index] + parts[duration_index + 1:]

    # Answer 3 is optional. If the user explicitly leaves it blank
    # (for example: `-polls Yes No "" 24h`), treat it as not provided.
    if len(answer_parts) == 3 and not answer_parts[2].strip():
        answer_parts = answer_parts[:2]

    if len(answer_parts) not in (2, 3):
        await ctx.send(
            "❌ Please provide **2 answers**, with **Answer 3 optional**.",
            delete_after=8
        )
        return

    # Discord's native Poll is the UI shown in the user's screenshot.
    # Native Discord polls have a minimum duration and a maximum duration.
    # Discord currently supports native poll durations from 1 hour through 7 days.
    if duration_seconds < 3600:
        await ctx.send(
            "❌ Discord's native poll (the same poll style as your screenshot) "
            "requires a minimum duration of **1 hour**.\n"
            "Use `1h`, `2h`, `1d`, etc. for the native poll style.",
            delete_after=10
        )
        return

    if duration_seconds > 7 * 86400:
        await ctx.send(
            "❌ Discord's native poll can stay open for a maximum of **7 days**.",
            delete_after=8
        )
        return

    if any(len(answer) > 55 for answer in answer_parts):
        await ctx.send(
            "❌ Each poll answer must be **55 characters or fewer** for Discord's native poll.",
            delete_after=8
        )
        return

    if not all(answer.strip() for answer in answer_parts):
        await ctx.send("❌ Poll answers cannot be blank.", delete_after=7)
        return

    if not hasattr(discord, "Poll"):
        await ctx.send(
            "❌ Your bot's `discord.py` version does not support native Discord polls. "
            "Please update `discord.py` to a version that supports `discord.Poll`.",
            delete_after=10
        )
        return

    # This is a REAL Discord Poll, not an embed with custom buttons.
    # Discord itself renders the selection circles, Vote button,
    # vote counter, remaining duration, and Show results exactly like
    # the screenshot the user provided.
    poll = discord.Poll(
        question="Poll",
        duration=datetime.timedelta(seconds=duration_seconds)
    )

    for answer in answer_parts:
        poll.add_answer(text=answer)

    try:
        await ctx.send(poll=poll)
    except (discord.Forbidden, discord.HTTPException) as error:
        print(f"Failed to send native poll: {error}")
        await ctx.send(
            "❌ I couldn't create the native Discord poll. "
            "Make sure the bot has **Send Messages** permission and that polls are allowed in this channel.",
            delete_after=10
        )

# --- AUTOMODE PREFIX COMMAND ---
@bot.command(name="automode")
@commands.has_permissions(administrator=True)
async def automode(ctx):
    """Post the all-in-one Automode security setup panel."""
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used in a server.", delete_after=5)
        return
    get_automode_config(ctx.guild.id)
    save_json_settings(AUTOMODE_FILE, automode_settings)
    await ctx.send(embed=build_automode_embed(ctx.guild), view=AutomodeView(ctx.guild.id))


# --- VOUCH SYSTEM ---
class VouchModal(discord.ui.Modal, title="Submit a Vouch"):
    def __init__(self, target: discord.Member):
        super().__init__(timeout=300)
        self.target = target
        self.reason = discord.ui.TextInput(
            label="Vouch Reason",
            placeholder="Why are you vouching for this member?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.rating = discord.ui.TextInput(
            label="Rating (1-5)",
            placeholder="5",
            required=True,
            max_length=1
        )
        self.add_item(self.reason)
        self.add_item(self.rating)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rating = int(self.rating.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Rating must be a number from **1 to 5**.", ephemeral=True)
            return
        if rating < 1 or rating > 5:
            await interaction.response.send_message("❌ Rating must be between **1 and 5**.", ephemeral=True)
            return

        stars = "⭐" * rating
        reviewed = datetime.datetime.now().strftime("%B %d, %Y • %I:%M %p")
        embed = discord.Embed(title="✨ New Vouch!", color=discord.Color.gold())
        # Show the member who submitted the vouch at the very top:
        # their real profile icon followed by their username.
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url
        )
        embed.add_field(name="📝 Vouch Reason", value=self.reason.value, inline=False)
        embed.add_field(name="🎯 Vouch To", value=self.target.mention, inline=False)
        embed.add_field(name="⭐ Rating", value=f"{stars} ({rating}/5)", inline=False)
        embed.add_field(name="📅 Reviewed", value=reviewed, inline=False)
        embed.set_thumbnail(url=self.target.display_avatar.url)
        await interaction.channel.send(embed=embed, view=VouchResultView())
        await interaction.response.send_message("✅ Your vouch has been submitted!", ephemeral=True)


class VouchAdminSelect(discord.ui.Select):
    def __init__(self, owner_id: int, admins: list[discord.Member], avatar_emojis: dict[int, discord.Emoji]):
        self.owner_id = owner_id
        self.admin_ids = {str(member.id): member.id for member in admins}
        self.avatar_emojis = avatar_emojis

        options = []
        for admin in admins[:25]:
            # Use the administrator's actual Discord avatar as a temporary
            # server emoji, so the select option shows their real profile image.
            profile_emoji = avatar_emojis.get(admin.id)
            if profile_emoji is None:
                raise RuntimeError("Could not create the administrator profile emoji.")

            options.append(
                discord.SelectOption(
                    label=admin.display_name[:100],
                    description=f"Administrator • {admin.name}"[:100],
                    emoji=profile_emoji,
                    value=str(admin.id)
                )
            )

        super().__init__(
            placeholder="Select an administrator to vouch for",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"vouch_admin_select:{owner_id}"
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ This vouch form belongs to another member.",
                ephemeral=True
            )
            return

        target_id = int(self.values[0])
        target = interaction.guild.get_member(target_id)
        if target is None or not target.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ That administrator is no longer available. Please start the vouch form again.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(VouchModal(target))


class VouchTargetView(discord.ui.View):
    def __init__(self, owner_id: int, guild: discord.Guild):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.guild = guild
        self.avatar_emojis: dict[int, discord.Emoji] = {}

    @classmethod
    async def create(cls, owner_id: int, guild: discord.Guild):
        view = cls(owner_id, guild)

        # ONLY members with the Discord Administrator permission are shown.
        admins = [
            member for member in guild.members
            if member.guild_permissions.administrator
        ]
        admins.sort(key=lambda member: member.display_name.lower())
        admins = admins[:25]

        if not admins:
            return view

        me = guild.me
        if me is None or not me.guild_permissions.manage_emojis_and_stickers:
            # Do not fall back to the crown: the requested UI must use the
            # administrator's real profile image.
            raise PermissionError(
                "The bot needs Manage Expressions to show administrator profile pictures in the select menu."
            )

        async with aiohttp.ClientSession() as session:
            for admin in admins:
                avatar_url = admin.display_avatar.replace(format="png", size=128).url
                async with session.get(avatar_url) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Could not download {admin.display_name}'s profile image.")
                    avatar_bytes = await response.read()

                # Discord select options only support emojis as icons. We create
                # a temporary custom emoji from the administrator's actual avatar.
                emoji_name = f"vouch_{admin.id}"[:32]
                try:
                    emoji = await guild.create_custom_emoji(
                        name=emoji_name,
                        image=avatar_bytes,
                        reason="Temporary administrator profile icon for vouch selector"
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    await view._cleanup_emojis()
                    raise RuntimeError(
                        "Discord could not create the administrator profile icon."
                    ) from exc

                view.avatar_emojis[admin.id] = emoji

        view.add_item(VouchAdminSelect(owner_id, admins, view.avatar_emojis))
        return view

    async def _cleanup_emojis(self):
        for emoji in list(self.avatar_emojis.values()):
            try:
                await emoji.delete(reason="Vouch administrator selector closed")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        self.avatar_emojis.clear()

    async def on_timeout(self):
        await self._cleanup_emojis()


class VouchResultView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit a Vouch", emoji="📝", style=discord.ButtonStyle.primary, custom_id="vouch_submit")
    async def submit_vouch(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            target_view = await VouchTargetView.create(interaction.user.id, interaction.guild)
        except PermissionError as exc:
            await interaction.response.send_message(
                f"❌ {exc}",
                ephemeral=True
            )
            return
        except RuntimeError as exc:
            await interaction.response.send_message(
                f"❌ {exc}",
                ephemeral=True
            )
            return

        if not target_view.children:
            await interaction.response.send_message(
                "❌ No administrators are available to vouch for.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Select an administrator to vouch for:",
            view=target_view,
            ephemeral=True
        )

    @discord.ui.button(label="Found Useful", emoji="❤️", style=discord.ButtonStyle.secondary, custom_id="vouch_found_useful")
    async def found_useful(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❤️ Thanks for the feedback!", ephemeral=True)


# --- BOOST / GREET VARIABLES ---
# These variables are available in BOTH boost and greet/welcome messages.
VARIABLE_DESCRIPTIONS = {
    # Member / user
    "member": "Mentions the member.",
    "user": "Mentions the member.",
    "mention": "Mentions the member.",
    "username": "Member username.",
    "display_name": "Member display name.",
    "displayname": "Member display name (alias).",
    "nickname": "Member nickname, or username if no nickname is set.",
    "user_id": "Member Discord ID.",
    "userid": "Member Discord ID (alias).",
    "discriminator": "Member discriminator/tag when available.",
    "bot": "True if the member is a bot, otherwise False.",
    "avatar": "Shows the member profile picture in the greet embed (no URL is inserted into the text).",
    "avatar_url": "Member avatar URL (alias).",
    "account_created": "Date the member's Discord account was created.",
    "account_age": "Approximate age of the member's Discord account.",
    "joined_at": "Date the member joined this server.",
    "server_joined": "Date the member joined this server (alias).",

    # Server / guild
    "server": "Server name.",
    "server_name": "Server name (alias).",
    "server_id": "Server Discord ID.",
    "member_count": "Current server member count.",
    "members": "Current server member count (alias).",
    "owner": "Mentions the server owner.",
    "owner_id": "Server owner's Discord ID.",
    "verification_level": "Server verification level.",
    "boost_level": "Server boost tier/level.",
    "boost_count": "Current number of server boosts.",
    "boosters": "Current number of server boosters.",
    "created_at": "Server creation date.",

    # Channel
    "channel": "Mentions the current/configured channel.",
    "channel_name": "Current/configured channel name.",
    "channel_id": "Current/configured channel Discord ID.",

    # Time / date
    "date": "Current date.",
    "time": "Current time.",
    "datetime": "Current date and time.",
    "timestamp": "Current Discord timestamp.",
    "unix": "Current Unix timestamp.",
    "year": "Current year.",
    "month": "Current month name.",
    "day": "Current day of the month.",

    # Roles / permissions
    "roles": "Member roles, excluding @everyone.",
    "role_count": "Number of member roles, excluding @everyone.",
    "highest_role": "Member's highest role.",
    "highest_role_id": "Member's highest role ID.",
    "color": "Member's top-role display color as a hex value.",

    # Boost-specific
    "booster": "Mentions the member who boosted the server.",
    "booster_name": "Display name of the member who boosted.",
    "booster_id": "Discord ID of the member who boosted.",
    "boost_started": "Date/time the member started boosting, if available.",
    "boost_duration": "Approximate time the member has been boosting, if available.",

    # Invite / welcome tracking
    "inviter": "Mentions the member who invited this new member, when detected.",
    "inviter_name": "Display name of the member who invited this new member.",
    "inviter_id": "Discord ID of the member who invited this new member.",
    "invite_code": "Invite code that was detected for this join.",
    "invite_url": "Discord invite URL that was detected for this join.",
    "invite_uses": "Current use count of the detected invite.",
    "invite_regular": "Regular invite count for the detected inviter.",
    "invite_leaves": "Leave count for the detected inviter.",
    "invite_fake": "Fake/young-account invite count for the detected inviter.",
    "invite_bonus": "Bonus invite count for the detected inviter.",
    "invite_total": "Regular + bonus invites for the detected inviter.",
}

def _format_dt(dt):
    if not dt:
        return ""
    return dt.strftime("%B %d, %Y • %I:%M %p")

def _account_age(created_at, now):
    if not created_at:
        return ""
    delta = now - created_at.replace(tzinfo=None)
    days = max(0, delta.days)
    years, rem = divmod(days, 365)
    months, days_left = divmod(rem, 30)
    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}mo")
    if days_left or not parts:
        parts.append(f"{days_left}d")
    return " ".join(parts)

def _boost_duration(boost_started, now):
    if not boost_started:
        return ""
    delta = now - boost_started.replace(tzinfo=None)
    days = max(0, delta.days)
    months, days_left = divmod(days, 30)
    years, months_left = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years}y")
    if months_left:
        parts.append(f"{months_left}mo")
    if days_left or not parts:
        parts.append(f"{days_left}d")
    return " ".join(parts)

def render_variables(template: str, member: discord.Member, channel=None, invite=None) -> str:
    """Replace all supported boost/greet placeholders."""
    if not isinstance(template, str):
        return template

    guild = member.guild
    now = datetime.datetime.now()
    created_at = getattr(member, "created_at", None)
    joined_at = getattr(member, "joined_at", None)
    boost_started = getattr(member, "premium_since", None)

    owner = guild.owner
    roles = [role for role in getattr(member, "roles", []) if role != guild.default_role]
    highest_role = getattr(member, "top_role", None)

    # Invite context is optional so existing greet/boost commands keep working.
    inviter = getattr(invite, "inviter", None) if invite else None
    inviter_stats = get_user_stats(guild.id, inviter.id) if inviter else {
        "regular": 0, "leaves": 0, "fake": 0, "bonus": 0
    }

    # Normalize old/corrupt JSON values before exposing invite variables.
    def _invite_int(field):
        try:
            return max(0, int(inviter_stats.get(field, 0) or 0))
        except (TypeError, ValueError):
            return 0

    invite_regular = _invite_int("regular")
    invite_leaves = _invite_int("leaves")
    invite_fake = _invite_int("fake")
    invite_bonus = _invite_int("bonus")
    # {invite_total} = accumulated regular + bonus invites.
    invite_total = invite_regular + invite_bonus
    invite_code = getattr(invite, "code", "") if invite else ""
    invite_uses = getattr(invite, "uses", 0) if invite else 0

    # Discord's current role color can be exposed as a normal hex string.
    role_color = getattr(highest_role, "color", None)
    color_value = f"#{role_color.value:06X}" if role_color and role_color.value else "#000000"

    # Discord channel variables are blank only when no channel context was supplied.
    channel_mention = channel.mention if channel else ""
    channel_name = channel.name if channel else ""
    channel_id = str(channel.id) if channel else ""

    values = {
        # Member / user
        "{member}": member.mention,
        "{user}": member.mention,
        "{mention}": member.mention,
        "{username}": member.name,
        "{display_name}": member.display_name,
        "{displayname}": member.display_name,
        "{nickname}": member.nick or member.name,
        "{user_id}": str(member.id),
        "{userid}": str(member.id),
        "{discriminator}": getattr(member, "discriminator", "") or "",
        "{bot}": str(bool(member.bot)),
        "{avatar}": "",  # Embed-only: the greet embed uses this to show the profile picture.
        "{avatar_url}": member.display_avatar.url,
        "{account_created}": _format_dt(created_at),
        "{account_age}": _account_age(created_at, now),
        "{joined_at}": _format_dt(joined_at),
        "{server_joined}": _format_dt(joined_at),

        # Server
        "{server}": guild.name,
        "{server_name}": guild.name,
        "{server_id}": str(guild.id),
        "{member_count}": str(guild.member_count or len(guild.members)),
        "{members}": str(guild.member_count or len(guild.members)),
        "{owner}": owner.mention if owner else "",
        "{owner_id}": str(owner.id) if owner else "",
        "{verification_level}": str(guild.verification_level).replace("_", " ").title(),
        "{boost_level}": str(getattr(guild, "premium_tier", 0)),
        "{boost_count}": str(getattr(guild, "premium_subscription_count", 0) or 0),
        "{boosters}": str(len(getattr(guild, "premium_subscribers", []) or [])),
        "{created_at}": _format_dt(getattr(guild, "created_at", None)),

        # Channel
        "{channel}": channel_mention,
        "{channel_name}": channel_name,
        "{channel_id}": channel_id,

        # Date / time
        "{date}": now.strftime("%B %d, %Y"),
        "{time}": now.strftime("%I:%M %p"),
        "{datetime}": now.strftime("%B %d, %Y • %I:%M %p"),
        "{timestamp}": f"<t:{int(now.timestamp())}:F>",
        "{unix}": str(int(now.timestamp())),
        "{year}": now.strftime("%Y"),
        "{month}": now.strftime("%B"),
        "{day}": now.strftime("%d"),

        # Roles / permissions
        "{roles}": ", ".join(role.name for role in roles) if roles else "None",
        "{role_count}": str(len(roles)),
        "{highest_role}": highest_role.mention if highest_role else "",
        "{highest_role_id}": str(highest_role.id) if highest_role else "",
        "{color}": color_value,

        # Boost-specific aliases
        "{booster}": member.mention,
        "{booster_name}": member.display_name,
        "{booster_id}": str(member.id),
        "{boost_started}": _format_dt(boost_started),
        "{boost_duration}": _boost_duration(boost_started, now),

        # Invite / welcome tracking
        "{inviter}": inviter.mention if inviter else "",
        "{inviter_name}": inviter.display_name if inviter else "",
        "{inviter_id}": str(inviter.id) if inviter else "",
        "{invite_code}": invite_code,
        "{invite_url}": f"https://discord.gg/{invite_code}" if invite_code else "",
        "{invite_uses}": str(invite_uses),
        "{invite_regular}": str(invite_regular),
        "{invite_leaves}": str(invite_leaves),
        "{invite_fake}": str(invite_fake),
        "{invite_bonus}": str(invite_bonus),
        "{invite_total}": str(invite_total),
    }

    for placeholder, value in values.items():
        template = template.replace(placeholder, str(value))
    return template


def build_variable_embed(title: str, description: str, variables: dict, ctx) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple()
    )
    items = list(variables.items())
    for start in range(0, len(items), 10):
        chunk = items[start:start + 10]
        value = "\n".join(f"`{{{name}}}` — {desc}" for name, desc in chunk)
        embed.add_field(
            name="Variables" if start == 0 else "More Variables",
            value=value,
            inline=False
        )
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    return embed


@bot.command(name="greetvariables")
async def greet_variables(ctx):
    """Show every placeholder available for greet/welcome messages."""
    await ctx.send(embed=build_variable_embed(
        "👋 Greet Variables",
        "Use these variables in `/customwelcome`. All listed variables are supported.",
        VARIABLE_DESCRIPTIONS,
        ctx
    ))


# --- BOOST NOTIFICATION SYSTEM ---
boost_group = app_commands.Group(name="boost", description="Configure server boost notifications.")


def build_boost_embed(booster: discord.Member, custom_message: str, channel=None) -> discord.Embed:
    """Build the boost notification embed in the requested style."""
    # Support useful placeholders in the configured message.
    rendered_message = render_variables(custom_message, booster, channel=channel)

    embed = discord.Embed(
        description=f"# ✨ **Someone just boosted!**\n\n{rendered_message}",
        color=discord.Color.from_rgb(255, 105, 180)
    )

    # The booster's real Discord profile/avatar is shown at the top of the embed.
    embed.set_author(
        name=booster.display_name,
        icon_url=booster.display_avatar.url
    )

    embed.set_footer(
        text=f"Boost • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}"
    )
    return embed


@boost_group.command(name="message", description="Set the boost notification message for this server.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(message="The message to send when someone boosts the server.")
async def boost_message(interaction: discord.Interaction, message: str):
    """Set the boost notification message and the channel where it is configured."""
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server channel.",
            ephemeral=True
        )
        return

    me = interaction.guild.me
    if me is None:
        await interaction.response.send_message(
            "❌ I could not find my server member information.",
            ephemeral=True
        )
        return

    missing = []
    if not me.guild_permissions.view_channel:
        missing.append("View Channel")
    if not me.guild_permissions.send_messages:
        missing.append("Send Messages")
    if not me.guild_permissions.embed_links:
        missing.append("Embed Links")

    if missing:
        await interaction.response.send_message(
            "❌ I need " + ", ".join(f"**{perm}**" for perm in missing) + " permission(s) in this channel.",
            ephemeral=True
        )
        return

    boost_settings[str(interaction.guild.id)] = {
        "channel_id": interaction.channel.id,
        "message": message
    }
    save_json_settings(BOOST_FILE, boost_settings)

    await interaction.response.send_message(
        f"✅ Boost notifications are now enabled in {interaction.channel.mention}.",
        ephemeral=True
    )


# /testboostmessage — preview the currently configured boost message in this channel.
@bot.tree.command(name="testboostmessage", description="Test the boost notification message you configured.")
@app_commands.checks.has_permissions(administrator=True)
async def testboostmessage(interaction: discord.Interaction):
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server channel.",
            ephemeral=True
        )
        return

    settings = boost_settings.get(str(interaction.guild.id))
    if not isinstance(settings, dict) or not isinstance(settings.get("message"), str) or not settings.get("message", "").strip():
        await interaction.response.send_message(
            "❌ No boost message is configured yet. Use `/boost message <message>` first.",
            ephemeral=True
        )
        return

    embed = build_boost_embed(interaction.user, settings["message"], interaction.channel)
    await interaction.response.send_message(embed=embed)


# Register the /boost command group once.
bot.tree.add_command(boost_group)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Send the configured boost embed when a member starts boosting the server."""
    # Only trigger when the member newly starts boosting.
    if before.premium_since is not None or after.premium_since is None:
        return

    settings = boost_settings.get(str(after.guild.id))
    if not isinstance(settings, dict):
        return

    channel_id = settings.get("channel_id")
    custom_message = settings.get("message")
    if not channel_id or not isinstance(custom_message, str) or not custom_message.strip():
        return

    channel = after.guild.get_channel(int(channel_id))
    if channel is None:
        return

    embed = build_boost_embed(after, custom_message, channel)

    try:
        await channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True)
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"Could not send boost notification in guild {after.guild.id}: {exc}")


@help_command.error
async def help_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        print(f"Help command error: {error.original}")
        await ctx.send("❌ The help command encountered an error. Check the bot logs.", delete_after=8)


# --- 5. PREFIX COMMANDS ---


# 📝 VOUCH PANEL COMMAND
@bot.command(name="vouch")
@commands.has_permissions(administrator=True)
async def vouch_panel(ctx):
    """Send the vouch panel in the channel where -vouch is used."""
    embed = discord.Embed(
        title="Vouch System",
        description="Click **📝 Submit a Vouch** below to leave a vouch for a member.",
        color=discord.Color.blurple()
    )
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass
    await ctx.send(embed=embed, view=VouchResultView())

# 🛑 TICKET DELETE COMMAND
@bot.command(name="delete")
@commands.has_permissions(manage_channels=True)
async def delete_ticket(ctx):
    if ctx.channel.name.startswith("ticket-"):
        await ctx.send("🔒 This ticket channel will be deleted in 5 seconds via prefix command...")
        await asyncio.sleep(5)
        await ctx.channel.delete()
    else:
        await ctx.send("❌ This command can only be used inside a ticket channel.", delete_after=5)

# 🧹 PURGE MESSAGES COMMAND
@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def purge_messages(ctx, amount: int):
    if amount < 1:
        await ctx.send("❌ Please specify an amount greater than 0.", delete_after=5)
        return
    # Delete the command message itself + requested amount
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 Successfully cleared `{len(deleted) - 1}` messages!", delete_after=5)

# 🔨 BAN COMMAND
@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban_member_prefix(ctx, member: discord.Member, *, reason: str):
    guild = ctx.guild
    if guild is None:
        await ctx.send(embed=discord.Embed(title="❌ Ban Failed", description="This command can only be used in a server.", color=discord.Color.red()))
        return
    me = guild.me
    if me is None or not me.guild_permissions.ban_members:
        await ctx.send(embed=discord.Embed(title="❌ Ban Failed", description="I need **Ban Members** permission to ban members.", color=discord.Color.red()))
        return
    if member.id == guild.owner_id:
        await ctx.send(embed=discord.Embed(title="❌ Ban Failed", description="I cannot ban the server owner.", color=discord.Color.red()))
        return
    if member == me or member.top_role >= me.top_role:
        await ctx.send(embed=discord.Embed(title="❌ Ban Failed", description="I cannot ban that member because of the role hierarchy.", color=discord.Color.red()))
        return
    try:
        await guild.ban(member, reason=reason)
    except discord.Forbidden:
        await ctx.send(embed=discord.Embed(title="❌ Ban Failed", description="Discord denied the ban. Check my **Ban Members** permission and role hierarchy.", color=discord.Color.red()))
        return
    except discord.HTTPException as e:
        await ctx.send(embed=discord.Embed(title="❌ Ban Failed", description=f"Discord returned an error: `{e}`", color=discord.Color.red()))
        return
    embed = discord.Embed(title="🔨 Member Banned", description=f"{member.mention} has been permanently banned.", color=discord.Color.red())
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


# 🔒 LOCK COMMAND
@bot.command(name="lock")
@commands.has_permissions(manage_channels=True)
async def lock_channel_prefix(ctx, channel: discord.TextChannel):
    try:
        await channel.set_permissions(ctx.guild.default_role, send_messages=False, reason=f"Channel locked by {ctx.author}")
    except discord.Forbidden:
        await ctx.send(embed=discord.Embed(title="❌ Lock Failed", description="I cannot change permissions for that channel.", color=discord.Color.red()))
        return
    except discord.HTTPException as e:
        await ctx.send(embed=discord.Embed(title="❌ Lock Failed", description=f"Discord returned an error: `{e}`", color=discord.Color.red()))
        return
    embed = discord.Embed(title="🔒 Channel Locked", description=f"{channel.mention} has been locked. Members can no longer send messages there.", color=discord.Color.red())
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)
    await ctx.send(embed=embed)


# 🔓 UNLOCK COMMAND
@bot.command(name="unlock")
@commands.has_permissions(manage_channels=True)
async def unlock_channel_prefix(ctx, channel: discord.TextChannel):
    try:
        await channel.set_permissions(ctx.guild.default_role, send_messages=None, reason=f"Channel unlocked by {ctx.author}")
    except discord.Forbidden:
        await ctx.send(embed=discord.Embed(title="❌ Unlock Failed", description="I cannot change permissions for that channel.", color=discord.Color.red()))
        return
    except discord.HTTPException as e:
        await ctx.send(embed=discord.Embed(title="❌ Unlock Failed", description=f"Discord returned an error: `{e}`", color=discord.Color.red()))
        return
    embed = discord.Embed(title="🔓 Channel Unlocked", description=f"{channel.mention} has been unlocked.", color=discord.Color.red())
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)
    await ctx.send(embed=embed)


# 🔨 BAN / 🔒 LOCK / 🔓 UNLOCK SLASH COMMANDS
@bot.tree.command(name="lock", description="Lock a target channel.")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="The text channel to lock")
async def lock_channel_slash(interaction: discord.Interaction, channel: discord.TextChannel):
    try:
        await channel.set_permissions(interaction.guild.default_role, send_messages=False, reason=f"Channel locked by {interaction.user}")
    except discord.Forbidden:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Lock Failed", description="I cannot change permissions for that channel.", color=discord.Color.red()), ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Lock Failed", description=f"Discord returned an error: `{e}`", color=discord.Color.red()), ephemeral=True)
        return
    embed = discord.Embed(title="🔒 Channel Locked", description=f"{channel.mention} has been locked. Members can no longer send messages there.", color=discord.Color.red())
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unlock", description="Unlock a target channel.")
@app_commands.checks.has_permissions(manage_channels=True)
@app_commands.describe(channel="The text channel to unlock")
async def unlock_channel_slash(interaction: discord.Interaction, channel: discord.TextChannel):
    try:
        await channel.set_permissions(interaction.guild.default_role, send_messages=None, reason=f"Channel unlocked by {interaction.user}")
    except discord.Forbidden:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Unlock Failed", description="I cannot change permissions for that channel.", color=discord.Color.red()), ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(embed=discord.Embed(title="❌ Unlock Failed", description=f"Discord returned an error: `{e}`", color=discord.Color.red()), ephemeral=True)
        return
    embed = discord.Embed(title="🔓 Channel Unlocked", description=f"{channel.mention} has been unlocked.", color=discord.Color.red())
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=False)
    await interaction.response.send_message(embed=embed)


# 🎉 GIVEAWAY COMMAND — FALCON™ REACTION FORMAT

# Use Discord's normal Unicode Tada emoji — not a custom/animated server emoji.
GIVEAWAY_EMOJI_TEXT = "🎉"
GIVEAWAY_EMOJI = GIVEAWAY_EMOJI_TEXT


def can_manage_giveaway(member: discord.Member) -> bool:
    """Allow server Administrators or a role named Staff to manage giveaways."""
    if member.guild_permissions.administrator:
        return True
    return any(role.name.lower() == "staff" for role in member.roles)


giveaway_tasks = {}

async def finish_giveaway(giveaway_id: str):
    """Finish a giveaway safely, including giveaways restored after a restart."""
    record = giveaway_records.get(str(giveaway_id))
    if not isinstance(record, dict) or record.get("ended"):
        return

    try:
        guild_id = int(record["guild_id"])
        channel_id = int(record["channel_id"])
        message_id = int(record["message_id"])
        winners = max(1, int(record.get("winners", 1)))
        prize = str(record.get("prize", "Giveaway"))
        host_id = int(record.get("host_id", 0))
    except (KeyError, TypeError, ValueError):
        return

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        giveaway_msg = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
        # Mark it ended so a broken/deleted message is not retried forever.
        record["ended"] = True
        save_json_settings(GIVEAWAY_FILE, giveaway_records)
        return

    entry_ids = []
    for reaction in giveaway_msg.reactions:
        if str(reaction.emoji) != GIVEAWAY_EMOJI_TEXT:
            continue
        try:
            async for user in reaction.users():
                if not user.bot and user.id not in entry_ids:
                    entry_ids.append(user.id)
        except (discord.Forbidden, discord.HTTPException):
            pass
        break

    ended_timestamp = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    giveaway_color = discord.Color.from_rgb(46, 204, 196)
    host_mention = f"<@{host_id}>" if host_id else "Unknown host"

    if not entry_ids:
        end_embed = discord.Embed(
            title=f"🎁 {prize} 🎁",
            description=(
                "🔹 **Winners:** No winners — nobody entered.\n"
                f"🔹 **Hosted by:** {host_mention}\n\n"
                "🏆 **Giveaway has ended!**"
            ),
            color=giveaway_color
        )
        winner_ids = []
    else:
        winner_count = min(winners, len(entry_ids))
        winner_ids = random.sample(entry_ids, winner_count)
        winner_mentions = ", ".join(f"<@{uid}>" for uid in winner_ids)
        end_embed = discord.Embed(
            title=f"🎁 {prize} 🎁",
            description=(
                f"🔹 **Winners:** {winner_mentions}\n"
                f"🔹 **Hosted by:** {host_mention}\n\n"
                f"🏆 **Congratulations {winner_mentions}!**\n"
                f"🎉 **You won {prize}!**"
            ),
            color=giveaway_color
        )

    ended_time_display = datetime.datetime.fromtimestamp(
        ended_timestamp,
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%B %d, %Y %I:%M %p")
    end_embed.set_footer(text=f"Ended at • {ended_time_display}")

    try:
        await giveaway_msg.edit(
            content="**Giveaway Ended**",
            embed=end_embed,
            allowed_mentions=discord.AllowedMentions(users=True)
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    record["ended"] = True
    record["winner_ids"] = winner_ids
    save_json_settings(GIVEAWAY_FILE, giveaway_records)


async def schedule_giveaway(giveaway_id: str, end_timestamp: int):
    try:
        delay = max(0, int(end_timestamp) - int(datetime.datetime.now(datetime.timezone.utc).timestamp()))
        await asyncio.sleep(delay)
        await finish_giveaway(giveaway_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"Giveaway {giveaway_id} failed to finish: {exc}")
    finally:
        giveaway_tasks.pop(str(giveaway_id), None)


@bot.command(name="gstart")
@commands.has_permissions(manage_guild=True)
async def start_giveaway(ctx, duration: str, winners: int, *, prize: str):
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used in a server.", delete_after=5)
        return

    seconds = convert_time(duration)
    if seconds <= 0:
        await ctx.send(
            "❌ Invalid time format. Use a duration greater than 0, such as `10m`, `1h`, or `1d`.",
            delete_after=10
        )
        return

    if winners < 1:
        await ctx.send("❌ You must have at least 1 winner.", delete_after=5)
        return
    if not prize.strip():
        await ctx.send("❌ Please provide a giveaway prize.", delete_after=5)
        return

    giveaway_color = discord.Color.from_rgb(46, 204, 196)
    end_timestamp = int((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).timestamp())

    embed = discord.Embed(
        title=f"🎁 {prize} 🎁",
        description=(
            f"🔹 **Winners:** {winners}\n"
            f"🔹 **Ends:** <t:{end_timestamp}:R> (<t:{end_timestamp}:F>)\n"
            f"🔹 **Hosted by:** {ctx.author.mention}\n\n"
            f"🔹 **React with {GIVEAWAY_EMOJI_TEXT} to participate!**"
        ),
        color=giveaway_color
    )
    end_time_display = datetime.datetime.fromtimestamp(
        end_timestamp, datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%B %d, %Y %I:%M %p")
    embed.set_footer(text=f"Ends at • {end_time_display}")

    try:
        giveaway_msg = await ctx.send(
            "**New Giveaway**",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True)
        )
        await giveaway_msg.add_reaction("🎉")
    except discord.Forbidden:
        await ctx.send("❌ I need **Send Messages**, **Embed Links**, and **Add Reactions** permission to run giveaways.", delete_after=10)
        return
    except discord.HTTPException as exc:
        print(f"Giveaway creation failed: {exc}")
        await ctx.send("❌ Discord could not create the giveaway. Please try again.", delete_after=10)
        return

    giveaway_records[str(giveaway_msg.id)] = {
        "guild_id": ctx.guild.id,
        "channel_id": ctx.channel.id,
        "message_id": giveaway_msg.id,
        "host_id": ctx.author.id,
        "prize": prize,
        "winners": winners,
        "ended": False,
        "winner_ids": [],
        "end_timestamp": end_timestamp
    }
    save_json_settings(GIVEAWAY_FILE, giveaway_records)

    task = asyncio.create_task(schedule_giveaway(str(giveaway_msg.id), end_timestamp))
    giveaway_tasks[str(giveaway_msg.id)] = task

# 🏁 FORCE-END GIVEAWAY COMMAND
@bot.command(name="gend")
async def end_giveaway(ctx, giveaway_id: str):
    """Immediately end an active giveaway using its giveaway message ID."""
    if ctx.guild is None or not isinstance(ctx.author, discord.Member) or not can_manage_giveaway(ctx.author):
        await ctx.send(
            "❌ You need the **Administrator** permission or the **Staff** role in this server to use `-gend`.",
            delete_after=5
        )
        return
    record = giveaway_records.get(str(giveaway_id))

    if not isinstance(record, dict):
        await ctx.send("❌ Giveaway not found. Use the giveaway message ID with `-gend <giveawayid>`." , delete_after=8)
        return

    if int(record.get("guild_id", 0)) != ctx.guild.id:
        await ctx.send("❌ That giveaway belongs to another server.", delete_after=7)
        return

    if record.get("ended"):
        await ctx.send("❌ This giveaway has already ended.", delete_after=7)
        return

    task = giveaway_tasks.get(str(giveaway_id))
    if task and not task.done():
        task.cancel()

    await finish_giveaway(str(giveaway_id))

    if giveaway_records.get(str(giveaway_id), {}).get("ended"):
        await ctx.send(f"✅ Giveaway `{giveaway_id}` has been force-ended.", delete_after=5)
    else:
        await ctx.send("❌ I couldn't end that giveaway. Check the giveaway message ID and my channel permissions.", delete_after=8)


# 🔄 GIVEAWAY REROLL COMMAND
@bot.command(name="greroll")
async def reroll_giveaway(ctx, giveaway_id: str):
    # Only server Administrators or members with the Staff role can use it.
    if ctx.guild is None or not isinstance(ctx.author, discord.Member) or not can_manage_giveaway(ctx.author):
        await ctx.send(
            "❌ You need the **Administrator** permission or the **Staff** role in this server to use `-greroll`.",
            delete_after=5
        )
        return
    """Reroll a completed giveaway using its giveaway message ID."""
    record = giveaway_records.get(str(giveaway_id))

    if not isinstance(record, dict):
        await ctx.send(
            "❌ Giveaway not found. Use the **giveaway message ID** with `-greroll <giveawayid>`.",
            delete_after=8
        )
        return

    if not record.get("ended"):
        await ctx.send("❌ This giveaway has not ended yet.", delete_after=7)
        return

    try:
        channel = ctx.guild.get_channel(int(record["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(record["channel_id"]))

        giveaway_msg = await channel.fetch_message(int(record["message_id"]))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
        await ctx.send("❌ I couldn't find the giveaway message or channel.", delete_after=8)
        return

    # Get the current 🎉 participants from the original giveaway reaction.
    entry_ids = []
    for reaction in giveaway_msg.reactions:
        if str(reaction.emoji) != GIVEAWAY_EMOJI_TEXT:
            continue
        try:
            async for user in reaction.users():
                if not user.bot and user.id not in entry_ids:
                    entry_ids.append(user.id)
        except (discord.Forbidden, discord.HTTPException):
            pass
        break

    if not entry_ids:
        await ctx.send("❌ Nobody entered this giveaway, so it cannot be rerolled.", delete_after=7)
        return

    previous_winners = {
        int(uid) for uid in record.get("winner_ids", [])
        if str(uid).isdigit()
    }
    eligible_ids = [uid for uid in entry_ids if uid not in previous_winners]

    # If everyone who entered was already a winner, allow a fresh random reroll.
    if not eligible_ids:
        eligible_ids = entry_ids

    new_winner_count = min(int(record.get("winners", 1)), len(eligible_ids))
    new_winners = random.sample(eligible_ids, new_winner_count)
    winner_mentions = ", ".join(f"<@{uid}>" for uid in new_winners)

    record["winner_ids"] = new_winners
    record["rerolled"] = True
    save_json_settings(GIVEAWAY_FILE, giveaway_records)

    # Keep the original giveaway embed/layout and replace only the winner section.
    embed = giveaway_msg.embeds[0].copy() if giveaway_msg.embeds else discord.Embed(
        title=f"🎁 {record.get('prize', 'Giveaway')} 🎁",
        color=discord.Color.from_rgb(46, 204, 196)
    )
    embed.description = (
        f"🔹 **Winners:** {winner_mentions}\n"
        f"🔹 **Hosted by:** <@{record.get('host_id')}>\n\n"
        f"🏆 **Congratulations {winner_mentions}!**\n"
        f"🎉 **You won {record.get('prize', 'the prize')}!**"
    )
    embed.set_footer(text=f"Rerolled • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}")

    await giveaway_msg.edit(
        content="**Giveaway Rerolled**",
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=True)
    )

    # Keep the reroll announcement permanently so the new winner message is not deleted.
    await ctx.send(
        f"🔄 Giveaway rerolled! New winner(s): {winner_mentions}",
        allowed_mentions=discord.AllowedMentions(users=True)
    )

# --- STAFF APPLICATION SYSTEM ---
# Each server has its own staff application setup.
# The Server Owner configures the panel, accepted role, review channel,
# result channel, and up to 13 DM questions.

STAFF_APPLY_QUESTION_COUNT = 13
staff_dm_sessions = {}


def get_staff_questions(guild_id: int) -> list[str]:
    settings = staff_apply_settings.get(str(guild_id), {})
    if not isinstance(settings, dict):
        return []
    questions = settings.get("questions", [])
    if not isinstance(questions, list):
        return []
    return [str(q).strip() for q in questions if str(q).strip()]


def build_staff_apply_panel_embed(guild: discord.Guild, role_name: str | None = None) -> discord.Embed:
    settings = staff_apply_settings.get(str(guild.id), {})

    description = (
        "**We are looking for active, mature and helpful staff.**\n"
        "Please complete the application through DM after pressing the button.\n\n"
        "You will be asked the application questions one at a time in DM.\n\n"
        "Click **Apply Now 📝** below to start your application."
    )

    embed = discord.Embed(
        title="🛡️ Staff Applications",
        description=description,
        color=discord.Color.blurple()
    )

    closed = bool(settings.get("closed", False)) if isinstance(settings, dict) else False
    status = "Closed" if closed else "Open"
    footer_role = role_name or "Staff"
    embed.set_footer(text=f"Staff role: {footer_role} • Applications {status}")
    return embed


def build_staff_dm_question_embed(
    guild_name: str,
    question: str,
    question_number: int,
    total_questions: int,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"🛡️ Staff Application — {guild_name}",
        description=(
            f"**Question {question_number}/{total_questions}**\n\n"
            f"{question}\n\n"
            "Reply to this message with your answer."
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Staff Application • {guild_name}")
    return embed


class StaffApplyPanelView(discord.ui.View):
    def __init__(self, guild_id: int, disabled: bool = False):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.children[0].custom_id = f"staff_apply_open:{guild_id}"
        self.children[0].disabled = disabled

    @discord.ui.button(
        label="Apply Now",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="staff_apply_open"
    )
    async def apply_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await interaction.response.send_message(
                "❌ This panel belongs to another server.",
                ephemeral=True
            )
            return

        settings = staff_apply_settings.get(str(self.guild_id))
        if not isinstance(settings, dict):
            await interaction.response.send_message(
                "❌ Staff applications are not configured.",
                ephemeral=True
            )
            return

        if settings.get("closed", False):
            await interaction.response.send_message(
                "🔒 Staff applications are currently **closed**.",
                ephemeral=True
            )
            return

        questions = get_staff_questions(self.guild_id)
        if not questions:
            await interaction.response.send_message(
                "❌ No staff application questions have been configured.",
                ephemeral=True
            )
            return

        key = (self.guild_id, interaction.user.id)
        if key in staff_dm_sessions:
            await interaction.response.send_message(
                "📩 You already have a staff application in progress. Please continue it in your DMs.",
                ephemeral=True
            )
            return

        staff_dm_sessions[key] = {
            "guild_id": self.guild_id,
            "guild_name": guild.name,
            "applicant_id": interaction.user.id,
            "question_index": 0,
            "questions": questions,
            "answers": []
        }

        try:
            first_question = discord.Embed(
                title=f"🛡️ Staff Application — {guild.name}",
                description=(
                    f"I'll ask you **{len(questions)} question(s)** one at a time.\n"
                    "Reply to each question with your answer.\n"
                    "You have **10 minutes** to complete the application.\n\n"
                    f"**Question 1/{len(questions)}**\n\n"
                    f"{questions[0]}\n\n"
                    "Reply to this message with your answer."
                ),
                color=discord.Color.blurple()
            )
            first_question.set_footer(text=f"Staff Application • {guild.name}")
            await interaction.user.send(embed=first_question)
        except (discord.Forbidden, discord.HTTPException):
            staff_dm_sessions.pop(key, None)
            await interaction.response.send_message(
                "❌ I couldn't DM you. Please enable **Direct Messages** for this server and try again.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "📩 Check your DMs. I've sent you the staff application questions.",
            ephemeral=True
        )


async def send_next_staff_dm_question(user: discord.User, session: dict):
    index = int(session["question_index"])
    questions = session["questions"]
    guild_name = str(session.get("guild_name", "Staff Application"))

    await user.send(
        embed=build_staff_dm_question_embed(
            guild_name, questions[index], index + 1, len(questions)
        )
    )


async def submit_staff_application_from_dm(
    user: discord.User,
    guild: discord.Guild,
    session: dict
):
    settings = staff_apply_settings.get(str(guild.id), {})
    review_channel_id = settings.get("review_channel_id")
    result_channel_id = settings.get("result_channel_id")
    role_id = settings.get("role_id")

    review_channel = guild.get_channel(int(review_channel_id)) if review_channel_id else None
    role = guild.get_role(int(role_id)) if role_id else None

    if not isinstance(review_channel, discord.TextChannel):
        await user.send("❌ The configured staff application review channel no longer exists.")
        return

    if role is None:
        await user.send("❌ The configured staff role no longer exists.")
        return

    questions = session["questions"]
    answers = session["answers"]

    embed = discord.Embed(
        title="📋 New Staff Application",
        description=f"Application submitted by {user.mention}",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="👤 Applicant",
        value=f"{user.mention}\n`{user.id}`",
        inline=False
    )

    for index, (question, answer) in enumerate(zip(questions, answers), start=1):
        # Discord embed field names are limited to 256 characters.
        field_name = f"Q{index}. {question}"[:256]
        embed.add_field(
            name=field_name,
            value=answer[:1024] if answer else "—",
            inline=False
        )

    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"Requested role: {role.name}")

    try:
        sent = await review_channel.send(
            embed=embed,
            view=StaffApplicationView(guild.id, user.id)
        )
    except (discord.Forbidden, discord.HTTPException):
        await user.send(
            "❌ I couldn't submit your application because the configured review channel "
            "is unavailable to the bot."
        )
        return

    staff_apply_settings.setdefault("applications", {})[str(sent.id)] = {
        "guild_id": guild.id,
        "applicant_id": user.id,
        "target_channel_id": review_channel.id,
        "result_channel_id": int(result_channel_id) if result_channel_id else None,
        "role_id": role.id,
        "status": "pending",
        "answers": dict(zip(questions, answers))
    }
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    await user.send(
        f"✅ Your staff application for **{guild.name}** has been submitted successfully!"
    )


class StaffApplicationView(discord.ui.View):
    def __init__(self, guild_id: int, applicant_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.applicant_id = applicant_id
        self.children[0].custom_id = f"staff_apply_accept:{guild_id}:{applicant_id}"
        self.children[1].custom_id = f"staff_apply_reject:{guild_id}:{applicant_id}"

    def _is_staff_reviewer(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.guild is not None
            and interaction.guild.id == self.guild_id
            and interaction.guild.owner_id == interaction.user.id
        )

    @discord.ui.button(
        label="Accept",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="staff_apply_accept"
    )
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_staff_reviewer(interaction):
            await interaction.response.send_message(
                "❌ Only the **Server Owner** can review staff applications.",
                ephemeral=True
            )
            return

        message_key = str(interaction.message.id) if interaction.message else None
        application = (
            staff_apply_settings.get("applications", {}).get(message_key)
            if message_key else None
        )

        if not isinstance(application, dict):
            await interaction.response.send_message(
                "❌ This application record could not be found.",
                ephemeral=True
            )
            return

        if application.get("status") != "pending":
            await interaction.response.send_message(
                "❌ This application has already been reviewed.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        member = guild.get_member(int(application["applicant_id"]))
        role = guild.get_role(int(application["role_id"]))

        if member is None:
            await interaction.response.send_message(
                "❌ The applicant is no longer in the server.",
                ephemeral=True
            )
            return

        if role is None:
            await interaction.response.send_message(
                "❌ The configured staff role no longer exists.",
                ephemeral=True
            )
            return

        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ I need **Manage Roles** permission to accept applications.",
                ephemeral=True
            )
            return

        if role >= me.top_role:
            await interaction.response.send_message(
                "❌ I cannot give that role because it is higher than or equal to my highest role.",
                ephemeral=True
            )
            return

        try:
            await member.add_roles(
                role,
                reason=f"Staff application accepted by {interaction.user}"
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "❌ Discord denied the role change. Check my **Manage Roles** permission and role hierarchy.",
                ephemeral=True
            )
            return

        application["status"] = "accepted"
        application["reviewed_by"] = interaction.user.id
        save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

        result_channel = guild.get_channel(
            int(application.get("result_channel_id") or 0)
        )
        if isinstance(result_channel, discord.TextChannel):
            await result_channel.send(
                f"✅ Staff application from <@{application['applicant_id']}> "
                f"was **accepted** by {interaction.user.mention}. "
                f"Role granted: {role.mention}"
            )

        try:
            await member.send(
                f"🎉 Your staff application in **{guild.name}** has been **accepted**!\n"
                f"You have been given the {role.mention} role."
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

        old_embed = (
            interaction.message.embeds[0]
            if interaction.message and interaction.message.embeds
            else discord.Embed(title="📋 Staff Application")
        )
        old_embed.set_footer(
            text=f"✅ Accepted by {interaction.user.display_name} • Role: {role.name}"
        )
        old_embed.add_field(
            name="📌 Status",
            value=f"✅ Accepted — {role.mention}",
            inline=False
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(embed=old_embed, view=self)

    @discord.ui.button(
        label="Reject",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="staff_apply_reject"
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_staff_reviewer(interaction):
            await interaction.response.send_message(
                "❌ Only the **Server Owner** can review staff applications.",
                ephemeral=True
            )
            return

        message_key = str(interaction.message.id) if interaction.message else None
        application = (
            staff_apply_settings.get("applications", {}).get(message_key)
            if message_key else None
        )

        if not isinstance(application, dict):
            await interaction.response.send_message(
                "❌ This application record could not be found.",
                ephemeral=True
            )
            return

        if application.get("status") != "pending":
            await interaction.response.send_message(
                "❌ This application has already been reviewed.",
                ephemeral=True
            )
            return

        application["status"] = "rejected"
        application["reviewed_by"] = interaction.user.id
        save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

        guild = interaction.guild
        result_channel = guild.get_channel(
            int(application.get("result_channel_id") or 0)
        )
        if isinstance(result_channel, discord.TextChannel):
            await result_channel.send(
                f"❌ Staff application from <@{application['applicant_id']}> "
                f"was **rejected** by {interaction.user.mention}."
            )

        member = guild.get_member(int(application["applicant_id"]))
        if member:
            try:
                await member.send(
                    f"❌ Your staff application in **{guild.name}** has been **rejected**."
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        old_embed = (
            interaction.message.embeds[0]
            if interaction.message and interaction.message.embeds
            else discord.Embed(title="📋 Staff Application")
        )
        old_embed.set_footer(
            text=f"❌ Rejected by {interaction.user.display_name}"
        )
        old_embed.add_field(
            name="📌 Status",
            value="❌ Rejected",
            inline=False
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(embed=old_embed, view=self)


@bot.tree.command(name="staffapply", description="Set up the staff application system.")
@app_commands.describe(
    panel_channel="Channel where the Apply Now panel will be sent.",
    role="Role given automatically when an application is accepted.",
    review_channel="Channel where completed application embeds are sent.",
    result_channel="Channel where accepted/rejected results are sent.",
    question1="Application question 1.",
    question2="Application question 2.",
    question3="Application question 3.",
    question4="Application question 4.",
    question5="Application question 5.",
    question6="Application question 6.",
    question7="Application question 7.",
    question8="Application question 8.",
    question9="Application question 9.",
    question10="Application question 10.",
    question11="Application question 11.",
    question12="Application question 12.",
    question13="Application question 13."
)
async def staffapply_slash(
    interaction: discord.Interaction,
    panel_channel: discord.TextChannel,
    role: discord.Role,
    review_channel: discord.TextChannel,
    result_channel: discord.TextChannel,
    question1: str | None = None,
    question2: str | None = None,
    question3: str | None = None,
    question4: str | None = None,
    question5: str | None = None,
    question6: str | None = None,
    question7: str | None = None,
    question8: str | None = None,
    question9: str | None = None,
    question10: str | None = None,
    question11: str | None = None,
    question12: str | None = None,
    question13: str | None = None
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    guild = interaction.guild

    # Server Owner only.
    if guild.owner_id != interaction.user.id:
        await interaction.response.send_message(
            "❌ Only the **Server Owner** can use `/staffapply`.",
            ephemeral=True
        )
        return

    me = guild.me
    if me is None:
        await interaction.response.send_message(
            "❌ I could not find my server member information.",
            ephemeral=True
        )
        return

    if not me.guild_permissions.send_messages or not me.guild_permissions.embed_links:
        await interaction.response.send_message(
            "❌ I need **Send Messages** and **Embed Links** permissions.",
            ephemeral=True
        )
        return

    if not me.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "⚠️ I need **Manage Roles** permission to give the staff role when an application is accepted.",
            ephemeral=True
        )
        return

    if role >= me.top_role:
        await interaction.response.send_message(
            "❌ I cannot give that role because it is higher than or equal to my highest role.",
            ephemeral=True
        )
        return

    questions = [
        q.strip() for q in (
            question1, question2, question3, question4, question5,
            question6, question7, question8, question9, question10,
            question11, question12, question13
        )
        if q and q.strip()
    ]

    if not questions:
        await interaction.response.send_message(
            "❌ Please provide at least **Question 1**.",
            ephemeral=True
        )
        return

    guild_key = str(guild.id)
    old = staff_apply_settings.get(guild_key, {})
    old_applications = old.get("applications", {}) if isinstance(old, dict) else {}

    staff_apply_settings[guild_key] = {
        "panel_channel_id": panel_channel.id,
        "application_channel_id": review_channel.id,
        "review_channel_id": review_channel.id,
        "result_channel_id": result_channel.id,
        "role_id": role.id,
        "closed": False,
        "panel_message_id": None,
        "questions": questions,
        "applications": old_applications if isinstance(old_applications, dict) else {}
    }
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    embed = build_staff_apply_panel_embed(guild, role.name)

    try:
        panel_message = await panel_channel.send(
            embed=embed,
            view=StaffApplyPanelView(guild.id, disabled=False)
        )
    except (discord.Forbidden, discord.HTTPException):
        await interaction.response.send_message(
            "❌ I could not send the staff application panel. Check my permissions in that channel.",
            ephemeral=True
        )
        return

    staff_apply_settings[guild_key]["panel_message_id"] = panel_message.id
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    await interaction.response.send_message(
        f"✅ Staff applications configured.\n"
        f"📝 Panel: {panel_channel.mention}\n"
        f"📋 Completed applications: {review_channel.mention}\n"
        f"📢 Results: {result_channel.mention}\n"
        f"🎭 Accepted role: {role.mention}\n"
        f"❓ Questions: **{len(questions)}**",
        ephemeral=True
    )


@bot.command(name="closed")
async def close_staff_apply(ctx):
    if ctx.guild is None:
        await ctx.send(
            "❌ This command can only be used in a server.",
            delete_after=5
        )
        return

    if ctx.guild.owner_id != ctx.author.id:
        await ctx.send(
            "❌ Only the **Server Owner** can use this command.",
            delete_after=7
        )
        return

    settings = staff_apply_settings.get(str(ctx.guild.id))
    if not isinstance(settings, dict) or not settings.get("panel_message_id"):
        await ctx.send(
            "❌ Staff applications are not configured. Use `/staffapply` first.",
            delete_after=7
        )
        return

    settings["closed"] = True
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    try:
        channel = ctx.guild.get_channel(int(
            settings.get("panel_channel_id") or settings.get("application_channel_id")
        ))
        message = (
            await channel.fetch_message(int(settings["panel_message_id"]))
            if channel else None
        )
        if message:
            role = ctx.guild.get_role(int(settings.get("role_id", 0)))
            await message.edit(
                embed=build_staff_apply_panel_embed(
                    ctx.guild,
                    role.name if role else "Staff"
                ),
                view=StaffApplyPanelView(ctx.guild.id, disabled=True)
            )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
        pass

    await ctx.send(
        "🔒 Staff applications are now **closed**. The Apply Now button is disabled.",
        delete_after=7
    )


@bot.command(name="open")
async def open_staff_apply(ctx):
    if ctx.guild is None:
        await ctx.send(
            "❌ This command can only be used in a server.",
            delete_after=5
        )
        return

    if ctx.guild.owner_id != ctx.author.id:
        await ctx.send(
            "❌ Only the **Server Owner** can use this command.",
            delete_after=7
        )
        return

    settings = staff_apply_settings.get(str(ctx.guild.id))
    if not isinstance(settings, dict) or not settings.get("panel_message_id"):
        await ctx.send(
            "❌ Staff applications are not configured. Use `/staffapply` first.",
            delete_after=7
        )
        return

    settings["closed"] = False
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    try:
        channel = ctx.guild.get_channel(int(
            settings.get("panel_channel_id") or settings.get("application_channel_id")
        ))
        message = (
            await channel.fetch_message(int(settings["panel_message_id"]))
            if channel else None
        )
        if message:
            role = ctx.guild.get_role(int(settings.get("role_id", 0)))
            await message.edit(
                embed=build_staff_apply_panel_embed(
                    ctx.guild,
                    role.name if role else "Staff"
                ),
                view=StaffApplyPanelView(ctx.guild.id, disabled=False)
            )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
        pass

    await ctx.send(
        "🔓 Staff applications are now **open**. The Apply Now button can be pressed again.",
        delete_after=7
    )

# --- 6. ERROR HANDLING FOR PREFIX COMMANDS ---
@delete_ticket.error
@purge_messages.error
@reroll_giveaway.error
@start_giveaway.error
@vouch_panel.error
@ban_member_prefix.error
@lock_channel_prefix.error
@unlock_channel_prefix.error
@automode.error
async def command_errors(ctx, error):
    is_moderation_command = ctx.command is not None and ctx.command.name in {"ban", "lock", "unlock"}
    if isinstance(error, commands.MissingPermissions):
        message = "❌ You do not have the required permissions to run this command."
        if is_moderation_command:
            await ctx.send(embed=discord.Embed(title="Command Error", description=message, color=discord.Color.red()), delete_after=5)
        else:
            await ctx.send(message, delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        if is_moderation_command:
            usage = {"ban": "`-ban <@user> <reason>`", "lock": "`-lock <#channel>`", "unlock": "`-unlock <#channel>`"}.get(ctx.command.name, "")
            await ctx.send(embed=discord.Embed(title="Command Error", description=f"❌ Missing required argument.\nUsage: {usage}", color=discord.Color.red()), delete_after=10)
        else:
            await ctx.send(f"❌ Missing fields! Formatting:\n`-purge <amount>`\n`-gstart <duration> <winners> <prize>`\n`/staffapply <panel> <role> <review> <results> <question1>...<question13>`\n`-closed` / `-open` — Close or reopen staff applications", delete_after=10)
    elif isinstance(error, commands.BadArgument):
        if is_moderation_command:
            await ctx.send(embed=discord.Embed(title="Command Error", description="❌ Invalid member or channel provided.", color=discord.Color.red()), delete_after=5)
        else:
            await ctx.send("❌ Invalid argument format provided. Check your numbers/letters.", delete_after=5)

@bot.event
async def on_command_error(ctx, error):
    # Local command error handlers above have already handled their errors.
    if hasattr(ctx.command, "on_error") and ctx.command.on_error is not None:
        return
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You do not have the required permissions to run this command.", delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument. Use `-help` to see the command format.", delete_after=7)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument. Check the command format and try again.", delete_after=7)
    elif isinstance(error, commands.CommandInvokeError):
        print(f"Unhandled error in {getattr(ctx.command, 'qualified_name', 'unknown')}: {error.original}")
        await ctx.send("❌ The command encountered an unexpected error. Check the bot console.", delete_after=8)
    else:
        print(f"Unhandled command error in {getattr(ctx.command, 'qualified_name', 'unknown')}: {error}")


# --- 7. INVITE TRACKING ---

@bot.event
async def on_message(message):
    # Handle staff application answers in DM.
    if message.guild is None:
        if message.author.bot:
            return

        matching_keys = [
            key for key in staff_dm_sessions
            if key[1] == message.author.id
        ]

        if matching_keys:
            key = matching_keys[0]
            session = staff_dm_sessions.get(key)

            if session:
                session["answers"].append(message.content.strip())
                session["question_index"] += 1

                if session["question_index"] < len(session["questions"]):
                    try:
                        await send_next_staff_dm_question(message.author, session)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    return

                staff_dm_sessions.pop(key, None)

                guild = bot.get_guild(int(session["guild_id"]))
                if guild is None:
                    try:
                        await message.author.send(
                            "❌ I couldn't find the server for this application."
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    return

                await submit_staff_application_from_dm(
                    message.author,
                    guild,
                    session
                )
                return

        await bot.process_commands(message)
        return

    # --- SUGGESTIONS AUTO-EMBED ---
    # Ignore bot messages and convert member messages in the configured channel.
    suggestion_config = suggestions_settings.get(str(message.guild.id))
    if not message.author.bot and isinstance(suggestion_config, dict):
        configured_channel_id = suggestion_config.get("channel_id")
        if configured_channel_id and message.channel.id == int(configured_channel_id):
            image_url = suggestion_config.get("image_url")
            # If the message contains an image attachment, use it automatically as the large embed image.
            if not image_url:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        image_url = attachment.url
                        break

            # Members can submit only one suggestion every 24 hours.
            # The cooldown is stored per server/member so it survives restarts
            # and does not affect the same member in another server.
            now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
            cooldown_key = f"suggestion_cooldown:{message.guild.id}:{message.author.id}"
            last_submission = suggestions_settings.get(cooldown_key)
            try:
                last_submission = float(last_submission)
            except (TypeError, ValueError):
                last_submission = None

            cooldown_seconds = 24 * 60 * 60
            if last_submission is not None and now_ts - last_submission < cooldown_seconds:
                remaining_seconds = max(0, int(cooldown_seconds - (now_ts - last_submission)))
                remaining_hours = remaining_seconds // 3600
                remaining_minutes = (remaining_seconds % 3600) // 60
                remaining_text = f"{remaining_hours}h {remaining_minutes}m"

                # Delete the attempted suggestion, then show the warning as an
                # embed. The member is timed out for 1 hour for bypassing the
                # 24-hour suggestion cooldown.
                try:
                    await message.delete()
                except (discord.Forbidden, discord.HTTPException):
                    pass

                timed_out = False
                member = message.author
                me = message.guild.me
                if (
                    isinstance(member, discord.Member)
                    and me is not None
                    and me.guild_permissions.moderate_members
                    and member.id != message.guild.owner_id
                    and member.top_role < me.top_role
                ):
                    try:
                        await member.timeout(
                            datetime.timedelta(hours=1),
                            reason="Suggestions: attempted to submit another suggestion during the 24-hour cooldown"
                        )
                        timed_out = True
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                warning_embed = discord.Embed(
                    title="⚠️ Suggestion Cooldown",
                    description=(
                        f"{message.author.mention}, you already submitted a suggestion within the last **24 hours**.\n\n"
                        f"⏳ **Time remaining:** {remaining_text}\n"
                        f"🔒 **Cooldown:** 24 hours\n"
                        f"{'⏱️ **Timeout:** 1 hour' if timed_out else '⚠️ **Timeout:** Could not be applied because I do not have permission to timeout you.'}"
                    ),
                    color=discord.Color.orange()
                )


                # Keep the cooldown warning private. Discord cannot make a normal
                # on_message response ephemeral, so DM the warning only to the
                # member who attempted another suggestion during the cooldown.
                try:
                    await message.author.send(
                        embed=warning_embed,
                        allowed_mentions=discord.AllowedMentions(users=True)
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"Could not DM suggestion cooldown warning to {message.author} in {message.guild.name}: {exc}")
                return


            embed = build_suggestion_embed(message, image_url)
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

            try:
                sent = await message.channel.send(
                    embed=embed,
                    view=SuggestionView(message.id),
                    allowed_mentions=discord.AllowedMentions(users=True)
                )
                suggestions_settings[f"suggestion:{message.id}"] = {
                    "guild_id": message.guild.id,
                    "channel_id": message.channel.id,
                    "message_id": sent.id,
                    "source_message_id": message.id,
                    "author_id": message.author.id,
                    "yes": 0,
                    "no": 0,
                    "voters": {},
                    "approved": False,
                    "approved_by": None,
                    "approved_at": None
                }
                # Start the 24-hour cooldown only after the suggestion was
                # successfully converted into the bot's suggestion embed.
                suggestions_settings[cooldown_key] = now_ts
                save_json_settings(SUGGESTIONS_FILE, suggestions_settings)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"Could not create suggestion embed in {message.guild.name}: {exc}")
            return

    # --- AUTOMODE SECURITY ---
    # Anti-Link deletes invite links immediately and sends a DM warning.
    if await handle_automode_antilink(message):
        await bot.process_commands(message)
        return
    # Anti-Spam: 5 messages within 1 second -> 2 hour timeout.
    if await handle_automode_antispam(message):
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        await bot.process_commands(message)
        return

    configured_channel = honeypot_settings.get(str(message.guild.id))
    if configured_channel is not None and message.channel.id == int(configured_channel):
        member = message.author

        # Remove the trigger message immediately.
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Permanently ban the member who triggers the honeypot.
        if message.guild.me and message.guild.me.guild_permissions.ban_members:
            if member.id != message.guild.owner_id and member.top_role < message.guild.me.top_role:
                try:
                    await message.guild.ban(
                        member,
                        delete_message_seconds=86400,
                        reason="Honeypot triggered - automatic ban"
                    )

                    # Keep a persistent ban count for the setup embed/statistics.
                    count_key = str(message.guild.id)
                    current_count = honeypot_settings.get(f"{count_key}:count", 0)
                    try:
                        current_count = int(current_count)
                    except (TypeError, ValueError):
                        current_count = 0
                    new_count = current_count + 1
                    honeypot_settings[f"{count_key}:count"] = new_count
                    save_json_settings(HONEYPOT_FILE, honeypot_settings)

                    # Update the original honeypot embed so the Ban counter
                    # changes immediately (for example: 0 -> 1 -> 2).
                    panel_message_id = honeypot_settings.get(f"{count_key}:message_id")
                    if panel_message_id:
                        try:
                            panel_message = await message.channel.fetch_message(int(panel_message_id))
                            if panel_message.embeds:
                                updated_embed = panel_message.embeds[0].copy()
                                for field_index, field in enumerate(updated_embed.fields):
                                    if field.name == "Ban":
                                        updated_embed.set_field_at(
                                            field_index,
                                            name="Ban",
                                            value=f"`{new_count}`",
                                            inline=field.inline
                                        )
                                        break
                                await panel_message.edit(embed=updated_embed)
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                            pass
                except discord.Forbidden:
                    print(f"Honeypot could not ban {member} in {message.guild.name}: permission or role hierarchy issue.")
                except discord.HTTPException as e:
                    print(f"Honeypot ban failed in {message.guild.name}: {e}")
        return

    # Keep all existing prefix commands working in normal channels.
    await bot.process_commands(message)


@bot.event
async def on_member_join(member):
    guild = member.guild

    # --- AUTOMODE ANTI-BOT ---
    if await handle_automode_antibot(member):
        return

    # --- AUTO ROLE ---
    # Give every new member the configured role before the existing welcome/invite logic.
    autorole_id = autorole_settings.get(str(guild.id))
    if autorole_id:
        autorole = guild.get_role(int(autorole_id))
        me = guild.me
        if autorole and me and me.guild_permissions.manage_roles and autorole < me.top_role:
            try:
                await member.add_roles(autorole, reason="Automatic new-member role")
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Could not give autorole in {guild.name}: {e}")

    # --- INVITE TRACKING ---
    # Resolve the used invite before rendering the welcome message so invite
    # placeholders such as {inviter}, {invite_code}, and {invite_total} work.
    used_invite = None
    try:
        current_invites = await guild.invites()
    except discord.Forbidden:
        current_invites = []

    cached_guild_invites = invite_cache.get(guild.id, {})
    for invite in current_invites:
        if invite.code in cached_guild_invites and invite.uses > cached_guild_invites[invite.code]:
            used_invite = invite
            break
        elif invite.uses > 0 and invite.code not in cached_guild_invites:
            used_invite = invite
            break

    # Refresh the cache for THIS server only.
    if current_invites:
        invite_cache[guild.id] = {inv.code: inv.uses for inv in current_invites}

    # Handle rejoining members.
    # Invite statistics MUST be updated before the welcome message is rendered.
    # Otherwise {invite_total}, {invite_regular}, etc. show the inviter's
    # previous total instead of the total after this member joins.
    history_key = _member_key(guild.id, member.id)
    if history_key in history_db:
        if used_invite and used_invite.inviter:
            inviter = used_invite.inviter
            inviter_stats = get_user_stats(guild.id, inviter.id)

            if member_inviter_map.get(_member_key(guild.id, member.id)) == inviter.id:
                if inviter_stats["leaves"] > 0:
                    inviter_stats["leaves"] -= 1
                save_invite_data()
        return

    # Handle brand-new members.
    if used_invite and used_invite.inviter:
        inviter = used_invite.inviter

        # Prevent self-invites.
        if inviter.id == member.id:
            history_db.add(_member_key(guild.id, member.id))
            save_invite_data()
            return

        inviter_stats = get_user_stats(guild.id, inviter.id)
        account_age = datetime.datetime.now(datetime.timezone.utc) - member.created_at

        if account_age.days < 1:
            inviter_stats["fake"] += 1
        else:
            inviter_stats["regular"] += 1

        member_inviter_map[_member_key(guild.id, member.id)] = inviter.id
        history_db.add(_member_key(guild.id, member.id))
        save_invite_data()
    else:
        history_db.add(_member_key(guild.id, member.id))
        save_invite_data()

    # --- CUSTOM WELCOME GREET SYSTEM (per server) ---
    # Render this AFTER invite tracking so {invite_total} and the other
    # invite placeholders contain the newly updated inviter statistics.
    guild_welcome = get_welcome_settings(guild.id)
    target_channel_id = guild_welcome["channel_id"]
    if target_channel_id:
        greet_channel = guild.get_channel(target_channel_id)
        if greet_channel:
            raw_msg = guild_welcome["message"]
            formatted_msg = render_variables(raw_msg, member, greet_channel, invite=used_invite)
            try:
                delete_after = int(guild_welcome.get("delete_after", 0) or 0)
                await greet_channel.send(
                    content=formatted_msg,
                    delete_after=delete_after if delete_after > 0 else None
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"Could not send welcome greeting in {guild.name}: {exc}")


@bot.event
async def on_guild_channel_delete(channel):
    await handle_automode_antinuke(channel.guild, discord.AuditLogAction.channel_delete, channel.id)


@bot.event
async def on_guild_role_delete(role):
    await handle_automode_antinuke(role.guild, discord.AuditLogAction.role_delete, role.id)


@bot.event
async def on_member_ban(guild, user):
    await handle_automode_antinuke(guild, discord.AuditLogAction.ban, user.id)


@bot.event
async def on_webhooks_update(channel):
    await handle_automode_antiwebhook(channel.guild)


@bot.event
async def on_member_remove(member):
    guild = member.guild

    try:
        current_invites = await guild.invites()
        invite_cache[guild.id] = {inv.code: inv.uses for inv in current_invites}
    except discord.Forbidden:
        pass

    member_key = _member_key(guild.id, member.id)
    if member_key in member_inviter_map:
        inviter_id = member_inviter_map[member_key]
        inviter_stats = get_user_stats(guild.id, inviter_id)
        inviter_stats["leaves"] += 1
        save_invite_data()


@bot.command(name="mc", aliases=["membercount"])
async def member_count(ctx):
    guild = ctx.guild
    if guild is None:
        return

    total_members = guild.member_count or len(guild.members)
    humans = sum(1 for member in guild.members if not member.bot)
    bots = sum(1 for member in guild.members if member.bot)

    embed = discord.Embed(
        title="Member Count",
        color=discord.Color.blurple()
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="👥 Members", value=f"`{total_members}`", inline=True)
    embed.add_field(name="👤 Humans", value=f"`{humans}`", inline=True)
    embed.add_field(name="🤖 Bots", value=f"`{bots}`", inline=True)
    embed.set_footer(text=f"{guild.name}")

    await ctx.send(embed=embed)


@bot.command(name="invites")
async def invites(ctx, member: discord.Member = None):
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used in a server.", delete_after=5)
        return
    target = member or ctx.author
    stats = get_user_stats(ctx.guild.id, target.id)

    real_invites = max(
        0,
        stats["regular"] + stats["bonus"] - stats["leaves"] - stats["fake"]
    )

    embed = discord.Embed(
        title=f"Invite Tracking — {target.display_name}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="✨ Real", value=f"`{real_invites}`", inline=True)
    embed.add_field(name="📩 Regular", value=f"`{stats['regular']}`", inline=True)
    embed.add_field(name="❌ Leaves", value=f"`{stats['leaves']}`", inline=True)
    embed.add_field(name="⚠️ Fake", value=f"`{stats['fake']}`", inline=True)
    embed.add_field(name="🎁 Bonus", value=f"`{stats['bonus']}`", inline=True)

    await ctx.send(embed=embed)


@bot.command(name="addbonus")
@commands.has_permissions(administrator=True)
async def add_bonus(ctx, member: discord.Member, amount: int):
    if amount == 0:
        await ctx.send("❌ Bonus amount cannot be 0.", delete_after=5)
        return
    stats = get_user_stats(ctx.guild.id, member.id)
    stats["bonus"] += amount
    save_invite_data()
    await ctx.send(
        f"✅ Modified bonus invites for **{member.display_name}** by `{amount}`."
    )


@bot.command(name="resetinvites")
@commands.has_permissions(administrator=True)
async def reset_invites(ctx, member: discord.Member = None):
    global invite_db, member_inviter_map, history_db
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used in a server.", delete_after=5)
        return

    if member:
        invite_key = _invite_key(ctx.guild.id, member.id)
        invite_db[invite_key] = {"regular": 0, "leaves": 0, "fake": 0, "bonus": 0}
        member_key = _member_key(ctx.guild.id, member.id)
        member_inviter_map.pop(member_key, None)
        history_db.discard(member_key)
        save_invite_data()
        await ctx.send(f"✅ Successfully reset invite data for **{member.display_name}**.")
    else:
        guild_prefix = f"{ctx.guild.id}:"
        invite_db = {k: v for k, v in invite_db.items() if not str(k).startswith(guild_prefix)}
        member_inviter_map = {k: v for k, v in member_inviter_map.items() if not str(k).startswith(guild_prefix)}
        history_db = {k for k in history_db if not str(k).startswith(guild_prefix)}
        save_invite_data()
        await ctx.send("✅ Successfully reset all invite data and history.")


# --- INVITE COMMAND ERROR HANDLING ---
@invites.error
@add_bonus.error
@reset_invites.error
async def invite_command_errors(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ You do not have the required permissions to run this command.",
            delete_after=5
        )
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            "❌ Missing fields. Examples: `-invites [@member]`, "
            "`-addbonus @member <amount>`, `-resetinvites [@member]`",
            delete_after=10
        )
    elif isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ Invalid member or number. Please check your command format.",
            delete_after=5
        )

# --- 8. SLASH COMMAND DEFINITION ---
@bot.tree.command(name="customwelcome", description="Set the custom welcome message.")
@app_commands.describe(message="Use -greetvariables to see all supported variables.")
@app_commands.checks.has_permissions(administrator=True)
async def customwelcome(interaction: discord.Interaction, message: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    guild_welcome = get_welcome_settings(interaction.guild.id)
    if not guild_welcome["channel_id"]:
        await interaction.response.send_message("❌ Please use `/channel_set` first.", ephemeral=True)
        return
    guild_welcome["message"] = message
    save_json_settings(WELCOME_FILE, welcome_settings)
    await interaction.response.send_message("✅ Custom welcome message updated and saved permanently!", ephemeral=True)


@bot.tree.command(name="welcome-delafter", description="Configure an automatic welcome message that deletes after a set time.")
@app_commands.describe(
    message="Welcome message. Supports /greetvariables placeholders.",
    deleteafter="How long the welcome message should remain (examples: 10s, 5m, 1h)."
)
@app_commands.checks.has_permissions(administrator=True)
async def welcome_delafter(interaction: discord.Interaction, message: str, deleteafter: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    seconds = convert_time(deleteafter)
    if seconds <= 0:
        await interaction.response.send_message(
            "❌ Invalid delete time. Use a format such as `10s`, `5m`, `1h`, or `1d`.",
            ephemeral=True
        )
        return

    guild_welcome = get_welcome_settings(interaction.guild.id)
    guild_welcome["message"] = message
    guild_welcome["channel_id"] = interaction.channel_id
    guild_welcome["delete_after"] = seconds
    save_json_settings(WELCOME_FILE, welcome_settings)

    await interaction.response.send_message(
        f"✅ Welcome message configured for {interaction.channel.mention}. "
        f"It will automatically delete after `{deleteafter}`.",
        ephemeral=True
    )


@bot.tree.command(name="testgreet", description="Test the custom welcome greeting.")
@app_commands.checks.has_permissions(administrator=True)
async def testgreet(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    guild_welcome = get_welcome_settings(interaction.guild.id)
    channel_id = guild_welcome["channel_id"]
    if not channel_id:
        await interaction.response.send_message("❌ Set a welcome channel first with `/channel_set`.", ephemeral=True)
        return
    channel = interaction.guild.get_channel(channel_id)
    if channel is None:
        await interaction.response.send_message("❌ Configured welcome channel was not found.", ephemeral=True)
        return
    raw_msg = guild_welcome["message"]
    show_avatar = "{avatar}" in raw_msg
    msg = render_variables(
        raw_msg,
        interaction.user,
        channel
    )
    embed = discord.Embed(title="✨ New Member Joined!", description=msg, color=discord.Color.green())
    if show_avatar:
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Test greet sent in {channel.mention}.", ephemeral=True)


@bot.tree.command(name="channel_set", description="Set the welcome greeting channel.")
@app_commands.describe(channel="The channel where welcome greetings will be sent.")
@app_commands.checks.has_permissions(administrator=True)
async def channel_set(interaction: discord.Interaction, channel: discord.TextChannel):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    guild_welcome = get_welcome_settings(interaction.guild.id)
    guild_welcome["channel_id"] = channel.id
    save_json_settings(WELCOME_FILE, welcome_settings)
    await interaction.response.send_message(f"✅ Welcome greet channel set to {channel.mention} and saved permanently.", ephemeral=True)


@bot.tree.command(name="announce", description="Send an announcement embed and DM all server members.")
@app_commands.describe(message="The announcement message.")
@app_commands.checks.has_permissions(administrator=True)
async def announce(interaction: discord.Interaction, message: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    embed = discord.Embed(title="📢 Announcement", description=message, color=discord.Color.blue())
    embed.set_footer(text=f"Announced by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    sent = failed = 0
    for member in interaction.guild.members:
        if member.bot:
            continue
        try:
            await member.send(embed=embed)
            sent += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
        await asyncio.sleep(0.15)
    await interaction.followup.send(f"✅ Announcement DMs: `{sent}` sent, `{failed}` failed.", ephemeral=True)


@bot.tree.command(name="reactionrole", description="Create a button that gives/removes a role when clicked.")
@app_commands.describe(role="The role members can get from the button.")
@app_commands.checks.has_permissions(administrator=True)
async def reactionrole(interaction: discord.Interaction, role: discord.Role):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    me = interaction.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ I need **Manage Roles** permission to create a reaction role.", ephemeral=True)
        return

    if role.is_default():
        await interaction.response.send_message("❌ You cannot use the @everyone role.", ephemeral=True)
        return

    if role >= me.top_role:
        await interaction.response.send_message(
            "❌ I cannot give this role because it is higher than or equal to my highest role.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🎭 Reaction Role",
        description=f"Click the **🎟️ Get Role** button below to receive {role.mention}.\n\nClick it again to remove the role.",
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Reaction Role System")

    await interaction.response.send_message(embed=embed, view=ReactionRoleView(role.id))

    # Store every reaction-role panel for this guild so all buttons survive restarts.
    guild_key = str(interaction.guild.id)
    configured_roles = reaction_role_settings.get(guild_key, [])
    if not isinstance(configured_roles, list):
        configured_roles = [configured_roles]
    if role.id not in configured_roles:
        configured_roles.append(role.id)
    reaction_role_settings[guild_key] = configured_roles
    save_json_settings(REACTION_ROLE_FILE, reaction_role_settings)


@bot.tree.command(name="autorole", description="Set the role automatically given to new members.")
@app_commands.describe(role="The role every new member should receive.")
@app_commands.checks.has_permissions(administrator=True)
async def autorole(interaction: discord.Interaction, role: discord.Role):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    me = interaction.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ I need **Manage Roles** permission to use autorole.", ephemeral=True)
        return

    if role.is_default():
        await interaction.response.send_message("❌ You cannot use the @everyone role.", ephemeral=True)
        return

    if role >= me.top_role:
        await interaction.response.send_message(
            "❌ I cannot automatically give this role because it is higher than or equal to my highest role.",
            ephemeral=True
        )
        return

    autorole_settings[str(interaction.guild.id)] = role.id
    save_json_settings(AUTOROLE_FILE, autorole_settings)
    await interaction.response.send_message(
        f"✅ Autorole configured! New members will automatically receive {role.mention}.",
        ephemeral=True
    )


@bot.tree.command(name="honeypot", description="Set the current channel as a honeypot spam trap.")
@app_commands.checks.has_permissions(administrator=True)
async def honeypot(interaction: discord.Interaction):
    """Configure the current channel as a persistent honeypot."""
    try:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "❌ This command can only be used in a server channel.",
                ephemeral=True
            )
            return

        guild = interaction.guild
        channel = interaction.channel
        me = guild.me

        if me is None:
            await interaction.response.send_message(
                "❌ I could not verify my server permissions. Please try again.",
                ephemeral=True
            )
            return

        if not me.guild_permissions.ban_members:
            await interaction.response.send_message(
                "❌ I need **Ban Members** permission to run the honeypot ban.",
                ephemeral=True
            )
            return

        channel_perms = channel.permissions_for(me)
        missing = []
        if not channel_perms.view_channel:
            missing.append("View Channel")
        if not channel_perms.send_messages:
            missing.append("Send Messages")
        if not channel_perms.embed_links:
            missing.append("Embed Links")
        if not channel_perms.manage_messages:
            missing.append("Manage Messages")

        if missing:
            await interaction.response.send_message(
                "❌ I cannot set up the honeypot here. Missing: "
                + ", ".join(f"**{name}**" for name in missing) + ".",
                ephemeral=True
            )
            return

        count_key = str(guild.id)
        try:
            honeypot_count = int(honeypot_settings.get(f"{count_key}:count", 0))
        except (TypeError, ValueError):
            honeypot_count = 0

        embed = discord.Embed(
            title="DO NOT SEND MESSAGES IN THIS CHANNEL",
            description=(
                "This channel is used to catch spam bots. Any messages sent here "
                "will result in a **permanent ban**."
            ),
            color=discord.Color.red()
        )
        embed.add_field(name="Ban", value=f"`{honeypot_count}`", inline=False)
        embed.set_footer(
            text=f"Honeypot • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}"
        )

        # Acknowledge the interaction first. This prevents Discord from showing
        # "The application did not respond" while the panel is being created.
        await interaction.response.defer(ephemeral=True)

        # Send the actual honeypot panel directly to the channel and use the
        # returned Message object. This avoids interaction.original_response(),
        # which was the failing step in the previous implementation.
        panel_message = await channel.send(embed=embed)

        # Store the configuration and panel message ID per guild so the
        # honeypot and counter continue working after restarts.
        honeypot_settings[str(guild.id)] = channel.id
        honeypot_settings[f"{guild.id}:message_id"] = panel_message.id
        honeypot_settings[f"{guild.id}:count"] = honeypot_count
        save_json_settings(HONEYPOT_FILE, honeypot_settings)

        await interaction.edit_original_response(
            content=f"✅ Honeypot enabled in {channel.mention}."
        )

    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"Honeypot setup error in guild {getattr(interaction.guild, 'id', 'unknown')}: {exc}")
        message = "❌ I couldn't create the honeypot panel. Please check my channel permissions and try again."
        if interaction.response.is_done():
            try:
                await interaction.edit_original_response(content=message)
            except (discord.NotFound, discord.HTTPException):
                pass
        else:
            try:
                await interaction.response.send_message(message, ephemeral=True)
            except (discord.NotFound, discord.HTTPException):
                pass
    except Exception as exc:
        print(f"Unexpected honeypot setup error in guild {getattr(interaction.guild, 'id', 'unknown')}: {exc}")
        message = "❌ The honeypot could not be set up. Check the bot console/logs for the exact error."
        if interaction.response.is_done():
            try:
                await interaction.edit_original_response(content=message)
            except (discord.NotFound, discord.HTTPException):
                pass
        else:
            try:
                await interaction.response.send_message(message, ephemeral=True)
            except (discord.NotFound, discord.HTTPException):
                pass


@lock_channel_slash.error
@unlock_channel_slash.error
async def moderation_slash_command_errors(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        description = "❌ You do not have the required permissions to use this command."
    elif isinstance(error, app_commands.TransformerError):
        description = "❌ Invalid channel or member provided."
    else:
        description = "❌ An error occurred while running this command."
    embed = discord.Embed(title="Command Error", description=description, color=discord.Color.red())
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


@customwelcome.error
@testgreet.error
@channel_set.error
@announce.error
@reactionrole.error
@autorole.error
@honeypot.error
@suggestions.error
async def welcome_slash_command_errors(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ You need Administrator permission to use this command."
    else:
        message = "❌ An error occurred while running the command."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="ticket-setup", description="Deploy the interactive support ticket panel.")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    """Set up the ticket system without requiring a category argument."""
    # Respond immediately. This avoids Discord leaving the command stuck on
    # "Kyrie is thinking..." while Discord is waiting for the interaction ACK.
    try:
        await interaction.response.send_message(
            "⏳ Setting up the ticket system...",
            ephemeral=True
        )

        guild = interaction.guild
        channel = interaction.channel

        if guild is None:
            await interaction.edit_original_response(
                content="❌ This command can only be used inside a server."
            )
            return

        if channel is None or not hasattr(channel, "send"):
            await interaction.edit_original_response(
                content="❌ I cannot post the ticket panel in this channel."
            )
            return

        me = guild.me
        if me is None:
            await interaction.edit_original_response(
                content="❌ I could not verify my bot permissions. Please try again."
            )
            return

        # Check the permissions in the channel where /ticket-setup is used.
        panel_perms = channel.permissions_for(me)
        missing = []
        if not panel_perms.view_channel:
            missing.append("View Channel")
        if not panel_perms.send_messages:
            missing.append("Send Messages")
        if not panel_perms.embed_links:
            missing.append("Embed Links")

        if missing:
            await interaction.edit_original_response(
                content="❌ I cannot post the ticket panel here. Missing: "
                        + ", ".join(f"**{name}**" for name in missing) + "."
            )
            return

        # /ticket-setup no longer needs <category>.
        # Use an existing category named "Tickets", or create it automatically.
        category = discord.utils.find(
            lambda c: isinstance(c, discord.CategoryChannel)
            and c.name.lower() == "tickets",
            guild.categories
        )

        if category is None:
            if not me.guild_permissions.manage_channels:
                await interaction.edit_original_response(
                    content=(
                        "❌ I need **Manage Channels** permission to automatically "
                        "create the **Tickets** category."
                    )
                )
                return

            category = await guild.create_category(
                "Tickets",
                reason="Automatic ticket system setup"
            )

        # Save the automatically selected category for this server.
        ticket_categories[str(guild.id)] = category.id
        save_json_settings(TICKET_FILE, ticket_categories)

        embed = discord.Embed(
            title="📩 Support Help Desk",
            description=(
                "Need assistance? Click the green button below to open a private "
                "support ticket window with our server staff team."
            ),
            color=discord.Color.green()
        )
        # KEEPING THE TICKET SYSTEM FOOTER UNCHANGED.
        embed.set_footer(
            text=f"Ticket System • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}"
        )

        # Post the actual panel in the channel where /ticket-setup was used.
        await channel.send(
            embed=embed,
            view=TicketControls()
        )

        await interaction.edit_original_response(
            content=f"✅ Ticket system configured! New tickets will be created in {category.mention}."
        )

    except discord.Forbidden:
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=(
                    "❌ Discord denied the setup. Please make sure the bot has "
                    "**View Channel**, **Send Messages**, **Embed Links**, and "
                    "**Manage Channels** permissions."
                )
            )
    except discord.HTTPException as e:
        print(f"Ticket setup Discord error in guild {interaction.guild.id if interaction.guild else 'DM'}: {e}")
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content="❌ Discord rejected the ticket setup. Please try `/ticket-setup` again."
            )
    except Exception as e:
        print(f"Ticket setup unexpected error in guild {interaction.guild.id if interaction.guild else 'DM'}: {e}")
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content="❌ An unexpected error occurred. Check the bot console for the exact error."
            )


@ticket_setup.error
async def ticket_setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ You need Administrator permission to use this command."
    else:
        print(f"Ticket setup command error: {error}")
        message = "❌ An error occurred while running `/ticket-setup`."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# Load token from Render safely
TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
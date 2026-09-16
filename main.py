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

# Stores the configured ticket category for each server.
ticket_categories = {}

# Reaction-role and autorole settings are stored per server.
REACTION_ROLE_FILE = "reaction_roles.json"
AUTOROLE_FILE = "autoroles.json"
HONEYPOT_FILE = "honeypot_settings.json"
BOOST_FILE = "boost_settings.json"
WELCOME_FILE = "welcome_settings.json"
AUTOMODE_FILE = "automode_settings.json"
STAFF_APPLY_FILE = "staff_apply_settings.json"

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
        category_id = ticket_categories.get(guild.id)
        category = guild.get_channel(category_id) if category_id else None

        if category is None or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "❌ Ticket category is not configured. An administrator must run `/ticket-setup <category>` first.",
                ephemeral=True
            )
            return

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{member.name}",
            category=category,
            overwrites=overrides
        )
        
        # Send confirmation within the private channel with a close button
        close_view = TicketCloseControl()
        embed = discord.Embed(
            title="Ticket Created!",
            description=f"Welcome {member.mention},\n\nPlease describe your issue or inquiry here. Support staff will assist you shortly.",
            color=discord.Color.blue()
        )
        await ticket_channel.send(embed=embed, view=close_view)
        await interaction.response.send_message(f"✅ Ticket created successfully! Go to {ticket_channel.mention}", ephemeral=True)

class TicketCloseControl(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket 🔒", style=discord.ButtonStyle.red, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 This ticket will be deleted in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

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


# --- 4. INTERACTIVE GIVEAWAY ACTIONS (JOIN BUTTON) ---
class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.entries = []

    @discord.ui.button(label="Join Giveaway 🎉 • 0", style=discord.ButtonStyle.blurple, custom_id="join_giveaway_btn")
    async def join_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.entries:
            await interaction.response.send_message("❌ You have already entered this giveaway!", ephemeral=True)
        else:
            self.entries.append(interaction.user.id)
            button.label = f"Join Giveaway 🎉 • {len(self.entries)}"
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("✅ You have successfully entered the giveaway!", ephemeral=True)

# --- INVITE & GREET CONFIGURATION ---
# Welcome/greet settings are stored persistently in welcome_settings.json.
# Each server gets its own message and channel configuration.
welcome_settings = load_json_settings(WELCOME_FILE)

def get_welcome_settings(guild_id):
    key = str(guild_id)
    if key not in welcome_settings or not isinstance(welcome_settings[key], dict):
        welcome_settings[key] = {
            "message": "Welcome {member} to the server! Make sure to read the guidelines.",
            "channel_id": None
        }
    else:
        # Keep older/incomplete saved settings compatible.
        welcome_settings[key].setdefault(
            "message",
            "Welcome {member} to the server! Make sure to read the guidelines."
        )
        welcome_settings[key].setdefault("channel_id", None)
    return welcome_settings[key]

# --- INVITE TRACKING CONFIGURATION ---
# Invite/welcome settings are also stored separately for each server.
# Do not use one global channel ID because that would send messages to another server.

invite_db = {}
invite_cache = {}
member_inviter_map = {}
history_db = set()

def get_user_stats(user_id):
    if user_id not in invite_db:
        invite_db[user_id] = {"regular": 0, "leaves": 0, "fake": 0, "bonus": 0}
    return invite_db[user_id]

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

# --- 4. YOUR DISCORD BOT LOGIC ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=["!", "?"], intents=discord.Intents.all())

    async def setup_hook(self):
        # Register persistent views so buttons continue working even after bot restarts
        self.add_view(TicketControls())
        self.add_view(TicketCloseControl())
        self.add_view(GiveawayView())
        self.add_view(VouchResultView())
        for guild_id in automode_settings:
            try:
                self.add_view(AutomodeView(int(guild_id)))
            except (TypeError, ValueError):
                continue

        # Restore staff application panels and review buttons after a bot restart.
        for guild_id, settings in staff_apply_settings.items():
            if guild_id == "applications" or not isinstance(settings, dict):
                continue
            try:
                self.add_view(StaffApplyPanelView(int(guild_id), disabled=bool(settings.get("closed", False))))
            except (TypeError, ValueError):
                continue
        for application in staff_apply_settings.get("applications", {}).values():
            try:
                self.add_view(StaffApplicationView(
                    int(application["guild_id"]),
                    int(application["applicant_id"])
                ))
            except (KeyError, TypeError, ValueError):
                continue

        # Restore reaction-role buttons after a bot restart.
        for configured_roles in reaction_role_settings.values():
            # New format stores a list; also accept the old single-role format.
            role_ids = configured_roles if isinstance(configured_roles, list) else [configured_roles]
            for role_id in role_ids:
                try:
                    self.add_view(ReactionRoleView(int(role_id)))
                except (TypeError, ValueError):
                    continue

        # Cache current invite uses for invite tracking
        for guild in self.guilds:
            try:
                invites = await guild.invites()
                invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
                print(f"Cached {len(invites)} invites for: {guild.name}")
            except discord.Forbidden:
                print(f"Missing invite permissions in: {guild.name}")

bot = MyBot()
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
# The bot owner can use !help in any server.
# Server administrators can use !help only in the server where they are an administrator.
# Set OWNER_ID in your environment (for example on Render) to your Discord user ID.
BOT_OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

@bot.command(name="help")
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
            "in this server to use `!help`.",
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
            "`!help` — Shows this command list.\n"
            "`!delete` — Deletes the current ticket channel.\n"
            "`!purge <amount>` — Deletes the specified number of messages.\n"
            "`!giveaway <duration> <winners> <prize>` — Starts a giveaway.\n"
            "`!invites [@member]` — Shows invite statistics.\n"
            "`!addbonus @member <amount>` — Adds bonus invites.\n"
            "`!resetinvites [@member]` — Resets invite statistics.\n"
            "`!vouch` — Sends the vouch panel in the current channel.\n"
            "`!ban <@user> <reason>` — Permanently bans a member.\n"
            "`!lock <#channel>` — Locks a target channel.\n"
            "`!unlock <#channel>` — Unlocks a target channel.\n"
            "`!automode` — Opens the all-in-one Automode security setup panel.\n"
            "`/staffapply <#application-channel> <@role> <#review-channel>` — Creates/configures the staff application panel.\n`!closed` — Closes staff applications and disables the Apply button.\n`!open` — Reopens staff applications and enables the Apply button.\n"
            "`?greetvariables` — Shows all boost message variables.\n"
            "`?greetvariables` — Shows all greet/welcome variables." 
        ),
        inline=False
    )
    embed.add_field(
        name="⚙️ Slash Commands",
        value=(
            "`/customwelcome <message>` — Sets the custom welcome message.\n"
            "`/testgreet` — Sends a test welcome greeting.\n"
            "`/channel_set <channel>` — Sets the welcome greeting channel.\n"
            "`/announce <message>` — Sends an announcement embed and DMs members.\n"
            "`/ticket-setup` — Creates the support ticket panel.\n"
            "`/reactionrole <role>` — Creates a button to get/remove a role.\n"
            "`/autorole <role>` — Automatically gives a role to new members.\n"
            "`/honeypot` — Makes this channel a spam trap.\n"
             "`/lock <channel>` — Locks a target channel.\n"
             "`/unlock <channel>` — Unlocks a target channel."
        ),
        inline=False
    )
    embed.add_field(
        name="🧩 Boost & Greet Variables",
        value=(
            "`?greetvariables` — Shows every supported boost variable.\n"
            "`?greetvariables` — Shows every supported greet/welcome variable.\n"
            "Both commands show the complete variable list and examples."
        ),
        inline=False
    )
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)


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

def render_variables(template: str, member: discord.Member, channel=None) -> str:
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

# --- 5. PREFIX COMMANDS ---


# 📝 VOUCH PANEL COMMAND
@bot.command(name="vouch")
@commands.has_permissions(administrator=True)
async def vouch_panel(ctx):
    """Send the vouch panel in the channel where !vouch is used."""
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


# 🎉 GIVEAWAY COMMAND
@bot.command(name="giveaway")
@commands.has_permissions(manage_guild=True)
async def start_giveaway(ctx, duration: str, winners: int, *, prize: str):
    seconds = convert_time(duration)
    if seconds == -1:
        await ctx.send(
            "❌ Invalid time format! Use `s` (seconds), `m` (minutes), `h` (hours), or `d` (days). Example: `10m`",
            delete_after=10
        )
        return

    if winners < 1:
        await ctx.send("❌ You must have at least 1 winner.", delete_after=5)
        return

    # Create the visual Giveaway Panel
    embed = discord.Embed(
        title=f"🎉 {prize}",
        description=(
            f"**Prize:** {prize}\n"
            f"**Winners:** {winners}\n"
            f"**Hosted by:** {ctx.author.mention}\n\n"
            f"Click the button to join!\n\n"
            f"⏰ **Ends in:** {duration}"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="Giveaway")

    view = GiveawayView()
    giveaway_msg = await ctx.send(embed=embed, view=view)

    # Wait until the giveaway duration has completely finished.
    await asyncio.sleep(seconds)

    # Get the entries and choose winners.
    entry_ids = list(view.entries)

    if len(entry_ids) == 0:
        # Update the original giveaway message when it ends.
        end_time = datetime.datetime.now(datetime.timezone.utc)
        end_embed = discord.Embed(
            title="🎉 GIVEAWAY ENDED",
            description=(
                f"🎁 **Prize:** {prize}\n"
                f"👑 **Winners:** No winners — nobody entered.\n"
                f"🎙️ **Hosted by:** {ctx.author.mention}\n\n"
            ),
            color=discord.Color.red()
        )
        end_embed.set_footer(
            text=f"Ended • {end_time.strftime('%B %d, %Y • %I:%M %p UTC')}"
        )
        await giveaway_msg.edit(embed=end_embed, view=None)
        await ctx.send(
            f"😭 No one entered the giveaway for **{prize}**. There are no winners."
        )
        return

    winner_count = min(winners, len(entry_ids))
    winning_ids = random.sample(entry_ids, winner_count)
    winner_mentions = ", ".join(f"<@{uid}>" for uid in winning_ids)

    # Edit the ORIGINAL giveaway message when it ends.
    end_time = datetime.datetime.now(datetime.timezone.utc)
    end_embed = discord.Embed(
        title="🎉 GIVEAWAY ENDED",
        description=(
            f"🎁 **Prize:** {prize}\n"
            f"👑 **Winner(s):** {winner_mentions}\n"
            f"🎙️ **Hosted by:** {ctx.author.mention}\n\n"
        ),
        color=discord.Color.green()
    )
    end_embed.set_footer(
        text=f"Ended • {end_time.strftime('%B %d, %Y • %I:%M %p UTC')}"
    )

    await giveaway_msg.edit(embed=end_embed, view=None)

    # Winner/claim message.
    claim_message = (
        f"🥳 Congratulations {winner_mentions}! You won **{prize}**!\n"
        f"**DM, {ctx.author.mention} or Create Ticket to claim your prize**"
    )
    await ctx.send(
        claim_message,
        allowed_mentions=discord.AllowedMentions(users=True)
    )

# --- STAFF APPLICATION SYSTEM ---
STAFF_APPLICATION_REQUIREMENTS = (
    "**We are looking for active, mature and helpful staff before that please fill up this first.**\n"
    "• Age:\n"
    "• Timezone:\n"
    "• Staff experience:\n"
    "• Why they should be accepted:\n"
    "• Availability:"
)

def get_staff_extra_requirements(guild_id: int) -> list[str]:
    settings = staff_apply_settings.get(str(guild_id), {})
    if not isinstance(settings, dict):
        return []
    requirements = settings.get("extra_requirements", [])
    if not isinstance(requirements, list):
        return []
    return [str(item).strip() for item in requirements if str(item).strip()]

def build_staff_apply_panel_embed(guild: discord.Guild, role_name: str | None = None) -> discord.Embed:
    settings = staff_apply_settings.get(str(guild.id), {})
    description = STAFF_APPLICATION_REQUIREMENTS

    extra_requirements = get_staff_extra_requirements(guild.id)
    if extra_requirements:
        # Keep each custom requirement on its own bullet directly below
        # Availability, without a separate Additional Requirements heading.
        availability_index = description.find("• Availability:")
        if availability_index != -1:
            before = description[:availability_index]
            availability_line = "• Availability:"
            after_requirements = "\n".join(f"• {item}" for item in extra_requirements)
            description = before + availability_line + "\n" + after_requirements

    description += "\n\nClick **Apply Now 📝** below to fill out the staff application form."

    embed = discord.Embed(
        title="🛡️ Staff Applications",
        description=description
    )

    closed = bool(settings.get("closed", False)) if isinstance(settings, dict) else False
    status = "Closed" if closed else "Open"
    footer_role = role_name or "Staff"
    embed.set_footer(text=f"Staff role: {footer_role} • Applications {status}")
    return embed

class StaffRequirementModal(discord.ui.Modal, title="Add Staff Requirement"):
    requirement = discord.ui.TextInput(
        label="Requirement",
        placeholder="Example: Must be active for at least 2 hours per day",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ This can only be used inside a server.",
                ephemeral=True
            )
            return

        if interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "❌ Only the **Server Owner** can add staff requirements.",
                ephemeral=True
            )
            return

        guild_key = str(interaction.guild.id)
        settings = staff_apply_settings.get(guild_key)
        if not isinstance(settings, dict) or not settings.get("panel_message_id"):
            await interaction.response.send_message(
                "❌ Staff applications are not configured. Use `/staffapply` first.",
                ephemeral=True
            )
            return

        requirement = self.requirement.value.strip()
        if not requirement:
            await interaction.response.send_message(
                "❌ Please enter a requirement.",
                ephemeral=True
            )
            return

        requirements = settings.setdefault("extra_requirements", [])
        if not isinstance(requirements, list):
            requirements = []
            settings["extra_requirements"] = requirements

        if len(requirements) >= 20:
            await interaction.response.send_message(
                "❌ You can have up to **20 additional requirements**.",
                ephemeral=True
            )
            return

        requirements.append(requirement)
        save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

        try:
            channel = interaction.guild.get_channel(int(settings["application_channel_id"]))
            message = await channel.fetch_message(int(settings["panel_message_id"])) if channel else None
            if message:
                role = interaction.guild.get_role(int(settings.get("role_id", 0)))
                role_name = role.name if role else "Staff"
                await message.edit(
                    embed=build_staff_apply_panel_embed(interaction.guild, role_name),
                    view=StaffApplyPanelView(
                        interaction.guild.id,
                        disabled=bool(settings.get("closed", False))
                    )
                )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
            pass

        await interaction.response.send_message(
            f"✅ Added this staff requirement:\n**• {requirement}**",
            ephemeral=True
        )

async def submit_staff_application(interaction: discord.Interaction, guild_id: int, target_channel_id: int, role_id: int, answers: dict[str, str]):
    guild = interaction.guild
    if guild is None or guild.id != guild_id:
        await interaction.response.send_message("❌ This application belongs to another server.", ephemeral=True)
        return

    target_channel = guild.get_channel(target_channel_id)
    role = guild.get_role(role_id)
    if target_channel is None or not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message("❌ The configured staff application channel no longer exists.", ephemeral=True)
        return
    if role is None:
        await interaction.response.send_message("❌ The configured staff role no longer exists.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 New Staff Application",
        description=f"Application submitted by {interaction.user.mention}",
    )
    embed.add_field(name="👤 Applicant", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
    embed.add_field(name="🎂 Age", value=answers.get("age", "—"), inline=True)
    embed.add_field(name="🌐 Timezone", value=answers.get("timezone", "—"), inline=True)
    embed.add_field(name="🛡️ Staff Experience", value=answers.get("experience", "—"), inline=False)
    embed.add_field(name="❓ Why should we accept you?", value=answers.get("why", "—"), inline=False)
    embed.add_field(name="🕐 Availability", value=answers.get("availability", "—"), inline=False)

    for requirement, answer in answers.get("extra_requirements", {}).items():
        embed.add_field(name=f"📌 {requirement}", value=answer or "—", inline=False)

    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.set_footer(text=f"Requested role: {role.name}")

    try:
        sent = await target_channel.send(
            embed=embed,
            view=StaffApplicationView(guild.id, interaction.user.id)
        )
    except (discord.Forbidden, discord.HTTPException):
        await interaction.response.send_message(
            "❌ I could not send the application to the configured target channel. Check my channel permissions.",
            ephemeral=True
        )
        return

    key = str(sent.id)
    staff_apply_settings.setdefault("applications", {})[key] = {
        "guild_id": guild.id,
        "applicant_id": interaction.user.id,
        "target_channel_id": target_channel.id,
        "role_id": role.id,
        "status": "pending",
        "answers": answers
    }
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    await interaction.response.send_message(
        "✅ Your staff application has been submitted!",
        ephemeral=True
    )


class StaffAdditionalRequirementsModal(discord.ui.Modal, title="Staff Application — Additional Questions"):
    def __init__(self, guild_id: int, target_channel_id: int, role_id: int, answers: dict[str, str], requirements: list[str], start_index: int = 0):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.target_channel_id = target_channel_id
        self.role_id = role_id
        self.answers = dict(answers)
        self.requirements = requirements
        self.start_index = start_index
        self.fields = []

        for index, requirement in enumerate(requirements[start_index:start_index + 5], start=start_index + 1):
            short_label = requirement.replace("\n", " ").strip()
            if len(short_label) > 32:
                short_label = short_label[:29].rstrip() + "..."
            field = discord.ui.TextInput(
                label=short_label[:45],
                placeholder=requirement.replace("\n", " ")[:100],
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1000
            )
            self.fields.append((requirement, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        self.answers.setdefault("extra_requirements", {})
        for requirement, field in self.fields:
            self.answers["extra_requirements"][requirement] = field.value.strip()

        next_index = self.start_index + len(self.fields)
        if next_index < len(self.requirements):
            await interaction.response.send_modal(
                StaffAdditionalRequirementsModal(
                    self.guild_id,
                    self.target_channel_id,
                    self.role_id,
                    self.answers,
                    self.requirements,
                    next_index
                )
            )
            return

        await submit_staff_application(
            interaction,
            self.guild_id,
            self.target_channel_id,
            self.role_id,
            self.answers
        )


class StaffApplicationModal(discord.ui.Modal, title="Staff Application"):
    def __init__(self, guild_id: int, target_channel_id: int, role_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.target_channel_id = target_channel_id
        self.role_id = role_id

        self.age = discord.ui.TextInput(
            label="Age",
            placeholder="Enter your age",
            required=True,
            max_length=3
        )
        self.timezone = discord.ui.TextInput(
            label="Timezone",
            placeholder="Example: GMT+8 / Philippines",
            required=True,
            max_length=100
        )
        self.experience = discord.ui.TextInput(
            label="Staff Experience",
            placeholder="Tell us about your previous staff experience",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.why = discord.ui.TextInput(
            label="Why should we accept you?",
            placeholder="Tell us why you want to become staff",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.availability = discord.ui.TextInput(
            label="Availability",
            placeholder="When are you usually available?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )

        for item in (self.age, self.timezone, self.experience, self.why, self.availability):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await interaction.response.send_message("❌ This application belongs to another server.", ephemeral=True)
            return

        settings = staff_apply_settings.get(str(guild.id), {})
        extra_requirements = get_staff_extra_requirements(guild.id)

        answers = {
            "age": self.age.value.strip(),
            "timezone": self.timezone.value.strip(),
            "experience": self.experience.value.strip(),
            "why": self.why.value.strip(),
            "availability": self.availability.value.strip(),
            "extra_requirements": {}
        }

        if extra_requirements:
            await interaction.response.send_modal(
                StaffAdditionalRequirementsModal(
                    self.guild_id,
                    self.target_channel_id,
                    self.role_id,
                    answers,
                    extra_requirements
                )
            )
            return

        await submit_staff_application(
            interaction,
            self.guild_id,
            self.target_channel_id,
            self.role_id,
            answers
        )


class StaffApplicationView(discord.ui.View):
    def __init__(self, guild_id: int, applicant_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.applicant_id = applicant_id
        # Give each application its own persistent button IDs so multiple
        # applications can be reviewed independently, including after restart.
        self.children[0].custom_id = f"staff_apply_accept:{guild_id}:{applicant_id}"
        self.children[1].custom_id = f"staff_apply_reject:{guild_id}:{applicant_id}"

    def _is_staff_reviewer(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.guild is not None
            and interaction.guild.id == self.guild_id
            and interaction.guild.owner_id == interaction.user.id
        )

    async def _delete_application_after_delay(self, message):
        await asyncio.sleep(10)
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    @discord.ui.button(label="Accept", emoji="✅", style=discord.ButtonStyle.success, custom_id="staff_apply_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_staff_reviewer(interaction):
            await interaction.response.send_message("❌ Only the **Server Owner** can review staff applications.", ephemeral=True)
            return

        application = None
        message_key = str(interaction.message.id) if interaction.message else None
        if message_key:
            application = staff_apply_settings.get("applications", {}).get(message_key)
        if not isinstance(application, dict):
            await interaction.response.send_message("❌ This application record could not be found.", ephemeral=True)
            return
        if application.get("status") != "pending":
            await interaction.response.send_message("❌ This application has already been reviewed.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(int(application["applicant_id"]))
        role = guild.get_role(int(application["role_id"]))
        if member is None:
            await interaction.response.send_message("❌ The applicant is no longer in the server.", ephemeral=True)
            return
        if role is None:
            await interaction.response.send_message("❌ The configured staff role no longer exists.", ephemeral=True)
            return

        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ I need **Manage Roles** permission to accept applications.", ephemeral=True)
            return
        if role >= me.top_role:
            await interaction.response.send_message("❌ I cannot give that role because it is higher than or equal to my highest role.", ephemeral=True)
            return

        try:
            await member.add_roles(role, reason=f"Staff application accepted by {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("❌ Discord denied the role change. Check my **Manage Roles** permission and role hierarchy.", ephemeral=True)
            return

        application["status"] = "accepted"
        application["reviewed_by"] = interaction.user.id
        save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

        review_channel = guild.get_channel(int(staff_apply_settings.get(str(self.guild_id), {}).get("review_channel_id", 0)))
        if isinstance(review_channel, discord.TextChannel):
            await review_channel.send(f"✅ Staff application from <@{application['applicant_id']}> was **accepted** by {interaction.user.mention}. Role granted: {role.mention}")

        old_embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="📋 Staff Application")
        old_embed.set_footer(text=f"✅ Accepted by {interaction.user.display_name} • Role: {role.name}")
        old_embed.add_field(name="📌 Status", value=f"✅ Accepted — {role.mention}", inline=False)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=old_embed, view=self)
        asyncio.create_task(self._delete_application_after_delay(interaction.message))

    @discord.ui.button(label="Reject", emoji="❌", style=discord.ButtonStyle.danger, custom_id="staff_apply_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_staff_reviewer(interaction):
            await interaction.response.send_message("❌ Only the **Server Owner** can review staff applications.", ephemeral=True)
            return

        message_key = str(interaction.message.id) if interaction.message else None
        application = staff_apply_settings.get("applications", {}).get(message_key) if message_key else None
        if not isinstance(application, dict):
            await interaction.response.send_message("❌ This application record could not be found.", ephemeral=True)
            return
        if application.get("status") != "pending":
            await interaction.response.send_message("❌ This application has already been reviewed.", ephemeral=True)
            return

        application["status"] = "rejected"
        application["reviewed_by"] = interaction.user.id
        save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

        guild = interaction.guild
        review_channel = guild.get_channel(int(staff_apply_settings.get(str(self.guild_id), {}).get("review_channel_id", 0))) if guild else None
        if isinstance(review_channel, discord.TextChannel):
            await review_channel.send(f"❌ Staff application from <@{application['applicant_id']}> was **rejected** by {interaction.user.mention}.")

        old_embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="📋 Staff Application")
        old_embed.set_footer(text=f"❌ Rejected by {interaction.user.display_name}")
        old_embed.add_field(name="📌 Status", value="❌ Rejected", inline=False)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=old_embed, view=self)
        asyncio.create_task(self._delete_application_after_delay(interaction.message))


class StaffApplyPanelView(discord.ui.View):
    def __init__(self, guild_id: int, disabled: bool = False):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.children[0].custom_id = f"staff_apply_open:{guild_id}"
        self.children[0].disabled = disabled
        self.children[1].custom_id = f"staff_apply_add_requirement:{guild_id}"

    @discord.ui.button(label="Apply Now", emoji="📝", style=discord.ButtonStyle.primary, custom_id="staff_apply_open")
    async def apply_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("❌ This panel belongs to another server.", ephemeral=True)
            return
        settings = staff_apply_settings.get(str(self.guild_id))
        if not isinstance(settings, dict):
            await interaction.response.send_message("❌ Staff applications are not configured.", ephemeral=True)
            return
        if settings.get("closed", False):
            await interaction.response.send_message("🔒 Staff applications are currently **closed**.", ephemeral=True)
            return
        try:
            await interaction.response.send_modal(
                StaffApplicationModal(
                    self.guild_id,
                    int(settings["application_channel_id"]),
                    int(settings["role_id"])
                )
            )
        except (KeyError, TypeError, ValueError):
            await interaction.response.send_message("❌ The staff application configuration is invalid.", ephemeral=True)

    @discord.ui.button(
        label="Add Requirements",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="staff_apply_add_requirement"
    )
    async def add_requirement(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("❌ This panel belongs to another server.", ephemeral=True)
            return

        if interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "❌ Only the **Server Owner** can add staff requirements.",
                ephemeral=True
            )
            return

        settings = staff_apply_settings.get(str(self.guild_id))
        if not isinstance(settings, dict):
            await interaction.response.send_message(
                "❌ Staff applications are not configured. Use `/staffapply` first.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(StaffRequirementModal())


@bot.tree.command(name="staffapply", description="Set up the staff application panel.")
@app_commands.describe(
    application_channel="Channel where staff applications will be submitted.",
    role="Role automatically given when an application is accepted.",
    review_channel="Channel where accept/reject results will be sent."
)
@app_commands.default_permissions(administrator=True)
async def staffapply_slash(
    interaction: discord.Interaction,
    application_channel: discord.TextChannel,
    role: discord.Role,
    review_channel: discord.TextChannel
):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need **Administrator** permission to use `/staffapply`.", ephemeral=True)
        return

    me = interaction.guild.me
    if me is None or not me.guild_permissions.send_messages or not me.guild_permissions.embed_links:
        await interaction.response.send_message("❌ I need **Send Messages** and **Embed Links** permissions.", ephemeral=True)
        return
    if not me.guild_permissions.manage_roles:
        await interaction.response.send_message("⚠️ I need **Manage Roles** before I can automatically give the staff role when an application is accepted.", ephemeral=True)
        return
    if role >= me.top_role:
        await interaction.response.send_message("❌ I cannot automatically give that role because it is higher than or equal to my highest role.", ephemeral=True)
        return
    guild_key = str(interaction.guild.id)
    old = staff_apply_settings.get(guild_key, {})
    old_applications = old.get("applications", {}) if isinstance(old, dict) else {}
    old_requirements = old.get("extra_requirements", []) if isinstance(old, dict) else []
    if not isinstance(old_requirements, list):
        old_requirements = []

    staff_apply_settings[guild_key] = {
        "message": STAFF_APPLICATION_REQUIREMENTS,
        "application_channel_id": application_channel.id,
        "role_id": role.id,
        "review_channel_id": review_channel.id,
        "closed": False,
        "panel_message_id": None,
        "extra_requirements": old_requirements,
        "applications": old_applications if isinstance(old_applications, dict) else {}
    }
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    embed = build_staff_apply_panel_embed(interaction.guild, role.name)

    try:
        panel_message = await application_channel.send(
            embed=embed,
            view=StaffApplyPanelView(interaction.guild.id, disabled=False)
        )
    except (discord.Forbidden, discord.HTTPException):
        await interaction.response.send_message("❌ I could not send the staff application panel. Check my permissions in that channel.", ephemeral=True)
        return

    staff_apply_settings[guild_key]["panel_message_id"] = panel_message.id
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)
    await interaction.response.send_message(
        f"✅ Staff applications set up in {application_channel.mention}.\n📋 Applications: {application_channel.mention}\n📢 Review results: {review_channel.mention}\n🎭 Role: {role.mention}",
        ephemeral=True
    )


@bot.command(name="closed")
async def close_staff_apply(ctx):
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used in a server.", delete_after=5)
        return

    if ctx.guild.owner_id != ctx.author.id:
        await ctx.send("❌ Only the **Server Owner** can use this command.", delete_after=7)
        return

    settings = staff_apply_settings.get(str(ctx.guild.id))
    if not isinstance(settings, dict) or not settings.get("panel_message_id"):
        await ctx.send("❌ Staff applications are not configured. Use `/staffapply` first.", delete_after=7)
        return

    settings["closed"] = True
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    try:
        channel = ctx.guild.get_channel(int(settings["application_channel_id"]))
        message = await channel.fetch_message(int(settings["panel_message_id"])) if channel else None
        if message:
            role = ctx.guild.get_role(int(settings.get("role_id", 0)))
            embed = build_staff_apply_panel_embed(ctx.guild, role.name if role else "Staff")
            await message.edit(embed=embed, view=StaffApplyPanelView(ctx.guild.id, disabled=True))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
        pass

    await ctx.send("🔒 Staff applications are now **closed**. The Apply Now button is disabled.", delete_after=7)

@bot.command(name="open")
async def open_staff_apply(ctx):
    if ctx.guild is None:
        await ctx.send("❌ This command can only be used in a server.", delete_after=5)
        return

    if ctx.guild.owner_id != ctx.author.id:
        await ctx.send("❌ Only the **Server Owner** can use this command.", delete_after=7)
        return

    settings = staff_apply_settings.get(str(ctx.guild.id))
    if not isinstance(settings, dict) or not settings.get("panel_message_id"):
        await ctx.send("❌ Staff applications are not configured. Use `/staffapply` first.", delete_after=7)
        return

    settings["closed"] = False
    save_json_settings(STAFF_APPLY_FILE, staff_apply_settings)

    try:
        channel = ctx.guild.get_channel(int(settings["application_channel_id"]))
        message = await channel.fetch_message(int(settings["panel_message_id"])) if channel else None
        if message:
            role = ctx.guild.get_role(int(settings.get("role_id", 0)))
            embed = build_staff_apply_panel_embed(ctx.guild, role.name if role else "Staff")
            await message.edit(embed=embed, view=StaffApplyPanelView(ctx.guild.id, disabled=False))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
        pass

    await ctx.send("🔓 Staff applications are now **open**. The Apply Now button can be pressed again.", delete_after=7)

# --- 6. ERROR HANDLING FOR PREFIX COMMANDS ---
@delete_ticket.error
@purge_messages.error
@start_giveaway.error
@vouch_panel.error
@ban_member_prefix.error
@lock_channel_prefix.error
@unlock_channel_prefix.error
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
            usage = {"ban": "`!ban <@user> <reason>`", "lock": "`!lock <#channel>`", "unlock": "`!unlock <#channel>`"}.get(ctx.command.name, "")
            await ctx.send(embed=discord.Embed(title="Command Error", description=f"❌ Missing required argument.\nUsage: {usage}", color=discord.Color.red()), delete_after=10)
        else:
            await ctx.send(f"❌ Missing fields! Formatting:\n`!purge <amount>`\n`!giveaway <duration> <winners> <prize>`\n`/staffapply <#application-channel> <@role> <#review-channel>`\n`!closed` / `!open` — Close or reopen staff applications", delete_after=10)
    elif isinstance(error, commands.BadArgument):
        if is_moderation_command:
            await ctx.send(embed=discord.Embed(title="Command Error", description="❌ Invalid member or channel provided.", color=discord.Color.red()), delete_after=5)
        else:
            await ctx.send("❌ Invalid argument format provided. Check your numbers/letters.", delete_after=5)

# --- 7. INVITE TRACKING ---

@bot.event
async def on_message(message):
    # Ignore DMs and bot messages so the honeypot cannot trap bots or itself.
    if message.guild is None or message.author.bot:
        await bot.process_commands(message)
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

                    # Keep a persistent softban count for the setup embed/statistics.
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
                                            name="Kicks",
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

    # --- CUSTOM WELCOME GREET SYSTEM (per server) ---
    # Only send the custom embed to this server's own configured channel.
    guild_welcome = get_welcome_settings(guild.id)
    target_channel_id = guild_welcome["channel_id"]
    if target_channel_id:
        greet_channel = guild.get_channel(target_channel_id)
        if greet_channel:
            raw_msg = guild_welcome["message"]
            show_avatar = "{avatar}" in raw_msg
            formatted_msg = render_variables(raw_msg, member, greet_channel)
            greet_embed = discord.Embed(
                title="✨ New Member Joined!",
                description=formatted_msg,
                color=discord.Color.green()
            )
            if show_avatar:
                greet_embed.set_thumbnail(url=member.display_avatar.url)
            await greet_channel.send(embed=greet_embed)

    # --- INVITE TRACKING ---
    # Invite tracking still runs, but it does NOT send any extra text messages.
    # This keeps the welcome channel clean with only the custom welcome embed.
    try:
        current_invites = await guild.invites()
    except discord.Forbidden:
        return

    used_invite = None
    cached_guild_invites = invite_cache.get(guild.id, {})

    for invite in current_invites:
        if invite.code in cached_guild_invites and invite.uses > cached_guild_invites[invite.code]:
            used_invite = invite
            break
        elif invite.uses > 0 and invite.code not in cached_guild_invites:
            used_invite = invite
            break

    # Refresh the cache for THIS server only.
    invite_cache[guild.id] = {inv.code: inv.uses for inv in current_invites}

    # Handle rejoining members.
    if member.id in history_db:
        if used_invite and used_invite.inviter:
            inviter = used_invite.inviter
            inviter_stats = get_user_stats(inviter.id)

            if member_inviter_map.get(member.id) == inviter.id:
                if inviter_stats["leaves"] > 0:
                    inviter_stats["leaves"] -= 1
        return

    # Handle brand-new members.
    if used_invite and used_invite.inviter:
        inviter = used_invite.inviter

        # Prevent self-invites.
        if inviter.id == member.id:
            history_db.add(member.id)
            return

        inviter_stats = get_user_stats(inviter.id)
        account_age = datetime.datetime.now(datetime.timezone.utc) - member.created_at

        if account_age.days < 1:
            inviter_stats["fake"] += 1
        else:
            inviter_stats["regular"] += 1

        member_inviter_map[member.id] = inviter.id
        history_db.add(member.id)
    else:
        history_db.add(member.id)


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

    if member.id in member_inviter_map:
        inviter_id = member_inviter_map[member.id]
        inviter_stats = get_user_stats(inviter_id)
        inviter_stats["leaves"] += 1


@bot.command(name="invites")
async def invites(ctx, member: discord.Member = None):
    target = member or ctx.author
    stats = get_user_stats(target.id)

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
    stats = get_user_stats(member.id)
    stats["bonus"] += amount
    await ctx.send(
        f"✅ Modified bonus invites for **{member.display_name}** by `{amount}`."
    )


@bot.command(name="resetinvites")
@commands.has_permissions(administrator=True)
async def reset_invites(ctx, member: discord.Member = None):
    if member:
        if member.id in invite_db:
            invite_db[member.id] = {
                "regular": 0,
                "leaves": 0,
                "fake": 0,
                "bonus": 0
            }
        if member.id in member_inviter_map:
            del member_inviter_map[member.id]

        await ctx.send(
            f"✅ Successfully reset invite data for **{member.display_name}**."
        )
    else:
        invite_db.clear()
        member_inviter_map.clear()
        history_db.clear()
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
            "❌ Missing fields. Examples: `!invites [@member]`, "
            "`!addbonus @member <amount>`, `!resetinvites [@member]`",
            delete_after=10
        )
    elif isinstance(error, commands.BadArgument):
        await ctx.send(
            "❌ Invalid member or number. Please check your command format.",
            delete_after=5
        )

# --- 8. SLASH COMMAND DEFINITION ---
@bot.tree.command(name="customwelcome", description="Set the custom welcome message.")
@app_commands.describe(message="Use ?greetvariables to see all supported variables.")
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
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server channel.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    channel = interaction.channel
    me = guild.me

    if me is None or not me.guild_permissions.ban_members:
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

    # Store the channel so the honeypot keeps working after a restart.
    honeypot_settings[str(guild.id)] = channel.id
    save_json_settings(HONEYPOT_FILE, honeypot_settings)

    embed = discord.Embed(
        title="DO NOT SEND MESSAGES IN THIS CHANNEL",
        description=(
            "This channel is used to catch spam bots. Any messages sent here "
            "will result in a **softban**."
        ),
        color=discord.Color.red()
    )
    count_key = str(guild.id)
    try:
        honeypot_count = int(honeypot_settings.get(f"{count_key}:count", 0))
    except (TypeError, ValueError):
        honeypot_count = 0
    embed.add_field(name="Ban", value=f"`{honeypot_count}`", inline=False)
    embed.set_footer(
        text=f"Honeypot • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}"
    )

    await interaction.response.send_message(embed=embed)

    # Save the panel message ID so the Ban counter can be updated live.
    panel_message = await interaction.original_response()
    honeypot_settings[f"{guild.id}:message_id"] = panel_message.id
    save_json_settings(HONEYPOT_FILE, honeypot_settings)


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
        ticket_categories[guild.id] = category.id

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


# Load token from Render safely
TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
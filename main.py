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
# Simple in-memory welcome message/channel configuration
# Per-server welcome settings. Each guild gets its own message and channel.
welcome_settings = {}

def get_welcome_settings(guild_id):
    if guild_id not in welcome_settings:
        welcome_settings[guild_id] = {
            "message": "Welcome {member} to the server! Make sure to read the guidelines.",
            "channel_id": None
        }
    return welcome_settings[guild_id]

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

# --- 4. YOUR DISCORD BOT LOGIC ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        # Register persistent views so buttons continue working even after bot restarts
        self.add_view(TicketControls())
        self.add_view(TicketCloseControl())
        self.add_view(GiveawayView())
        self.add_view(VouchResultView())

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
            "`!vouch` — Sends the vouch panel in the current channel."
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
            "`/honeypot` — Makes this channel a spam trap; messages trigger a softban."
        ),
        inline=False
    )
    embed.add_field(
        name="👋 Welcome Variables",
        value=(
            "`{member}` — Pings the new member.\n"
            "`{user}` — Pings the new member.\n"
            "`{mention}` — Pings the new member.\n"
            "`{username}` — Shows their username.\n"
            "`{display_name}` — Shows their display name.\n"
            "`{server}` — Shows the server name."
        ),
        inline=False
    )
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)


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

# --- 6. ERROR HANDLING FOR PREFIX COMMANDS ---
@delete_ticket.error
@purge_messages.error
@start_giveaway.error
@vouch_panel.error
async def command_errors(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You do not have the required permissions to run this command.", delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing fields! Formatting:\n`!purge <amount>`\n`!giveaway <duration> <winners> <prize>`", delete_after=10)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument format provided. Check your numbers/letters.", delete_after=5)

# --- 7. INVITE TRACKING ---

@bot.event
async def on_message(message):
    # Ignore DMs and bot messages so the honeypot cannot trap bots or itself.
    if message.guild is None or message.author.bot:
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
            formatted_msg = (
                raw_msg
                .replace("{member}", member.mention)
                .replace("{user}", member.mention)
                .replace("{mention}", member.mention)
                .replace("{username}", member.name)
                .replace("{display_name}", member.display_name)
                .replace("{server}", guild.name)
            )
            greet_embed = discord.Embed(
                title="✨ New Member Joined!",
                description=formatted_msg,
                color=discord.Color.green()
            )
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
@app_commands.describe(message="Use {member}, {user}, or {mention} to ping. Also: {username}, {display_name}, {server}.")
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
    await interaction.response.send_message("✅ Custom welcome message updated!", ephemeral=True)


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
    msg = (
        guild_welcome["message"]
        .replace("{member}", interaction.user.mention)
        .replace("{user}", interaction.user.mention)
        .replace("{mention}", interaction.user.mention)
        .replace("{username}", interaction.user.name)
        .replace("{display_name}", interaction.user.display_name)
        .replace("{server}", interaction.guild.name if interaction.guild else "the server")
    )
    embed = discord.Embed(title="✨ New Member Joined!", description=msg, color=discord.Color.green())
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
    await interaction.response.send_message(f"✅ Welcome greet channel set to {channel.mention}.", ephemeral=True)


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
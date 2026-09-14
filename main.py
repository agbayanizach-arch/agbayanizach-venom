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

# Stores the configured ticket category for each server.\nticket_categories = {}\n\n# --- 2. INTERACTIVE TICKET ACTIONS (BUTTON CODES) ---
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

# --- 3. INTERACTIVE GIVEAWAY ACTIONS (JOIN BUTTON) ---
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
            "`!resetinvites [@member]` — Resets invite statistics."
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
            "`/ticket-setup` — Creates the support ticket panel."
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

# --- 5. PREFIX COMMANDS ---

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
async def command_errors(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You do not have the required permissions to run this command.", delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing fields! Formatting:\n`!purge <amount>`\n`!giveaway <duration> <winners> <prize>`", delete_after=10)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument format provided. Check your numbers/letters.", delete_after=5)

# --- 7. INVITE TRACKING ---

@bot.event
async def on_member_join(member):
    guild = member.guild

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


@customwelcome.error
@testgreet.error
@channel_set.error
@announce.error
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
@app_commands.describe(category="The category where newly created tickets will be placed.")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction, category: discord.CategoryChannel):
    # Acknowledge the interaction immediately so Discord does not time out.
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used inside a server.",
            ephemeral=True
        )
        return

    # Verify the bot can send the panel and create ticket channels.
    me = interaction.guild.me
    if me is None:
        await interaction.response.send_message(
            "❌ I could not verify my server permissions. Please try again.",
            ephemeral=True
        )
        return

    required = [
        (me.guild_permissions.manage_channels, "Manage Channels"),
        (me.guild_permissions.send_messages, "Send Messages"),
        (me.guild_permissions.embed_links, "Embed Links"),
    ]
    missing = [name for ok, name in required if not ok]
    if missing:
        await interaction.response.send_message(
            "❌ I am missing these permissions: "
            + ", ".join(f"**{name}**" for name in missing)
            + ".",
            ephemeral=True
        )
        return

    # Save the selected category for this server.
    ticket_categories[interaction.guild.id] = category.id

    embed = discord.Embed(
        title="📩 Support Help Desk",
        description=(
            "Need assistance? Click the green button below to open a private "
            "support ticket window with our server staff team."
        ),
        color=discord.Color.green()
    )
    embed.set_footer(
        text=f"Ticket System • {datetime.datetime.now().strftime('%B %d, %Y • %I:%M %p')}"
    )

    try:
        # Send the public ticket panel.
        await interaction.channel.send(
            embed=embed,
            view=TicketControls()
        )

        # Confirm success after the panel is posted.
        await interaction.response.send_message(
            f"✅ Ticket system configured! New tickets will be created in {category.mention}.",
            ephemeral=True
        )

    except discord.Forbidden:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ I don't have permission to send the ticket panel in this channel.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ I don't have permission to send the ticket panel in this channel.",
                ephemeral=True
            )

    except discord.HTTPException as e:
        message = f"❌ Discord rejected the ticket panel message. Error: `{e}`"
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)

    except Exception as e:
        print(f"Ticket setup error in guild {interaction.guild.id}: {e}")
        message = "❌ An unexpected error occurred while setting up the ticket system. Check the bot console."
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)


# Load token from Render safely
TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)
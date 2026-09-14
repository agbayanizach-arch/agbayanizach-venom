import os
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask
from threading import Thread
import asyncio
import random
import re

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
        ticket_channel = await guild.create_text_channel(name=f"ticket-{member.name}", overwrites=overrides)
        
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

    @discord.ui.button(label="Join Giveaway 🎉", style=discord.ButtonStyle.blurple, custom_id="join_giveaway_btn")
    async def join_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.entries:
            await interaction.response.send_message("❌ You have already entered this giveaway!", ephemeral=True)
        else:
            self.entries.append(interaction.user.id)
            await interaction.response.send_message("✅ You have successfully entered the giveaway!", ephemeral=True)

# --- 4. YOUR DISCORD BOT LOGIC ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        # Register persistent views so buttons continue working even after bot restarts
        self.add_view(TicketControls())
        self.add_view(TicketCloseControl())
        self.add_view(GiveawayView())

bot = MyBot()

@bot.event
async def on_ready():
    # Force the bot's status circle to show as online and add an activity message
    await bot.change_presence(
        status=discord.Status.online,
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
        await ctx.send("❌ Invalid time format! Use `s` (seconds), `m` (minutes), `h` (hours), or `d` (days). Example: `10m`", delete_after=10)
        return
    if winners < 1:
        await ctx.send("❌ You must have at least 1 winner.", delete_after=5)
        return

    # Create the visual Giveaway Panel
    embed = discord.Embed(
        title=f"🎁 GIVEAWAY: {prize} 🎁",
        description=f"Click the button below to join!\n\n⏳ **Duration:** {duration}\n👥 **Winners:** {winners}\n🎙️ **Hosted by:** {ctx.author.mention}",
        color=discord.Color.gold()
    )
    
    view = GiveawayView()
    giveaway_msg = await ctx.send(embed=embed, view=view)

    # Wait for the specified duration to run out
    await asyncio.sleep(seconds)

    # Pull list of valid entry IDs
    entry_ids = view.entries

    if len(entry_ids) == 0:
        await ctx.send(f"😭 No one entered the giveaway for **{prize}**. There are no winners.")
        return

    # Determine winners based on configuration
    winner_count = min(winners, len(entry_ids))
    winning_ids = random.sample(entry_ids, winner_count)
    winner_mentions = ", ".join([f"<@{uid}>" for uid in winning_ids])

    # Announce the outcome
    end_embed = discord.Embed(
        title="🎉 GIVEAWAY ENDED 🎉",
        description=f"🎁 **Prize:** {prize}\n👑 **Winners:** {winner_mentions}\n🎙️ **Hosted by:** {ctx.author.mention}",
        color=discord.Color.green()
    )
    await giveaway_msg.edit(embed=end_embed, view=None)
    await ctx.send(f"🥳 Congratulations {winner_mentions}! You won **{prize}**!")

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

# --- 7. SLASH COMMAND DEFINITION ---
@bot.tree.command(name="ticket-setup", description="Deploy the interactive support ticket panel into this channel.")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📩 Support Help Desk",
        description="Need assistance? Click the green button below to open a private support ticket window with our server staff team.",
        color=discord.Color.green()
    )
    embed.set_footer(text="Wither Cloud Ticket System")
    
    view = TicketControls()
    await interaction.response.send_message("Deploying ticket dashboard...", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)

# Load token from Render safely
TOKEN = os.environ.get("DISCORD_TOKEN")
bot.run(TOKEN)

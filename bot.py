import discord
import sqlite3
import random
import time
from discord import app_commands
from discord.ext import commands
from typing import Optional
import math
from pypresence import Presence
import asyncio
import wavelink
from dotenv import load_dotenv
import os
from typing import List
import sqlite3, json, os
from PIL import Image
from PIL import Image, ImageDraw, ImageFont
from discord.ui import View, Button
import shutil

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# === DYNAMIC DATABASE PATH ===
DB_PATH = "/mnt/data/west.db" if os.path.exists("/mnt/data") else "west.db"

# Set up intents to read messages and manage messages
intents = discord.Intents.default()
intents.guilds = True  # ✅ Required for full event context
intents.presences = True
intents.message_content = True
intents.messages = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Replace with your actual Discord Application Client ID
CLIENT_ID = "1400670306397589685"

# User ID of the allowed user
ALLOWED_USER_ID = 488015447417946151

# List of allowed server IDs
ALLOWED_SERVER_IDS = [1286227801426624582]

# The IDs of the channels where users are allowed to make guesses
ALLOWED_CHANNEL_IDS = [1340486756918759527]

# Role ID for verified users
VERIFIED_ROLE_ID = 1389128704055050343

# Your Discord user ID (admin)
YOUR_DISCORD_ID = 1389128704055050343

ADMIN_IDS = [
    488015447417946151,  # your main ID
    878253813553844254,  # example other admin
    1259041735514918952   # add as many as you like
]

ALLOWED_COMMANDS = ["/bingo_bonus_join", "/bingo_bonus_card", "/bingo_bonus_rules"]
RESTRICTED_CHANNEL_ID = 1401920349402042449  # Replace with your channel ID

# Global variables
guesses = []
starting_balance_set = False
final_balance = None
starting_balance = 0

# Initialize SQLite database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Create tables if they don't exist
cursor.execute("""
CREATE TABLE IF NOT EXISTS wagers (
    viewer_id INTEGER,
    viewer_name TEXT,
    rainbet_username TEXT UNIQUE,
    amount REAL DEFAULT 0
)
""")

cursor.execute('''
CREATE TABLE IF NOT EXISTS wager_winners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        viewer_id INTEGER,
        viewer_name TEXT,
        rainbet_username TEXT,
        wager_won INTEGER,
        rewards REAL
    )
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS rainbet_connections (
    user_id INTEGER PRIMARY KEY,
    viewer_name TEXT,
    rainbet_username TEXT
)
''')

cursor.execute('''CREATE TABLE IF NOT EXISTS gtb_balances (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    starting_balance REAL,
    final_balance REAL
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS gtb_guesses (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    guess REAL,
    winner INTEGER DEFAULT 0,
    rerolled INTEGER DEFAULT 0
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS gtb_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active INTEGER DEFAULT 0
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS persistent_hunt (
    guild_id INTEGER PRIMARY KEY,
    message_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER
)''')

cursor.execute('INSERT OR IGNORE INTO gtb_state (id, active) VALUES (1, 0)')
cursor.execute('INSERT OR IGNORE INTO gtb_balances (id, starting_balance, final_balance) VALUES (1, NULL, NULL)')
conn.commit()


@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user.name}')

    try:
        # Load Bingo cog
        await bot.add_cog(BingoBonus(bot))
        bot.tree.add_command(bingo_bonus_rules)

        # Sync slash commands
        synced = await bot.tree.sync()
        print(f'🔄 Synced {len(synced)} commands')

        # ✅ Restore persistent /bingo_bonus_hunt buttons
        cursor = bot.conn.cursor()
        cursor.execute("SELECT guild_id, message_id, channel_id, user_id FROM persistent_hunt")
        rows = cursor.fetchall()

        for guild_id, message_id, channel_id, user_id in rows:
            guild = bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        msg = await channel.fetch_message(message_id)
                        member = guild.get_member(user_id)
                        if member:
                            view = HuntPaginator(guild_id, member, bot.conn)
                            view.message = msg
                            await msg.edit(view=view)
                            print(f"🔄 Restored hunt paginator in {guild.name}")
                        else:
                            print(f"⚠️ Could not find member {user_id} in {guild.name}")
                    except Exception as e:
                        print(f"⚠️ Could not restore hunt paginator in {guild.name}: {e}")

    except Exception as e:
        print(f'❌ Error in on_ready: {e}')



@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # 🚫 Restrict /bingo commands in a specific channel
    if message.channel.id == RESTRICTED_CHANNEL_ID:
        if not any(message.content.strip().startswith(cmd) for cmd in ALLOWED_COMMANDS):
            await message.delete()
            await message.channel.send(
                f"❌ {message.author.mention}, only `/bingo_bonus_join` `/bingo_bonus_card` and `/bingo_bonus_rules` commands are allowed in this channel.",
                delete_after=6
            )
            return

    # ✅ Kick username verification
    if message.channel.id == 1400737018417516684:
        new_nick = message.content.strip()

        if 1 <= len(new_nick) <= 32:
            try:
                await message.author.edit(nick=new_nick)
                role = message.guild.get_role(1341577464601645066)
                if role:
                    await message.author.add_roles(role)

                await message.channel.send(
                    f"🚫 {message.author.mention}, only verified users can enter **Westside**.\n\n"
                    f"We need to confirm that your Kick username is **{new_nick}**. If it’s not, you’ll have to verify again.\n\n"
                    f"📍 Please go to <#1389143621613391952> and verify to gain access to all channels.\n"
                    f"🎫 *Note: Open a ticket and verify your Rainbet account to receive the **Rainbet** role.*"
                )
            except discord.Forbidden:
                await message.channel.send("❌ I don't have permission to change your nickname or assign the role.")
            except discord.HTTPException as e:
                await message.channel.send(f"❌ Could not verify username. `{e}`")
        else:
            await message.channel.send("❌ Please send your Kick username (1–32 characters) to complete verification.")

        return  # 🛑 Stop here so it doesn’t process commands/guesses

    # 🎯 GTB game handling
    if message.channel.id in ALLOWED_CHANNEL_IDS:
        cursor.execute("SELECT active FROM gtb_state WHERE id = 1")
        result = cursor.fetchone()
        if not result or result[0] == 0:
            return

        content = message.content.strip().replace(",", "")
        try:
            guess = float(content)
        except ValueError:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, please enter a valid number like `420.69`.")
            return

        if guess <= 0 or guess > 1_000_000:
            await message.delete()
            await message.channel.send(f"{message.author.mention}, your guess must be between 1 and 1,000,000.")
            return

        cursor.execute("SELECT 1 FROM gtb_guesses WHERE user_id = ?", (message.author.id,))
        if cursor.fetchone():
            await message.delete()
            await message.channel.send(f"{message.author.mention}, you've already submitted a guess.")
            return

        try:
            cursor.execute(
                "INSERT INTO gtb_guesses (user_id, username, guess) VALUES (?, ?, ?)",
                (message.author.id, message.author.display_name, guess)
            )
            conn.commit()
        except Exception as e:
            await message.channel.send(f"❌ Error saving guess: `{e}`")
            return

        await message.channel.send(f"{message.author.mention} guessed **${guess:,.2f}** ✅")

    # ✅ Always call this at the very end
    await bot.process_commands(message)

async def rainbet_username_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> List[app_commands.Choice[str]]:
    cursor.execute("SELECT rainbet_username FROM rainbet_connections WHERE rainbet_username LIKE ? LIMIT 25", (f"%{current}%",))
    rows = cursor.fetchall()
    return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]


@bot.tree.command(name="update_wager", description="Update the wager amount for a Rainbet user.")
@app_commands.describe(rainbet_username="Select the registered Rainbet username", amount="Wager amount")
@app_commands.autocomplete(rainbet_username=rainbet_username_autocomplete)
async def update_wager(interaction: discord.Interaction, rainbet_username: str, amount: float):
    if interaction.user.id != ALLOWED_USER_ID:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    # Look up the Discord user info from rainbet_connections
    cursor.execute(
        "SELECT user_id, viewer_name FROM rainbet_connections WHERE rainbet_username = ?",
        (rainbet_username,)
    )
    result = cursor.fetchone()

    if not result:
        await interaction.response.send_message("❌ This Rainbet username is not registered.", ephemeral=True)
        return

    user_id, viewer_name = result

    # Insert or update wager based on rainbet_username
    cursor.execute("""
        INSERT INTO wagers (viewer_id, viewer_name, rainbet_username, amount)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(rainbet_username) DO UPDATE SET amount = amount + excluded.amount
    """, (user_id, viewer_name, rainbet_username, amount))

    conn.commit()

    await interaction.response.send_message(
        f"✅ Wager updated for **{rainbet_username}**: +${amount:.2f}", ephemeral=False
    )


# /Prizes Leaderboard (Anyone can use)
@bot.tree.command(name="wager_leaderboard", description="Display the leaderboard sorted by highest wager.")
async def wager_leaderboard(interaction: discord.Interaction, page: Optional[int] = 1):
    items_per_page = 5  # Set the number of items per page
    offset = (page - 1) * items_per_page  # Calculate the offset for pagination

    # Database connection and query
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Fetch leaderboard data (use discord_id instead of viewer_name)
    cursor.execute('''
        SELECT w.viewer_name, w.viewer_id, r.rainbet_username, w.amount
        FROM wagers w
        LEFT JOIN rainbet_connections r ON w.viewer_id = r.user_id
        ORDER BY w.amount DESC
        LIMIT ? OFFSET ?
    ''', (items_per_page, offset))

    wager_leaderboard_data = cursor.fetchall()
    if not wager_leaderboard_data:
        await interaction.response.send_message("No leaderboard data available.", ephemeral=True)
        return

    cursor.execute('SELECT SUM(rewards) FROM wager_winners')
    total_prizes = cursor.fetchone()[0]
    if total_prizes is None:
        total_prizes = 0  # Handle case when there are no rewards

    # Server Status Embed
    embed = discord.Embed(title="𝕎𝔸𝔾𝔼ℝ 𝕃𝔼𝔸𝔻𝔼ℝ𝔹𝕆𝔸ℝ𝔻", color=discord.Color.green())
    embed.add_field(
        name="Prizes for this Month Wager Leaderboard",
        value="1st Place = **$150**\n2nd Place = **$100**\n3rd Place = **$50**\n\n*Note:  All prizes tipped directly to your Rainbet account!*")
    embed.add_field(name="Total Wager Prizes Given", value=f"**${total_prizes:.2f}**")

    # List of embeds for leaderboard
    embeds = [embed]

    # Generate embeds for each viewer
    for rank, (viewer_name, discord_id, username, amount) in enumerate(wager_leaderboard_data, start=1 + offset):
        try:
            user = await bot.fetch_user(discord_id)  # Fetch the latest user data
        except:
            user = None

        # Default avatar URL in case user is not found
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
        if user:
            avatar_url = user.display_avatar.url
            display_name = user.mention
        else:
            display_name = f"<@{discord_id}>"

        leaderboard_embed = discord.Embed(
            title=f"Rank {rank}: {username}",
            color=discord.Color.purple(),
            description=(
                f"Viewer: **{display_name}**\n"
                f"Wagered: **${amount}**"
            ),
        ).set_thumbnail(url=avatar_url)

        embeds.append(leaderboard_embed)

    # Calculate total pages for pagination
    cursor.execute('SELECT COUNT(*) FROM wagers')
    total_viewers = cursor.fetchone()[0]
    total_pages = math.ceil(total_viewers / items_per_page)

    # Add pagination buttons
    view = WagerLeaderboardPaginationView(interaction, page, total_pages, generate_wager_leaderboard_embeds)

    # Send a single message with all embeds
    await interaction.response.send_message(embeds=embeds, view=view)

    conn.close()


# Pagination View for Prizes Leaderboard
class WagerLeaderboardPaginationView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, current_page: int, total_pages: int, embed_callback):
        super().__init__()
        self.interaction = interaction
        self.current_page = current_page
        self.total_pages = total_pages
        self.embed_callback = embed_callback  # Make sure this is assigned

        # Navigation Buttons
        self.prev_button = discord.ui.Button(label="Previous", style=discord.ButtonStyle.primary,
                                             disabled=(current_page == 1))
        self.next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.primary,
                                             disabled=(current_page == total_pages))

        # Assign Callbacks
        self.prev_button.callback = self.prev_page
        self.next_button.callback = self.next_page

        # Add Buttons
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    async def prev_page(self, interaction: discord.Interaction):
        self.current_page -= 1
        embeds = await self.embed_callback(self.interaction, self.current_page)  # Pass interaction
        self.prev_button.disabled = self.current_page == 1
        self.next_button.disabled = self.current_page == self.total_pages
        await interaction.response.edit_message(embeds=embeds, view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.current_page += 1
        embeds = await self.embed_callback(self.interaction, self.current_page)  # Pass interaction
        self.prev_button.disabled = self.current_page == 1
        self.next_button.disabled = self.current_page == self.total_pages
        await interaction.response.edit_message(embeds=embeds, view=self)


# Generate leaderboard embeds for pagination
async def generate_wager_leaderboard_embeds(interaction: discord.Interaction, page: int):
    items_per_page = 5
    offset = (page - 1) * items_per_page

    # Database connection and query
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    cursor.execute('''
        SELECT w.viewer_name, w.viewer_id, r.rainbet_username, w.amount
        FROM wagers w
        LEFT JOIN rainbet_connections r ON w.viewer_id = r.user_id
        ORDER BY w.amount DESC
        LIMIT ? OFFSET ?
    ''', (items_per_page, offset))

    leaderboard_data = cursor.fetchall()
    embeds = []

    # Server Status Embed
    embed = discord.Embed(title="# 𝕎𝔸𝔾𝔼ℝ 𝕃𝔼𝔸𝔻𝔼ℝ𝔹𝕆𝔸ℝ𝔻", color=discord.Color.green())
    embed.add_field(
        name="Prizes for this Month Wager Leaderboard",
        value="1st Place = $150\n2nd Place = $100\n3rd Place = $50\n\n*Note:  All prizes tipped directly to your Rainbet account!*",
        inline=False
    )

    # List of embeds (first embed will always be the server status)
    embeds = [embed]

    # Generate embeds for each viewer
    for rank, (viewer_name, discord_id, username, amount) in enumerate(leaderboard_data, start=1 + offset):
        try:
            user = await bot.fetch_user(discord_id)
        except:
            user = None

        # Default avatar URL if the user is not found
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"
        display_name = f"<@{discord_id}>" if not user else user.mention

        if user:
            # Fetch member from the guild directly using the user ID
            member = interaction.guild.get_member(user.id)  # This checks if the user is in the guild
            if member:
                display_name = member.mention  # Use mention if the user is in the server
            else:
                display_name = user.name  # Use the username if the user is not in the server
            avatar_url = user.avatar.url if user.avatar else avatar_url  # Get user's avatar URL if available
        else:
            display_name = f"<@{discord_id}>"

        leaderboard_embed = discord.Embed(
            title=f"Rank {rank}: {username}",
            color=discord.Color.purple(),
            description=(
                f"Viewer: **{display_name}**\n"
                f"Wagered: **${amount}**"
            ),
        ).set_thumbnail(url=user.display_avatar.url if user else avatar_url)

        embeds.append(leaderboard_embed)

    conn.close()
    return embeds


@bot.tree.command(name="reset_wager_leaderboard", description="Reset the wager leaderboard.")
@app_commands.choices(
    confirm=[app_commands.Choice(name="YES", value="YES"), app_commands.Choice(name="NO", value="NO")])
async def reset_wager_leaderboard(interaction: discord.Interaction, confirm: str):
    if interaction.user.id != ALLOWED_USER_ID:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if confirm == "YES":
        cursor.execute("DELETE FROM wagers")
        conn.commit()
        await interaction.response.send_message("The wager leaderboard has been reset.", ephemeral=False)
    else:
        await interaction.response.send_message("Leaderboard reset cancelled.", ephemeral=True)

# /Wager Winner (Admin Only)
@bot.tree.command(name="wager_winner", description="Register a wager winner (Admin only)")
@app_commands.describe(
    viewer="Select the viewer",
    rainbet_username="Enter the Rainbet username",
    wager_won="Enter the number of wagers won",
    rewards="Enter the reward amount"
)
@app_commands.checks.has_permissions(administrator=True)
async def wager_winner(
    interaction: discord.Interaction,
    viewer: discord.Member,
    rainbet_username: str,
    wager_won: int,
    rewards: float
):
    cursor.execute("INSERT INTO wager_winners (viewer_id, viewer_name, rainbet_username, wager_won, rewards) VALUES (?, ?, ?, ?, ?)",
                   (viewer.id, viewer.name, rainbet_username, wager_won, rewards))
    conn.commit()

    embed = discord.Embed(title="✅ Wager Winner Recorded!", color=discord.Color.green())
    embed.add_field(name="Viewer", value=viewer.mention, inline=True)
    embed.add_field(name="Rainbet Username", value=rainbet_username, inline=True)
    embed.add_field(name="Wagers Won", value=str(wager_won), inline=True)
    embed.add_field(name="Rewards", value=f"${rewards:.2f}", inline=True)
    embed.set_thumbnail(url=viewer.avatar.url)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="gtb_startingbalance", description="Set the starting balance for West GTB")
@commands.has_permissions(administrator=True)
async def gtb_startingbalance(interaction: discord.Interaction, balance: float):
    role_id = 1389128704055050343
    role = interaction.guild.get_role(role_id)

    # 💡 Reset all guesses when new GTB starts
    cursor.execute("DELETE FROM gtb_guesses")

    # Set new starting balance and clear final balance
    cursor.execute("UPDATE gtb_balances SET starting_balance = ?, final_balance = NULL WHERE id = 1", (balance,))
    conn.commit()

    embed = discord.Embed(
        title="West Guess The Balance",
        description=f"We are playing **Guess The Balance** today with **${balance:.2f}**\n\nSubmit your guesses when guessing opens!",
        color=discord.Color.gold()
    )

    await interaction.response.send_message(
        content=role.mention,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True)
    )

@bot.tree.command(name="gtb_startguessing", description="Open GTB guessing for West")
@commands.has_permissions(administrator=True)
async def gtb_startguessing(interaction: discord.Interaction):
    role_id = 1389128704055050343
    role = interaction.guild.get_role(role_id)

    perms = interaction.channel.overwrites_for(role)
    perms.send_messages = True
    await interaction.channel.set_permissions(role, overwrite=perms)

    cursor.execute("UPDATE gtb_state SET active = 1 WHERE id = 1")
    conn.commit()

    await interaction.response.send_message(
        f"# <@&{role_id}> GTB Guessing is now OPEN!\n\nJust type your final balance guess like `420.69`\n\n*Note: Only one guess per user. No editing allowed.*",
        allowed_mentions=discord.AllowedMentions(roles=True)
    )


@bot.tree.command(name="gtb_closedguessing", description="Close GTB guessing")
@commands.has_permissions(administrator=True)
async def gtb_closedguessing(interaction: discord.Interaction):
    role_id = 1389128704055050343
    role = interaction.guild.get_role(role_id)

    perms = interaction.channel.overwrites_for(role)
    perms.send_messages = False
    await interaction.channel.set_permissions(role, overwrite=perms)

    cursor.execute("UPDATE gtb_state SET active = 0 WHERE id = 1")
    conn.commit()

    await interaction.response.send_message("GTB Guessing is now CLOSED. Good luck!")

    cursor.execute("SELECT guess FROM gtb_guesses")
    guesses = [row[0] for row in cursor.fetchall()]
    if guesses:
        await interaction.channel.send(
            f"**Guess Stats:**\n- Total Players: {len(guesses)}\n- Lowest: ${min(guesses):.2f}\n- Highest: ${max(guesses):.2f}"
        )
    else:
        await interaction.channel.send("No guesses were submitted.")


@bot.tree.command(name="gtb_finalbalance", description="Set final balance for GTB")
@commands.has_permissions(administrator=True)
async def gtb_finalbalance(interaction: discord.Interaction, balance: float):
    cursor.execute("SELECT starting_balance FROM gtb_balances WHERE id = 1")
    if not cursor.fetchone()[0]:
        await interaction.response.send_message("Set the starting balance first.")
        return

    cursor.execute("UPDATE gtb_balances SET final_balance = ? WHERE id = 1", (balance,))
    conn.commit()
    await interaction.response.send_message(f"Final balance has been set to **${balance:.2f}**.")


@bot.tree.command(name="gtb_winner", description="Announce GTB winner")
@commands.has_permissions(administrator=True)
async def gtb_winner(interaction: discord.Interaction):
    await interaction.response.defer()

    cursor.execute("SELECT starting_balance, final_balance FROM gtb_balances WHERE id = 1")
    row = cursor.fetchone()
    if not row or row[0] is None or row[1] is None:
        await interaction.followup.send("Starting or final balance is missing.")
        return

    _, final_balance = row
    cursor.execute("SELECT user_id, guess FROM gtb_guesses WHERE rerolled = 0")
    rows = cursor.fetchall()

    members = []
    for uid, guess in rows:
        try:
            member = await interaction.guild.fetch_member(uid)
            members.append((member, guess))
        except:
            continue

    if not members:
        await interaction.followup.send("No valid guesses found.")
        return

    # Sort by absolute difference from final balance
    sorted_guesses = sorted(members, key=lambda x: abs(final_balance - x[1]))
    winner, winner_guess = sorted_guesses[0]
    difference = abs(final_balance - winner_guess)

    # Update winner in DB
    cursor.execute("UPDATE gtb_guesses SET winner = 0")
    cursor.execute("UPDATE gtb_guesses SET winner = 1, username = ? WHERE user_id = ?", (winner.display_name, winner.id))
    conn.commit()

    # Create embed
    embed = discord.Embed(
        title="🏆 West GTB Winner",
        description=f"Final Balance: **${final_balance:,.2f}**",
        color=discord.Color.green()
    )
    embed.add_field(name=winner.display_name, value=(
        f"**Guess:** ${winner_guess:,.2f}\n"
        f"**Difference:** ${difference:,.2f}"), inline=False)
    embed.set_thumbnail(url=winner.display_avatar.url)

    await interaction.channel.send(embed=embed)



@bot.tree.command(name="gtb_reset", description="Reset all GTB data")
@commands.has_permissions(administrator=True)
async def gtb_reset(interaction: discord.Interaction):
    cursor.execute("DELETE FROM gtb_guesses")
    cursor.execute("UPDATE gtb_balances SET starting_balance = NULL, final_balance = NULL WHERE id = 1")
    conn.commit()
    await interaction.response.send_message("✅ GTB data reset.")

RAINBET_CHANNEL_ID = 1400738936271405097  # 🔁 Replace with your actual channel ID

@bot.tree.command(name="connect_rainbet", description="Link your Rainbet username (only works in designated channel).")
@app_commands.describe(username="Enter your Rainbet username")
async def connect_rainbet(interaction: discord.Interaction, username: str):
    if interaction.channel.id != RAINBET_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ This command can only be used in the designated channel.", ephemeral=True
        )
        return

    # Save or update the username in the DB
    cursor.execute('''
        INSERT INTO rainbet_connections (user_id, viewer_name, rainbet_username)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET rainbet_username = excluded.rainbet_username
    ''', (interaction.user.id, interaction.user.display_name, username))
    conn.commit()

    # Confirm to user
    await interaction.response.send_message(
        f"✅ Your Rainbet username **{username}** has been recorded.")


# === CONFIG ===
ICON_SIZE = 100
GRID_SIZE = 5
PADDING = 10
WILD_IMAGE = "bingo_icons/wild.webp"
DB_PATH = "/mnt/data/west.db" if os.path.exists("/mnt/data") else "west.db"

SLOT_NAMES = ["2 Wild 2 Die", "5 Lions Megaways", "Beast Below", "Benny the Beer", "Big Bass Bonanza", "Book of Time", "Bouncy Bombs", "Chicken Man",
    "Cloud Princess", "Cursed Seas", "Dark Summoning", "Densho", "Donny Dough", "Donut Division", "Dork Unit", "Dragon's Domain", "Evil Eyes",
    "Extra Juicy", "Fear the Dark", "Fire Portals", "Fish Eye", "Fist of Destruction", "FRKN Bananas", "Fruit Party", "Fruit Party 2", "Fruity Treats",
    "Gates of Olympus", "Gates of Olympus 1000", "Gates of Olympus Super Scatter", "Gemhalla", "Gems Bonanza", "Hand of Anubis", "Heart of Cleopatra",
    "Hoot Shot the Sheriff", "Hot Fiesta", "Hounds of Hell", "Jewel Rush", "Le Bandit", "Le King", "Le Pharaoh", "Le Viking", "Life and Death",
    "Madame Destiny Megaways", "Mayan Stackways", "Outsourced", "Phoenix DuelReels", "Pickle Bandit", "Pirate Bonanza", "Power of Thor Megaways",
    "Pyrofox", "Rad Maxx", "Rise of Ymir", "Rotten", "SixSixSix", "Sky Bounty", "Slayers Inc", "Starlight Princess", "Starlight Princess 1000",
    "Stormforged", "Sugar Rush", "Sugar Rush 1000", "Sugar Supreme Powernudge", "Sweet Bonanza 1000", "Ultimate Slot of America", "Vending Machine",
    "Wanted Dead or a Wild", "Wheel o'Gold", "Wild Bison Charge", "Wild West Gold", "Wild West Gold Blazing Bounty", "Wild West Gold Megaways",
    "Wings of Horus", "Wisdom of Athena 1000", "Zeus vs Hades Gods of War", "Ze Zeus"]

# === FUNCTIONS ===

def sanitize_filename(name):
    return (
        name.lower()
        .replace(" ", "_")
        .replace(":", "")
        .replace("’", "")
        .replace("'", "")
        .replace('"', "")
    ) + ".webp"

def generate_bingo_card(conn):
    from random import sample

    cursor = conn.cursor()
    cursor.execute("SELECT slot_name FROM slots WHERE is_marked = 0")
    available_slots = [row[0] for row in cursor.fetchall()]

    if len(available_slots) < 24:
        raise ValueError("Not enough unmarked slots left to generate a card.")

    chosen_slots = sample(available_slots, 24)
    chosen_slots.insert(12, "WILD")  # Middle of the 5x5 grid

    # Reshape into 5x5 grid
    card = [chosen_slots[i*5:(i+1)*5] for i in range(5)]
    return card



def load_slot_icon(slot_name):
    possible_filenames = [
        slot_name.lower().replace(" ", "_").replace(":", "") + ".webp",
        slot_name.lower().replace(" ", "").replace(":", "") + ".webp",
        slot_name + ".webp",
    ]

    for fname in possible_filenames:
        path = os.path.join("bingo_icons", fname)
        if os.path.exists(path):
            return Image.open(path).resize((ICON_SIZE, ICON_SIZE))

    # fallback gray box if nothing found
    return Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (50, 50, 50, 255))

from PIL import ImageFont

def build_bingo_image(card, marked_slots=[]):
    letters = ["B", "O", "N", "U", "S"]
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)

    header_height = 50
    img_width = ICON_SIZE * GRID_SIZE + PADDING * (GRID_SIZE + 1)
    img_height = header_height + ICON_SIZE * GRID_SIZE + PADDING * (GRID_SIZE + 1)

    card_img = Image.new("RGBA", (img_width, img_height), (20, 20, 20, 255))
    draw = ImageDraw.Draw(card_img)

    # Draw B O N U S header
    for col in range(GRID_SIZE):
        bbox = draw.textbbox((0, 0), letters[col], font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = PADDING + col * (ICON_SIZE + PADDING) + (ICON_SIZE - w) // 2
        y = (header_height - h) // 2
        draw.text((x, y), letters[col], font=font, fill=(255, 215, 0))

    # Draw slots
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            slot = card[row][col]
            x = PADDING + col * (ICON_SIZE + PADDING)
            y = header_height + PADDING + row * (ICON_SIZE + PADDING)

            # Load icon
            if row == 2 and col == 2:
                icon = Image.open(WILD_IMAGE).resize((ICON_SIZE, ICON_SIZE))
                is_marked = True
            else:
                icon = load_slot_icon(slot)
                is_marked = slot in marked_slots

            # Paste icon
            card_img.paste(icon, (x, y))

            # Draw border if marked
            if is_marked:
                border_color = (255, 215, 0, 255)  # gold
                border_thickness = 4
                for i in range(border_thickness):
                    draw.rectangle(
                        [x - i, y - i, x + ICON_SIZE + i - 1, y + ICON_SIZE + i - 1],
                        outline=border_color
                    )

    return card_img

# === BINGO COG ===
class BingoBonus(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bingo_cards (
                user_id TEXT PRIMARY KEY,
                username TEXT,
                card TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                slot_name TEXT PRIMARY KEY,
                is_marked INTEGER DEFAULT 0
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS slot_owners (
                slot_name TEXT,
                user_id TEXT,
                UNIQUE(slot_name, user_id)
            )
        """)
        self.conn.commit()

        # ✅ Init slot list if not done yet
        initialize_slots_table(self.conn)

    @app_commands.command(name="bingo_bonus_join", description="Join the Bingo Bonus event and get your card")
    async def bingo_bonus_join(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            user_id = str(interaction.user.id)
            username = interaction.user.name  # ✅ Get username

            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM bingo_cards WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                await interaction.followup.send(
                    "🎟️ You already have a Bingo Bonus card! Use `/bingo_bonus_card`.",
                    ephemeral=True
                )
                return

            # ✅ Generate new card
            card = generate_bingo_card(self.conn)

            # ✅ Insert into DB
            cursor.execute(
                "INSERT INTO bingo_cards (user_id, username, card) VALUES (?, ?, ?)",
                (user_id, username, json.dumps(card))
            )
            self.conn.commit()

            await interaction.followup.send(
                "✅ Card created! Use `/bingo_bonus_card` to view it.",
                ephemeral=True
            )

        except Exception as e:
            print(f"❌ Error in /bingo_bonus_join: {e}")
            await interaction.followup.send(
                "⚠️ An error occurred while creating your card. Please try again.",
                ephemeral=True
            )


    @app_commands.command(name="bingo_bonus_card", description="Show your Bingo Bonus card")
    async def bingo_bonus_card(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        cursor = self.conn.cursor()
        cursor.execute("SELECT card FROM bingo_cards WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if not result:
            await interaction.response.send_message("❌ You don't have a card yet. Use `/bingo_bonus_join`.", ephemeral=True)
            return

        card = json.loads(result[0])

        # Fetch globally marked slots
        cursor.execute("SELECT slot_name FROM slots WHERE is_marked = 1")
        marked_slots = [row[0] for row in cursor.fetchall()]

        # Build image with marked slots highlighted
        image = build_bingo_image(card, marked_slots)
        image.save("temp_card.png")

        file = discord.File("temp_card.png", filename="bingo_card.png")
        embed = discord.Embed(title="🎰 Your BINGO BONUS Card", color=discord.Color.gold())
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_image(url="attachment://bingo_card.png")
        await interaction.response.send_message(file=file, embed=embed, ephemeral=False)

    @app_commands.command(name="mark_slot", description="Mark a slot as played")
    @app_commands.describe(slot_name="Select slot to mark as played")
    async def mark_slot(self, interaction: discord.Interaction, slot_name: str):
        # Optional admin check
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message("⛔ Only the admin can mark slots.", ephemeral=True)
            return

        cursor = self.conn.cursor()

        # Check if slot exists
        cursor.execute("SELECT 1 FROM slots WHERE slot_name = ?", (slot_name,))
        if not cursor.fetchone():
            await interaction.response.send_message(f"❌ Slot '{slot_name}' not found.", ephemeral=True)
            return

        # Mark it
        cursor.execute("UPDATE slots SET is_marked = 1 WHERE slot_name = ?", (slot_name,))

        # Find users who have that slot
        cursor.execute("SELECT user_id, card FROM bingo_cards")
        affected_users = []
        for user_id, card_json in cursor.fetchall():
            card = json.loads(card_json)
            flat_card = [slot for row in card for slot in row]
            if slot_name in flat_card:
                affected_users.append(user_id)

        self.conn.commit()

        # Format mention list
        mentions = " ".join(f"<@{uid}>" for uid in affected_users) if affected_users else "*No one had this slot.*"

        await interaction.response.send_message(
            f"🎉 **{slot_name}** has been marked — it BONUSED!\n\n{mentions}",
            ephemeral=False
        )

        # Get leaderboard
        leaders = get_top_players_close_to_win(self.conn)

        if leaders:
            leaderboard_msg = "**📊 Players closest to winning:**\n"
            for rank, (user_id, missing) in enumerate(leaders, start=1):
                leaderboard_msg += f"**{rank}.** <@{user_id}> – needs **{missing}** more\n"
            await interaction.channel.send(leaderboard_msg)

            winners = self.check_for_winners()
            if winners:
                mentions = ", ".join(f"<@{uid}>" for uid in winners)
                await interaction.channel.send(
                    f"🏆 **BINGO BONUS WINNERS!** 🏆\n\n🎉 Congrats {mentions} — you just completed a winning pattern!\nEvent ends here!"
                )

   # Autocomplete for slot names
    @mark_slot.autocomplete("slot_name")
    async def mark_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = self.conn.cursor()
        cursor.execute("SELECT slot_name FROM slots WHERE is_marked = 0 AND slot_name LIKE ?", (f"%{current}%",))
        slots = cursor.fetchall()
        return [
            app_commands.Choice(name=slot[0], value=slot[0])
            for slot in slots[:25]
        ]

    @app_commands.command(name="unmark_slot", description="Unmark a slot (admin only)")
    @app_commands.describe(slot_name="Slot to unmark")
    async def unmark_slot(self, interaction: discord.Interaction, slot_name: str):
        # Optional admin check
        if interaction.user.id not in ADMIN_IDS:
            await interaction.response.send_message("⛔ Only the admin can unmark slots.", ephemeral=True)
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT is_marked FROM slots WHERE slot_name = ?", (slot_name,))
        row = cursor.fetchone()

        if not row:
            await interaction.response.send_message(f"❌ Slot '{slot_name}' not found.", ephemeral=True)
            return

        if row[0] == 0:
            await interaction.response.send_message(f"⚠️ **{slot_name}** is already unmarked.", ephemeral=True)
            return

        # Unmark the slot
        cursor.execute("UPDATE slots SET is_marked = 0 WHERE slot_name = ?", (slot_name,))
        self.conn.commit()

        # Tag users who have this slot
        cursor.execute("SELECT user_id, card FROM bingo_cards")
        tagged_users = []
        for user_id, card_json in cursor.fetchall():
            card = json.loads(card_json)
            if any(slot_name in row for row in card):
                tagged_users.append(f"<@{user_id}>")

        tags = ", ".join(tagged_users) if tagged_users else "*No players have this slot.*"
        await interaction.response.send_message(
            f"❎ Unmarked **{slot_name}** as not bonused anymore.\n\n🔔 **Affected Players:** {tags}"
        )

        # Then show leaderboard again
        await show_leaderboard(interaction)

    @unmark_slot.autocomplete("slot_name")
    async def unmark_slot_autocomplete(self, interaction: discord.Interaction, current: str):
        cursor = self.conn.cursor()
        cursor.execute("SELECT slot_name FROM slots WHERE is_marked = 1 AND slot_name LIKE ?", (f"%{current}%",))
        slots = cursor.fetchall()
        return [
            app_commands.Choice(name=slot[0], value=slot[0])
            for slot in slots[:25]
        ]

    def check_for_winners(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT user_id, card FROM bingo_cards")
        players = cursor.fetchall()

        winners = []

        for user_id, card_json in players:
            card = json.loads(card_json)

            # Collect marked slots including center WILD
            marked = []
            for row in range(5):
                for col in range(5):
                    if row == 2 and col == 2:
                        marked.append("WILD")
                    else:
                        slot = card[row][col]
                        cursor.execute("SELECT is_marked FROM slots WHERE slot_name = ?", (slot,))
                        result = cursor.fetchone()
                        if result and result[0] == 1:
                            marked.append(slot)

            # Check if any pattern is completed
            for pattern in WINNING_PATTERNS:
                if all(card[r][c] == "WILD" or card[r][c] in marked for r, c in pattern):
                    winners.append(user_id)
                    break  # No need to check other patterns for this user

        return winners

@app_commands.command(name="bingo_bonus_hunt", description="List all unmarked slots to be played (random order)")
async def bingo_bonus_hunt(self, interaction: discord.Interaction):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message("⛔ Only the admin can use this command.", ephemeral=True)
        return

    cursor = self.conn.cursor()
    cursor.execute("SELECT slot_name FROM slots WHERE is_marked = 0")
    unmarked_slots = [row[0] for row in cursor.fetchall()]

    if not unmarked_slots:
        await interaction.response.send_message("✅ All slots have been marked already.", ephemeral=True)
        return

    random.shuffle(unmarked_slots)
    slots_per_page = 15
    total_pages = math.ceil(len(unmarked_slots) / slots_per_page)

    def get_page_embed(page):
        start = page * slots_per_page
        end = start + slots_per_page
        chunk = unmarked_slots[start:end]
        description = "\n".join(f"{start + j + 1}. {slot}" for j, slot in enumerate(chunk))
        embed = discord.Embed(
            title=f"🎯 Bingo Bonus Hunt — Page {page + 1}/{total_pages}",
            description=description,
            color=discord.Color.orange()
        )
        return embed

    class HuntPaginator(View):
        def __init__(self, ctx_id, user, conn):
            super().__init__(timeout=None)
            self.ctx_id = ctx_id
            self.user = user
            self.page = 0
            self.message = None
            self.conn = conn

        def is_valid_context(self, interaction):
            cursor = self.conn.cursor()
            cursor.execute("SELECT message_id FROM persistent_hunt WHERE guild_id = ?", (self.ctx_id,))
            row = cursor.fetchone()
            return row and self.message and self.message.id == row[0]

        @discord.ui.button(label="⏮ Prev", style=discord.ButtonStyle.gray)
        async def prev_button(self, interaction_button: discord.Interaction, button: Button):
            if interaction_button.user.id != self.user.id:
                return await interaction_button.response.send_message(
                    "❌ Only the command user can control pagination.", ephemeral=True)

            if not self.is_valid_context(interaction_button):
                return await interaction_button.response.send_message(
                    "❌ This hunt session is outdated. Run `/bingo_bonus_hunt` again to see the updated list.",
                    ephemeral=True
                )

            if self.page > 0:
                self.page -= 1
                await interaction_button.response.edit_message(embed=get_page_embed(self.page), view=self)

        @discord.ui.button(label="⏭ Next", style=discord.ButtonStyle.gray)
        async def next_button(self, interaction_button: discord.Interaction, button: Button):
            if interaction_button.user.id != self.user.id:
                return await interaction_button.response.send_message(
                    "❌ Only the command user can control pagination.", ephemeral=True)

            if not self.is_valid_context(interaction_button):
                return await interaction_button.response.send_message(
                    "❌ This hunt session is outdated. Run `/bingo_bonus_hunt` again to see the updated list.",
                    ephemeral=True
                )

            if self.page < total_pages - 1:
                self.page += 1
                await interaction_button.response.edit_message(embed=get_page_embed(self.page), view=self)

    # Show first page
    view = HuntPaginator(interaction.guild_id, interaction.user, self.conn)
    await interaction.response.send_message(embed=get_page_embed(0), view=view)
    view.message = await interaction.original_response()

    # ✅ Save in DB
    cursor.execute("""
        INSERT OR REPLACE INTO persistent_hunt (guild_id, message_id, channel_id, user_id)
        VALUES (?, ?, ?, ?)
    """, (interaction.guild_id, view.message.id, interaction.channel.id, interaction.user.id))
    self.conn.commit()

    @app_commands.command(name="bingo_bonus_reset", description="⚠️ Reset all marked slots and bingo cards")
    async def bingo_bonus_reset(self, interaction: discord.Interaction):
        if interaction.user.id != 488015447417946151:
            await interaction.response.send_message("⛔ Only the admin can reset the bingo event.", ephemeral=True)
            return

        conn = self.conn  # capture this outside to pass into the view

        class ConfirmResetView(View):
            def __init__(self, conn):
                super().__init__(timeout=30)
                self.conn = conn

            @discord.ui.button(label="✅ Confirm Reset", style=discord.ButtonStyle.danger)
            async def confirm(self, interaction_button: discord.Interaction, button: Button):
                if interaction.user != interaction_button.user:
                    await interaction_button.response.send_message("❌ Only the admin can confirm this reset.",
                                                                   ephemeral=True)
                    return

                cursor = self.conn.cursor()
                cursor.execute("UPDATE slots SET is_marked = 0")
                cursor.execute("DELETE FROM bingo_cards")
                self.conn.commit()

                await interaction_button.response.edit_message(
                    content="✅ All bingo cards and marked slots have been reset.", view=None)

            @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, interaction_button: discord.Interaction, button: Button):
                if interaction.user != interaction_button.user:
                    await interaction_button.response.send_message("❌ Only the admin can cancel this.", ephemeral=True)
                    return

                await interaction_button.response.edit_message(content="❌ Reset cancelled.", view=None)

        view = ConfirmResetView(conn)
        await interaction.response.send_message(
            "**⚠️ Are you sure you want to reset all Bingo Bonus progress?**\nThis will clear all player cards and unmark all 75 slots.",
            view=view,
            ephemeral=True
        )

def initialize_slots_table(conn):
    cursor = conn.cursor()
    for slot in SLOT_NAMES:
        cursor.execute("INSERT OR IGNORE INTO slots (slot_name) VALUES (?)", (slot,))
    conn.commit()

@app_commands.command(name="bingo_bonus_rules", description="Show the Bingo Bonus rules and winning patterns")
async def bingo_bonus_rules(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎯 BINGO BONUS RULES",
        description=(
            "**Whenever West is on a Bonus Hunt Event, the WEST BINGO BONUS is active!**\n\n"
            "🟡 **Only 75 slots** from the Bingo Cards will be played.\n"
            "🟡 For fairness, West will use the `/bingo_hunt` command to determine the slots order to play.\n"
            "🟡 West can end the hunt at any time.\n"
            "🟡 If he hunts again on a different day, he must run `/bingo_hunt` again to get new slots order to play.\n"
            "🟡 The event continues **until someone completes one of the winning patterns**.\n\n"
            "📌 Below are the **12 Winning Patterns** you need to complete to win!"
        ),
        color=discord.Color.gold()
    )
    embed.set_image(url="attachment://winning_patterns.png")

    file = discord.File("winning_patterns.png", filename="winning_patterns.png")
    await interaction.response.send_message(embed=embed, file=file, ephemeral=False)


WINNING_PATTERNS = [
    # Vertical B O N U S
    [(0,0), (1,0), (2,0), (3,0), (4,0)],
    [(0,1), (1,1), (2,1), (3,1), (4,1)],
    [(0,2), (1,2), (2,2), (3,2), (4,2)],
    [(0,3), (1,3), (2,3), (3,3), (4,3)],
    [(0,4), (1,4), (2,4), (3,4), (4,4)],
    # Horizontal lines
    [(0,0), (0,1), (0,2), (0,3), (0,4)],
    [(1,0), (1,1), (1,2), (1,3), (1,4)],
    [(2,0), (2,1), (2,2), (2,3), (2,4)],
    [(3,0), (3,1), (3,2), (3,3), (3,4)],
    [(4,0), (4,1), (4,2), (4,3), (4,4)],
    # Diagonals
    [(0,0), (1,1), (2,2), (3,3), (4,4)],
    [(0,4), (1,3), (2,2), (3,1), (4,0)]
]

def get_top_players_close_to_win(conn, top_n=3, max_missing=4):
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, card FROM bingo_cards")
    all_players = cursor.fetchall()

    player_progress = []

    for user_id, card_json in all_players:
        card = json.loads(card_json)

        # Flatten marked slots (includes WILD center slot)
        marked = []
        for row in range(5):
            for col in range(5):
                if row == 2 and col == 2:
                    marked.append("WILD")
                elif card[row][col]:
                    slot_name = card[row][col]
                    cursor.execute("SELECT is_marked FROM slots WHERE slot_name = ?", (slot_name,))
                    result = cursor.fetchone()
                    if result and result[0] == 1:
                        marked.append(slot_name)

        # Track how close the player is to winning
        fewest_missing = 5
        for pattern in WINNING_PATTERNS:
            needed = 0
            for r, c in pattern:
                slot = card[r][c]
                if slot != "WILD" and slot not in marked:
                    needed += 1
            fewest_missing = min(fewest_missing, needed)

        if fewest_missing <= max_missing:
            player_progress.append((user_id, fewest_missing))

    # Sort: closest to winning first
    player_progress.sort(key=lambda x: x[1])

    return player_progress[:top_n]
print("Loaded token:", TOKEN)
bot.run(TOKEN)

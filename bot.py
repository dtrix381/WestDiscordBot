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

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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

# Global variables
guesses = []
starting_balance_set = False
final_balance = None
starting_balance = 0

# Initialize SQLite database
conn = sqlite3.connect("west.db")
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

cursor.execute('INSERT OR IGNORE INTO gtb_state (id, active) VALUES (1, 0)')
cursor.execute('INSERT OR IGNORE INTO gtb_balances (id, starting_balance, final_balance) VALUES (1, NULL, NULL)')
conn.commit()


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

    # Sync Slash Commands
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} commands')
    except Exception as e:
        print(f'Error syncing commands: {e}')


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)  # Important for slash commands to work

    if message.channel.id == 1276958742373601374:
        new_nick = message.content.strip()

        # Optional: Validate nickname
        if 1 <= len(new_nick) <= 32:
            try:
                # Change the nickname
                await message.author.edit(nick=new_nick)

                # Add the verification role
                role = message.guild.get_role(1341577464601645066)
                if role:
                    await message.author.add_roles(role)
                await message.channel.send(
                    f"🚫 {message.author.mention}, only verified users can enter **Westside**. "
                    f"We need to confirm that your Kick username is **{new_nick}**. If it’s not, you’ll have to verify again."
                )
            except discord.Forbidden:
                await message.channel.send(
                    "❌ I don't have permission to change your nickname or assign the verification role."
                )
            except discord.HTTPException as e:
                await message.channel.send(
                    f"❌ We couldn’t verify your Kick username. Please check and try again. `{e}`"
                )
        else:
            await message.channel.send(
                "❌ Please send your Kick username (1–32 characters) to complete verification."
            )

        return  # Prevent further processing for this message

    if message.channel.id not in ALLOWED_CHANNEL_IDS:
        return

    # Check if GTB is active
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

    # Check for duplicate guess
    cursor.execute("SELECT 1 FROM gtb_guesses WHERE user_id = ?", (message.author.id,))
    if cursor.fetchone():
        await message.delete()
        await message.channel.send(f"{message.author.mention}, you've already submitted a guess.")
        return

    # Save guess to DB
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
    conn = sqlite3.connect('west.db')
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
    conn = sqlite3.connect('west.db')
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

print("Loaded token:", TOKEN)
bot.run(TOKEN)



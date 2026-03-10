import discord
from redbot.core import commands, Config
import aiomysql

class DatabaseConfigModal(discord.ui.Modal, title="Database Configuration"):
    host = discord.ui.TextInput(
        label="MySQL Host",
        placeholder="e.g., localhost",
        required=True
    )
    port = discord.ui.TextInput(
        label="MySQL Port",
        placeholder="e.g., 3306",
        default="3306",
        required=True
    )
    user = discord.ui.TextInput(
        label="MySQL Username",
        placeholder="e.g., root",
        required=True
    )
    password = discord.ui.TextInput(
        label="MySQL Password",
        placeholder="Enter password...",
        required=True
    )
    database = discord.ui.TextInput(
        label="Database Name",
        placeholder="e.g., nbzhc_stats",
        required=True
    )

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            port_num = int(self.port.value)
        except ValueError:
            await interaction.followup.send("Port must be a valid number.", ephemeral=True)
            return

        await self.config.host.set(self.host.value)
        await self.config.port.set(port_num)
        await self.config.user.set(self.user.value)
        await self.config.password.set(self.password.value)
        await self.config.database.set(self.database.value)

        await interaction.followup.send("Database credentials have been saved successfully.", ephemeral=True)


class NBZHCRank(commands.Cog):
    """A rank system for NBZHC."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847392183, force_registration=True)
        default_global = {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "",
            "database": ""
        }
        self.config.register_global(**default_global)

    @commands.command()
    @commands.is_owner()
    async def rankset(self, ctx: commands.Context):
        """Configure the MySQL Database credentials for the Rank cog.
        This opens a secure form to input the database details.
        """
        # Red uses traditional text commands. Modals require interactions.
        # We need a message with a button to trigger the modal if called via text command,
        # or we just use a slash command. Redbot supports slash commands natively now.
        # But `ctx` might be from a prefix command.
        
        view = discord.ui.View()
        button = discord.ui.Button(label="Configure Database", style=discord.ButtonStyle.primary)
        
        async def button_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("You are not authorized to use this.", ephemeral=True)
                return
            await interaction.response.send_modal(DatabaseConfigModal(self.config))
            
        button.callback = button_callback
        view.add_item(button)
        
        await ctx.send("Click the button below to configure the database credentials securely.", view=view)

    async def get_db_connection(self):
        """Helper to get a database connection using Config credentials."""
        config_data = await self.config.all()
        # Ensure all required fields are set
        if not all([config_data.get("host"), config_data.get("user"), config_data.get("database")]):
            raise ValueError("Database credentials are not fully configured.")

        # Let exceptions bubble up so we can print the exact reason to Discord
        conn = await aiomysql.connect(
            host=config_data["host"],
            port=config_data["port"],
            user=config_data["user"],
            password=config_data["password"],
            db=config_data["database"],
            autocommit=True
        )
        return conn

    @commands.command()
    async def rank(self, ctx: commands.Context, *, playername: str):
        """Fetch and display rank statistics for a specific player."""
        
        # 1. Provide an initial feedback message since DB queries might take a second
        # and we don't want the bot to seem unresponsive.
        async with ctx.typing():
            try:
                conn = await self.get_db_connection()
            except Exception as e:
                await ctx.send(f"Database connection could not be established. Please check your credentials using `[p]rankset`. \n**Error details:** `{type(e).__name__}: {e}`")
                return

            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    # Execute secure parameterized query to prevent SQL injection
                    await cur.execute("SELECT * FROM players WHERE name = %s", (playername,))
                    player_data = await cur.fetchone()
            except Exception as e:
                await ctx.send(f"An error occurred while querying the database: `{e}`")
                return
            finally:
                conn.close()

            # 2. Check if player exists
            if not player_data:
                await ctx.send(f"Could not find any rank statistics for a player named **{playername}**.")
                return

            # 3. Build the Embed
            # Fields: name, capt_pvp_tons, capt_ai_tons, crew_pvp_tons, crew_ai_tons, deahts, 
            # player_kills, villager_kills, survivor_kills, survivor_saves, aa_dmg, cannon_dmg, collision_dmg, torpedo_dmg
            
            # Using a nice standard embed color, can be customized later
            color = await ctx.embed_color() 
            embed = discord.Embed(
                title=f"Rank Statistics: {player_data.get('name', playername)}",
                color=color
            )
            
            # Group stats logically for better readability
            # Captain Stats
            capt_stats = (
                f"**PvP Tons:** {player_data.get('capt_pvp_tons', 0):,}\n"
                f"**AI Tons:** {player_data.get('capt_ai_tons', 0):,}"
            )
            embed.add_field(name="🚢 Captain Stats", value=capt_stats, inline=True)

            # Crew Stats
            crew_stats = (
                f"**PvP Tons:** {player_data.get('crew_pvp_tons', 0):,}\n"
                f"**AI Tons:** {player_data.get('crew_ai_tons', 0):,}"
            )
            embed.add_field(name="⚓ Crew Stats", value=crew_stats, inline=True)
            
            # Empty field for formatting (to keep rows of 3 if desired, or just let Discord wrap it)
            embed.add_field(name="\u200B", value="\u200B", inline=True)

            # Combat Stats 1
            combat_stats_1 = (
                f"**Player Kills:** {player_data.get('player_kills', 0):,}\n"
                f"**Villager Kills:** {player_data.get('villager_kills', 0):,}\n"
                f"**Deaths:** {player_data.get('deahts', 0):,}" # Using exact column name 'deahts' from prompt
            )
            embed.add_field(name="⚔️ Combat", value=combat_stats_1, inline=True)

            # Survivor Stats
            survivor_stats = (
                f"**Survivor Sills:** {player_data.get('survivor_kills', 0):,}\n"
                f"**Survivor Saves:** {player_data.get('survivor_saves', 0):,}"
            )
            embed.add_field(name="🏊 Survivor Interaction", value=survivor_stats, inline=True)
            
            embed.add_field(name="\u200B", value="\u200B", inline=True)
            
            # Damage Stats
            damage_stats = (
                f"**AA Damage:** {player_data.get('aa_dmg', 0):,}\n"
                f"**Cannon Damage:** {player_data.get('cannon_dmg', 0):,}\n"
                f"**Torpedo Damage:** {player_data.get('torpedo_dmg', 0):,}\n"
                f"**Collision Damage:** {player_data.get('collision_dmg', 0):,}"
            )
            embed.add_field(name="💥 Damage Output", value=damage_stats, inline=False)
            
            # Add a footer with standard redbot avatar or just text
            embed.set_footer(text="Data provided by NBZHC Database")

            # 4. Send the embed
            await ctx.send(embed=embed)


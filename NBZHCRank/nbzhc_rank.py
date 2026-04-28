import discord
from redbot.core import commands, Config
import aiomysql
import aiohttp

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

    def __init__(self, config: Config, msg: discord.Message):
        super().__init__()
        self.config = config
        self.msg = msg

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        # discord.ui.TextInput values are strings. We must cast the port.
        try:
            port_num = int(self.port.value)
        except ValueError:
            await interaction.followup.send("Port must be a valid number.", ephemeral=True)
            return

        # Print debug to terminal
        print(f"[NBZHCRank Debug] Modal Submit:")
        print(f"Host: '{self.host.value}'")
        print(f"User: '{self.user.value}'")
        print(f"DB: '{self.database.value}'")

        # Use set() directly on the variables to store them in the root of the cog's global config
        # We use db_ prefix to avoid collisions with Redbot's built-in config groups like config.user()
        await self.config.db_host.set(str(self.host.value))
        await self.config.db_port.set(port_num)
        await self.config.db_user.set(str(self.user.value))
        await self.config.db_password.set(str(self.password.value))
        await self.config.db_name.set(str(self.database.value))
        
        # Verify it saved
        # Deleted logging config.all() to prevent password leak

        # Delete the original prompt message
        try:
            if self.msg:
                await self.msg.delete()
        except discord.HTTPException:
            pass

        await interaction.followup.send("Database credentials have been saved successfully.", ephemeral=True)


class NBZHCRank(commands.Cog):
    """A rank system for NBZHC."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847392183, force_registration=True)
        
        # We need to register the defaults so they act as a base structure
        default_global = {
            "db_host": "",
            "db_port": 3306,
            "db_user": "",
            "db_password": "",
            "db_name": ""
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
        
        msg = None
        
        async def button_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("You are not authorized to use this.", ephemeral=True)
                return
            await interaction.response.send_modal(DatabaseConfigModal(self.config, msg))
            
        button.callback = button_callback
        view.add_item(button)
        
        msg = await ctx.send("Click the button below to configure the database credentials securely.", view=view)

    async def get_db_connection(self):
        """Helper to get a database connection using Config credentials."""
        config_data = await self.config.all()
        # Deleted logging config.all() to prevent password leak
        # Ensure all required fields are set
        if not all([config_data.get("db_host"), config_data.get("db_user"), config_data.get("db_name")]):
            raise ValueError("Database credentials are not fully configured.")

        # Let exceptions bubble up so we can print the exact reason to Discord
        conn = await aiomysql.connect(
            host=config_data["db_host"],
            port=config_data["db_port"],
            user=config_data["db_user"],
            password=config_data["db_password"],
            db=config_data["db_name"],
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
            except Exception:
                await ctx.send("Database connection could not be established. Please check your credentials using `[p]rankset`.")
                return

            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    # Execute secure parameterized query to prevent SQL injection
                    await cur.execute("SELECT * FROM players WHERE name = %s", (playername,))
                    player_data = await cur.fetchone()

                    # Fallback to Mojang API UUID lookup if player name is not found
                    if not player_data:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(f"https://api.mojang.com/users/profiles/minecraft/{playername}") as resp:
                                if resp.status == 200:
                                    mojang_data = await resp.json()
                                    uuid = mojang_data.get("id")
                                    if uuid:
                                        # Mojang API provides UUIDs without hyphens
                                        formatted_uuid = f"{uuid[:8]}-{uuid[8:12]}-{uuid[12:16]}-{uuid[16:20]}-{uuid[20:]}"
                                        
                                        # Check the players table mapping for this uuid (both formatted and unformatted)
                                        await cur.execute("SELECT * FROM players WHERE uuid = %s OR uuid = %s", (uuid, formatted_uuid))
                                        player_data = await cur.fetchone()
            except Exception:
                await ctx.send("An error occurred while querying the database.")
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
                f"**Deaths:** {player_data.get('deaths', 0):,}"
            )
            embed.add_field(name="⚔️ Combat", value=combat_stats_1, inline=True)

            # Survivor Stats
            survivor_stats = (
                f"**Survivor Kills:** {player_data.get('survivor_kills', 0):,}\n"
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

            # Rank lookup map (1 to 35)
            # Extrapolated with enlisted, warrant, midshipmen, and officers based on standard naval gradations
            rank_map = {
                1: "Seaman Recruit",
                2: "Seaman Apprentice",
                3: "Seaman",
                4: "Petty Officer 3rd Class",
                5: "Petty Officer 2nd Class",
                6: "Petty Officer 1st Class",
                7: "Chief Petty Officer",
                8: "Senior Chief Petty Officer",
                9: "Master Chief Petty Officer",
                10: "Warrant Officer",
                11: "Chief Warrant Officer 1",
                12: "Chief Warrant Officer 2",
                13: "Chief Warrant Officer 3",
                14: "Cheif Warrant Officer 4",
                15: "Chief Warrant Officer 5",
                21: "Ensign",
                22: "Lieutenant Junior Grade",
                23: "Lieutenant",
                24: "Lieutenant Commander",
                25: "Commander",
                26: "Captain",
                30: "Commodore Admiral",
                31: "Rear Admiral Lower Half",
                32: "Rear Admiral Upper Half",
                33: "Vice Admiral",
                34: "Admiral",
                35: "Fleet Admiral"
            }
            # Fallback to "Unknown (ID: #)" if an unexpected rank ID appears
            raw_rank_id = player_data.get('hcn_rank_id', 0)
            rank_name = rank_map.get(raw_rank_id, f"Unknown (ID: {raw_rank_id})")

            # Convert playtime from Milliseconds to Days, Hours, Minutes
            raw_playtime_ms = player_data.get('total_time', 0)
            if raw_playtime_ms > 0:
                seconds = raw_playtime_ms // 1000
                minutes = (seconds // 60) % 60
                hours = (seconds // 3600) % 24
                days = seconds // 86400
                
                parts = []
                if days > 0: parts.append(f"{days}d")
                if hours > 0: parts.append(f"{hours}h")
                if minutes > 0: parts.append(f"{minutes}m")
                playtime_str = " ".join(parts) if parts else "< 1m"
            else:
                playtime_str = "0m"

            #Rank & Playtime
            rank_playtime_stats = (
                f"**Rank:** {rank_name}\n"
                f"**Playtime:** {playtime_str}"
            )
            embed.add_field(name="🏅 Rank & Playtime", value=rank_playtime_stats, inline=True)
            
            # Add a footer with standard redbot avatar or just text
            embed.set_footer(text="Data provided by NBZHC Database")

            # 4. Send the embed
            await ctx.send(embed=embed)


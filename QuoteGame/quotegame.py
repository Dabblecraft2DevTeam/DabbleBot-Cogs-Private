import discord
from redbot.core import commands, Config
import asyncio
import random
import aiohttp
import re
from datetime import datetime, timezone
from .historical_quotes import HISTORICAL_QUOTES

class VotingView(discord.ui.View):
    \"\"\"View for voting on the best quote game answer.\"\"\"
    def __init__(self, cog, guild, options):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild = guild
        self.options = options
        
        for i, option in enumerate(options):
            # Buttons have a max label length of 100
            label = (option[:97] + \"...\") if len(option) > 100 else option
            button = discord.ui.Button(label=label, custom_id=f\"qg_vote_{guild.id}_{i}\")
            button.callback = self.button_callback
            self.add_component(button)

    async def button_callback(self, interaction: discord.Interaction):
        # Prevent non-guild members or bots from voting
        if not interaction.guild or interaction.user.bot:
            return await interaction.response.send_message(\"Invalid interaction.\", ephemeral=True)
            
        game_data = await self.cog.config.guild(self.guild).current_game()
        if not game_data or game_data.get(\"phase\") != \"voting\":
            return await interaction.response.send_message(\"The voting phase is no longer active.\", ephemeral=True)
            
        # Extract the option index from the custom_id
        try:
            option_index = int(interaction.data[\"custom_id\"].split(\"_\")[-1])
        except (ValueError, KeyError, IndexError):
            return await interaction.response.send_message(\"Error processing vote.\", ephemeral=True)
            
        votes = game_data.get(\"votes\", {})
        user_id = str(interaction.user.id)
        
        if user_id in game_data.get(\"voted_users\", []):
            return await interaction.response.send_message(\"You have already voted in this game!\", ephemeral=True)
            
        # Record the vote
        votes[option_index] = votes.get(option_index, 0) + 1
        if \"voted_users\" not in game_data:
            game_data[\"voted_users\"] = []
        game_data[\"voted_users\"].append(user_id)
        game_data[\"votes\"] = votes
        
        await self.cog.config.guild(self.guild).current_game.set(game_data)
        await interaction.response.send_message(f\"You voted for: **{self.options[option_index]}**\", ephemeral=True)

class QuoteGame(commands.Cog):
    \"\"\"Weekly Fill-in-the-blank Quote Game\"\"\"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8472948375)
        default_guild = {
            \"channel_id\": None,
            \"is_active\": False,
            \"current_game\": {},
            \"last_game_time\": 0,
            \"current_winners\": [],
            \"min_submissions\": 1,
            \"ping_role_id\": 1008940890678636544,
            \"winner_role_id\": 1484535497651916872
        }
        self.config.register_guild(**default_guild)
        self.loop_task = bot.loop.create_task(self.game_loop())

    def cog_unload(self):
        if self.loop_task:
            self.loop_task.cancel()

    @commands.group(aliases=[\"qg\"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quotegame(self, ctx):
        \"\"\"Settings for QuoteGame\"\"\"
        pass

    @quotegame.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        \"\"\"Set the channel for the weekly quote game.\"\"\"
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f\"Quote game channel set to {channel.mention}.\")

    @quotegame.command()
    async def setpingrole(self, ctx, role: discord.Role):
        \"\"\"Set the role to ping when a game starts or ends.\"\"\"
        await self.config.guild(ctx.guild).ping_role_id.set(role.id)
        await ctx.send(f\"Ping role set to {role.mention}.\")

    @quotegame.command()
    async def setwinnerrole(self, ctx, role: discord.Role):
        \"\"\"Set the role given to winners.\"\"\"
        await self.config.guild(ctx.guild).winner_role_id.set(role.id)
        await ctx.send(f\"Winner role set to {role.mention}.\")

    @quotegame.command()
    async def toggle(self, ctx, state: bool):
        \"\"\"Enable or disable the weekly game in this server.\"\"\"
        await self.config.guild(ctx.guild).is_active.set(state)
        await ctx.send(f\"Quote game is now {'enabled' if state else 'disabled'}.\")
        
    @quotegame.command()
    async def force(self, ctx):
        \"\"\"Force start a game right now in the configured channel.\"\"\"
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            return await ctx.send(\"Please set a channel first.\")
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send(\"Configured channel not found.\")
        
        await self.start_game(ctx.guild, channel)
        await ctx.send(\"Forced a new game to start.\")

    @quotegame.command()
    async def setmin(self, ctx, amount: int):
        \"\"\"Set the minimum number of submissions required to proceed to voting.\"\"\"
        if amount < 1:
            return await ctx.send(\"Minimum submissions must be at least 1.\")
        await self.config.guild(ctx.guild).min_submissions.set(amount)
        await ctx.send(f\"Minimum submissions set to {amount}.\")

    async def fetch_quote(self):
        # Always pick from our historical quotes list
        return random.choice(HISTORICAL_QUOTES)

    def blank_out_word(self, quote):
        # Find words that are >= 4 letters
        words = re.findall(r'\\b[a-zA-Z]{4,}\\b', quote)
        if not words:
            words = re.findall(r'\\b[a-zA-Z]+\\b', quote)
        
        if not words:
            return quote, \"\" # Fallback if quote is completely unusual

        target = random.choice(words)
        
        # Replace just one occurrence, keeping punctuation intact
        blanked = re.sub(rf'\\b{re.escape(target)}\\b', '________', quote, count=1, flags=re.IGNORECASE)
        return blanked, target

    async def start_game(self, guild, channel):
        # Remove role from previous winners
        winner_role_id = await self.config.guild(guild).winner_role_id()
        role = guild.get_role(winner_role_id)
        if role:
            winners = await self.config.guild(guild).current_winners()
            for uid in winners:
                member = guild.get_member(int(uid))
                if member:
                    try:
                        await member.remove_roles(role)
                    except discord.Forbidden:
                        pass
        await self.config.guild(guild).current_winners.set([])

        quote, author = await self.fetch_quote()
        blanked, target_word = self.blank_out_word(quote)
        
        embed = discord.Embed(title=\"Weekly Quote Game: Fill in the Blank!\",
                              description=f\"**Quote:**\\n\\\"{blanked}\\\"\\n- {author}\\n\\n\"\
                                          \"**How to play:**\\nType your 1-word answer directly in this channel! \"\
                                          \"I will instantly delete your message to keep your answer a secret. \"\
                                          \"In 24 hours, everyone will get to vote on the best or funniest answer!\",
                              color=discord.Color.blue())
        embed.set_footer(text=\"Submissions: 0\")
        
        ping_role_id = await self.config.guild(guild).ping_role_id()
        ping_content = f\"<@&{ping_role_id}>\" if ping_role_id else \"\"
        
        try:
            msg = await channel.send(content=ping_content, embed=embed)
        except discord.Forbidden:
            return # Bot cannot send messages in this channel
            
        game_data = {
            \"quote\": quote,
            \"blanked\": blanked,
            \"author\": author,
            \"target_word\": target_word,
            \"start_time\": datetime.now(timezone.utc).timestamp(),
            \"phase\": \"answering\",
            \"answers\": {},  # user_id (str) -> answer (str)
            \"message_id\": msg.id
        }
        await self.config.guild(guild).current_game.set(game_data)
        await self.config.guild(guild).last_game_time.set(datetime.now(timezone.utc).timestamp())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
            
        channel_id = await self.config.guild(message.guild).channel_id()
        if not channel_id or message.channel.id != channel_id:
            return
            
        game_data = await self.config.guild(message.guild).current_game()
        if not game_data or game_data.get(\"phase\") != \"answering\":
            return
            
        # Check if it's a 1-word answer (allow simple punctuation but primarily 1 word)
        words = message.content.strip().split()
        if len(words) == 1:
            # Record it
            user_id = str(message.author.id)
            word = words[0]
            # Max length for an answer, cap at 32 characters
            if len(word) > 32:
                return
                
            game_data[\"answers\"][user_id] = word
            await self.config.guild(message.guild).current_game.set(game_data)
            
            # Update the embed footer with live submissions count
            message_id = game_data.get(\"message_id\")
            if message_id:
                try:
                    game_msg = await message.channel.fetch_message(message_id)
                    if game_msg.embeds:
                        embed = game_msg.embeds[0]
                        embed.set_footer(text=f\"Submissions: {len(game_data['answers'])}\")
                        await game_msg.edit(embed=embed)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            
            # Try to delete the user's message to keep it a secret
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

    async def game_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                all_data = await self.config.all_guilds()
                for guild_id, data in all_data.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue
                        
                    if not data.get(\"is_active\"):
                        continue
                        
                    channel_id = data.get(\"channel_id\")
                    if not channel_id:
                        continue
                        
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue
                        
                    current_game = data.get(\"current_game\")
                    last_game_time = data.get(\"last_game_time\", 0)
                    now = datetime.now(timezone.utc).timestamp()
                    
                    if not current_game:
                        # Check if a week has passed (7 days = 604800 seconds)
                        if now - last_game_time >= 604800:
                            await self.start_game(guild, channel)
                    else:
                        phase = current_game.get(\"phase\")
                        start_time = current_game.get(\"start_time\", 0)
                        
                        if phase == \"answering\":
                            # 24 hours = 86400 seconds
                            if now - start_time >= 86400:
                                await self.start_voting(guild, channel, current_game)
                        elif phase == \"voting\":
                            # Wait another 24 hours for voting
                            vote_start = current_game.get(\"vote_start_time\", start_time)
                            if now - vote_start >= 86400:
                                await self.end_game(guild, channel, current_game)
            except Exception as e:
                import logging
                logging.getLogger(\"red.quotegame\").error(\"Error in QuoteGame loop\", exc_info=e)
                
            await asyncio.sleep(60)  # Check every minute

    async def start_voting(self, guild, channel, game_data):
        answers = game_data.get(\"answers\", {})
        real_word = game_data.get(\"target_word\", \"\")
        
        ping_role_id = await self.config.guild(guild).ping_role_id()
        ping_content = f\"<@&{ping_role_id}>\" if ping_role_id else \"\"

        if len(answers) == 1 and not game_data.get(\"extended\"):
            try:
                await channel.send(content=f\"{ping_content}\\nOnly one person has submitted an answer so far! Extending the answering phase by another 24 hours.\")
            except discord.Forbidden:
                pass
            game_data[\"extended\"] = True
            game_data[\"start_time\"] = game_data.get(\"start_time\", datetime.now(timezone.utc).timestamp()) + 86400
            await self.config.guild(guild).current_game.set(game_data)
            return
            
        min_subs = await self.config.guild(guild).min_submissions()
        if len(answers) < min_subs:
            try:
                if min_subs == 1:
                    await channel.send(content=f\"{ping_content}\\nThe quote game ended, but nobody submitted any answers! 😢\\n\\nFor the record, the missing word was **{real_word}**.\")
                else:
                    await channel.send(content=f\"{ping_content}\\nThe quote game ended, but not enough people submitted answers (Minimum: {min_subs})! 😢\\n\\nFor the record, the missing word was **{real_word}**.\")
            except discord.Forbidden:
                pass
            await self.config.guild(guild).current_game.set({})\n            return
            
        unique_answers = list(set(answers.values()))
        if real_word not in unique_answers:
            unique_answers.append(real_word)
            
        random.shuffle(unique_answers)
        
        # Limit to 25 options because of Discord button limits (5 rows * 5 buttons)
        if len(unique_answers) > 25:
            if real_word in unique_answers:
                unique_answers.remove(real_word)
                unique_answers = random.sample(unique_answers, 24)
                unique_answers.append(real_word)
                random.shuffle(unique_answers)
            else:
                unique_answers = random.sample(unique_answers, 25)
                
        desc = f\"**Quote:**\\n\\\"{game_data['blanked']}\\\"\\n\\n**Vote for the best answer by clicking the buttons below!**\"
        
        embed = discord.Embed(title=\"Quote Game: Voting Phase!\",
                              description=desc,
                              color=discord.Color.green())
        
        view = VotingView(self, guild, unique_answers)
        
        try:
            msg = await channel.send(content=ping_content, embed=embed, view=view)
        except discord.Forbidden:
            await self.config.guild(guild).current_game.set({})
            return
            
        game_data[\"phase\"] = \"voting\"
        game_data[\"vote_start_time\"] = datetime.now(timezone.utc).timestamp()
        game_data[\"poll_message_id\"] = msg.id
        game_data[\"options\"] = unique_answers
        game_data[\"votes\"] = {i: 0 for i in range(len(unique_answers))}
        game_data[\"voted_users\"] = []
        
        await self.config.guild(guild).current_game.set(game_data)

    async def end_game(self, guild, channel, game_data):
        poll_msg_id = game_data.get(\"poll_message_id\")
        options = game_data.get(\"options\", [])
        votes = game_data.get(\"votes\", {})
        
        try:
            # Remove buttons by editing the message
            try:
                msg = await channel.fetch_message(poll_msg_id)
                await msg.edit(view=None)
            except (discord.NotFound, discord.Forbidden):
                pass
            
            results = []
            for index, count in votes.items():
                if index < len(options):
                    results.append((options[index], count))
                        \n            if results:\n                results.sort(key=lambda x: x[1], reverse=True)\n                winner_ans, winner_votes = results[0]\n                \n                real_word = game_data.get(\"target_word\", \"\")\n                \n                if winner_votes == 0:\n                    winner_text = f\"Nobody voted! 😢\\n\\nFor the record, the real word was **{real_word}**.\"\n                else:\n                    top_vote = winner_votes\n                    tied_answers = [ans for ans, votes_val in results if votes_val == top_vote]\n                    \n                    winner_text_parts = []\n                    new_winners = []\n                    for ans in tied_answers:\n                        winners = []\n                        for uid, submitted_ans in game_data.get(\"answers\", {}).items():\n                            if submitted_ans.lower() == ans.lower():\n                                winners.append(f\"<@{uid}>\")\n                                new_winners.append(uid)\n                                \n                        if not winners:\n                            if ans.lower() == real_word.lower():\n                                winners_mentions = \"The Original Quote\"\n                            else:\n                                winners_mentions = \"Nobody (Option was present but not found in submissions)\"\n                        else:\n                            winners_mentions = \", \".join(winners)\n                            \n                        winner_text_parts.append(f\"**{ans}** (by {winners_mentions})\")\n                        \n                    if len(tied_answers) == 1:\n                        if winner_ans.lower() == real_word.lower():\n                            winner_text = f\"The winning answer was **{winner_ans}** with {top_vote} votes!\\nThis was actually the real word!\"\n                        else:\n                            winner_text = f\"The winning answer was {winner_text_parts[0]} with {top_vote} votes!\\n\\nThe real word was **{real_word}**.\"\n                    else:\n                        tied_str = \"\\n\".join(f\"- {p}\" for p in winner_text_parts)\n                        winner_text = f\"It's a tie with {top_vote} votes each for:\\n{tied_str}\\n\\nThe real word was **{real_word}**.\"\n                        \n                    if new_winners:\n                        winner_role_id = await self.config.guild(guild).winner_role_id()\n                        role = guild.get_role(winner_role_id)\n                        if role:\n                            for uid in new_winners:\n                                member = guild.get_member(int(uid))\n                                if member:\n                                    try:\n                                        await member.add_roles(role)\n                                    except discord.Forbidden:\n                                        pass\n                        await self.config.guild(guild).current_winners.set(new_winners)\n                        \n                full_quote = game_data.get('quote', \"\")\n                author = game_data.get('author', \"\")\n                \n                embed = discord.Embed(title=\"Quote Game: Results!\",\n                                      description=f\"{winner_text}\\n\\n**Original Quote:**\\n\\\"{full_quote}\\\"\\n- {author}\",\n                                      color=discord.Color.gold())\n                \n                ping_role_id = await self.config.guild(guild).ping_role_id()\n                ping_content = f\"<@&{ping_role_id}>\" if ping_role_id else \"\"\n                await channel.send(content=ping_content, embed=embed)\n            else:\n                ping_role_id = await self.config.guild(guild).ping_role_id()\n                ping_content = f\"<@&{ping_role_id}>\" if ping_role_id else \"\"\n                await channel.send(content=f\"{ping_content}\\nCouldn't read the poll results!\\nFor the record, the real word was **{game_data.get('target_word', '')}**.\")\n                \n        except discord.NotFound:\n            ping_role_id = await self.config.guild(guild).ping_role_id()\n            ping_content = f\"<@&{ping_role_id}>\" if ping_role_id else \"\"\n            try:\n                await channel.send(content=f\"{ping_content}\\nThe voting message was deleted, so I couldn't count the votes!\\nFor the record, the real word was **{game_data.get('target_word', '')}**.\")\n            except discord.Forbidden:\n                pass\n        except discord.Forbidden:\n            pass\n        except Exception as e:\n            import logging\n            logging.getLogger(\"red.quotegame\").error(\"Error in end_game\", exc_info=e)\n            \n        await self.config.guild(guild).current_game.set({})\n
import discord
from redbot.core import commands, Config
import asyncio
import random
import aiohttp
import re
from datetime import datetime
from .historical_quotes import HISTORICAL_QUOTES

class QuoteGame(commands.Cog):
    """Weekly Fill-in-the-blank Quote Game"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8472948375)
        default_guild = {
            "channel_id": None,
            "is_active": False,
            "current_game": {},
            "last_game_time": 0,
            "current_winners": []
        }
        self.config.register_guild(**default_guild)
        self.loop_task = bot.loop.create_task(self.game_loop())

    def cog_unload(self):
        if self.loop_task:
            self.loop_task.cancel()

    @commands.group(aliases=["qg"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quotegame(self, ctx):
        """Settings for QuoteGame"""
        pass

    @quotegame.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for the weekly quote game."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Quote game channel set to {channel.mention}.")

    @quotegame.command()
    async def toggle(self, ctx, state: bool):
        """Enable or disable the weekly game in this server."""
        await self.config.guild(ctx.guild).is_active.set(state)
        await ctx.send(f"Quote game is now {'enabled' if state else 'disabled'}.")
        
    @quotegame.command()
    async def force(self, ctx):
        """Force start a game right now in the configured channel."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            return await ctx.send("Please set a channel first.")
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            return await ctx.send("Configured channel not found.")
        
        await self.start_game(ctx.guild, channel)
        await ctx.send("Forced a new game to start.")

    async def fetch_quote(self):
        # Always pick from our historical quotes list
        return random.choice(HISTORICAL_QUOTES)

    def blank_out_word(self, quote):
        # Find words that are >= 4 letters
        words = re.findall(r'\b[a-zA-Z]{4,}\b', quote)
        if not words:
            words = re.findall(r'\b[a-zA-Z]+\b', quote)
        
        if not words:
            return quote, "" # Fallback if quote is completely unusual

        target = random.choice(words)
        
        # Replace just one occurrence, keeping punctuation intact
        blanked = re.sub(rf'\b{re.escape(target)}\b', '________', quote, count=1, flags=re.IGNORECASE)
        return blanked, target

    async def start_game(self, guild, channel):
        # Remove role from previous winners
        role_id = 1484535497651916872
        role = guild.get_role(role_id)
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
        
        embed = discord.Embed(title="Weekly Quote Game: Fill in the Blank!",
                              description=f"**Quote:**\n\"{blanked}\"\n- {author}\n\n"
                                          "**How to play:**\nType your 1-word answer directly in this channel! "
                                          "I will instantly delete your message to keep your answer a secret. "
                                          "In 24 hours, everyone will get to vote on the best or funniest answer!",
                              color=discord.Color.blue())
        try:
            msg = await channel.send(embed=embed)
        except discord.Forbidden:
            return # Bot cannot send messages in this channel
            
        game_data = {
            "quote": quote,
            "blanked": blanked,
            "author": author,
            "target_word": target_word,
            "start_time": datetime.utcnow().timestamp(),
            "phase": "answering",
            "answers": {},  # user_id (str) -> answer (str)
            "message_id": msg.id
        }
        await self.config.guild(guild).current_game.set(game_data)
        await self.config.guild(guild).last_game_time.set(datetime.utcnow().timestamp())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
            
        channel_id = await self.config.guild(message.guild).channel_id()
        if not channel_id or message.channel.id != channel_id:
            return
            
        game_data = await self.config.guild(message.guild).current_game()
        if not game_data or game_data.get("phase") != "answering":
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
                
            game_data["answers"][user_id] = word
            await self.config.guild(message.guild).current_game.set(game_data)
            
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
                        
                    if not data.get("is_active"):
                        continue
                        
                    channel_id = data.get("channel_id")
                    if not channel_id:
                        continue
                        
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue
                        
                    current_game = data.get("current_game")
                    last_game_time = data.get("last_game_time", 0)
                    now = datetime.utcnow().timestamp()
                    
                    if not current_game:
                        # Check if a week has passed (7 days = 604800 seconds)
                        if now - last_game_time >= 604800:
                            await self.start_game(guild, channel)
                    else:
                        phase = current_game.get("phase")
                        start_time = current_game.get("start_time", 0)
                        
                        if phase == "answering":
                            # 24 hours = 86400 seconds
                            if now - start_time >= 86400:
                                await self.start_voting(guild, channel, current_game)
                        elif phase == "voting":
                            # Wait another 24 hours for voting
                            vote_start = current_game.get("vote_start_time", start_time)
                            if now - vote_start >= 86400:
                                await self.end_game(guild, channel, current_game)
            except Exception as e:
                import logging
                logging.getLogger("red.quotegame").error("Error in QuoteGame loop", exc_info=e)
                
            await asyncio.sleep(60)  # Check every minute

    async def start_voting(self, guild, channel, game_data):
        answers = game_data.get("answers", {})
        real_word = game_data.get("target_word", "")
        
        if not answers:
            try:
                await channel.send(f"The quote game ended, but nobody submitted any answers! 😢\n\nFor the record, the missing word was **{real_word}**.")
            except discord.Forbidden:
                pass
            await self.config.guild(guild).current_game.set({})
            return
            
        unique_answers = list(set(answers.values()))
        if real_word not in unique_answers:
            unique_answers.append(real_word)
            
        random.shuffle(unique_answers)
        
        # Limit to 10 options because of simple emoji reactions
        if len(unique_answers) > 10:
            if real_word in unique_answers:
                unique_answers.remove(real_word)
                unique_answers = random.sample(unique_answers, 9)
                unique_answers.append(real_word)
                random.shuffle(unique_answers)
            else:
                unique_answers = random.sample(unique_answers, 10)
                
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        desc = f"**Quote:**\n\"{game_data['blanked']}\"\n\n**Vote for the best answer!**\n\n"
        for i, ans in enumerate(unique_answers):
            desc += f"{emojis[i]} {ans}\n"
            
        embed = discord.Embed(title="Quote Game: Voting Phase!",
                              description=desc,
                              color=discord.Color.green())
        try:
            msg = await channel.send(embed=embed)
            for i in range(len(unique_answers)):
                await msg.add_reaction(emojis[i])
        except discord.Forbidden:
            await self.config.guild(guild).current_game.set({})
            return
            
        game_data["phase"] = "voting"
        game_data["vote_start_time"] = datetime.utcnow().timestamp()
        game_data["poll_message_id"] = msg.id
        game_data["options"] = unique_answers
        
        await self.config.guild(guild).current_game.set(game_data)

    async def end_game(self, guild, channel, game_data):
        poll_msg_id = game_data.get("poll_message_id")
        options = game_data.get("options", [])
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        
        try:
            msg = await channel.fetch_message(poll_msg_id)
            
            results = []
            for reaction in msg.reactions:
                if str(reaction.emoji) in emojis:
                    index = emojis.index(str(reaction.emoji))
                    if index < len(options):
                        # Subtract 1 for the bot's own reaction (if present)
                        count = reaction.count - 1 if reaction.me else reaction.count
                        results.append((options[index], max(0, count)))
                        
            if results:
                results.sort(key=lambda x: x[1], reverse=True)
                winner_ans, winner_votes = results[0]
                
                real_word = game_data.get("target_word", "")
                
                if winner_votes == 0:
                    winner_text = f"Nobody voted! 😢\n\nFor the record, the real word was **{real_word}**."
                else:
                    top_vote = winner_votes
                    tied_answers = [ans for ans, votes in results if votes == top_vote]
                    
                    winner_text_parts = []
                    new_winners = []
                    for ans in tied_answers:
                        winners = []
                        for uid, submitted_ans in game_data.get("answers", {}).items():
                            if submitted_ans.lower() == ans.lower():
                                winners.append(f"<@{uid}>")
                                new_winners.append(uid)
                                
                        if not winners:
                            if ans.lower() == real_word.lower():
                                winners_mentions = "The Original Quote"
                            else:
                                winners_mentions = "Nobody (Option was present but not found in submissions)"
                        else:
                            winners_mentions = ", ".join(winners)
                            
                        winner_text_parts.append(f"**{ans}** (by {winners_mentions})")
                        
                    if len(tied_answers) == 1:
                        if winner_ans.lower() == real_word.lower():
                            winner_text = f"The winning answer was **{winner_ans}** with {top_vote} votes!\nThis was actually the real word!"
                        else:
                            winner_text = f"The winning answer was {winner_text_parts[0]} with {top_vote} votes!\n\nThe real word was **{real_word}**."
                    else:
                        tied_str = "\n".join(f"- {p}" for p in winner_text_parts)
                        winner_text = f"It's a tie with {top_vote} votes each for:\n{tied_str}\n\nThe real word was **{real_word}**."
                        
                    if new_winners:
                        role_id = 1484535497651916872
                        role = guild.get_role(role_id)
                        if role:
                            for uid in new_winners:
                                member = guild.get_member(int(uid))
                                if member:
                                    try:
                                        await member.add_roles(role)
                                    except discord.Forbidden:
                                        pass
                        await self.config.guild(guild).current_winners.set(new_winners)
                        
                full_quote = game_data.get('quote', "")
                author = game_data.get('author', "")
                
                embed = discord.Embed(title="Quote Game: Results!",
                                      description=f"{winner_text}\n\n**Original Quote:**\n\"{full_quote}\"\n- {author}",
                                      color=discord.Color.gold())
                await channel.send(embed=embed)
            else:
                await channel.send(f"Couldn't read the poll results!\nFor the record, the real word was **{game_data.get('target_word', '')}**.")
                
        except discord.NotFound:
            try:
                await channel.send(f"The voting message was deleted, so I couldn't count the votes!\nFor the record, the real word was **{game_data.get('target_word', '')}**.")
            except discord.Forbidden:
                pass
        except discord.Forbidden:
            pass
        except Exception as e:
            import logging
            logging.getLogger("red.quotegame").error("Error in end_game", exc_info=e)
            
        await self.config.guild(guild).current_game.set({})

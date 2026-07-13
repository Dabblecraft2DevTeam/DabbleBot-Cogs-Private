import discord
from redbot.core import bank

class LevelShopView(discord.ui.View):
    """A view for purchasing leveler cosmetics using Red's bank."""
    def __init__(self, cog, user: discord.Member, db, item_type: str, config_options: list):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.db = db
        self.item_type = item_type  # 'title_color', 'bar_color', or 'background_id'
        
        self.options = config_options
        
        if not self.options:
            self.options = [{"label": "Empty Shop", "value": "none", "price": 0}]
            
        # Select options can only have max 25 items
        select_options = [
            discord.SelectOption(
                label=f"{opt['label'][:25]} ({opt['price']}c)",
                value=str(opt['value'])[:100],
                description=f"Change your {item_type.replace('_', ' ')}."
            ) for opt in self.options[:25]
        ]
        
        self.select = discord.ui.Select(
            placeholder="Choose an item to purchase...",
            min_values=1,
            max_values=1,
            options=select_options
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return False
        return True

    async def select_callback(self, interaction: discord.Interaction):
        selected_value = self.select.values[0]
        selected_opt = next((opt for opt in self.options if opt['value'] == selected_value), None)
        
        if not selected_opt:
            return await interaction.response.send_message("Invalid option selected.", ephemeral=True)
            
        price = selected_opt['price']
        
        try:
            can_spend = await bank.can_spend(self.user, price)
            if not can_spend:
                return await interaction.response.send_message(f"You don't have enough credits. (Need {price})", ephemeral=True)
                
            # Withdraw
            await bank.withdraw_credits(self.user, price)
            
            # Update DB
            kwargs = {self.item_type: selected_value}
            await self.db.update_user_cosmetics(self.user.guild.id, self.user.id, **kwargs)
            
            await interaction.response.send_message(f"Successfully purchased {selected_opt['label']} {self.item_type.replace('_', ' ')} for {price} credits!", ephemeral=True)
            
            # Disable view after purchase
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(view=self)
            
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

class LeaderboardPaginationView(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed]):
        super().__init__(timeout=120)
        self.embeds = embeds
        self.current_page = 0
        
    @discord.ui.button(label="Previous", style=discord.ButtonStyle.grey)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
        else:
            await interaction.response.defer()
            
    @discord.ui.button(label="Next", style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)
        else:
            await interaction.response.defer()

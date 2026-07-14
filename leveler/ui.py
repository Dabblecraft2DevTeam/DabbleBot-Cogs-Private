import discord
from redbot.core import bank

class MainShopView(discord.ui.View):
    def __init__(self, cog, user: discord.Member, db, config):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.db = db
        self.config = config

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🎨 Buy Colors", style=discord.ButtonStyle.primary)
    async def btn_colors(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = await self.config.guild(self.user.guild).shop_colors()
        view = ShopCategoryView(self.cog, self.user, self.db, self.config, "bar_color", options, "Colors", self)
        await interaction.response.edit_message(content="**🎨 Shop: Colors**\nSelect a color below to purchase it.", view=view)

    @discord.ui.button(label="🖼️ Buy Backgrounds", style=discord.ButtonStyle.primary)
    async def btn_backgrounds(self, interaction: discord.Interaction, button: discord.ui.Button):
        options = await self.config.guild(self.user.guild).shop_backgrounds()
        view = ShopCategoryView(self.cog, self.user, self.db, self.config, "background_id", options, "Backgrounds", self)
        await interaction.response.edit_message(content="**🖼️ Shop: Backgrounds**\nSelect a background below to purchase it.", view=view)

    @discord.ui.button(label="🎒 Inventory", style=discord.ButtonStyle.success)
    async def btn_inventory(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = InventoryCategoryView(self.cog, self.user, self.db, self.config, self)
        await interaction.response.edit_message(content="**🎒 Your Inventory**\nChoose a category to equip items you already own.", view=view)

class ShopCategoryView(discord.ui.View):
    def __init__(self, cog, user, db, config, item_type: str, config_options: list, category_name: str, parent_view: discord.ui.View):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.db = db
        self.config = config
        self.item_type = item_type
        self.options = config_options
        self.parent_view = parent_view
        
        if not self.options:
            self.options = [{"label": "Empty Shop", "value": "none", "price": 0}]
            
        select_options = [
            discord.SelectOption(
                label=f"{opt['label'][:25]} ({opt['price']}c)",
                value=str(opt['value'])[:100],
                description=f"Buy this {category_name.lower()[:-1]}."
            ) for opt in self.options[:25]
        ]
        
        self.select = discord.ui.Select(
            placeholder=f"Choose a {category_name.lower()[:-1]} to purchase...",
            min_values=1, max_values=1, options=select_options
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)
        
        btn_back = discord.ui.Button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
        btn_back.callback = self.back_callback
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return False
        return True

    async def back_callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="**🛒 Leveler Shop**\nChoose a category below:", view=self.parent_view)

    async def select_callback(self, interaction: discord.Interaction):
        selected_value = self.select.values[0]
        if selected_value == "none":
            return await interaction.response.send_message("This category is empty.", ephemeral=True)
            
        selected_opt = next((opt for opt in self.options if opt['value'] == selected_value), None)
        if not selected_opt:
            return await interaction.response.send_message("Invalid option selected.", ephemeral=True)
            
        member_conf = self.config.member(self.user)
        if self.item_type == "bar_color":
            inv = await member_conf.inventory_colors()
        else:
            inv = await member_conf.inventory_backgrounds()
            
        if selected_value in [item["value"] for item in inv]:
            return await interaction.response.send_message("You already own this item! Check your 🎒 Inventory to equip it.", ephemeral=True)
            
        price = selected_opt['price']
        
        try:
            can_spend = await bank.can_spend(self.user, price)
            if not can_spend:
                return await interaction.response.send_message(f"You don't have enough credits. (Need {price}c)", ephemeral=True)
                
            await bank.withdraw_credits(self.user, price)
            
            if self.item_type == "bar_color":
                inv.append({"label": selected_opt["label"], "value": selected_value})
                await member_conf.inventory_colors.set(inv)
            else:
                inv.append({"label": selected_opt["label"], "value": selected_value})
                await member_conf.inventory_backgrounds.set(inv)
            
            kwargs = {self.item_type: selected_value}
            await self.db.update_user_cosmetics(self.user.guild.id, self.user.id, **kwargs)
            
            await interaction.response.send_message(f"✅ Successfully purchased and equipped **{selected_opt['label']}** for {price}c!", ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

class InventoryCategoryView(discord.ui.View):
    def __init__(self, cog, user, db, config, parent_view: discord.ui.View):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.db = db
        self.config = config
        self.parent_view = parent_view
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🎨 Equip Colors", style=discord.ButtonStyle.primary)
    async def btn_colors(self, interaction: discord.Interaction, button: discord.ui.Button):
        inv = await self.config.member(self.user).inventory_colors()
        view = InventoryEquipView(self.cog, self.user, self.db, self.config, "bar_color", inv, "Colors", self)
        await interaction.response.edit_message(content="**🎨 Inventory: Colors**\nSelect a color below to equip it.", view=view)

    @discord.ui.button(label="🖼️ Equip Backgrounds", style=discord.ButtonStyle.primary)
    async def btn_backgrounds(self, interaction: discord.Interaction, button: discord.ui.Button):
        inv = await self.config.member(self.user).inventory_backgrounds()
        view = InventoryEquipView(self.cog, self.user, self.db, self.config, "background_id", inv, "Backgrounds", self)
        await interaction.response.edit_message(content="**🖼️ Inventory: Backgrounds**\nSelect a background below to equip it.", view=view)

    @discord.ui.button(label="🔙 Back to Shop", style=discord.ButtonStyle.secondary)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="**🛒 Leveler Shop**\nChoose a category below:", view=self.parent_view)

class InventoryEquipView(discord.ui.View):
    def __init__(self, cog, user, db, config, item_type: str, inv: list, category_name: str, parent_view: discord.ui.View):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.db = db
        self.config = config
        self.item_type = item_type
        self.inv = inv
        self.parent_view = parent_view
        
        if not self.inv:
            self.inv = [{"label": "No items owned", "value": "none"}]
            
        select_options = [
            discord.SelectOption(
                label=f"{opt['label'][:25]}",
                value=str(opt['value'])[:100],
                description=f"Equip this {category_name.lower()[:-1]}."
            ) for opt in self.inv[:24]
        ]
        
        if self.item_type == "background_id" and "default" not in [i["value"] for i in self.inv]:
            select_options.insert(0, discord.SelectOption(label="Default Background", value="default", description="Reset to default background."))
            
        self.select = discord.ui.Select(
            placeholder=f"Choose a {category_name.lower()[:-1]} to equip...",
            min_values=1, max_values=1, options=select_options
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)
        
        btn_back = discord.ui.Button(label="🔙 Back", style=discord.ButtonStyle.secondary, row=1)
        btn_back.callback = self.back_callback
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return False
        return True

    async def back_callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="**🎒 Your Inventory**\nChoose a category to equip items you already own.", view=self.parent_view)

    async def select_callback(self, interaction: discord.Interaction):
        selected_value = self.select.values[0]
        if selected_value == "none":
            return await interaction.response.send_message("You don't own any items in this category.", ephemeral=True)
            
        try:
            kwargs = {self.item_type: selected_value}
            await self.db.update_user_cosmetics(self.user.guild.id, self.user.id, **kwargs)
            
            selected_label = next((opt['label'] for opt in self.inv if opt['value'] == selected_value), "Default")
            if selected_value == "default":
                selected_label = "Default"
                
            await interaction.response.send_message(f"✅ Successfully equipped **{selected_label}**!", ephemeral=True)
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

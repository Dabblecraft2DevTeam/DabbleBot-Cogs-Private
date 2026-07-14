import io
import os
import urllib.request
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from concurrent.futures import ThreadPoolExecutor
import asyncio

# Setup a ThreadPoolExecutor for blocking image operations
executor = ThreadPoolExecutor(max_workers=4)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
FONT_PATH = os.path.join(ASSETS_DIR, "font.ttf")

def ensure_assets():
    if not os.path.exists(ASSETS_DIR):
        os.makedirs(ASSETS_DIR)
    if not os.path.exists(FONT_PATH):
        try:
            url = "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Regular.ttf"
            urllib.request.urlretrieve(url, FONT_PATH)
        except Exception as e:
            print(f"Failed to download font: {e}")

# Ensure assets on load
ensure_assets()

def generate_profile_card_sync(username: str, avatar_bytes: bytes, xp: int, level: int, rank: int, title_color: str, bar_color: str, bio: str, background_id: str, prestige_url: str = "") -> io.BytesIO:
    """Synchronous function to generate a profile card."""
    width, height = 950, 280
    # Background (Dark gray if default)
    img = Image.new("RGBA", (width, height), (35, 39, 42, 255))
    
    if background_id and background_id != "default":
        if background_id.startswith("http"):
            try:
                req = urllib.request.Request(background_id, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    bg = Image.open(io.BytesIO(resp.read())).convert("RGBA")
                bg = bg.resize((width, height))
                img.paste(bg, (0,0))
                # Add overlay
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 150))
                img = Image.alpha_composite(img, overlay)
            except Exception:
                pass
                
    draw = ImageDraw.Draw(img)

    try:
        font_large = ImageFont.truetype(FONT_PATH, 40)
        font_medium = ImageFont.truetype(FONT_PATH, 30)
        font_small = ImageFont.truetype(FONT_PATH, 20)
    except IOError:
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Draw avatar
    if avatar_bytes:
        try:
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            avatar = avatar.resize((150, 150))
            # Circular mask
            mask = Image.new("L", (150, 150), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 150, 150), fill=255)
            img.paste(avatar, (40, 50), mask)
        except Exception:
            pass

    # Draw Username
    draw.text((220, 50), username, font=font_large, fill=title_color)

    # Draw Rank and Level
    draw.text((220, 100), f"Rank: #{rank}   Level: {level}", font=font_medium, fill=(255, 255, 255, 255))

    # Draw Bio
    if bio:
        draw.text((220, 140), bio, font=font_small, fill=(200, 200, 200, 255))
        
    # Calculate XP progress (Mee6 formula approximate next level)
    required_xp = int((5/6) * (level + 1) * (2 * ((level + 1)**2) + 27 * (level + 1) + 91))
    current_level_xp_base = int((5/6) * level * (2 * (level**2) + 27 * level + 91)) if level > 0 else 0
    progress_xp = xp - current_level_xp_base
    tier_xp = required_xp - current_level_xp_base
    progress = max(0, min(1, progress_xp / tier_xp)) if tier_xp > 0 else 1

    # Draw XP text
    xp_text = f"{xp} / {required_xp} XP"
    draw.text((720, 160), xp_text, font=font_small, fill=(200, 200, 200, 255))

    # Draw Progress Bar
    bar_x, bar_y = 220, 190
    bar_width, bar_height = 690, 20
    draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + bar_height], fill=(50, 55, 60, 255), outline=(0, 0, 0, 255))
    
    # Draw filled portion
    fill_width = int(bar_width * progress)
    if fill_width > 0:
        draw.rectangle([bar_x, bar_y, bar_x + fill_width, bar_y + bar_height], fill=bar_color)

    # Draw Prestige Badge if available (drawn last so it can overlap if needed)
    if prestige_url and prestige_url.startswith("http"):
        try:
            clean_url = prestige_url.strip("<>}{")
            clean_url = clean_url.replace("%7D", "")
            req = urllib.request.Request(clean_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                badge = Image.open(io.BytesIO(resp.read())).convert("RGBA")
            badge = badge.resize((60, 60))
            img.paste(badge, (850, 215), badge)
        except Exception as e:
            print(f"Failed to load prestige badge from {prestige_url}: {e}")

    output = io.BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

def generate_levelup_card_sync(username: str, avatar_bytes: bytes, new_level: int) -> io.BytesIO:
    """Synchronous function to generate a level-up notification."""
    width, height = 550, 120
    img = Image.new("RGBA", (width, height), (35, 39, 42, 255))
    draw = ImageDraw.Draw(img)

    try:
        font_large = ImageFont.truetype(FONT_PATH, 24)
        font_small = ImageFont.truetype(FONT_PATH, 16)
    except IOError:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    if avatar_bytes:
        try:
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            avatar = avatar.resize((80, 80))
            mask = Image.new("L", (80, 80), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 80, 80), fill=255)
            img.paste(avatar, (20, 20), mask)
        except Exception:
            pass

    display_name = username
    if len(display_name) > 25:
        display_name = display_name[:22] + "..."

    draw.text((120, 30), f"Level Up, {display_name}!", font=font_large, fill=(255, 215, 0, 255))
    draw.text((120, 70), f"You are now level {new_level}!", font=font_small, fill=(200, 200, 200, 255))

    output = io.BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

async def generate_profile_card(username: str, avatar_bytes: bytes, xp: int, level: int, rank: int, title_color: str, bar_color: str, bio: str, background_id: str, prestige_url: str = "") -> io.BytesIO:
    """Async wrapper for profile card generation."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, generate_profile_card_sync, username, avatar_bytes, xp, level, rank, title_color, bar_color, bio, background_id, prestige_url)

async def generate_levelup_card(username: str, avatar_bytes: bytes, new_level: int) -> io.BytesIO:
    """Async wrapper for level-up card generation."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, generate_levelup_card_sync, username, avatar_bytes, new_level)

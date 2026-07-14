import asyncio
import io
import os
from image_gen import generate_profile_card_sync
from PIL import Image

def test():
    img_bytes = generate_profile_card_sync(
        username="TestUser",
        avatar_bytes=None,
        xp=150,
        level=1,
        rank=1,
        title_color="#FFFFFF",
        bar_color="#00FF00",
        bio="Test Bio",
        background_id="default",
        prestige_url="https://c-four.org/discord-assets/glass-prestige.png%7D"
    )
    img = Image.open(img_bytes)
    img.save("test_out.png")
    print("Image saved successfully.")

test()

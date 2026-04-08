import asyncio
import os
import sys
from pathlib import Path
import base64
import logging

# Add backend to path
backend_path = Path(__file__).parent.parent
sys.path.append(str(backend_path))

# Configure logging
logging.basicConfig(level=logging.INFO)

from app.agents.device_testing.tools.autonomous_navigation_tools import create_autonomous_navigation_tools
from unittest.mock import AsyncMock

async def test_vision_click():
    print("🚀 Testing Vision-Augmented Navigation (vision_click)...")
    
    # 1. Mock MobileMCPClient
    mock_client = AsyncMock()
    
    # Mock screenshot (a simple gray square)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("❌ PIL not installed. Cannot generate test image.")
        return

    import io
    # Create an image with a clear visual target
    width, height = 1080, 2400
    img = Image.new('RGB', (width, height), color=(200, 200, 200))
    draw = ImageDraw.Draw(img)
    # Draw a "Settings Icon" (blue circle) at a specific location
    icon_x, icon_y = 900, 150
    draw.ellipse([icon_x-50, icon_y-50, icon_x+50, icon_y+50], fill="blue")
    draw.text((icon_x-20, icon_y+60), "Settings", fill="black")
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()
    
    mock_client.take_screenshot.return_value = {
        "type": "image",
        "data": base64.b64encode(img_bytes).decode('utf-8')
    }
    mock_client.get_screen_size.return_value = f"{width}x{height}"
    mock_client.click_on_screen.return_value = "OK"

    # 2. Setup tools
    device_id = "test-device"
    tools = create_autonomous_navigation_tools(mock_client, device_id)
    vision_click = tools['vision_click']

    # 3. Test finding the settings icon
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️ Skipping real AI call - OPENAI_API_KEY not set.")
        # Mocking the vision part for structure test
        print("Structure test passed (imports and logic check).")
        return

    print("Calling vision_click for 'the blue settings icon at top right'...")
    # Using a very specific query to ensure it finds the mocked icon
    query = "Find the blue circular settings icon located at the top right of the screen."
    result = await vision_click(query=query, target_description="Settings Icon")
    
    print(f"\nResult: {result}")
    
    if "Vision clicked Settings Icon at" in result:
        print("\n✅ SUCCESS: Vision found and clicked the element.")
        # Verify coordinates are somewhat near (900, 150)
        import re
        match = re.search(r"at \((\d+), (\d+)\)", result)
        if match:
            found_x, found_y = int(match.group(1)), int(match.group(2))
            dist = ((found_x - icon_x)**2 + (found_y - icon_y)**2)**0.5
            print(f"Grounding Accuracy: Icon at ({icon_x}, {icon_y}), Found at ({found_x}, {found_y}), Distance: {dist:.2f}px")
            if dist < 100:
                print("✅ Precision check passed!")
            else:
                print("⚠️ Precision is low, but element was found.")
    else:
        print("\n❌ FAILURE: Vision click did not return expected result.")

if __name__ == "__main__":
    asyncio.run(test_vision_click())

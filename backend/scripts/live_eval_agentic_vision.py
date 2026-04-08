import sys
import os
import asyncio
from pathlib import Path
import io

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.agents.device_testing.agentic_vision_service import AgenticVisionClient
from dotenv import load_dotenv

# Load env (for OPENAI_API_KEY)
load_dotenv()

async def run_live_eval():
    print("🚀 Starting Live Agentic Vision Evaluation...")
    
    # Check key
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️ OPENAI_API_KEY not found in env. Please ensure it is set.")
        # Don't exit, client might throw meaningful error or use Google key if present
    
    # 1. Generate Test Image
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (800, 800), color = (255, 255, 255))
        d = ImageDraw.Draw(img)
        
        # Add a blue square - EXACT color (0, 0, 255)
        # Width 10. 
        # Outer box: 200,200 to 600,600 (400x400)
        # Inner box: 210,210 to 590,590 (380x380)
        # Area ≈ 400^2 - 380^2 = 160000 - 144400 = 15600 pixels roughly
        # Let PIL handle drawing
        d.rectangle([200, 200, 600, 600], outline=(0, 0, 255), width=10)
        
        # Save to bytes
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()
        print("✅ Generated test image (800x800) with Blue Square.")
    except Exception as e:
        print(f"FAILED to generate image: {e}")
        return

    # 2. Initialize Client
    model_name = os.getenv("AGENTIC_VISION_MODEL", "gpt-5.4")
    print(f"Initializing client (Model: {model_name})...")
    try:
        # Force OpenAI model (Universal path) by using the model name
        # If user wants Gemini, they would set AGENTIC_VISION_MODEL=gemini-2.0-flash
        client = AgenticVisionClient(model=model_name) 
        print(f"✅ Initialized AgenticVisionClient")
    except Exception as e:
        print(f"FAILED to init client: {e}")
        return

    # 3. Execute Zoom Analysis
    query = "Calculate the exact number of pure blue pixels (0, 0, 255) in this image using Python. Do not estimate."
    print(f"❓ Query: {query}")
    
    try:
        result = await client.analyze_visual_math(image_bytes, query)
        
        print("\n" + "="*50)
        print("EVALUATION RESULT")
        print("="*50)
        print(f"Success: {result.success}")
        print(f"Steps: {result.total_steps}")
        print(f"Final Analysis: {result.final_analysis}")
        
        if result.steps:
            print("\n--- Steps Trace ---")
            for i, step in enumerate(result.steps):
                print(f"Step {i+1}: {step.action.value}")
                if step.code_generated:
                    print(f"Code:\n{step.code_generated}")
                if step.code_output:
                    print(f"Output: {step.code_output.strip()}")
                print("-" * 20)
        
        # Check if code execution happened
        if result.total_steps > 0:
            print("\n✅ PASS: Agent generated and executed code!")
        else:
            print("\n❌ SEMI-FAIL: Agent answered without code (Vision-only).")
            
    except Exception as e:
        print(f"Evaluation Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_live_eval())

import os
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Load environment variables
load_dotenv()

# Configuration
API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = "gpt-5.4"  # Use strong model for verification (Mar 2026 flagship)

async def verify_code():
    if not API_KEY:
        print("Error: OPENAI_API_KEY not set.")
        return

    client = AsyncOpenAI(api_key=API_KEY)

    # Read files to verify
    files_to_check = [
        "app/agents/device_testing/agentic_vision_service.py",
        "app/agents/device_testing/tools/agentic_vision_tools.py",
        "tests/test_agentic_vision.py"
    ]
    
    code_content = ""
    for file_path in files_to_check:
        try:
            with open(file_path, "r") as f:
                code_content += f"\n\n--- FILE: {file_path} ---\n\n"
                code_content += f.read()
        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    prompt = f"""You are a Senior AI Engineer specializing in Code Verification.
    
    Review the following Python implementation of an "Agentic Vision" service using Gemini 3 Flash.
    
    Key Requirements:
    1. Implementation of Think-Act-Observe loop in `AgenticVisionClient`.
    2. Proper error handling and dependency checks (lazy loading).
    3. Correct tool definitions in `agentic_vision_tools.py`.
    4. Unit testing coverage in `test_agentic_vision.py` (focus on logic, ignore missing dependencies).
    
    Code to Review:
    {code_content}
    
    Provide a verification report in Markdown format:
    1. **Architecture Review**: Is the Agentic Pattern correctly implemented?
    2. **Code Quality**: Any bugs, security risks, or style issues?
    3. **Test Coverage**: Do the tests cover the critical paths?
    4. **Final Verdict**: PASS or FAIL.
    """

    print(f"🔍 Analyzing {len(files_to_check)} files with {MODEL}...")
    
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a code verification agent."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        
        report = response.choices[0].message.content
        print("\n" + "="*50)
        print("AI VERIFICATION REPORT")
        print("="*50 + "\n")
        print(report)
        
        # Save report
        with open("ai_verification_report.md", "w") as f:
            f.write(report)
            
    except Exception as e:
        print(f"Verification failed: {e}")

if __name__ == "__main__":
    asyncio.run(verify_code())

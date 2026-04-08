
import pytest
import base64
from unittest.mock import AsyncMock, MagicMock, patch
from app.agents.device_testing.agentic_vision_service import AgenticVisionClient, AgenticVisionResult, VisionStep
from app.agents.device_testing.agentic_vision_core import LLMProvider

# Mock Provider
class MockLLMProvider(LLMProvider):
    async def generate_response(
        self, 
        prompt: str, 
        images: list = None,
        system_instruction: str = None
    ) -> str:
        return "Mock Response"

@pytest.mark.asyncio
async def test_universal_initialization():
    # Test initialization with specific provider
    mock_provider = MockLLMProvider()
    client = AgenticVisionClient(provider=mock_provider)
    await client._ensure_initialized()
    assert client._initialized
    assert client.provider == mock_provider
    assert not client.using_gemini_sdk

@pytest.mark.asyncio
async def test_universal_execution_flow():
    mock_provider = MockLLMProvider()
    
    # Mock response to return Python code
    mock_provider.generate_response = AsyncMock(side_effect=[
        # Step 1: Return code to crop
        "I need to crop the image.\n```python\nprint('Cropping...')\n```",
        # Step 2: Final answer
        "The cropped image shows a serial number: 12345."
    ])
    
    client = AgenticVisionClient(provider=mock_provider, max_steps=3)
    
    # Mock local executor to succeed
    client.local_executor.execute = MagicMock(return_value={
        "success": True,
        "stdout": "Cropping...",
        "locals": {}
    })
    
    result = await client.analyze_with_zooming(b"fake_image_bytes", "Read serial number")
    
    # Verify loop happened
    assert result.success
    assert len(result.steps) == 1 # One code execution step
    assert result.steps[0].code_generated == "print('Cropping...')"
    assert "12345" in result.final_analysis
    
    # Verify provider called twice
    assert mock_provider.generate_response.call_count == 2

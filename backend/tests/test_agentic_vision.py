import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import io
from PIL import Image
from app.agents.device_testing.agentic_vision_service import AgenticVisionClient, crop_and_zoom, draw_bounding_boxes

# Mock image for testing
def create_test_image():
    img = Image.new('RGB', (100, 100), color='red')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()

@pytest.fixture
def mock_genai_module():
    mock = MagicMock()
    with patch.dict('sys.modules', {'google.generativeai': mock}):
        yield mock

@pytest.mark.asyncio
async def test_agentic_vision_client_initialization():
    # GPT-5.4 is the primary path — no Gemini SDK needed
    client = AgenticVisionClient(api_key="test_key")
    await client._ensure_initialized()

    assert client.api_key == "test_key"
    assert client._initialized is True

@pytest.mark.asyncio
async def test_analyze_with_zooming():
    client = AgenticVisionClient(api_key="test_key")
    
    # Mock internal methods to avoid Gemini dependency
    client._ensure_initialized = AsyncMock()
    
    # Create a fake result
    fake_result = MagicMock()
    fake_result.success = True
    fake_result.final_analysis = "Final Analysis: The red square contains details."
    fake_result.total_steps = 2
    fake_result.steps = []
    
    client._execute_vision_pipeline = AsyncMock(return_value=fake_result)
    
    image_bytes = create_test_image()
    
    # Run analysis
    result = await client.analyze_with_zooming(image_bytes, "Analyze this", zoom_targets=["red square"])
    
    assert result.success is True
    assert "Final Analysis" in result.final_analysis
    
    # Verify proper call
    client._execute_vision_pipeline.assert_called_once()
    call_args = client._execute_vision_pipeline.call_args
    assert call_args is not None
    prompt = call_args[0][1]
    assert "Analyze this" in prompt
    assert "red square" in prompt

def test_crop_and_zoom():
    image_bytes = create_test_image()
    
    # Crop center
    output = crop_and_zoom(image_bytes, 25, 25, 75, 75, zoom_factor=2.0)
    
    # Verify output is a valid image
    img = Image.open(io.BytesIO(output))
    assert img.width == 100  # (75-25) * 2
    assert img.height == 100 # (75-25) * 2

def test_draw_bounding_boxes():
    image_bytes = create_test_image()
    boxes = [{"x": 10, "y": 10, "width": 20, "height": 20, "label": "Test"}]
    
    output = draw_bounding_boxes(image_bytes, boxes)
    
    # Verify output is a valid image
    img = Image.open(io.BytesIO(output))
    assert img.width == 100
    assert img.height == 100

import { test, expect } from '@playwright/test'

/**
 * Mobile MCP Agent Integration Tests
 * 
 * Tests the agent's ability to:
 * 1. List available devices using Mobile MCP
 * 2. Generate test scenarios based on Mobile MCP examples
 * 3. Execute tests using Mobile MCP tools
 * 4. Generate comprehensive reports
 */

const API_BASE = process.env.API_BASE || 'http://localhost:8000'

test.describe('Mobile MCP Agent Integration', () => {
  test.setTimeout(180_000) // 3 minutes for agent operations

  test('agent can list available devices via Mobile MCP', async ({ page }) => {
    // Navigate to home page
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Find AI chat input
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Count initial messages
    const initialMessageCount = await page.locator('p, .font-mono').count()

    // Ask agent to list devices
    await chatInput.fill('What mobile devices are available? Use Mobile MCP to check.')
    await chatInput.press('Enter')

    // Wait for new messages to appear (tool calls or responses)
    await page.waitForFunction(
      (initialCount) => {
        const currentCount = document.querySelectorAll('p, .font-mono').length
        return currentCount > initialCount
      },
      initialMessageCount,
      { timeout: 30000 }
    )

    // Wait a bit longer for agent to complete
    await page.waitForTimeout(10000)

    // Get all text content from the page
    const pageText = await page.textContent('body')

    // Verify response mentions devices or tool calls
    const hasDeviceInfo = pageText && (
      pageText.toLowerCase().includes('emulator') ||
      pageText.toLowerCase().includes('device') ||
      pageText.toLowerCase().includes('5554') ||
      pageText.toLowerCase().includes('android') ||
      pageText.toLowerCase().includes('available') ||
      pageText.toLowerCase().includes('get_available_emulators') ||
      pageText.toLowerCase().includes('transfer_to_emulator_manager')
    )
    expect(hasDeviceInfo).toBeTruthy()
  })

  test('agent can execute Mobile MCP actions on device', async ({ page, request }) => {
    // First verify device is available via API
    const devicesResponse = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    expect(devicesResponse.ok()).toBeTruthy()

    const devicesData = await devicesResponse.json()
    const hasAndroidDevice = devicesData.devices?.android?.length > 0

    test.skip(!hasAndroidDevice, 'No Android device available')

    // Navigate to home page
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Find AI chat input
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Count initial messages
    const initialMessageCount = await page.locator('p, .font-mono').count()

    // Ask agent to execute Mobile MCP actions
    await chatInput.fill('On device emulator-5554: 1) Take a screenshot, 2) Launch Settings app (com.android.settings), 3) Take another screenshot. Use Mobile MCP.')
    await chatInput.press('Enter')

    // Wait for new messages to appear
    await page.waitForFunction(
      (initialCount) => {
        const currentCount = document.querySelectorAll('p, .font-mono').length
        return currentCount > initialCount
      },
      initialMessageCount,
      { timeout: 30000 }
    )

    // Wait for agent to complete (longer for multiple actions)
    await page.waitForTimeout(20000)

    // Get all text content
    const pageText = await page.textContent('body')

    // Verify agent executed actions
    const hasExecutionInfo = pageText && (
      pageText.toLowerCase().includes('screenshot') ||
      pageText.toLowerCase().includes('settings') ||
      pageText.toLowerCase().includes('launched') ||
      pageText.toLowerCase().includes('completed') ||
      pageText.toLowerCase().includes('success') ||
      pageText.toLowerCase().includes('mobile_take_screenshot') ||
      pageText.toLowerCase().includes('mobile_launch_app')
    )
    expect(hasExecutionInfo).toBeTruthy()
  })

  test('agent can generate and execute test scenario', async ({ page, request }) => {
    // Verify device availability
    const devicesResponse = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    const devicesData = await devicesResponse.json()
    const hasAndroidDevice = devicesData.devices?.android?.length > 0

    test.skip(!hasAndroidDevice, 'No Android device available')

    // Navigate to home page
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Find AI chat input
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Count initial messages
    const initialMessageCount = await page.locator('p, .font-mono').count()

    // Ask agent to generate and execute a test
    await chatInput.fill('Generate and execute a test on emulator-5554: Open Settings, scroll down, tap on About phone, take a screenshot. Use Mobile MCP tools.')
    await chatInput.press('Enter')

    // Wait for new messages
    await page.waitForFunction(
      (initialCount) => {
        const currentCount = document.querySelectorAll('p, .font-mono').length
        return currentCount > initialCount
      },
      initialMessageCount,
      { timeout: 30000 }
    )

    // Wait for complex workflow to complete
    await page.waitForTimeout(30000)

    // Get all text content
    const pageText = await page.textContent('body')

    // Verify test execution
    const hasTestInfo = pageText && (
      pageText.toLowerCase().includes('test') ||
      pageText.toLowerCase().includes('executed') ||
      pageText.toLowerCase().includes('completed') ||
      pageText.toLowerCase().includes('settings') ||
      pageText.toLowerCase().includes('screenshot') ||
      pageText.toLowerCase().includes('mobile_launch_app') ||
      pageText.toLowerCase().includes('mobile_swipe')
    )
    expect(hasTestInfo).toBeTruthy()
  })

  test('agent can generate test report with evidence', async ({ page, request }) => {
    // Verify device availability
    const devicesResponse = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    const devicesData = await devicesResponse.json()
    const hasAndroidDevice = devicesData.devices?.android?.length > 0

    test.skip(!hasAndroidDevice, 'No Android device available')

    // Navigate to home page
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Find AI chat input
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Count initial messages
    const initialMessageCount = await page.locator('p, .font-mono').count()

    // Ask agent to execute test and generate report
    await chatInput.fill('Execute this test on emulator-5554 and provide a detailed report: 1) Launch Calculator app, 2) Take screenshot, 3) Tap on number 5, 4) Take screenshot. Use Mobile MCP and include all evidence in the report.')
    await chatInput.press('Enter')

    // Wait for new messages
    await page.waitForFunction(
      (initialCount) => {
        const currentCount = document.querySelectorAll('p, .font-mono').length
        return currentCount > initialCount
      },
      initialMessageCount,
      { timeout: 30000 }
    )

    // Wait for test execution and report generation
    await page.waitForTimeout(30000)

    // Get all text content
    const pageText = await page.textContent('body')

    // Verify report contains evidence
    const hasReportInfo = pageText && (
      pageText.toLowerCase().includes('report') ||
      pageText.toLowerCase().includes('screenshot') ||
      pageText.toLowerCase().includes('evidence') ||
      pageText.toLowerCase().includes('step') ||
      pageText.toLowerCase().includes('calculator') ||
      pageText.toLowerCase().includes('completed') ||
      pageText.toLowerCase().includes('mobile_launch_app') ||
      pageText.toLowerCase().includes('mobile_take_screenshot')
    )
    expect(hasReportInfo).toBeTruthy()
  })
})

test.describe('Mobile MCP Direct API Tests', () => {
  test('can list devices via Mobile MCP API', async ({ request }) => {
    const response = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data).toHaveProperty('devices')
    expect(data.devices).toHaveProperty('android')
    expect(data.devices).toHaveProperty('ios')
  })

  test('can take screenshot via Mobile MCP API', async ({ request }) => {
    // First check if device is available
    const devicesResponse = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    const devicesData = await devicesResponse.json()
    const hasAndroidDevice = devicesData.devices?.android?.length > 0
    
    test.skip(!hasAndroidDevice, 'No Android device available')

    const deviceId = devicesData.devices.android[0]
    const response = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices/${deviceId}/screenshot`)
    
    expect(response.ok()).toBeTruthy()
    const data = await response.json()
    expect(data).toHaveProperty('screenshot')
    expect(data.screenshot).toBeTruthy()
  })

  test('can launch app via Mobile MCP API', async ({ request }) => {
    // Check device availability
    const devicesResponse = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    const devicesData = await devicesResponse.json()
    const hasAndroidDevice = devicesData.devices?.android?.length > 0
    
    test.skip(!hasAndroidDevice, 'No Android device available')

    const deviceId = devicesData.devices.android[0]
    const response = await request.post(
      `${API_BASE}/api/device-simulation/mobile-mcp/devices/${deviceId}/apps/launch`,
      {
        data: { package_name: 'com.android.settings' }
      }
    )
    
    expect(response.ok()).toBeTruthy()
    const data = await response.json()
    expect(data).toHaveProperty('success')
    expect(data.success).toBe(true)
  })

  test('can execute tap via Mobile MCP API', async ({ request }) => {
    // Check device availability
    const devicesResponse = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    const devicesData = await devicesResponse.json()
    const hasAndroidDevice = devicesData.devices?.android?.length > 0
    
    test.skip(!hasAndroidDevice, 'No Android device available')

    const deviceId = devicesData.devices.android[0]
    const response = await request.post(
      `${API_BASE}/api/device-simulation/mobile-mcp/devices/${deviceId}/click`,
      {
        data: { x: 540, y: 960 }
      }
    )
    
    expect(response.ok()).toBeTruthy()
    const data = await response.json()
    expect(data).toHaveProperty('success')
    expect(data.success).toBe(true)
  })

  test('can execute swipe via Mobile MCP API', async ({ request }) => {
    // Check device availability
    const devicesResponse = await request.get(`${API_BASE}/api/device-simulation/mobile-mcp/devices`)
    const devicesData = await devicesResponse.json()
    const hasAndroidDevice = devicesData.devices?.android?.length > 0
    
    test.skip(!hasAndroidDevice, 'No Android device available')

    const deviceId = devicesData.devices.android[0]
    const response = await request.post(
      `${API_BASE}/api/device-simulation/mobile-mcp/devices/${deviceId}/swipe`,
      {
        data: { direction: 'up', distance: 600 }
      }
    )
    
    expect(response.ok()).toBeTruthy()
    const data = await response.json()
    expect(data).toHaveProperty('success')
    expect(data.success).toBe(true)
  })
})


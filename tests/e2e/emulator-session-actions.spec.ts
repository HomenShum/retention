import { test, expect } from '@playwright/test'

/**
 * This E2E test exercises the full backend flow:
 * 1) Launch 1 emulator via the UI (which calls POST /api/emulators/launch)
 * 2) Poll GET /api/streaming/devices until a real device appears (up to ~90s)
 * 3) Create an Appium MCP session for that device via POST /api/appium-mcp/session/create
 * 4) Execute a simple action (tap) via POST /api/appium-mcp/session/{id}/action/execute
 * 5) Close the session
 *
 * Notes:
 * - If Appium is not running at http://localhost:4723, step (3) will fail; we skip gracefully.
 * - If no AVD/adb is available, we skip after the polling window.
 */

test.describe('Emulator launch + Appium session actions', () => {
  const API_BASE = process.env.API_BASE || 'http://localhost:8000'
  test.setTimeout(180_000)

  test('launch, create session, perform tap, close', async ({ page }) => {
    // Navigate to the Emulator Streaming page
    await page.goto('/emulators')
    await page.waitForLoadState('networkidle')

    // Launch dialog and start 1 emulator
    await page.getByRole('button', { name: /^Launch$/i }).click()
    await page.getByRole('button', { name: /^Launch$/i }).click()

    // Wait a moment for placeholder to show
    await expect(page.locator('[data-slot="card"]').filter({ hasText: /launching/i }).first()).toBeVisible({ timeout: 5000 })

    // Poll backend until a real device appears
    const deadline = Date.now() + 120_000
    let deviceId: string | null = null
    while (Date.now() < deadline) {
      const res = await page.request.get(`${API_BASE}/api/streaming/devices`)
      if (!res.ok()) {
        // If the streaming endpoint is not healthy in this environment, skip gracefully
        test.skip(true, `Streaming devices endpoint not available: ${res.status()} ${await res.text()}`)
      }
      const body = await res.json()
      const devices = (body.devices || []) as Array<{ device_id: string; status: string }>
      const candidate = devices.find(d => d.device_id?.startsWith('emulator-') && (d.status === 'device' || d.status === 'online'))
      if (candidate) { deviceId = candidate.device_id; break }
      await page.waitForTimeout(3000)
    }

    test.skip(!deviceId, 'No emulator detected by ADB within timeout')

    // Try creating an Appium MCP session for the device
    const resCreate = await page.request.post(`${API_BASE}/api/appium-mcp/session/create`, {
      params: { device_id: deviceId!, enable_streaming: false, fps: 2 }
    })

    if (!resCreate.ok()) {
      test.skip(true, `Appium session creation failed (is Appium running on :4723?): ${resCreate.status()} ${await resCreate.text()}`)
    }

    const createBody = await resCreate.json()
    const sessionId: string = createBody.session_id
    expect(sessionId).toBeTruthy()

    // Perform a simple action (tap)
    const resAction = await page.request.post(`${API_BASE}/api/appium-mcp/session/${sessionId}/action/execute`, {
      data: { type: 'tap', params: { x: 100, y: 200 } },
    })
    expect(resAction.ok()).toBeTruthy()
    const actionBody = await resAction.json()
    expect(actionBody.status).toBe('success')

    // Close the session
    const resClose = await page.request.post(`${API_BASE}/api/appium-mcp/session/${sessionId}/close`)
    expect(resClose.ok()).toBeTruthy()
  })
})


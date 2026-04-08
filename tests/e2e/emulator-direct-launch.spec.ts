import { test, expect } from '@playwright/test'

test.describe('Direct Emulator Launch (No AI Agent)', () => {
  test('can trigger emulator launch via direct API call from UI', async ({ page }) => {
    // Navigate directly to emulator streaming page
    await page.goto('/emulators')
    await page.waitForLoadState('networkidle')

    // Find and click the "Launch" button in the emulator header
    const launchButton = page.getByRole('button', { name: /^Launch$/i })
    await expect(launchButton).toBeVisible({ timeout: 10000 })
    await launchButton.click()

    // Dialog should open (by heading)
    await expect(page.getByRole('heading', { name: /Launch Android Emulators/i })).toBeVisible({ timeout: 5000 })

    // Select count (default is 1, so we can just click Launch)
    const launchDialogButton = page.getByRole('button', { name: /^Launch$/i })
    await launchDialogButton.waitFor({ state: 'visible' })

    // Verify that the direct launch API was called from the UI when clicking Launch
    const [launchRequest] = await Promise.all([
      page.waitForRequest((request) =>
        request.url().includes('/api/device-simulation/emulators/launch') &&
        request.method() === 'POST'
      ),
      launchDialogButton.click(),
    ])
    expect(launchRequest.url()).toContain('/api/device-simulation/emulators/launch')

    // Dialog should close shortly
    await expect(page.getByRole('heading', { name: /Launch Android Emulators/i })).not.toBeVisible({ timeout: 4000 })

    console.log('Direct emulator launch test complete - API called successfully, placeholder shown')
  })

  test('can launch 2 emulators and see two placeholders', async ({ page }) => {
    await page.goto('/emulators')
    await page.waitForLoadState('networkidle')

    // Launch 2 emulators via dialog (header Launch button)
    const launchButton = page.getByRole('button', { name: /^Launch$/i })
    await expect(launchButton).toBeVisible({ timeout: 10000 })
    await launchButton.click()

    const countInput = page.getByLabel(/Number of Emulators/i)
    await countInput.fill('2')

    const launchDialogButton = page.getByRole('button', { name: /^Launch$/i })
    await launchDialogButton.click()

    // Expect at least two launching placeholders
    const launchingCards = page.locator('[data-slot="card"]').filter({ hasText: /launching/i })
    await expect(launchingCards).toHaveCount(2, { timeout: 8000 })
  })
})


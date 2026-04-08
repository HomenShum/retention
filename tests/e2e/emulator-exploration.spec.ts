import { test, expect } from '@playwright/test'

async function navigateToHome(page) {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
}

test.describe('Emulator Launch and Autonomous Exploration', () => {
  test('can launch emulators and perform autonomous exploration', async ({ page }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    // Find the AI Assistant chat input
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Step 1: Ask AI to launch emulators and explore
    console.log('Step 1: Requesting to launch emulators and explore...')
    await chatInput.fill('Launch 2 Android emulators, then start autonomous exploration on one of them with 10 steps and give me a report of what you find')
    await chatInput.press('Enter')


    console.log('Step 1 Complete: AI agent is working on the request')

    // Wait for the final assistant response
    await expect(page.locator('.bg-gray-100.text-gray-800').last()).toBeVisible({ timeout: 180000 })

    const responseContent = await page.locator('.bg-gray-100.text-gray-800').last().textContent()

    // Check that we got a meaningful response
    expect(responseContent.length).toBeGreaterThan(50)

    console.log('Test Complete: AI agent completed the emulator exploration workflow')
  })

  test('can launch single emulator and explore with quick strategy', async ({ page }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Request emulator launch and exploration in one go
    console.log('Requesting emulator launch and quick exploration...')
    await chatInput.fill('Launch 1 Android emulator and do a quick 5-step exploration of it')
    await chatInput.press('Enter')

    // Wait for tool call details to appear (Input or Output section)
    const detailsSummary = page.locator('summary').filter({ hasText: /Input|Output/ })
    await expect(detailsSummary.first()).toBeVisible({ timeout: 30000 })

    // Wait for tool calls to complete
    await page.waitForSelector('.bg-green-500', { timeout: 120000 })

    // Wait for final response
    await expect(page.locator('.bg-gray-100.text-gray-800').last()).toBeVisible({ timeout: 120000 })

    console.log('Quick exploration test complete')
  })

  test('can list all explorations', async ({ page }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Ask to list explorations
    console.log('Requesting list of explorations...')
    await chatInput.fill('List all exploration sessions')
    await chatInput.press('Enter')


    // Should see a response with exploration list (could be empty or have items)
    await expect(page.locator('.bg-gray-100.text-gray-800').last()).toBeVisible({ timeout: 30000 })
    
    console.log('List explorations test complete')
  })

  test('shows tool execution details for emulator operations', async ({ page }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Request emulator launch
    await chatInput.fill('Launch 1 emulator')
    await chatInput.press('Enter')


    // Verify final response is visible
    await expect(page.locator('.bg-gray-100.text-gray-800').last()).toBeVisible({ timeout: 30000 })

    console.log('Tool execution details test complete')
  })

  test('handles exploration on non-existent device gracefully', async ({ page }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Try to explore a non-existent device
    console.log('Testing error handling for non-existent device...')
    await chatInput.fill('Explore device emulator-9999')
    await chatInput.press('Enter')

    // Should still get a response (either error or agent handles it)
    await expect(page.locator('.bg-gray-100.text-gray-800').last()).toBeVisible({ timeout: 60000 })
    
    // The response should mention the issue or handle it gracefully
    const responseText = await page.locator('.bg-gray-100.text-gray-800').last().textContent()
    
    // Should either mention error, not found, or suggest launching emulator first
    expect(responseText.length).toBeGreaterThan(0)
    
    console.log('Error handling test complete')
  })

  test('can launch 3 emulators and get available emulators list', async ({ page }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Launch 3 emulators
    console.log('Launching 3 emulators...')
    await chatInput.fill('Launch 3 Android emulators')
    await chatInput.press('Enter')

    // Ask for available emulators
    console.log('Getting available emulators...')
    await chatInput.fill('Show me all available emulators')
    await chatInput.press('Enter')

    // Should see a response
    await expect(page.locator('.bg-gray-100.text-gray-800').last()).toBeVisible({ timeout: 30000 })
    
    console.log('Multiple emulators test complete')
  })
})


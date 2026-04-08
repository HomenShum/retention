import { test, expect } from '@playwright/test'

async function navigateToHome(page) {
  await page.goto('/')
  // Wait for page to load - title might be "vite-project" or "Test Studio"
  await page.waitForLoadState('domcontentloaded')
}

test.describe('AI Chat Assistant', () => {
  test('shows tool names correctly when searching for bugs', async ({ page }) => {
    await navigateToHome(page)

    // Wait for the page to load
    await page.waitForLoadState('networkidle')

    // Find the AI Assistant chat input (placeholder is "Ask anything...")
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Type a message that will trigger a tool call and press Enter to send
    await chatInput.fill('search mobile bugs')
    await chatInput.press('Enter')

    // Wait for at least one tool call name to appear in the inspector
    await page.waitForSelector('.font-mono.text-\\[10px\\].text-purple-700', { timeout: 30000 })

    // Check if we see actual tool names instead of "unknown"
    const toolCallElements = await page.locator('.font-mono.text-\\[10px\\].text-purple-700').all()

    let foundToolName = false
    let foundUnknown = false

    for (const element of toolCallElements) {
      const text = (await element.textContent())?.trim()
      // Any non-empty name counts as a tool name; we only assert that it is not "unknown"
      if (text && text.length > 0) {
        foundToolName = true
      }
      if (text === 'unknown') {
        foundUnknown = true
      }
    }

    // We should find at least one actual tool name (not "unknown")
    expect(foundToolName).toBe(true)

    // We should NOT find "unknown"
    expect(foundUnknown).toBe(false)

    // Wait for the final assistant's response message (not in a tool call box)
    await expect(page.locator('.bg-gray-100.text-gray-800').last()).toBeVisible({ timeout: 20000 })
  })

  test('displays tool input and output details', async ({ page }) => {
    await navigateToHome(page)

    // Wait for the page to load
    await page.waitForLoadState('networkidle')

    // Find the AI Assistant chat input
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Type a message and press Enter to send
    await chatInput.fill('search for login bugs')
    await chatInput.press('Enter')

    // Wait for tool call details (Input or Output) to appear
    const detailsSummary = page.locator('summary').filter({ hasText: /Input|Output/ })
    await expect(detailsSummary.first()).toBeVisible({ timeout: 30000 })

    // Click to expand the first available details section
    await detailsSummary.first().click()

    // Should see some JSON content
    const inputContent = page.locator('pre').first()
    await expect(inputContent).toBeVisible()
    
    // Wait for output
    const outputSummary = page.locator('summary:has-text("Output")')
    await expect(outputSummary.first()).toBeVisible({ timeout: 15000 })
  })

  test('shows completed status with green indicator', async ({ page }) => {
    await navigateToHome(page)

    // Wait for the page to load
    await page.waitForLoadState('networkidle')

    // Find the AI Assistant chat input
    const chatInput = page.getByPlaceholder(/Ask anything/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Type a simple message and press Enter to send
    await chatInput.fill('search payment bugs')
    await chatInput.press('Enter')

    // Wait for the tool call to complete (green indicator)
    await page.waitForSelector('.bg-green-500', { timeout: 25000 })
    
    // Verify we have at least one completed tool call (green dot)
    const greenIndicators = await page.locator('.bg-green-500').count()
    expect(greenIndicators).toBeGreaterThan(0)
  })
})


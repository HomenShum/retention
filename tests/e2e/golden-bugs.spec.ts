import { test, expect } from '@playwright/test'

async function navigateToHome(page) {
  // Bypass the DemoGate by setting localStorage
  await page.goto('/')
  await page.evaluate(() => {
    localStorage.setItem('ta_trial_email', 'test@example.com')
  })
  await page.goto('/demo')
  await page.waitForLoadState('domcontentloaded')
}

async function navigateToAgentSessions(page) {
  // Bypass the DemoGate by setting localStorage
  await page.goto('/')
  await page.evaluate(() => {
    localStorage.setItem('ta_trial_email', 'test@example.com')
  })
  await page.goto('/demo/agent_sessions')
  await page.waitForLoadState('domcontentloaded')
}

test.describe('Golden Bug Workflows via AI Chat and Agent Sessions', () => {
  test('can list golden bugs via AI chat', async ({ page }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    const chatInput = page.getByPlaceholder(/Type a message/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Ask the assistant to list golden bugs
    await chatInput.fill('List all golden bugs')
    await chatInput.press('Enter')

    // Wait for final assistant response (excluding the greeting)
    const lastMessage = page.locator('.bg-white.text-gray-800').last()
    await expect(async () => {
      const text = (await lastMessage.textContent()) || ''
      expect(text.toLowerCase()).not.toContain('how can i help you')
      expect(text.toLowerCase()).toContain('golden')
    }).toPass({ timeout: 60000 })

    const text = (await lastMessage.textContent()) || ''
  })

  test('can run a specific golden bug via AI chat and see it in Agent Sessions', async ({ page, context }) => {
    await navigateToHome(page)
    await page.waitForLoadState('networkidle')

    const chatInput = page.getByPlaceholder(/Type a message/i)
    await expect(chatInput).toBeVisible({ timeout: 10000 })

    // Ask the assistant to run a specific golden bug (GOLDEN-001)
    await chatInput.fill('Run golden bug GOLDEN-001 and summarize the result')
    await chatInput.press('Enter')

    // Wait for final assistant response that should mention the bug id
    const lastMessage = page.locator('.bg-white.text-gray-800').last()
    await expect(async () => {
      const text = (await lastMessage.textContent()) || ''
      expect(text.toLowerCase()).not.toContain('how can i help you')
      expect(text).toMatch(/GOLDEN-001|golden bug/i)
    }).toPass({ timeout: 120000 })

    const text = (await lastMessage.textContent()) || ''

    // Open Agent Sessions page in same context to verify a session was recorded
    const sessionsPage = await context.newPage()
    await navigateToAgentSessions(sessionsPage)

    // Wait for sessions list to load
    const sessionsList = sessionsPage.locator('h3:text("Recent Sessions")')
    await expect(sessionsList).toBeVisible({ timeout: 15000 })

    // There should be at least one session
    const sessionTitles = sessionsPage.locator('button div.text-sm.font-medium')
    await expect(sessionTitles.first()).toBeVisible({ timeout: 15000 })

    // Best-effort check: one of the recent sessions should mention "golden" or the bug id in the title or goal
    const titlesCount = await sessionTitles.count()
    let foundGoldenSession = false
    for (let i = 0; i < titlesCount; i++) {
      const titleText = ((await sessionTitles.nth(i).textContent()) || '').toLowerCase()
      if (titleText.includes('golden') || titleText.includes('golden-001')) {
        foundGoldenSession = true
        break
      }
    }

    expect(foundGoldenSession).toBe(true)
  })
})


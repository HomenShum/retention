import { test, expect, type Page } from '@playwright/test'

const TICKET_ID = 'T243448315'
const TICKET_TITLE = '[BUG][Twilight Android] Background images missing from categories in Browse by genre section'

async function openBugWorkflowHome(page: Page) {
  await page.route('**/api/health', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok' }),
    })
  })

  await page.route('**/api/device-simulation/emulators', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        emulators: [{ id: 'emulator-5554', name: 'Pixel 8 API 35' }],
      }),
    })
  })

  await page.route('**/api/ai-agent/sessions', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        sessions: [
          {
            id: 'session-20260314-claim-repro',
            title: `Investigate ${TICKET_ID} exact-match regression`,
            createdAt: '2026-03-14 12:00:00',
            status: 'running',
            deviceId: 'pixel-8-android-15',
            goal: `Reproduce ${TICKET_TITLE} on the pinned exact device for ${TICKET_ID}`,
            steps: [
              { id: 1, stepNumber: 1, description: 'Claim the mirrored ticket context' },
              { id: 2, stepNumber: 2, description: 'Validate the exact device/build pair' },
            ],
          },
        ],
      }),
    })
  })

  await page.goto('/')
  await page.evaluate(() => {
    localStorage.setItem('ta_trial_email', 'test@example.com')
  })
  await page.goto('/demo/home')
  await page.waitForLoadState('domcontentloaded')
}

test.describe('Bug workflow UI', () => {
  test('surfaces truthful workflow state across home and detail views', async ({ page }) => {
    await openBugWorkflowHome(page)

    const ticketRow = page.locator('div.rounded-lg.border').filter({ hasText: TICKET_TITLE }).first()
    await expect(ticketRow).toBeVisible({ timeout: 15000 })

    await ticketRow.click()

    await expect(page.getByText('Exact match', { exact: true })).toBeVisible()
    await expect(page.getByText('Final verdict', { exact: true })).toBeVisible()
    await expect(page.getByText('Reproduction steps', { exact: true })).toBeVisible()
    await expect(page.getByText('Execution timeline', { exact: true })).toBeVisible()
    await expect(page.getByText('Fix verification checklist', { exact: true })).toBeVisible()

    await page.getByRole('tab', { name: 'Prompt' }).click()
    await expect(page.getByText('Progressive disclosure prompt plan', { exact: true })).toBeVisible()

    await page.getByRole('tab', { name: 'Evidence' }).click()
    await expect(page.getByText('Evidence and notes', { exact: true })).toBeVisible()

    await page.getByRole('heading', { name: TICKET_TITLE, exact: true }).first().click()
    await expect(page).toHaveURL(new RegExp(`/demo/ticket/${TICKET_ID}$`))

    await page.getByRole('tab', { name: 'Workflow' }).click()
    await expect(page.getByText('Live agent session match', { exact: true })).toBeVisible()
    await expect(page.getByText('session-20260314-claim-repro', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Open assistant handoff', exact: true }).click()
    await expect(page.getByText('Selected Devices (1)', { exact: true })).toBeVisible()
    await expect(page.getByText('Target devices', { exact: true })).toBeVisible()
    await expect(page.getByText(TICKET_ID, { exact: true }).first()).toBeVisible()
  })
})
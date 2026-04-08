import { test, expect } from '@playwright/test'

test.describe('Memory graph dashboard', () => {
  test('opens graph inspector when a demo node is clicked', async ({ page }) => {
    await page.setViewportSize({ width: 1100, height: 780 })
    await page.goto('/memory')

    await expect(page.getByRole('heading', { name: /Exploration Memory/i })).toBeVisible({ timeout: 15000 })
    await page.getByRole('button', { name: 'graph' }).click()

    const appSelect = page.locator('select').first()
    await expect(appSelect).toBeVisible({ timeout: 15000 })
    await appSelect.selectOption('demo_edgar_kyb_v2')

    await expect(page.getByText(/18 screens .* 10 transitions .* depth 5/i)).toBeVisible({ timeout: 15000 })

    await page.getByText('Filing Document').first().click()

    await expect(page.getByText('DEPTH 5 · screen_013')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/^Tapped 10-K filing$/).last()).toBeVisible()
    await expect(page.getByText('Download PDF')).toBeVisible()
    await expect(page.getByText('View Exhibits')).toBeVisible()
  })
})
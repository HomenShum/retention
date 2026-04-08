import { test, expect } from '@playwright/test'

// Helper to open the Vector Search tab from the home page
async function openVectorSearch(page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Vector Search' }).click()
  // Wait for stats block or loading to settle
  await expect(page.getByText('records')).toBeVisible({ timeout: 15000 })
}

test.describe('Vector Search Tab', () => {
  test('loads stats and records', async ({ page }) => {
    await openVectorSearch(page)
    // Stats show total records and embedding model/dims
    await expect(page.getByText('records')).toBeVisible()
    await expect(page.getByText('dims')).toBeVisible()

    // Ensure at least one record rendered (card list)
    // We look for status badges like Running/Failed/Finished or any record title element
    const anyRecord = page.locator('div').filter({ hasText: /\[(BUG|IPT|Web|Mobile|API|Desktop|iOS|Android)\]|% match|No embedded records yet/ })
    await expect(anyRecord.first()).toBeVisible()
  })

  test('performs a semantic search and shows match score', async ({ page }) => {
    await openVectorSearch(page)

    const input = page.getByPlaceholder('Search bug reports using AI semantic search...')
    await input.fill('login')
    await input.press('Enter')

    // Should either show results with % match or explicitly say No results found
    const matchBadge = page.getByText('% match')
    const noResults = page.getByText('No results found')

    await expect(matchBadge.or(noResults)).toBeVisible({ timeout: 20000 })
  })

  test('can refresh stats/records and toggle sort', async ({ page }) => {
    await openVectorSearch(page)

    // Refresh button
    await expect(page.getByRole('button', { name: 'Refresh' })).toBeVisible()
    await page.getByRole('button', { name: 'Refresh' }).click()

    // Toggle sort a couple of times
    await page.getByRole('button', { name: /Date/ }).click()
    await page.getByRole('button', { name: /Severity/ }).click()
    await page.getByRole('button', { name: 'Repros', exact: true }).click()
  })
})


import { chromium } from '@playwright/test';

async function testEmulatorExploration() {
  console.log('🚀 Starting emulator exploration test...\n');
  
  const browser = await chromium.launch({ 
    headless: false,
    slowMo: 500 // Slow down actions so we can see them
  });
  
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Navigate to the app
    console.log('📱 Navigating to http://localhost:5173...');
    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');

    // Find the AI chat input
    console.log('💬 Finding AI chat input...');
    const chatInput = page.getByPlaceholder(/Ask anything/i);
    await chatInput.waitFor({ state: 'visible', timeout: 10000 });

    // Request emulator launch and exploration
    console.log('🤖 Requesting: "Launch 2 Android emulators and explore one of them with 10 steps. Give me a detailed report of what you find including all apps and device features discovered."\n');
    await chatInput.fill('Launch 2 Android emulators and explore one of them with 10 steps. Give me a detailed report of what you find including all apps and device features discovered.');
    await chatInput.press('Enter');

    // Wait for tool calls to appear
    console.log('⏳ Waiting for AI agent to process request...');
    await page.waitForSelector('.bg-purple-50\\/50', { timeout: 30000 });
    console.log('✅ Tool calls started!\n');

    // Wait for emulator launch to complete
    console.log('⏳ Waiting for emulators to launch...');
    await page.waitForTimeout(5000); // Wait a bit for the launch to start

    // Navigate to Emulators tab to see the visual streams
    console.log('📺 Navigating to Emulators tab...');
    const emulatorsTab = page.getByRole('link', { name: /emulators/i });
    await emulatorsTab.click();
    await page.waitForLoadState('networkidle');
    console.log('✅ On Emulators tab!\n');

    // Wait for emulators to boot (60 seconds)
    console.log('⏳ Waiting 60 seconds for emulators to fully boot...');
    await page.waitForTimeout(60000);
    console.log('✅ Emulators should be booted!\n');

    // Go back to home to check exploration progress
    console.log('🏠 Going back to Home tab...');
    const homeTab = page.getByRole('link', { name: /home/i });
    await homeTab.click();
    await page.waitForLoadState('networkidle');

    // Wait for exploration to complete (this could take a while)
    console.log('⏳ Waiting for exploration to complete (up to 5 minutes)...');
    await page.waitForSelector('.bg-green-500', { timeout: 300000 });
    console.log('✅ Exploration completed!\n');

    // Wait for final response
    console.log('⏳ Waiting for final AI response...');
    await page.waitForSelector('.bg-gray-100.text-gray-800', { timeout: 60000 });
    console.log('✅ Response received!\n');

    // Get all tool call boxes
    const toolCallBoxes = await page.locator('.bg-purple-50\\/50').all();
    console.log(`📊 Tool Calls Made: ${toolCallBoxes.length}\n`);

    // Get the final response
    const responses = await page.locator('.bg-gray-100.text-gray-800').all();
    const finalResponse = responses[responses.length - 1];
    const responseText = await finalResponse.textContent();

    console.log('\n' + '='.repeat(80));
    console.log('📋 EXPLORATION REPORT');
    console.log('='.repeat(80));
    console.log(responseText);
    console.log('='.repeat(80));

    // Take a screenshot of the report
    console.log('\n📸 Taking screenshot of report...');
    await page.screenshot({
      path: 'test-results/emulator-exploration-report.png',
      fullPage: true
    });
    console.log('✅ Screenshot saved to test-results/emulator-exploration-report.png');

    // Navigate back to Emulators tab to see the visual streams
    console.log('\n📺 Navigating to Emulators tab to see visual streams...');
    const emulatorsTabFinal = page.getByRole('link', { name: /emulators/i });
    await emulatorsTabFinal.click();
    await page.waitForLoadState('networkidle');

    // Take screenshot of emulator streams
    console.log('📸 Taking screenshot of emulator streams...');
    await page.waitForTimeout(2000); // Wait for streams to load
    await page.screenshot({
      path: 'test-results/emulator-streams.png',
      fullPage: true
    });
    console.log('✅ Screenshot saved to test-results/emulator-streams.png');

    // Keep browser open for 2 minutes so user can see the result
    console.log('\n⏸️  Keeping browser open for 2 minutes so you can see the emulators...');
    console.log('    Press Ctrl+C to close early if needed.');
    await page.waitForTimeout(120000);

  } catch (error) {
    console.error('❌ Error:', error);
    
    // Take error screenshot
    await page.screenshot({ 
      path: 'test-results/emulator-exploration-error.png',
      fullPage: true 
    });
    console.log('📸 Error screenshot saved to test-results/emulator-exploration-error.png');
  } finally {
    await browser.close();
    console.log('\n✅ Test complete!');
  }
}

testEmulatorExploration();


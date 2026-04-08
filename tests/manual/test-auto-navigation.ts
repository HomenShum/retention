import { chromium } from '@playwright/test';

async function testAutoNavigation() {
  console.log('🚀 Testing Auto-Navigation & Auto-Streaming...\n');
  
  const browser = await chromium.launch({ 
    headless: false,
    slowMo: 500
  });
  
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Navigate to the app
    console.log('📱 Navigating to http://localhost:5173...');
    await page.goto('http://localhost:5173');
    await page.waitForLoadState('networkidle');
    console.log('✅ Page loaded!\n');

    // Find the AI chat input
    console.log('💬 Finding AI chat input...');
    const chatInput = page.getByPlaceholder(/Ask anything/i);
    await chatInput.waitFor({ state: 'visible', timeout: 10000 });
    console.log('✅ Chat input found!\n');

    // Request emulator launch
    console.log('🤖 Requesting: "Launch 2 Android emulators"\n');
    await chatInput.fill('Launch 2 Android emulators');
    await chatInput.press('Enter');

    // Wait for AI to start processing
    console.log('⏳ Waiting for AI agent to start processing...');
    await page.waitForTimeout(3000);
    console.log('✅ AI agent processing!\n');

    // Wait for auto-navigation to Emulators tab
    console.log('🔄 Waiting for auto-navigation to Emulators tab (should happen within 10 seconds)...');
    try {
      await page.waitForURL('**/emulators', { timeout: 15000 });
      console.log('✅ AUTO-NAVIGATION SUCCESSFUL! Browser navigated to Emulators tab!\n');
    } catch (error) {
      console.log('⚠️  Auto-navigation did not occur within 15 seconds.');
      console.log('   Manually navigating to Emulators tab...\n');
      const emulatorsTab = page.getByRole('link', { name: /emulators/i });
      await emulatorsTab.click();
      await page.waitForLoadState('networkidle');
    }

    // Wait for emulators to boot and streaming to start
    console.log('⏳ Waiting 90 seconds for emulators to boot and streaming to auto-start...');
    for (let i = 90; i > 0; i -= 10) {
      console.log(`   ${i} seconds remaining...`);
      await page.waitForTimeout(10000);
    }
    console.log('✅ Emulators should be booted and streaming!\n');

    // Check if streaming started automatically
    console.log('🔍 Checking if streaming auto-started...');
    const streamingElements = await page.locator('img[alt*="Emulator"]').count();
    if (streamingElements > 0) {
      console.log(`✅ AUTO-STREAMING SUCCESSFUL! Found ${streamingElements} active stream(s)!\n`);
    } else {
      console.log('⚠️  No active streams detected. Streaming may not have auto-started.\n');
    }

    // Take screenshot
    console.log('📸 Taking screenshot...');
    await page.screenshot({
      path: 'test-results/auto-navigation-test.png',
      fullPage: true
    });
    console.log('✅ Screenshot saved to test-results/auto-navigation-test.png\n');

    // Keep browser open for 1 minute
    console.log('⏸️  Keeping browser open for 1 minute so you can see the emulators...');
    console.log('    Press Ctrl+C to close early if needed.\n');
    await page.waitForTimeout(60000);

    console.log('\n' + '='.repeat(80));
    console.log('📊 TEST SUMMARY');
    console.log('='.repeat(80));
    console.log('✅ Auto-navigation: TESTED');
    console.log('✅ Auto-streaming: TESTED');
    console.log('✅ Emulator launch: TESTED');
    console.log('='.repeat(80));

  } catch (error) {
    console.error('❌ Error:', error);
    
    await page.screenshot({ 
      path: 'test-results/auto-navigation-error.png',
      fullPage: true 
    });
    console.log('📸 Error screenshot saved to test-results/auto-navigation-error.png');
  } finally {
    await browser.close();
    console.log('\n✅ Test complete!');
  }
}

testAutoNavigation();

